"""Offline provisioning plans and release transaction regression tests."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_harness import admin


class AdminTests(unittest.TestCase):
    def invoke(self, argv, installer=False):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            result = admin.main(argv, installer=installer)
        return result, out.getvalue()

    def test_install_dry_run_runs_tests_but_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'releases'
            with patch.object(admin, 'run_tests') as tests, patch.object(admin, 'root_required') as root:
                code, output = self.invoke(['--dry-run', '--prefix', str(target)], True)
            self.assertEqual(code, 0)
            tests.assert_called_once_with(admin.SOURCE)
            root.assert_not_called()
            self.assertFalse(target.exists())
            self.assertFalse(json.loads(output)['activate_services'])

    def test_failed_tests_prevent_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'releases'
            with patch.object(admin, 'run_tests', side_effect=subprocess.CalledProcessError(1, ['tests'])):
                code, _ = self.invoke(['--prefix', str(target)], True)
            self.assertEqual(code, 64)
            self.assertFalse(target.exists())

    def test_install_and_rollback_replace_current_and_units(self):
        # Root operations are simulated only inside an isolated temporary tree.
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            prefix, source = base / 'releases', base / 'source'
            source.mkdir()
            for name in ('VERSION', 'README.md', 'install.sh'):
                (source / name).write_text('1.2.3' if name == 'VERSION' else 'sample')
            (source / 'systemd/user').mkdir(parents=True)
            (source / 'systemd/broker.service').write_text('/opt/agent-harness/current/bin/secret-broker')
            (source / 'systemd/user/watch.timer').write_text('sample timer')
            with patch.multiple(admin, SOURCE=source, SYSTEM=base / 'system', USER_UNITS=base / 'user'), \
                    patch.object(admin, 'run_tests'), patch.object(admin, 'trusted'), \
                    patch.object(admin, 'root_required'), patch.object(admin.os, 'chown'), \
                    patch.object(admin.subprocess, 'run') as run:
                code, _ = self.invoke(['--prefix', str(prefix)], True)
                self.assertEqual(code, 0)
                self.assertEqual((prefix / 'current').readlink(), Path('1.2.3'))
                (source / 'VERSION').write_text('1.2.4')
                self.assertEqual(self.invoke(['--prefix', str(prefix)], True)[0], 0)
                self.assertEqual((prefix / 'current').readlink(), Path('1.2.4'))
                # Simulate root ownership when testing a rollback as an ordinary user.
                with patch.object(admin, 'protected_release'):
                    self.assertEqual(self.invoke(['--prefix', str(prefix), '--rollback', '1.2.3'], True)[0], 0)
                self.assertEqual((prefix / 'current').readlink(), Path('1.2.3'))
                self.assertIn(str(prefix / 'current'), (base / 'system/broker.service').read_text())
                self.assertEqual(run.call_args.args[0], ['systemctl', 'daemon-reload'])

    def test_rollback_missing_or_invalid_target(self):
        for version in ('../outside', '9.9.9'):
            self.assertEqual(self.invoke(['--dry-run', '--rollback', version], True)[0], 64)

    def test_profile_dry_run_has_limits_and_no_mutations(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(admin.pwd, 'getpwnam', side_effect=KeyError), \
                patch.object(admin, 'HOME_ROOT', Path(temporary) / 'homes'), \
                patch.object(admin, 'ETC', Path(temporary) / 'etc'), \
                patch.object(admin.subprocess, 'run') as run:
            root = Path(temporary) / 'projects'
            code, output = self.invoke(['profile', 'add', 'sample', '--root', str(root), '--dry-run'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)['MemoryHigh'], '8G')
            self.assertEqual(json.loads(output)['mode'], '0700')
            run.assert_not_called()
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_profile_rejects_existing_users_and_destinations(self):
        with patch.object(admin.pwd, 'getpwnam', return_value=object()):
            self.assertEqual(self.invoke(['profile', 'add', 'sample', '--dry-run'])[0], 64)
        with tempfile.TemporaryDirectory() as temporary, patch.object(admin.pwd, 'getpwnam', side_effect=KeyError):
            (Path(temporary) / 'sample').mkdir()
            self.assertEqual(self.invoke(['profile', 'add', 'sample', '--root', temporary, '--dry-run'])[0], 64)
        self.assertEqual(self.invoke(['profile', 'add', '../escape', '--dry-run'])[0], 64)

    def test_source_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / 'bad').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                admin.safe_source(source)

    def test_version_and_doctor_missing_configuration(self):
        code, output = self.invoke(['version'])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), (admin.SOURCE / 'VERSION').read_text().strip())
        with tempfile.TemporaryDirectory() as temporary, patch.dict(admin.os.environ, {'HARNESS_CONFIG_DIR': temporary}):
            code, output = self.invoke(['doctor'])
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(output)['checks']['config_pool'])
