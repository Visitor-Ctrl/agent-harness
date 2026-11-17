"""Inherited machine-wide flock slots for expensive commands."""
import argparse
import fcntl
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get(
        'HARNESS_HEAVY_CONFIG', '/etc/agent-harness/heavy-lock.toml')))
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required')
    try:
        cfg = {}
        if args.config.exists():
            with args.config.open('rb') as stream:
                cfg = tomllib.load(stream).get('heavy_lock', {})
        slots = cfg.get('slots', 2)
        interval = cfg.get('poll_seconds', 0.2)
        if type(slots) is not int or not 1 <= slots <= 1024:
            raise ValueError('slots must be an integer from 1 to 1024')
        if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
            raise ValueError('poll_seconds must be finite and positive')
        root = Path(cfg.get('lock_dir', '/run/lock/agent-harness')).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        # A nested command inherits the actual open lock, not just a boolean.
        inherited = os.environ.get('HARNESS_HEAVY_FD', '')
        if inherited.isdigit():
            fd = int(inherited)
            try:
                held = os.fstat(fd)
                for n in range(slots):
                    candidate = root / f'heavy-{n}.lock'
                    if candidate.exists() and (held.st_dev, held.st_ino) == (
                            candidate.stat().st_dev, candidate.stat().st_ino):
                        rc = subprocess.call(command, pass_fds=(fd,))
                        return rc if rc >= 0 else 128 - rc
            except OSError:
                pass
        waiting = False
        while True:
            for n in range(slots):
                path = root / f'heavy-{n}.lock'
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o660)
                    # Dispatch has a private umask; shared slots still need group access.
                    os.fchmod(fd, 0o660)
                except FileExistsError:
                    # flock on Linux accepts a read-only descriptor. Other profiles
                    # need only group read access to existing slot files.
                    fd = os.open(path, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                try:
                    if waiting:
                        print(f'heavy-lock: acquired slot {n + 1}/{slots}', file=sys.stderr, flush=True)
                    env = dict(os.environ, HARNESS_HEAVY_FD=str(fd))
                    rc = subprocess.call(command, env=env, pass_fds=(fd,))
                    return rc if rc >= 0 else 128 - rc
                finally:
                    os.close(fd)
            if not waiting:
                print(f'heavy-lock: all {slots} slots busy; waiting', file=sys.stderr, flush=True)
                waiting = True
            time.sleep(interval)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f'heavy-lock: {exc}', file=sys.stderr)
        return 64
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
