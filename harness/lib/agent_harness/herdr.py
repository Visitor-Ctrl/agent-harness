"""Small, JSON-based helpers for the herdr command line."""
import argparse
import fcntl
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tomllib


class Herdr:
    def __init__(self, config):
        self.cfg = {}
        path = config / 'herdr.toml'
        if path.exists():
            with path.open('rb') as stream:
                self.cfg = tomllib.load(stream).get('herdr', {})
        self.command = self.cfg.get('command', ['herdr'])
        if not isinstance(self.command, list) or not self.command or not all(
                isinstance(x, str) and x for x in self.command):
            raise ValueError('herdr.command must be a nonempty argv array')

    def call(self, session, *args):
        result = subprocess.run(self.command + ['--session', session, *args],
                                capture_output=True, text=True, check=True, timeout=15)
        return json.loads(result.stdout or '{}').get('result', {})

    def workspace(self, session, label, cwd):
        workspaces = self.call(session, 'workspace', 'list').get('workspaces', [])
        for item in workspaces:
            if item.get('label') == label:
                return item['workspace_id']
        return self.call(session, 'workspace', 'create', '--cwd', str(cwd),
                         '--label', label, '--no-focus')['workspace']['workspace_id']

    def launch(self, cwd, label, command):
        session = self.cfg.get('session', 'workers')
        workspace = self.workspace(session, self.cfg.get('workspace', 'workers'), cwd)
        tab = self.call(session, 'tab', 'create', '--workspace', workspace,
                        '--cwd', str(cwd), '--label', label, '--no-focus')
        self.call(session, 'pane', 'run', tab['root_pane']['pane_id'], command)
        return tab['tab']['tab_id']


def main(kind, argv=None):
    parser = argparse.ArgumentParser(description=f'Ensure herdr {kind}')
    parser.add_argument('--config-dir', type=Path, default=Path(os.environ.get(
        'HARNESS_CONFIG_DIR', '~/.config/agent-harness')).expanduser())
    parser.add_argument('--cwd', type=Path)
    args = parser.parse_args(argv)
    try:
        herdr = Herdr(args.config_dir)
        coordinator = kind == 'coordinator'
        session = herdr.cfg.get('coordinator_session', 'coordinator') if coordinator else herdr.cfg.get('session', 'workers')
        label = 'coordinator' if coordinator else herdr.cfg.get('workspace', 'workers')
        cwd = (args.cwd or Path(herdr.cfg.get('cwd', '.')).expanduser()).resolve(strict=True)
        state = Path(os.environ.get('HARNESS_HERDR_STATE_DIR', '~/.local/state/agent-harness/herdr')).expanduser()
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (state / 'ensure.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            workspace = herdr.workspace(session, label, cwd)
            if coordinator:
                tabs = herdr.call(session, 'tab', 'list', '--workspace', workspace).get('tabs', [])
                if not any(t.get('label') == 'coordinator' for t in tabs):
                    tab = herdr.call(session, 'tab', 'create', '--workspace', workspace,
                                     '--cwd', str(cwd), '--label', 'coordinator', '--no-focus')
                    command = herdr.cfg.get('coordinator_command')
                    if command:
                        if not isinstance(command, list) or not all(isinstance(x, str) and x for x in command):
                            raise ValueError('coordinator_command must be an argv array')
                        herdr.call(session, 'pane', 'run', tab['root_pane']['pane_id'], shlex.join(command))
            print(workspace)
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'herdr-ensure-{kind}: {exc}', file=sys.stderr)
        return 64
