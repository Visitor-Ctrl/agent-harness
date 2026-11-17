"""Root-owned hook installation and settings-template rendering."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

from .guard import EVENTS
from .rules import Policy


def settings_block(command):
    return {event: [{'matcher': '*', 'hooks': [{'type': 'command', 'command': command, 'timeout': 10}]}]
            for event in EVENTS}


def render_settings(settings, command):
    result = dict(settings)
    # Installer owns this block. Re-running it never duplicates event handlers.
    result['hooks'] = settings_block(command)
    permissions = dict(result.get('permissions', {}))
    permissions['defaultMode'] = 'bypassPermissions'
    permissions['deny'] = sorted(set(permissions.get('deny', [])) | {
        'Read(**/.env)', 'Read(**/.env.*)', 'Read(**/.credentials.json)',
        'Read(**/.codex/auth.json)', 'Read(**/.ssh/id_*)',
        'Edit(/opt/agent-harness/**)', 'Write(/opt/agent-harness/**)',
        'Edit(**/.claude/settings*.json)', 'Write(**/.claude/settings*.json)',
    })
    result['permissions'] = permissions
    return result


def _safe_parent(path):
    # Reject symlinks in every existing ancestor before privileged writes.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Installer paths cannot contain symbolic links')
        if item.exists() and (item.stat().st_uid != 0 or item.stat().st_mode & 0o022):
            raise ValueError('Installer paths must be root owned and not group/world writable')


def install(source, destination, config, settings, output):
    if os.geteuid() != 0:
        raise PermissionError('Run guard-install as root, or use --dry-run to review settings')
    Policy.load(config)
    destination, output = Path(destination).absolute(), Path(output).absolute()
    _safe_parent(destination)
    _safe_parent(output)
    if destination.exists():
        raise ValueError('Destination already exists; install to a new version directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.guard-install-', dir=destination.parent))
    try:
        package = stage / 'lib/agent_harness/guard'
        package.mkdir(parents=True)
        (package.parent / '__init__.py').write_text('"""Agent harness."""\n')
        for item in (source / 'lib/agent_harness/guard').glob('*.py'):
            if item.is_symlink():
                raise ValueError('Source links are not accepted')
            shutil.copyfile(item, package / item.name)
        (stage / 'bin').mkdir()
        shutil.copyfile(source / 'bin/guard-hook', stage / 'bin/guard-hook')
        (stage / 'config').mkdir()
        shutil.copyfile(config, stage / 'config/guard.toml')
        for root, dirs, files in os.walk(stage):
            os.chown(root, 0, 0)
            os.chmod(root, 0o755)
            for name in files:
                path = Path(root) / name
                os.chown(path, 0, 0)
                os.chmod(path, 0o755 if path.parent.name == 'bin' else 0o644)
        command = shlex.join([str(destination / 'bin/guard-hook'), '--config', str(destination / 'config/guard.toml')])
        rendered = render_settings(settings, command)
        stage.rename(destination)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.guard-settings-', dir=output.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(rendered, stream, indent=2)
                stream.write('\n')
                os.fchmod(stream.fileno(), 0o644)
                os.fchown(stream.fileno(), 0, 0)
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = Path(__file__).resolve().parents[3]
    parser.add_argument('--source', type=Path, default=source)
    parser.add_argument('--destination', type=Path, default=Path('/opt/agent-harness/guard'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--settings-template', type=Path)
    parser.add_argument('--output', type=Path, default=Path('/opt/agent-harness/settings.template.json'))
    parser.add_argument('--dry-run', action='store_true', help='Print rendered settings; no writes or privilege required')
    args = parser.parse_args(argv)
    try:
        config = args.config or args.source / 'config/guard.example.toml'
        Policy.load(config)
        template = args.settings_template or args.source / 'templates/settings.template.json'
        settings = json.loads(template.read_text())
        command = shlex.join([str(args.destination / 'bin/guard-hook'), '--config', str(args.destination / 'config/guard.toml')])
        if args.dry_run:
            print(json.dumps(render_settings(settings, command), indent=2))
        else:
            install(args.source, args.destination, config, settings, args.output)
        return 0
    except (OSError, ValueError):
        parser.exit(1, 'guard-install: installation rejected; check permissions, policy and paths.\n')
