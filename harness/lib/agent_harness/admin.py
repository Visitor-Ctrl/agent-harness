"""Administrator release installation and explicit profile provisioning."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[2]
PREFIX = Path('/opt/agent-harness')
ETC = Path('/etc/agent-harness')
SYSTEM = Path('/etc/systemd/system')
USER_UNITS = Path('/etc/systemd/user')
HOME_ROOT = Path('/home')
NAME = re.compile(r'[a-z][a-z0-9-]{0,30}\Z')
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?\Z')


def root_required():
    if os.geteuid() != 0:
        raise ValueError('administrator privileges required; use --dry-run to review')


def trusted(path):
    """Require static root-owned ancestors; installers must run from trusted code."""
    path = path.absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('symlink in administrator destination')
        if part.exists():
            st = part.stat()
            if st.st_uid != 0 or st.st_mode & 0o022 or not part.is_dir():
                raise ValueError('administrator destination must have root-owned unwritable ancestors')


def run_tests(source):
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                    str(source / 'tests')], check=True, cwd=source.parent)


def safe_source(source):
    for path in source.rglob('*'):
        if '__pycache__' in path.parts or '.git' in path.parts:
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('release source must contain regular files and directories only')


def protected_release(target):
    trusted(target)
    for path in target.rglob('*'):
        st = path.stat()
        if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('rollback release is not root-owned and protected')


def install(args):
    prefix = args.prefix.absolute()
    version = args.rollback or (SOURCE / 'VERSION').read_text().strip()
    if not VERSION.fullmatch(version):
        raise ValueError('invalid release version')
    source = prefix / version if args.rollback else SOURCE
    if args.rollback and (source.is_symlink() or not source.is_dir()):
        raise ValueError('rollback release does not exist')
    if args.rollback:
        protected_release(source)
    safe_source(source)
    # This is deliberately also run during --dry-run. Failure precedes all writes.
    run_tests(source)
    plan = {'action': 'rollback' if args.rollback else 'install', 'version': version,
            'release': str(prefix / version), 'current': str(prefix / 'current'),
            'system_units': str(SYSTEM), 'user_units': str(USER_UNITS),
            'activate_services': False}
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    root_required()
    for directory in (prefix, SYSTEM, USER_UNITS):
        trusted(directory)
    target = prefix / version
    if not args.rollback and target.exists():
        raise ValueError('release already exists; choose a new VERSION or --rollback VERSION')
    current = prefix / 'current'
    if current.exists() and not current.is_symlink():
        raise ValueError('current must be a release symlink')
    prefix.mkdir(parents=True, exist_ok=True, mode=0o755)
    if not args.rollback:
        stage = Path(tempfile.mkdtemp(prefix='.install-', dir=prefix))
        try:
            for name in ('bin', 'lib', 'config', 'templates', 'systemd', 'tests', 'docs', 'extras'):
                if (source / name).exists():
                    shutil.copytree(source / name, stage / name,
                                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            for name in ('VERSION', 'README.md', 'install.sh'):
                shutil.copyfile(source / name, stage / name)
            for path in [stage, *stage.rglob('*')]:
                path.chmod(0o755 if path.is_dir() or path.parent == stage / 'bin'
                           or path.name == 'install.sh' else 0o644)
                os.chown(path, 0, 0)
            stage.rename(target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    # Stage unit files before switching current; service activation is explicit.
    for srcdir, destdir in ((target / 'systemd', SYSTEM), (target / 'systemd/user', USER_UNITS)):
        destdir.mkdir(parents=True, exist_ok=True)
        for unit in srcdir.glob('*'):
            if unit.is_file():
                dest = destdir / unit.name
                if dest.is_symlink():
                    raise ValueError('unit destination is a symlink')
                content = unit.read_text().replace('/opt/agent-harness/current', str(prefix / 'current'))
                atomic_text(dest, content, 0o644)
    # Use a unique sibling and atomic replacement, including first installation.
    link = prefix / ('.current-' + str(os.getpid()))
    try:
        link.symlink_to(version)
        link.replace(current)
    finally:
        link.unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    return 0


def atomic_text(path, text, mode):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text)
            os.fchmod(out.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def add_profile(args):
    name = args.name
    if not NAME.fullmatch(name) or name == 'root':
        raise ValueError('profile must be a lowercase Linux user name, other than root')
    root = args.root.absolute()
    if not root.is_absolute() or root == Path('/'):
        raise ValueError('use an absolute dedicated project root')
    try:
        pwd.getpwnam(name)
    except KeyError:
        pass
    else:
        raise ValueError('Linux user already exists; automatic adoption is refused')
    home, project = HOME_ROOT / name, root / name
    registry = ETC / 'profiles' / (name + '.json')
    if any(p.exists() or p.is_symlink() for p in (home, project, registry)):
        raise ValueError('profile destination already exists; inspect partial provisioning manually')
    plan = {'profile': name, 'home': str(home), 'project': str(project),
            'mode': '0700', 'group': 'agent-heavy', 'MemoryHigh': args.memory_high,
            'CPUWeight': args.cpu_weight, 'TasksMax': args.tasks_max,
            'config': str(home / '.config/agent-harness')}
    print(json.dumps(plan, indent=2))
    if args.dry_run:
        return 0
    root_required()
    for directory in (HOME_ROOT, root, ETC, SYSTEM):
        trusted(directory)
    subprocess.run(['groupadd', '--force', 'agent-heavy'], check=True)
    subprocess.run(['useradd', '--create-home', '--home-dir', str(home),
                    '--shell', '/bin/bash', '--groups', 'agent-heavy', name], check=True)
    account = pwd.getpwnam(name)
    home.chmod(0o700)
    project.mkdir(parents=True, mode=0o700)
    os.chown(project, account.pw_uid, account.pw_gid)
    config = home / '.config/agent-harness'
    config.mkdir(parents=True, mode=0o700)
    for src in (SOURCE / 'config').glob('*.example.toml'):
        text = src.read_text()
        if src.name == 'guard.example.toml':
            text = text.replace('profile = "example"', 'profile = ' + json.dumps(name))
            text = text.replace('root = "/srv/projects/example"', 'root = ' + json.dumps(str(project)))
        if src.name in ('herdr.example.toml', 'coordinator.example.toml'):
            text = re.sub(r'^cwd = .*$', 'cwd = ' + json.dumps(str(project)), text, flags=re.M)
        atomic_text(config / src.name.replace('.example', ''), text, 0o600)
    for name_in_template in ('CLAUDE.md', 'AGENTS.md', 'COORDINATOR-STATE.md', 'HANDOVER.md', 'NORTH-STAR.md'):
        shutil.copyfile(SOURCE / 'templates' / name_in_template, project / name_in_template)
        (project / name_in_template).chmod(0o600)
        os.chown(project / name_in_template, account.pw_uid, account.pw_gid)
    for path in [home / '.config', config, *config.iterdir()]:
        os.chown(path, account.pw_uid, account.pw_gid)
    dropin = SYSTEM / f'user-{account.pw_uid}.slice.d'
    trusted(dropin)
    dropin.mkdir(parents=True, mode=0o755)
    atomic_text(dropin / '50-agent-harness.conf',
                f'[Slice]\nMemoryHigh={args.memory_high}\nCPUWeight={args.cpu_weight}\nTasksMax={args.tasks_max}\n', 0o644)
    registry.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    plan['uid'] = account.pw_uid
    atomic_text(registry, json.dumps(plan, indent=2) + '\n', 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    return 0


def doctor():
    checks = {'python_3_11': sys.version_info >= (3, 11),
              'release_version': (SOURCE / 'VERSION').is_file()}
    for command in ('herdr', 'systemctl', 'git'):
        checks['command_' + command] = shutil.which(command) is not None
    cfg = Path(os.environ.get('HARNESS_CONFIG_DIR', '~/.config/agent-harness')).expanduser()
    for name in ('engines', 'pool', 'dispatch', 'herdr', 'coordinator'):
        path = cfg / (name + '.toml')
        try:
            import tomllib
            with path.open('rb') as stream:
                tomllib.load(stream)
            checks['config_' + name] = True
        except (OSError, ValueError):
            checks['config_' + name] = False
    print(json.dumps({'checks': checks, 'scope': 'local prerequisites only; run coordinator-health and isolation acceptance checks'}, indent=2))
    return 0 if all(checks.values()) else 1


def memory(value):
    if not re.fullmatch(r'[1-9][0-9]*[KMGT]?', value):
        raise argparse.ArgumentTypeError('use a positive byte count or K/M/G/T size')
    return value


def main(argv=None, installer=False):
    parser = argparse.ArgumentParser(description=__doc__)
    if installer:
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--prefix', type=Path, default=PREFIX)
        parser.add_argument('--rollback', metavar='VERSION')
    else:
        commands = parser.add_subparsers(dest='command', required=True)
        commands.add_parser('version')
        commands.add_parser('doctor')
        profiles = commands.add_parser('profile').add_subparsers(dest='action', required=True)
        profiles.add_parser('list')
        add = profiles.add_parser('add')
        add.add_argument('name')
        add.add_argument('--dry-run', action='store_true')
        add.add_argument('--root', type=Path, default=Path(os.environ.get('HARNESS_ROOT', '/srv/projects')))
        add.add_argument('--memory-high', type=memory, default='8G')
        add.add_argument('--cpu-weight', type=int, choices=range(1, 10001), default=100, metavar='1..10000')
        add.add_argument('--tasks-max', type=int, choices=range(1, 1000001), default=4096, metavar='1..1000000')
    args = parser.parse_args(argv)
    try:
        if installer:
            return install(args)
        if args.command == 'version':
            print((SOURCE / 'VERSION').read_text().strip())
        elif args.command == 'doctor':
            return doctor()
        elif args.action == 'add':
            return add_profile(args)
        else:
            for path in sorted((ETC / 'profiles').glob('*.json')):
                data = json.loads(path.read_text())
                print(f"{data['profile']}\t{data['uid']}\t{data['project']}")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'harness: {exc}', file=sys.stderr)
        return 64
