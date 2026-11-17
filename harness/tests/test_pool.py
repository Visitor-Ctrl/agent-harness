"""Offline behavior tests using temporary accounts and a fake engine executable."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from agent_harness.pool import Pool, classify, is_stream, reset_time, transfer_session

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "ai-pool"
FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ["FAKE_HOME"])
mode = (root / "mode").read_text().strip()
if mode == "sleep":
    print(os.getpid(), flush=True)
    time.sleep(30)
    sys.exit(0)
if mode == "unknown":
    print("unrecognized failure", file=sys.stderr)
    sys.exit(9)
if mode == "signedout":
    print("Not logged in")
    sys.exit(0)
if mode == "license":
    print("No seat", file=sys.stderr)
    sys.exit(1)
if mode == "limit" or (mode == "failover" and os.environ["AI_POOL_ACCOUNT"] == "acct-a"):
    if "--session-id" in sys.argv:
        sid = sys.argv[sys.argv.index("--session-id") + 1]
        folder = pathlib.Path(os.environ["FAKE_CONFIG"]) / "projects" / "work"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (sid + ".jsonl")).write_text("checkpoint")
    print("Usage limit reached; retry in 2 minutes")
    sys.exit(0)
print(json.dumps(dict(argv=sys.argv[1:], account=os.environ["AI_POOL_ACCOUNT"],
                     config=os.environ["FAKE_CONFIG"], stdin=sys.stdin.read(),
                     retry_env=os.environ.get("AI_POOL_RETRY_FILE"))))
'''


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = self.root / "cfg"
        self.state = self.root / "state"
        self.cfg.mkdir()
        self.fake = self.root / "fake-engine"
        self.fake.write_text(FAKE)
        self.fake.chmod(0o700)
        self.mode("ok")
        self.env = dict(os.environ, HARNESS_CONFIG_DIR=str(self.cfg),
                        HARNESS_STATE_DIR=str(self.state), FAKE_HOME=str(self.root))
        self.write_config()
        self.pool = Pool(self.cfg, self.state)
        self.engine = self.pool.engines["custom"]
        self.a, self.b = self.pool.accounts

    def mode(self, mode):
        (self.root / "mode").write_text(mode)

    def write_config(self):
        command = json.dumps([sys.executable, str(self.fake)])
        self.cfg.joinpath("engines.toml").write_text('''[engines.custom]
command = COMMAND
probe_command = COMMAND
config_env = "FAKE_CONFIG"
model_flag = ["--model", "{model}"]
effort_flag = ["--effort", "{effort}"]
session_flag = ["--session-id", "{session}"]
resume_flag = ["--resume", "{session}"]
transcript_glob = "projects/*/{session}.jsonl"
stream_flags = [["--output-format", "stream-json"]]
[engines.custom.result]
type_key = "type"
type_value = "result"
text_key = "result"
error_key = "is_error"
success_key = "subtype"
success_value = "success"
[engines.custom.patterns]
limit = ["usage limit", "rate_limit"]
auth = ["not logged in"]
unlicensed = ["no seat"]
notice_limit = ["^usage limit reached"]
notice_auth = ["^not logged in"]
'''.replace("COMMAND", command))
        entries = ["[pool]", "probe_timeout = 0.2", "probe_interval = 1800"]
        for name, priority in (("acct-a", 0), ("acct-b", 5)):
            path = self.root / name
            path.mkdir(exist_ok=True)
            entries += ["[[accounts]]", f'name = "{name}"', 'engine = "custom"',
                        f"config_dir = {json.dumps(str(path))}", f"priority = {priority}", "slots = 1"]
        self.cfg.joinpath("pool.toml").write_text("\n".join(entries))

    def cli(self, *args, expected=0, **kwargs):
        result = subprocess.run([str(CLI), *args], env=self.env, input=kwargs.pop("input", ""),
                                text=True, capture_output=True, timeout=8, **kwargs)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def record(self, account):
        return json.loads(self.state.joinpath("state.json").read_text()).get(account["key"], {})

    def test_dynamic_engine_model_effort_and_replayed_stdin(self):
        self.mode("failover")
        result = self.cli("run", "--engine", "custom", "--model", "sample", "--effort", "high", "--", "task", input="payload")
        success = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(success["account"], "acct-b")
        self.assertEqual(success["stdin"], "payload")
        self.assertEqual(success["argv"], ["--model", "sample", "--effort", "high", "task"])
        self.assertEqual(self.record(self.a)["state"], "cooling")
        self.assertEqual(self.record(self.b)["state"], "ok")

    def test_health_requires_verified_success(self):
        self.assertEqual(self.cli("healthy").stdout.strip(), "0")
        self.cli("probe")
        self.assertEqual(self.cli("healthy").stdout.strip(), "2")
        self.cli("off", "--engine", "custom", "acct-a")
        self.assertEqual(self.cli("healthy", "--names").stdout.strip(), "custom/acct-b")
        self.cli("on", "--engine", "custom", "acct-a")
        self.assertEqual(self.cli("healthy").stdout.strip(), "2")

    def test_signed_out_exit_zero(self):
        self.mode("signedout")
        self.cli("probe")
        self.assertEqual(self.record(self.a)["state"], "signed-out")
        self.assertEqual(self.cli("healthy").stdout.strip(), "0")
        self.cli("run", "--engine", "custom", expected=75)

    def test_unlicensed_recheck_and_no_duplicate_events(self):
        self.mode("license")
        self.cli("probe")
        self.cli("probe", "--force")
        self.assertEqual(self.record(self.a)["state"], "unlicensed")
        events = self.state.joinpath("events.log").read_text().splitlines()
        self.assertEqual(len(events), 2)
        with self.pool.locked() as state:
            state[self.a["key"]]["last_auth_fail"] = 0
            self.assertTrue(self.pool.eligible(self.a, state[self.a["key"]]))
        self.mode("ok")
        self.cli("run", "--engine", "custom")
        self.assertEqual(self.record(self.a)["state"], "ok")

    def test_cool_retry_marker_private_state_and_no_env_leak(self):
        retry = self.root / "retry"
        self.env["AI_POOL_RETRY_FILE"] = str(retry)
        self.mode("limit")
        result = self.cli("run", "--engine", "custom", expected=75)
        self.assertIn("AI_POOL_RETRY_AT=", result.stderr)
        self.assertGreater(int(retry.read_text()), time.time())
        for path in (self.state / "state.json", self.state / "events.log", retry):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.cli("on", "--engine", "custom", "acct-a")
        self.mode("ok")
        self.cli("probe", "--force")
        output = self.cli("run", "--engine", "custom").stdout
        self.assertIsNone(json.loads(output)["retry_env"])

    def test_probe_interval_timeout_and_uncertain_preserves_verdict(self):
        self.cli("probe")
        self.mode("license")
        self.assertEqual(self.cli("probe").stdout, "")
        self.mode("sleep")
        self.cli("probe", "--force")
        self.assertEqual(self.record(self.a)["state"], "ok")
        self.assertTrue(self.record(self.a)["uncertain"])
        self.assertEqual(self.cli("healthy").stdout.strip(), "0")
        self.mode("ok")
        self.cli("probe", "--force")
        self.assertFalse(self.record(self.a)["uncertain"])

    def test_stale_success_cannot_clear_new_limit(self):
        started = time.time() - 1
        with self.pool.locked() as state:
            self.pool.mark(state, self.a, "cooling")
            self.pool.mark(state, self.a, "ok", started)
        self.assertEqual(self.record(self.a)["state"], "cooling")

    def test_slot_locks_drain_and_release(self):
        fd = self.pool.slot(self.a)
        self.addCleanup(os.close, fd)
        self.assertIsNone(self.pool.slot(self.a))
        self.cli("drain", "--engine", "custom", "acct-a")
        self.assertEqual(self.record(self.a)["manual"], "drain")
        self.assertEqual(json.loads(self.cli("run", "--engine", "custom").stdout)["account"], "acct-b")
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.cli("status")
        self.assertEqual(self.record(self.a)["manual"], "off")

    def test_all_slots_busy_tempfail(self):
        held = [self.pool.slot(a) for a in self.pool.accounts]
        try:
            self.cli("run", "--engine", "custom", expected=75)
        finally:
            for fd in held:
                os.close(fd)

    def test_resume_on_failover_uses_transcript(self):
        self.mode("failover")
        result = self.cli("run", "--engine", "custom", "--session", "session-123", "--", "task")
        success = json.loads(result.stdout.splitlines()[-1])
        self.assertIn("--resume", success["argv"])
        self.assertNotIn("--session-id", success["argv"])
        self.assertIn("do not repeat completed side effects", success["stdin"])
        self.assertEqual((self.b["path"] / "projects/work/session-123.jsonl").read_text(), "checkpoint")

    def test_session_sidecars_private_and_links_rejected(self):
        folder = self.a["path"] / "projects/work"
        folder.mkdir(parents=True)
        (folder / "session-123.jsonl").write_text("checkpoint")
        (folder / "session-123").mkdir()
        (folder / "session-123/tool").write_text("result")
        (folder / "session-123/link").symlink_to(self.root / "mode")
        self.assertTrue(transfer_session(self.pool, self.pool.accounts, self.b, "session-123"))
        target = self.b["path"] / "projects/work/session-123"
        self.assertEqual((target / "tool").stat().st_mode & 0o777, 0o600)
        self.assertFalse((target / "link").exists())
        (target / "tool").unlink()
        (target / "tool").symlink_to(self.root / "mode")
        with self.assertRaises(ValueError):
            transfer_session(self.pool, self.pool.accounts, self.b, "session-123")
        self.assertEqual((self.root / "mode").read_text(), "ok")

    def test_stream_result_semantics(self):
        result = json.dumps(dict(type="result", subtype="success", is_error=False,
                                 result="Done; inspected usage limit and rate_limit logs"))
        self.assertEqual(classify(self.engine, 1, result, "", True)[0], "ok")
        raw = '{"type":"rate_limit_event","tokens":401}\n'
        self.assertEqual(classify(self.engine, 137, raw, "", True)[0], "unknown")
        notice = result.replace("Done; inspected usage limit and rate_limit logs", "Usage limit reached")
        self.assertEqual(classify(self.engine, 0, notice, "", True)[0], "cooling")
        quoted = 'Here is a log:\n' + notice + '\nDone'
        self.assertEqual(classify(self.engine, 0, quoted, "", False)[0], "ok")
        self.assertFalse(is_stream(self.engine, ["task mentioning --output-format stream-json"]))
        self.assertTrue(is_stream(self.engine, ["--output-format", "stream-json"]))

    def test_nonretryable_failure_keeps_exit_status(self):
        self.mode("unknown")
        self.cli("run", "--engine", "custom", expected=9)
        self.assertFalse(self.record(self.b))

    def test_closed_output_readers_preserve_exit_status(self):
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        os.close(r1)
        os.close(r2)
        try:
            child = subprocess.run([str(CLI), "run", "--engine", "custom"], env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=w1, stderr=w2, timeout=5)
            self.assertEqual(child.returncode, 0)
        finally:
            os.close(w1)
            os.close(w2)

    def test_termination_reaps_child_and_releases_slot(self):
        self.mode("sleep")
        child = subprocess.Popen([str(CLI), "run", "--engine", "custom"], env=self.env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            engine_pid = int(child.stdout.readline())
            child.terminate()
            self.assertEqual(child.wait(timeout=5), 130)
            with self.assertRaises(ProcessLookupError):
                os.kill(engine_pid, 0)
            fd = self.pool.slot(self.a)
            self.assertIsNotNone(fd)
            os.close(fd)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdout.close()
            child.stderr.close()

    def test_parent_crash_does_not_free_running_child_slot(self):
        self.mode("sleep")
        child = subprocess.Popen([str(CLI), "run", "--engine", "custom"], env=self.env,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        engine_pid = None
        try:
            engine_pid = int(child.stdout.readline())
            child.kill()
            child.wait(timeout=5)
            self.assertIsNone(self.pool.slot(self.a))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if engine_pid:
                import signal
                os.killpg(engine_pid, signal.SIGKILL)
            child.stdout.close()
            child.stderr.close()

    def test_config_validation_and_corrupt_state_fail_closed(self):
        self.cli("run", "--engine", "missing", expected=64)
        self.cli("cool", "--engine", "custom", "acct-a", "--seconds", "nan", expected=64)
        self.cli("run", "--engine", "custom", "--resume", "../escape", expected=64)
        self.cli("status")
        self.state.joinpath("state.json").write_text("broken json")
        self.cli("run", "--engine", "custom", expected=64)

    def test_example_engine_definitions_parse(self):
        import tomllib
        data = tomllib.loads(ROOT.joinpath("config/engines.example.toml").read_text())
        self.assertEqual(set(data["engines"]), {"claude", "codex", "gemini"})
        for engine in data["engines"].values():
            self.assertTrue(engine["config_env"])
            for patterns in engine["patterns"].values():
                import re
                for pattern in patterns:
                    re.compile(pattern)

    def test_reset_duration(self):
        now = time.time()
        self.assertAlmostEqual(reset_time("retry in 2 minutes", 100), now + 120, delta=1)
        self.assertAlmostEqual(reset_time("no timestamp", 100), now + 100, delta=1)


if __name__ == "__main__":
    unittest.main()
