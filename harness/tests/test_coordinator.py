"""Coordinator tests use only fake local CLIs, temporary accounts and sockets."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from agent_harness.coordinator import Coordinator, health
from agent_harness.pool import Pool

ENGINE = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ['FAKE_ROOT'])
feed = sys.stdin.read()
(root / 'prompt').write_text(feed)
(root / 'argv').write_text(json.dumps(sys.argv))
(root / 'engine-pid').write_text(str(os.getpid()))
account = os.environ['AI_POOL_ACCOUNT']
with (root / 'attempts').open('a') as f: f.write(account + '\\n')
if (root / 'sleep').exists():
 with (root / 'pane').open('a') as f: f.write('\\nUsage limit reached; retry in 2 minutes\\n')
 time.sleep(30)
if (root / 'limit').exists() and account == 'acct-b':
 print('Usage limit reached; retry in 2 minutes'); sys.exit(1)
if '--new' in sys.argv:
 sid = sys.argv[sys.argv.index('--new') + 1]
 (pathlib.Path(os.environ['FAKE_ACCOUNT']) / (sid + '.jsonl')).write_text('checkpoint')
print('Done')
'''


class CoordinatorTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = self.root / 'config'; self.cfg.mkdir()
        self.state = self.root / 'coordinator'
        self.pool_state = self.root / 'pool'
        self.env = dict(os.environ, FAKE_ROOT=str(self.root), HARNESS_CONFIG_DIR=str(self.cfg),
                        HARNESS_COORDINATOR_DIR=str(self.state), HARNESS_STATE_DIR=str(self.pool_state))
        self.engine = self.exe('engine', ENGINE)
        self.herdr = self.exe('herdr', '''#!/usr/bin/env python3
import os, pathlib, sys
root = pathlib.Path(os.environ['FAKE_ROOT'])
(root / 'herdr-argv').write_text(repr(sys.argv))
print((root / 'pane').read_text(), end='')
''')
        (self.root / 'pane').write_text('Earlier output\n')
        (self.root / 'COORDINATOR-STATE.md').write_text('Keep finished work')
        (self.root / 'HANDOVER.md').write_text('Latest checkpoint')
        self.cfg.joinpath('engines.toml').write_text('''[engines.custom]
command=COMMAND
probe_command=COMMAND
config_env="FAKE_ACCOUNT"
model_flag=["--model","{model}"]
session_flag=["--new","{session}"]
resume_flag=["--resume","{session}"]
transcript_glob="{session}.jsonl"
[engines.custom.patterns]
limit=["usage limit"]
notice_limit=["^Usage limit reached"]
'''.replace('COMMAND', json.dumps([str(self.engine)])))
        entries = []
        for name, reserved in [('acct-a', ''), ('acct-b', 'coordinator'), ('acct-c', 'other')]:
            account = self.root / name; account.mkdir()
            entries.append('[[accounts]]\nname=' + json.dumps(name) + '\nengine="custom"\nconfig_dir=' + json.dumps(str(account)) + '\nreserved_for=' + json.dumps(reserved))
        self.cfg.joinpath('pool.toml').write_text('\n'.join(entries))
        self.cfg.joinpath('herdr.toml').write_text('[herdr]\ncommand=' + json.dumps([str(self.herdr)]))
        self.cfg.joinpath('coordinator.toml').write_text('''[coordinator]
engine="custom"
model="test-model"
cwd=CWD
pane="stable-pane"
remote_name="stable-session"
rotate_turns=2
rotate_bytes=1000000
retry_seconds=0.1
turn_delay_seconds=0.1
[health]
minimum_worker_accounts=1
minimum_coordinator_accounts=1
guard_paths=GUARD
broker_socket=SOCKET
'''.replace('CWD', json.dumps(str(self.root))).replace('GUARD', json.dumps([str(self.root / 'guard')])).replace('SOCKET', json.dumps(str(self.root / 'broker.sock'))))
        (self.root / 'guard').write_text('installed')
        self.cfg.joinpath('notify.toml').write_text('backends=[]')

    def exe(self, name, body):
        path = self.root / name; path.write_text(body); path.chmod(0o700)
        return path

    def cli(self, name='coordinator', *args, expected=0):
        result = subprocess.run([str(ROOT / 'bin' / name), *args], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def coordinator(self):
        return Coordinator(self.cfg, self.state)

    def data(self):
        return json.loads((self.state / 'coordinator.json').read_text())

    def wait_for(self, condition):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.03)
        self.fail('condition timed out')

    def test_reservation_and_coordinator_preference(self):
        result = self.cli('ai-pool', 'run', '--engine', 'custom')
        self.assertEqual(result.stdout.strip(), 'Done')
        self.assertEqual((self.root / 'attempts').read_text(), 'acct-a\n')
        self.cli('ai-pool', 'run', '--engine', 'custom', '--account', 'acct-b', expected=75)
        self.cli('coordinator', '--once')
        self.assertEqual((self.root / 'attempts').read_text(), 'acct-a\nacct-b\n')
        self.cli('ai-pool', 'probe', '--force')
        self.assertEqual(self.cli('ai-pool', 'healthy').stdout.strip(), '1')
        self.assertEqual(self.cli('ai-pool', 'healthy', '--role', 'coordinator').stdout.strip(), '2')

    def test_resume_rotation_injects_latest_checkpoint_same_pane(self):
        self.cli('coordinator', '--once')
        session = self.data()['session']
        self.assertIn('--new', (self.root / 'argv').read_text())
        self.cli('coordinator', '--once')
        self.assertEqual(self.data()['session'], session)
        self.assertIn('--resume', (self.root / 'argv').read_text())
        newer = self.root / 'new-HANDOVER.md'
        newer.write_text('Next checkpoint')
        os.utime(newer, (time.time() + 10, time.time() + 10))
        self.cli('coordinator', '--once')
        self.assertNotEqual(self.data()['session'], session)
        self.assertEqual(self.data()['turns'], 1)
        self.assertIn('Next checkpoint', (self.root / 'prompt').read_text())
        self.assertIn('Keep finished work', (self.root / 'prompt').read_text())
        self.assertIn('stable-pane', (self.root / 'herdr-argv').read_text())
        self.assertIn('stable-session', (self.root / 'herdr-argv').read_text())
        self.assertEqual((self.state / 'coordinator.json').stat().st_mode & 0o777, 0o600)

    def test_manual_and_size_rotation_need_checkpoint(self):
        self.cli('coordinator', '--once')
        session = self.data()['session']
        self.cli('coordinator-rotate')
        self.cli('coordinator', '--once')
        self.assertNotEqual(self.data()['session'], session)
        with patch.dict(os.environ, self.env):
            coordinator = self.coordinator()
            with coordinator.locked() as data: data['output_bytes'] = 1000000
        (self.root / 'HANDOVER.md').unlink()
        self.cli('coordinator', '--once', expected=64)
        self.cli('coordinator-rotate', expected=64)

    def test_engines_without_new_session_flag_use_checkpoint_turns(self):
        path = self.cfg / 'engines.toml'
        path.write_text(path.read_text().replace('session_flag=["--new","{session}"]\n', ''))
        self.cli('coordinator', '--once')
        self.cli('coordinator', '--once')
        self.assertNotIn('--new', (self.root / 'argv').read_text())
        self.assertNotIn('--resume', (self.root / 'argv').read_text())
        self.assertIn('Latest checkpoint', (self.root / 'prompt').read_text())

    def test_failed_account_falls_back_and_excludes_other_roles(self):
        (self.root / 'limit').touch()
        self.cli('coordinator', '--once', expected=75)
        self.cli('coordinator', '--once')
        self.assertEqual((self.root / 'attempts').read_text(), 'acct-b\nacct-a\n')
        self.assertEqual(json.loads((self.pool_state / 'state.json').read_text())['custom/acct-b']['state'], 'cooling')

    def test_watcher_ignores_old_notices_records_hold_and_restarts_live_child(self):
        (self.root / 'pane').write_text('Usage limit reached yesterday\n')
        (self.root / 'sleep').touch()
        child = subprocess.Popen([str(ROOT / 'bin' / 'coordinator'), '--once'], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_for(lambda: (self.root / 'engine-pid').exists())
            self.cli('coordinator', '--once', expected=64)  # Singleton launcher.
            engine_pid = int((self.root / 'engine-pid').read_text())
            self.cli('coordinator-watch')
            self.assertTrue(self.data()['restart'])
            self.assertEqual(self.data()['last_hold']['account'], 'acct-b')
            self.assertNotIn('Usage limit', json.dumps(self.data()['last_hold']))
            self.cli('coordinator-watch')  # Does not extend the hold on repeated reads.
            self.assertEqual(child.wait(timeout=6), 75)
            with self.assertRaises(ProcessLookupError): os.kill(engine_pid, 0)
            self.assertNotIn('active', self.data())
            (self.root / 'sleep').unlink()
            self.cli('coordinator', '--once')
            self.assertEqual((self.root / 'attempts').read_text(), 'acct-b\nacct-a\n')
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM); child.wait(timeout=6)
            child.stdout.close(); child.stderr.close()

    def test_launcher_loop_fails_over_without_another_invocation(self):
        (self.root / 'limit').touch()
        child = subprocess.Popen([str(ROOT / 'bin' / 'coordinator')], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            attempts = self.root / 'attempts'
            self.wait_for(lambda: attempts.exists() and len(attempts.read_text().splitlines()) >= 2)
            self.assertEqual(attempts.read_text().splitlines()[:2], ['acct-b', 'acct-a'])
            child.terminate()
            self.assertEqual(child.wait(timeout=6), 130)
        finally:
            if child.poll() is None: child.kill(); child.wait()
            child.stdout.close(); child.stderr.close()

    def test_size_rotation_and_invalid_reservation(self):
        self.cli('coordinator', '--once')
        session = self.data()['session']
        with patch.dict(os.environ, self.env):
            coordinator = self.coordinator()
            with coordinator.locked() as data: data['output_bytes'] = 1000000
        self.cli('coordinator', '--once')
        self.assertNotEqual(self.data()['session'], session)
        path = self.cfg / 'pool.toml'
        path.write_text(path.read_text().replace('reserved_for="coordinator"', 'reserved_for=3'))
        self.cli('ai-pool', 'status', expected=64)

    def test_watcher_pane_failure_does_not_cool_account(self):
        with patch.dict(os.environ, self.env):
            coordinator = self.coordinator()
            with coordinator.locked() as data:
                data.update(active=dict(account='acct-b', generation='g'), cursor='old')
            with patch.object(coordinator, 'pane_text', side_effect=subprocess.CalledProcessError(1, 'fake')):
                with self.assertRaises(subprocess.CalledProcessError): coordinator.watch()
            with coordinator.pool.locked() as state:
                self.assertEqual(state, {})

    def test_watcher_no_new_text_and_scroll_overlap(self):
        with patch.dict(os.environ, self.env):
            coordinator = self.coordinator()
            with coordinator.locked() as data:
                data.update(active=dict(account='acct-b', generation='g'), cursor='Usage limit reached\nTail\n')
            (self.root / 'pane').write_text('Usage limit reached\nTail\n')
            self.assertFalse(coordinator.watch())
            (self.root / 'pane').write_text('Tail\nNew ordinary output\n')
            self.assertFalse(coordinator.watch())
            (self.root / 'pane').write_text('New ordinary output\nUsage limit reached\n')
            self.assertTrue(coordinator.watch())

    def test_termination_reaps_pool_engine_and_clears_identity(self):
        (self.root / 'sleep').touch()
        child = subprocess.Popen([str(ROOT / 'bin' / 'coordinator')], env=self.env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_for(lambda: (self.root / 'engine-pid').exists())
            engine_pid = int((self.root / 'engine-pid').read_text())
            child.terminate()
            self.assertEqual(child.wait(timeout=6), 130)
            with self.assertRaises(ProcessLookupError): os.kill(engine_pid, 0)
            self.assertNotIn('active', self.data())
        finally:
            if child.poll() is None: child.kill(); child.wait()
            child.stdout.close(); child.stderr.close()

    def test_identity_and_corrupt_state_fail_closed(self):
        self.cli('coordinator', '--once')
        path = self.cfg / 'coordinator.toml'
        path.write_text(path.read_text().replace('stable-pane', 'different-pane'))
        self.cli('coordinator', '--once', expected=64)
        (self.state / 'coordinator.json').write_text('invalid')
        self.cli('coordinator-watch', expected=64)

    def test_health_success_and_failures_notify(self):
        messages = []
        def sender(config, message): messages.append(message)
        def run(command, **kwargs):
            if command[0] == 'systemctl':
                return subprocess.CompletedProcess(command, 0, stdout='')
            return subprocess.CompletedProcess(command, 0, stdout='2')
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(str(self.root / 'broker.sock'))
        guard = self.root / 'guard'
        guard.chmod(0o644)
        # Simulate root metadata without changing host files or requiring root.
        actual_stat = Path.stat
        def metadata(path, *args, **kwargs):
            result = actual_stat(path, *args, **kwargs)
            if path == guard:
                fields = list(result); fields[4] = 0
                return os.stat_result(fields)
            return result
        with patch.object(Path, 'stat', metadata):
            self.assertEqual(health(self.cfg, run=run, sender=sender), 0)
        self.assertIn('all checks passed', messages[-1])
        guard.chmod(0o666)
        def failed(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)
        self.assertEqual(health(self.cfg, run=failed, sender=sender), 1)
        self.assertIn('pool check failed', messages[-1])
        self.assertIn('guard ownership or mode is unsafe', messages[-1])
        (self.root / 'broker.sock').unlink()
        self.assertEqual(health(self.cfg, run=run, sender=sender), 1)
        self.assertIn('broker socket is missing', messages[-1])


if __name__ == '__main__':
    unittest.main()
