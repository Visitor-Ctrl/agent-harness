"""Offline dispatch protocol tests with fake engines and herdr."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from agent_harness.dispatch import summary

ENGINE = '''#!/usr/bin/env python3
import os, pathlib, sys, time
root = pathlib.Path(os.environ['TEST_ROOT'])
(root / 'brief').write_text(sys.stdin.read())
(root / 'argv').write_text(repr(sys.argv))
(root / 'pid').write_text(str(os.getpid()))
if (root / 'sleep').exists(): time.sleep(30)
text = 'Incomplete' if (root / 'bad').exists() else 'Detail\\n\\n## Summary\\nTask complete.'
if (root / 'json').exists():
 import json; print(json.dumps({'type': 'result', 'result': text}))
else: print(text)
'''
HERDR = '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
p = root / ('herdr-' + sys.argv[2])
s = json.loads(p.read_text()) if p.exists() else {'workspaces': [], 'tabs': [], 'runs': 0}
a = sys.argv[3:]; r = {}
if a[:2] == ['workspace', 'list']: r = {'workspaces': s['workspaces']}
if a[:2] == ['workspace', 'create']:
 w = {'workspace_id': 'ws', 'label': a[a.index('--label') + 1]}; s['workspaces'].append(w); r = {'workspace': w}
if a[:2] == ['tab', 'list']: r = {'tabs': s['tabs']}
if a[:2] == ['tab', 'create']:
 t = {'tab_id': 'tab', 'label': a[a.index('--label') + 1]}; s['tabs'].append(t); r = {'tab': t, 'root_pane': {'pane_id': 'pane'}}
if a[:2] == ['pane', 'run']:
 s['runs'] += 1
 subprocess.Popen(a[3], shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
p.write_text(json.dumps(s)); print(json.dumps({'result': r}))
'''


class DispatchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cfg = self.root / 'cfg'; self.cfg.mkdir()
        self.state = self.root / 'jobs'
        self.env = dict(os.environ, HARNESS_CONFIG_DIR=str(self.cfg), HARNESS_DISPATCH_DIR=str(self.state),
                        HARNESS_STATE_DIR=str(self.root / 'pool'), HARNESS_HERDR_STATE_DIR=str(self.root / 'herdr-state'), TEST_ROOT=str(self.root))
        engine = self.exe('engine', ENGINE)
        self.herdr = self.exe('herdr', HERDR)
        account = self.root / 'account'; account.mkdir()
        self.cfg.joinpath('engines.toml').write_text('[engines.custom]\ncommand=COMMAND\nprobe_command=COMMAND\nconfig_env="TEST_ACCOUNT"\nmodel_flag=["--model","{model}"]\neffort_flag=["--effort","{effort}"]\n'.replace('COMMAND', json.dumps([str(engine)])))
        self.cfg.joinpath('pool.toml').write_text('[[accounts]]\nname="acct-a"\nengine="custom"\nconfig_dir=' + json.dumps(str(account)))
        self.cfg.joinpath('dispatch.toml').write_text('[dispatch]\nmax_workers=1\n')
        self.cfg.joinpath('herdr.toml').write_text('[herdr]\ncommand=["/absent/herdr"]\n')
        heavy = self.cfg / 'heavy.toml'
        heavy.write_text('[heavy_lock]\nslots=1\nlock_dir=' + json.dumps(str(self.root / 'locks')))
        self.env['HARNESS_HEAVY_CONFIG'] = str(heavy)
        self.addCleanup(self.stop_all)

    def exe(self, name, body):
        path = self.root / name; path.write_text(body); path.chmod(0o700)
        return path

    def cli(self, *args, ok=True):
        result = subprocess.run([str(ROOT / 'bin' / 'dispatch'), *args], env=self.env, capture_output=True, text=True, timeout=10)
        if ok: self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def start(self, action='start', name='job', *extra):
        return self.cli(action, name, '--engine', 'custom', '--model', 'sample', '--effort', 'high', '--cwd', str(self.root), '--task', 'Do the task.', *extra)

    def meta(self):
        return json.loads((self.state / 'job' / 'meta.json').read_text())

    def wait(self, status):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            data = self.meta()
            if data['status'] == status: return data
            time.sleep(.03)
        self.fail(str(data))

    def stop_all(self):
        for path in self.state.glob('*/meta.json'):
            self.cli('stop', path.parent.name, ok=False)

    def test_fallback_brief_pool_summary_continue_archive(self):
        for name, text in [('NORTH-STAR.md', 'Approved objective'), ('AGENTS.md', 'Operating rules'), ('COORDINATOR-STATE.md', 'Checkpoint')]:
            (self.root / name).write_text(text)
        self.start('start', 'job', '--heavy')
        first = self.wait('done'); attempt = Path(first['attempt'])
        brief = (attempt / 'brief.md').read_text()
        for token in ('Approved objective', 'Operating rules', 'Checkpoint', 'Do the task.', str(attempt / 'HANDOVER.md')):
            self.assertIn(token, brief)
        for token in ('sample', 'high'): self.assertIn(token, (self.root / 'argv').read_text())
        self.assertEqual(self.cli('summary', 'job').stdout, '## Summary\nTask complete.\n')
        (attempt / 'HANDOVER.md').write_text('Preserve completed work.')
        self.start('continue'); second = self.wait('done')
        self.assertNotEqual(first['attempt'], second['attempt'])
        self.assertIn('Preserve completed work.', (Path(second['attempt']) / 'brief.md').read_text())
        self.assertTrue((attempt / 'result.md').exists())
        self.cli('clean', 'job')
        self.assertFalse((self.state / 'job').exists())
        self.assertEqual(len(list((self.state / 'archive').glob('job-*'))), 1)

    def test_validation(self):
        for args in [('start', 'job', '--engine', 'custom', '--task', 'x'),
                     ('start', '../escape', '--engine', 'custom', '--model', 'm', '--task', 'x'),
                     ('start', 'job', '--engine', 'unknown', '--model', 'm', '--task', 'x')]:
            self.assertNotEqual(self.cli(*args, ok=False).returncode, 0)
        self.start(); self.wait('done')
        self.assertNotEqual(self.cli('continue', 'job', '--engine', 'custom', '--model', 'm', '--task', 'x', ok=False).returncode, 0)

    def test_cap_stop_status(self):
        (self.root / 'sleep').touch(); self.start(); self.wait('running')
        self.assertNotEqual(self.cli('start', 'other', '--engine', 'custom', '--model', 'm', '--task', 'x', ok=False).returncode, 0)
        self.assertNotEqual(self.cli('clean', 'job', ok=False).returncode, 0)
        self.cli('stop', 'job')
        self.assertEqual(json.loads(self.cli('status').stdout)[0]['status'], 'stopped')

    def test_missing_summary(self):
        (self.root / 'bad').touch(); self.start()
        self.assertEqual(self.wait('failed')['exit_code'], 65)
        self.assertIn('did not end', self.cli('summary', 'job').stdout)
        self.assertIsNone(summary('## Summary\nok\n## Later\nno'))

    def test_concurrent_launch_reservation(self):
        (self.root / 'sleep').touch()
        processes = [subprocess.Popen([str(ROOT / 'bin' / 'dispatch'), 'start', name,
                     '--engine', 'custom', '--model', 'sample', '--task', 'Task',
                     '--cwd', str(self.root)], env=self.env, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True) for name in ('one', 'two')]
        for process in processes:
            process.communicate(timeout=10)
        self.assertEqual(sorted(p.returncode for p in processes), [0, 64])

    def test_json_result_schema(self):
        (self.root / 'json').touch()
        with (self.cfg / 'engines.toml').open('a') as stream:
            stream.write('\n[engines.custom.result]\ntype_key="type"\ntype_value="result"\ntext_key="result"\n')
        self.start(); self.wait('done')
        self.assertEqual(self.cli('summary', 'job').stdout, '## Summary\nTask complete.\n')

    def test_herdr_and_helpers_idempotent(self):
        self.cfg.joinpath('herdr.toml').write_text('[herdr]\ncommand=' + json.dumps([str(self.herdr)]) + '\n')
        self.start(); self.wait('done')
        self.assertEqual(json.loads((self.root / 'herdr-workers').read_text())['runs'], 1)
        for kind in ('workspace', 'coordinator'):
            for _ in range(2):
                result = subprocess.run([str(ROOT / 'bin' / ('herdr-ensure-' + kind)), '--cwd', str(self.root)], env=self.env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.root / 'herdr-coordinator').read_text())
        self.assertEqual(len(data['workspaces']), 1)
        self.assertEqual(len(data['tabs']), 1)
