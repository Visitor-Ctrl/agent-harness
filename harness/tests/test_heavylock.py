"""Real flock contention, nested inheritance, and validation offline."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

CLI = Path(__file__).resolve().parents[1] / 'bin' / 'heavy-lock'


class HeavyLockTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.cfg = self.root / 'heavy.toml'
        self.cfg.write_text('[heavy_lock]\nslots=1\npoll_seconds=0.02\nlock_dir=' + json.dumps(str(self.root / 'locks')))
        self.env = dict(os.environ, HARNESS_HEAVY_CONFIG=str(self.cfg))

    def test_nested_exit_code(self):
        p = subprocess.run([str(CLI), '--', str(CLI), '--', sys.executable, '-c', 'raise SystemExit(7)'], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(p.returncode, 7, p.stderr)
        self.assertNotIn('waiting', p.stderr)

    def test_wait_logged_and_two_slots(self):
        locks = self.root / 'locks'; locks.mkdir()
        with (locks / 'heavy-0.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            child = subprocess.Popen([str(CLI), '--', sys.executable, '-c', 'print("finished")'], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(.2); self.assertIsNone(child.poll())
                fcntl.flock(lock, fcntl.LOCK_UN)
                out, err = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0)
                self.assertIn('waiting', err); self.assertIn('acquired slot', err)
                self.assertIn('finished', out)
            finally:
                if child.poll() is None: child.kill(); child.communicate()
            self.cfg.write_text(self.cfg.read_text().replace('slots=1', 'slots=2'))
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = subprocess.run([str(CLI), '--', sys.executable, '-c', 'pass'], env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0); self.assertNotIn('waiting', result.stderr)

    def test_child_keeps_slot_after_wrapper_death(self):
        ready = self.root / 'ready'
        code = 'import pathlib,time; pathlib.Path(' + repr(str(ready)) + ').touch(); time.sleep(1)'
        wrapper = subprocess.Popen([str(CLI), '--', sys.executable, '-c', code],
                                   env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(ready.exists())
            wrapper.kill(); wrapper.wait(timeout=3)
            contender = subprocess.run([str(CLI), '--', sys.executable, '-c', 'pass'],
                                       env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(contender.returncode, 0)
            self.assertIn('waiting', contender.stderr)
        finally:
            if wrapper.poll() is None:
                wrapper.kill(); wrapper.wait()

    def test_invalid_config(self):
        for value in ('0', 'true', '1.5'):
            self.cfg.write_text('[heavy_lock]\nslots=' + value)
            result = subprocess.run([str(CLI), '--', 'true'], env=self.env, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 64)
