"""Persistent batch workers routed exclusively through ai-pool."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
import uuid

from .herdr import Herdr
from .pool import atomic

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = {'starting', 'running'}


def identity(pid):
    try:
        # comm may contain spaces or parentheses; fields after its final ')' are stable.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def summary(text):
    matches = list(re.finditer(r'^## Summary[ \t]*$', text, re.M))
    if not matches:
        return None
    section = text[matches[-1].start():].strip()
    if re.search(r'^#{1,2} ', section.split('\n', 1)[-1], re.M):
        return None
    return section + '\n'


class Dispatch:
    def __init__(self, config, state):
        self.config, self.state = config.resolve(), state.resolve()
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cfg = {}
        path = self.config / 'dispatch.toml'
        if path.exists():
            with path.open('rb') as stream:
                self.cfg = tomllib.load(stream).get('dispatch', {})
        self.cap = self.cfg.get('max_workers', 3)
        if type(self.cap) is not int or not 1 <= self.cap <= 1024:
            raise ValueError('dispatch.max_workers must be an integer from 1 to 1024')

    @contextlib.contextmanager
    def locked(self):
        with (self.state / 'registry.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def path(self, name):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', name):
            raise ValueError('job name must contain lowercase letters, digits, or hyphens')
        return self.state / name

    def read(self, name):
        data = json.loads((self.path(name) / 'meta.json').read_text())
        if data['status'] == 'running' and identity(data['pid']) != data['identity']:
            data['status'] = 'lost'
            self.save(name, data)
        elif data['status'] == 'starting' and time.time() - data['created'] > 30:
            data['status'] = 'lost'
            self.save(name, data)
        return data

    def save(self, name, data):
        atomic(self.path(name) / 'meta.json', json.dumps(data).encode())

    def all(self):
        return [self.read(p.parent.name) for p in sorted(self.state.glob('*/meta.json'))
                if p.parent.name != 'archive']

    def context(self, key, default, cwd):
        path = Path(self.cfg.get(key, default)).expanduser()
        if not path.is_absolute():
            path = cwd / path
        return path.read_text() if path.exists() else f'(No {default} found; verify the approved task context.)'

    def start(self, args):
        cwd = args.cwd.resolve(strict=True)
        with (self.config / 'engines.toml').open('rb') as stream:
            engines = tomllib.load(stream)['engines']
        if args.engine not in engines:
            raise ValueError(f'unknown engine: {args.engine}')
        if not args.model.strip():
            raise ValueError('model must not be empty')
        task = args.task_file.read_text() if args.task_file else args.task
        if not task or not task.strip():
            raise ValueError('a nonempty --task or --task-file is required')
        job = self.path(args.name)
        with self.locked():
            previous = '(Fresh task.)'
            old = None
            if (job / 'meta.json').exists():
                old = self.read(args.name)
                if old['status'] in ACTIVE:
                    raise ValueError('job is already active')
            if args.action == 'continue':
                if old is None:
                    raise ValueError('cannot continue an unknown job')
                handover = Path(old['attempt']) / 'HANDOVER.md'
                if not handover.is_file():
                    raise ValueError('previous HANDOVER.md is required for continuation')
                previous = handover.read_text()
            elif old is not None:
                raise ValueError('job exists; use continue or clean')
            if sum(item['status'] in ACTIVE for item in self.all()) >= self.cap:
                raise ValueError('dispatch concurrency cap reached')
            attempt = job / uuid.uuid4().hex
            attempt.mkdir(parents=True, mode=0o700)
            template = Path(self.cfg.get('template', ROOT / 'templates' / 'brief.md')).expanduser()
            if not template.is_absolute():
                template = cwd / template
            brief = template.read_text().format(
                name=args.name, engine=args.engine, model=args.model, effort=args.effort or '(default)',
                cwd=cwd, task=task, previous_handover=previous, handover=attempt / 'HANDOVER.md',
                north_star=self.context('north_star', 'NORTH-STAR.md', cwd),
                operating_context=self.context('operating_context', 'AGENTS.md', cwd),
                state_excerpt=self.context('state_excerpt', 'COORDINATOR-STATE.md', cwd),
                stop_rule=self.cfg.get('stop_rule', 'Complete the task, write HANDOVER.md, and stop.'))
            (attempt / 'brief.md').write_text(brief)
            (attempt / 'task.md').write_text(task)
            data = dict(name=args.name, engine=args.engine, model=args.model, effort=args.effort,
                        heavy=args.heavy, cwd=str(cwd), attempt=str(attempt), status='starting',
                        created=time.time())
            self.save(args.name, data)
        command = [sys.executable, str(ROOT / 'bin' / 'dispatch'), '--config-dir', str(self.config),
                   '--state-dir', str(self.state), '_run', args.name, str(attempt)]
        try:
            tab = Herdr(self.config).launch(cwd, 'dispatch:' + args.name, shlex.join(command))
            print(f'{args.name}: herdr tab {tab}; {attempt}')
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            print(f'dispatch: herdr unavailable ({exc}); running detached', file=sys.stderr)
            try:
                with (attempt / 'launcher.log').open('ab') as log:
                    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=log, start_new_session=True)
            except OSError:
                with self.locked():
                    data = self.read(args.name)
                    data['status'] = 'failed'
                    self.save(args.name, data)
                raise
            print(f'{args.name}: detached; {attempt}')
        return 0

    def run(self, name, attempt):
        if os.getpgrp() != os.getpid():
            os.setsid()
        with self.locked():
            data = self.read(name)
            if data['attempt'] != str(attempt) or data['status'] != 'starting':
                return 0  # cancelled, superseded, or duplicate pane command
            data.update(status='running', pid=os.getpid(), identity=identity(os.getpid()))
            self.save(name, data)
        command = [str(ROOT / 'bin' / 'ai-pool'), '--config-dir', str(self.config), 'run',
                   '--engine', data['engine'], '--model', data['model']]
        if data['effort']:
            command += ['--effort', data['effort']]
        if data['heavy']:
            command = [str(ROOT / 'bin' / 'heavy-lock'), '--'] + command
        rc = 64
        try:
            with (attempt / 'brief.md').open('rb') as brief, (attempt / 'output.log').open('wb') as output, (attempt / 'stderr.log').open('wb') as errors:
                rc = subprocess.call(command, stdin=brief, stdout=output, stderr=errors, cwd=data['cwd'])
            text = (attempt / 'output.log').read_text(errors='replace')
            # Honor the same terminal-result schema as the pool without engine branches.
            with (self.config / 'engines.toml').open('rb') as stream:
                schema = tomllib.load(stream)['engines'][data['engine']].get('result', {})
            if schema:
                for line in text.splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get(schema.get('type_key', 'type')) == schema.get('type_value', 'result'):
                        value = event.get(schema.get('text_key', 'result'))
                        if isinstance(value, str):
                            text = value
            if summary(text) is None:
                text = text.rstrip() + '\n\n## Summary\nWorker output did not end with a valid Summary section; inspect output.log and HANDOVER.md.\n'
                rc = rc or 65
        except (OSError, ValueError) as exc:
            text = f'## Summary\nWorker launch failed: {exc}\n'
        atomic(attempt / 'result.md', text.encode())
        with self.locked():
            current = self.read(name)
            if current['attempt'] == str(attempt) and current['status'] == 'running':
                current.update(status='done' if rc == 0 else 'failed', exit_code=rc, finished=time.time())
                self.save(name, current)
        return rc if rc >= 0 else 128 - rc

    def action(self, args):
        with self.locked():
            if args.action == 'status':
                items = [self.read(args.name)] if args.name else self.all()
                print(json.dumps(items, indent=2))
                return 0
            data = self.read(args.name)
            if args.action == 'summary':
                text = summary((Path(data['attempt']) / 'result.md').read_text())
                if text is None:
                    raise ValueError('no Summary section')
                print(text, end='')
            elif args.action == 'stop':
                if data['status'] == 'running' and identity(data['pid']) == data['identity']:
                    # Kill the complete process group, including pool and engine descendants.
                    os.killpg(data['pid'], signal.SIGTERM)
                if data['status'] in ACTIVE:
                    data.update(status='stopped', finished=time.time())
                    self.save(args.name, data)
            elif args.action == 'clean':
                if data['status'] in ACTIVE:
                    raise ValueError('stop the job before cleaning')
                archive = self.state / 'archive'
                archive.mkdir(exist_ok=True, mode=0o700)
                self.path(args.name).rename(archive / (args.name + '-' + uuid.uuid4().hex))
        return 0


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', type=Path, default=Path(os.environ.get('HARNESS_CONFIG_DIR', '~/.config/agent-harness')).expanduser())
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('HARNESS_DISPATCH_DIR', '~/.local/state/agent-harness/dispatch')).expanduser())
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ('start', 'continue'):
        cmd = commands.add_parser(action)
        cmd.add_argument('name')
        cmd.add_argument('--engine', required=True)
        cmd.add_argument('--model', required=True)
        cmd.add_argument('--effort')
        cmd.add_argument('--heavy', action='store_true')
        cmd.add_argument('--cwd', type=Path, default=Path.cwd())
        task = cmd.add_mutually_exclusive_group(required=True)
        task.add_argument('--task')
        task.add_argument('--task-file', type=Path)
    for action in ('status', 'summary', 'stop', 'clean'):
        cmd = commands.add_parser(action)
        cmd.add_argument('name', nargs='?' if action == 'status' else None)
    cmd = commands.add_parser('_run', help=argparse.SUPPRESS)
    cmd.add_argument('name')
    cmd.add_argument('attempt', type=Path)
    args = parser.parse_args(argv)
    try:
        dispatch = Dispatch(args.config_dir, args.state_dir)
        if args.action in ('start', 'continue'):
            return dispatch.start(args)
        if args.action == '_run':
            return dispatch.run(args.name, args.attempt)
        return dispatch.action(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'dispatch: {exc}', file=sys.stderr)
        return 64


if __name__ == '__main__':
    raise SystemExit(main())
