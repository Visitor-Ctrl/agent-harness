"""Offline security boundaries, transports and watcher regression tests."""
import base64
from collections import namedtuple
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agent_harness.secrets.broker import Broker, peer_uid, read_line
from agent_harness.secrets import run as secret_run
from agent_harness import notify, watch


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = b"synthetic-test-payload"
        self.oci = self.root / "oci"
        self.oci.write_text('#!/usr/bin/python3\nimport sys,json\n'
                            'assert sys.argv[1:]==["secrets","secret-bundle","get","--auth",'
                            '"instance_principal","--secret-id","ocid1.vaultsecret.example.placeholder"]\n'
                            'print(json.dumps({"data":{"secret-bundle-content":{"content":'
                            + repr(base64.b64encode(self.marker).decode()) + '}}}))\n')
        self.oci.chmod(0o700)
        self.profiles = {"example": {"uid": os.getuid(), "secrets": {
            "service-token": "ocid1.vaultsecret.example.placeholder"}}}
        self.broker = Broker(self.profiles, self.root / "audit", oci=str(self.oci), rate_limit=2)

    def call(self, request, broker=None):
        server, client = socket.socketpair()
        with server, client:
            self.assertEqual(peer_uid(server), os.getuid())
            client.sendall(json.dumps(request).encode() + b"\n")
            (broker or self.broker).handle(server, "example")
            return read_line(client, 10000)

    def test_fake_oci_and_real_peer_credentials(self):
        response = self.call({"name": "service-token"})
        self.assertEqual(base64.b64decode(response["value"]), self.marker)
        audit = (self.root / "audit").read_text()
        self.assertNotIn(self.marker.decode(), audit)
        record = json.loads(audit)
        self.assertEqual((record["uid"], record["profile"], record["secret"], record["result"]),
                         (os.getuid(), "example", "service-token", "ok"))
        self.assertEqual((self.root / "audit").stat().st_mode & 0o777, 0o600)

    def test_denies_another_uid_without_fetching(self):
        self.profiles["example"]["uid"] = os.getuid() + 1
        with patch.object(self.broker, "fetch") as fetch:
            self.assertFalse(self.call({"name": "service-token"})["ok"])
            fetch.assert_not_called()

    def test_allowlist_and_invalid_name(self):
        with patch.object(self.broker, "fetch") as fetch:
            for name in ("not-allowed", "bad\nname"):
                self.assertFalse(self.call({"name": name})["ok"])
            fetch.assert_not_called()
        self.assertNotIn("bad\\nname", (self.root / "audit").read_text())

    def test_rate_limit_and_expiry(self):
        now = [100]
        self.broker.clock = lambda: now[0]
        self.assertTrue(self.call({"name": "service-token"})["ok"])
        self.assertTrue(self.call({"name": "service-token"})["ok"])
        self.assertFalse(self.call({"name": "service-token"})["ok"])
        self.assertEqual(json.loads((self.root / "audit").read_text().splitlines()[-1])["result"], "rate-limited")
        now[0] += 61
        self.assertTrue(self.call({"name": "service-token"})["ok"])

    def test_fetch_failure_and_audit_failure_are_closed(self):
        self.oci.write_text('#!/bin/sh\necho synthetic-test-payload >&2\nexit 1\n')
        self.assertEqual(self.call({"name": "service-token"}), {"ok": False})
        self.broker.audit = self.root / "missing" / "audit"
        self.assertEqual(self.call({"name": "service-token"}), {"ok": False})

    def test_audit_rejects_public_file(self):
        audit = self.root / "audit"
        audit.touch(mode=0o644)
        audit.chmod(0o644)
        self.assertEqual(self.call({"name": "service-token"}), {"ok": False})

    def test_oversized_or_malformed_request_is_denied(self):
        for frame in (b"x" * 4097 + b"\n", b"[]\n", b"{invalid}\n"):
            server, client = socket.socketpair()
            with server, client:
                client.sendall(frame)
                self.broker.handle(server, "example")
                self.assertEqual(read_line(client, 100), {"ok": False})

    def test_secret_run_child_env_and_stdin_never_print_value(self):
        wrapper = Path(__file__).resolve().parents[1] / "bin" / "secret-run"
        # Spawn the actual wrapper and child with a local protocol server. Only the
        # broker-root assertion is replaced for this unprivileged offline test.
        site = self.root / "sitecustomize.py"
        site.write_text('import socket,struct\nold=socket.socket.getsockopt\n'
                        'def credentials(self,level,opt,*args):\n'
                        ' if level==socket.SOL_SOCKET and opt==socket.SO_PEERCRED:\n'
                        '  return struct.pack("3i",1,0,0)\n'
                        ' return old(self,level,opt,*args)\n'
                        'socket.socket.getsockopt=credentials\n')
        for option in (["--env", "TEST_VALUE"], ["--stdin"]):
            path = str(self.root / "broker.sock")
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(path)
                listener.listen()
                failures = []
                def serve():
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            self.assertEqual(read_line(connection, 4096), {"name": "service-token"})
                            connection.sendall(json.dumps({"ok": True, "value": base64.b64encode(self.marker).decode()}).encode() + b"\n")
                    except Exception as exc:
                        failures.append(exc)
                thread = threading.Thread(target=serve, daemon=True)
                thread.start()
                expression = 'import os,sys; value=' + ('os.environ["TEST_VALUE"].encode()' if option[0] == '--env' else 'sys.stdin.buffer.read()') + '; sys.exit(0 if value==b"synthetic-test-payload" else 3)'
                result = subprocess.run([sys.executable, str(wrapper), "--socket", path, *option,
                                         "service-token", "--", sys.executable, "-c", expression],
                                        env={**os.environ, "PYTHONPATH": str(self.root)},
                                        capture_output=True, timeout=10)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertFalse(failures)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
            Path(path).unlink()

    def test_client_rejects_nonroot_broker(self):
        path = str(self.root / "broker.sock")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(path)
            listener.listen()
            with patch.object(secret_run, "peer_uid", return_value=42):
                with self.assertRaises(ValueError):
                    secret_run.request_secret(path, "service-token")

    def test_child_launch_error_does_not_echo_value(self):
        output = io.StringIO()
        with patch.object(secret_run, "request_secret", return_value=self.marker), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit):
                secret_run.main(["--socket", "/unused", "--env", "TEST_VALUE", "service-token", "--", "/missing/child"])
        self.assertNotIn(self.marker.decode(), output.getvalue())


class NotifyTests(unittest.TestCase):
    def test_all_transports(self):
        requests, mails = [], []
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def transport(request, timeout):
            requests.append(request)
            return Response()
        def sendmail(command, **kwargs):
            mails.append((command, kwargs))
        notify.send({"backends": [
            {"type": "ntfy", "url": "https://example.com/topic"},
            {"type": "webhook", "url": "https://example.com/hook"},
            {"type": "email", "from": "harness@example.com", "to": "operator@example.com"}]},
            "hello", urlopen=transport, run=sendmail)
        self.assertEqual(requests[0].data, b"hello")
        self.assertEqual(json.loads(requests[1].data), {"message": "hello"})
        self.assertEqual(mails[0][0], ["/usr/sbin/sendmail", "-i", "-t"])
        self.assertIn(b"hello", mails[0][1]["input"])

    def test_failure_does_not_skip_later_backend(self):
        calls = []
        def mail(*args, **kwargs): calls.append(True)
        with self.assertRaises(ValueError):
            notify.send({"backends": [{"type": "webhook", "url": "http://example.com"},
                                     {"type": "email", "from": "a@example.com", "to": "b@example.com"}]},
                        "hello", run=mail)
        self.assertEqual(calls, [True])
        with self.assertRaises(ValueError): notify.send({}, "hello")


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fake_run(self, command, **kwargs):
        self.assertTrue(kwargs["check"])
        if command[0] == "systemctl":
            output = "failed.service loaded failed failed\n" if "--user" in command else ""
        elif command[-1] == "healthy": output = "1\n"
        else:
            output = json.dumps([{"engine": "custom", "name": "acct-a", "state": "signed-out", "checked": 9999},
                                 {"engine": "custom", "name": "acct-b", "state": "ok", "checked": 1}])
        return subprocess.CompletedProcess(command, 0, output, "")

    def test_all_signals_and_no_job_payload_in_alerts(self):
        (self.root / "active.json").write_text(json.dumps({"status": "running", "updated_at": 1, "prompt": "private-prompt"}))
        (self.root / "done.json").write_text(json.dumps({"status": "completed", "finished_at": 1}))
        (self.root / "read.json").write_text(json.dumps({"status": "completed", "finished_at": 1, "read_at": 3}))
        usage = namedtuple("Usage", "total used free")(100, 95, 5)
        alerts = watch.collect({"jobs_dir": str(self.root), "disk_paths": ["/example"], "signin_stale_seconds": 100},
                               now=10000, run=self.fake_run, disk_usage=lambda _: usage)
        self.assertEqual(set(alerts), {"stalled:active", "unread:done", "unit:user:failed.service", "pool:healthy",
                                      "signin:custom:acct-a", "signin-stale:custom:acct-b", "disk:/example"})
        self.assertNotIn("private-prompt", str(alerts))

    def test_probe_failures_are_visible(self):
        def fail(*args, **kwargs): raise OSError()
        alerts = watch.collect({"jobs_dir": str(self.root), "disk_paths": []}, run=fail)
        self.assertEqual(set(alerts), {"check:systemd:system", "check:systemd:user", "check:pool"})

    def test_dedupe_repeat_recovery_and_failed_delivery(self):
        path = self.root / "watch.json"
        delivered = []
        sender = lambda config, message: delivered.append(message)
        def deliver(alerts, now):
            return watch.deliver(alerts, path, {}, now=now, repeat_seconds=10, sender=sender)
        self.assertEqual(deliver({"a": "alert"}, 100), 1)
        self.assertEqual(deliver({"a": "alert"}, 105), 0)
        self.assertEqual(deliver({"a": "alert"}, 110), 1)
        self.assertEqual(deliver({}, 111), 0)
        self.assertEqual(deliver({"a": "alert"}, 112), 1)
        def fail(*args): raise ValueError("unavailable")
        with self.assertRaises(ValueError):
            watch.deliver({"b": "new"}, path, {}, now=120, sender=fail)
        self.assertNotIn("b", json.loads(path.read_text()))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_dispatch_metadata_logs_and_acknowledgement(self):
        job = self.root / "worker-a"
        attempt = job / "attempt"
        attempt.mkdir(parents=True)
        (attempt / "output.log").write_text("progress")
        os.utime(attempt / "output.log", (9900, 9900))
        meta = job / "meta.json"
        record = {"status": "running", "created": 1, "attempt": str(attempt)}
        meta.write_text(json.dumps(record))
        config = {"jobs_dir": str(self.root), "disk_paths": []}
        self.assertNotIn("stalled:worker-a", watch.collect(config, now=10000, run=self.fake_run))
        record.update(status="done", finished=100)
        meta.write_text(json.dumps(record))
        self.assertIn("unread:worker-a", watch.collect(config, now=10000, run=self.fake_run))
        self.assertNotIn("unread:worker-a", watch.collect(config, now=10000, run=self.fake_run,
                                                        acknowledgements={"worker-a": 101}))
        record["finished"] = 200
        meta.write_text(json.dumps(record))
        self.assertIn("unread:worker-a", watch.collect(config, now=10000, run=self.fake_run,
                                                     acknowledgements={"worker-a": 101}))
