"""Config-driven account pool. No engine names or credential readers live here."""
import argparse
import contextlib
import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
STATES = {"ok", "unknown", "cooling", "unlicensed", "signed-out"}
DEFAULTS = dict(cool_seconds=3600, probe_interval=1800, unlicensed_recheck=1500,
                probe_timeout=90, slot_wait=0)


def atomic(path, data):
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def emit(data, target):
    try:
        target.buffer.write(data)
        target.buffer.flush()
    except BrokenPipeError:
        # Python's shutdown flush must not replace the child's exit code with 120.
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, target.fileno())
        os.close(fd)


def load_toml(path):
    with open(path, "rb") as stream:
        return tomllib.load(stream)


class Pool:
    def __init__(self, config, state):
        self.engines = load_toml(config / "engines.toml")["engines"]
        settings = load_toml(config / "pool.toml")
        self.settings = DEFAULTS | settings.get("pool", {})
        for key in DEFAULTS:
            value = self.settings[key]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"pool.{key} must be a finite nonnegative number")
        self.accounts = settings.get("accounts", [])
        seen = set()
        for engine, definition in self.engines.items():
            if not NAME.fullmatch(engine):
                raise ValueError("invalid engine name")
            env = definition.get("config_env", "")
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", env):
                raise ValueError(f"{engine}: config_env must be an environment variable name")
            for field in ("command", "probe_command"):
                self.argv(definition, field, required=True)
            for field in ("resume_command", "model_flag", "effort_flag", "resume_flag", "session_flag"):
                self.argv(definition, field)
            for patterns in definition.get("patterns", {}).values():
                for pattern in patterns:
                    re.compile(pattern, re.I)
            glob = definition.get("transcript_glob", "")
            if glob and (Path(glob).is_absolute() or ".." in Path(glob).parts or "{session}" not in glob):
                raise ValueError("transcript_glob must be relative and contain {session}")
        for account in self.accounts:
            name, engine = account["name"], account["engine"]
            if not NAME.fullmatch(name) or engine not in self.engines:
                raise ValueError("account name or engine is invalid")
            account["key"] = f"{engine}/{name}"
            if account["key"] in seen:
                raise ValueError("duplicate account")
            seen.add(account["key"])
            account["slots"] = account.get("slots", 1)
            if type(account["slots"]) is not int or not 1 <= account["slots"] <= 1024:
                raise ValueError("slots must be an integer between 1 and 1024")
            if type(account.get("priority", 0)) is not int:
                raise ValueError("priority must be an integer")
            account["path"] = Path(account["config_dir"]).expanduser().absolute()
        self.root = state
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "slots").mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def argv(engine, key, required=False, **values):
        args = engine.get(key, [])
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args) or (required and not args):
            raise ValueError(f"{key} must be an argv array")
        if values:
            return [arg.format_map(values) for arg in args]
        return args

    @contextlib.contextmanager
    def locked(self):
        fd = os.open(self.root / "state.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                state = json.loads((self.root / "state.json").read_text())
            except FileNotFoundError:
                state = {}
            # Corrupt state is an error; never silently re-enable disabled accounts.
            yield state
            atomic(self.root / "state.json", json.dumps(state, sort_keys=True).encode())
        finally:
            os.close(fd)

    def event(self, account, old, new):
        if old == new:
            return
        entry = dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     account=account["key"], old=old, new=new)
        fd = os.open(self.root / "events.log", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(entry) + "\n").encode())
        finally:
            os.close(fd)

    def mark(self, state, account, verdict, started=0, until=None):
        record = state.setdefault(account["key"], {})
        record["checked"] = time.time()
        if verdict == "unknown":
            record["uncertain"] = True
            return  # An inconclusive probe keeps the previous health verdict.
        if verdict == "ok" and record.get("changed", 0) > started and record.get("state") in STATES - {"ok", "unknown"}:
            return  # An older successful job cannot clear a newer failure.
        old = record.get("state", "unknown")
        record.update(state=verdict, uncertain=False)
        if old != verdict:
            record["changed"] = time.time()
        if verdict == "cooling":
            record["until"] = until or time.time() + self.settings["cool_seconds"]
            record["last_limited"] = time.time()
        else:
            record.pop("until", None)
        if verdict == "unlicensed":
            record["last_auth_fail"] = time.time()
        self.event(account, old, verdict)

    @staticmethod
    def effective(record):
        state = record.get("state", "unknown")
        if state == "cooling" and record.get("until", 0) <= time.time():
            return "unknown"
        return state

    def eligible(self, account, record):
        if record.get("manual") in ("off", "drain") or not account["path"].is_dir():
            return False
        verdict = self.effective(record)
        return verdict in ("ok", "unknown") or (verdict == "unlicensed" and
            time.time() - record.get("last_auth_fail", 0) >= self.settings["unlicensed_recheck"])

    def slot(self, account):
        for index in range(account["slots"]):
            path = self.root / "slots" / (account["key"].replace("/", "--") + f".{index}")
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
        return None

    def busy(self, account):
        held = []
        try:
            for _ in range(account["slots"]):
                fd = self.slot(account)
                if fd is None:
                    break
                held.append(fd)
            return account["slots"] - len(held)
        finally:
            for fd in held:
                os.close(fd)

    def settle(self, state, accounts):
        for account in accounts:
            record = state.get(account["key"], {})
            if record.get("manual") == "drain" and not self.busy(account):
                record["manual"] = "off"
                self.event(account, "drain", "off")

    def acquire(self, accounts, tried):
        deadline = time.monotonic() + self.settings["slot_wait"]
        while True:
            with self.locked() as state:
                self.settle(state, accounts)
                candidates = [a for a in accounts if a["key"] not in tried and self.eligible(a, state.get(a["key"], {}))]
                candidates.sort(key=lambda a: (
                    self.effective(state.get(a["key"], {})) == "unlicensed",
                    a.get("priority", 0), state.get(a["key"], {}).get("last_limited", 0)))
                for account in candidates:
                    fd = self.slot(account)
                    if fd is not None:
                        return account, fd
            if not candidates or time.monotonic() >= deadline:
                return None, None
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def environment(self, account):
        env = dict(os.environ)
        # Only this invocation's account directory is supplied; credentials stay opaque.
        env[self.engines[account["engine"]]["config_env"]] = str(account["path"])
        env["AI_POOL_ACCOUNT"] = account["name"]
        env["AI_POOL_ENGINE"] = account["engine"]
        env.pop("AI_POOL_RETRY_FILE", None)
        return env

    def selected(self, engine=None, name=None):
        if engine and engine not in self.engines:
            raise ValueError("unknown engine")
        accounts = [a for a in self.accounts if (not engine or a["engine"] == engine) and (not name or a["name"] == name)]
        if name and not accounts:
            raise ValueError("unknown account")
        return accounts


def matches(engine, kind, text):
    return any(re.search(rx, text, re.I) for rx in engine.get("patterns", {}).get(kind, []))


def classify(engine, code, out, err, structured=False):
    """Recognize terminal events only in explicitly selected structured output."""
    text = out.strip()
    event = None
    schema = engine.get("result", {})
    if structured and schema:
        for line in reversed(out.splitlines()):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get(schema["type_key"]) == schema["type_value"]:
                event = value
                break
        text = str(event.get(schema["text_key"], "")) if event else ""
    for kind, verdict in (("auth", "signed-out"), ("unlicensed", "unlicensed"), ("limit", "cooling")):
        if len(text) < 400 and matches(engine, "notice_" + kind, text):
            return verdict, text
    if event and not event.get(schema["error_key"]) and event.get(schema["success_key"]) == schema["success_value"]:
        return "ok", text
    if code == 0 and not (event and event.get(schema["error_key"])):
        return "ok", text
    evidence = err[-8000:] + "\n" + text[-4000:]
    for kind, verdict in (("auth", "signed-out"), ("unlicensed", "unlicensed"), ("limit", "cooling")):
        if matches(engine, kind, evidence):
            return verdict, evidence
    return "unknown", evidence


def reset_time(text, default):
    now = time.time()
    epoch = re.search(r"\|(\d{10})\b", text)
    if epoch and float(epoch[1]) > now:
        return float(epoch[1])
    relative = re.search(r"(?:retry|try again) in (\d+)\s*(sec|second|min|minute|hour|hr)", text, re.I)
    if relative:
        return now + int(relative[1]) * {"s": 1, "m": 60, "h": 3600}[relative[2][0].lower()]
    return now + default


def is_stream(engine, args):
    return any(any(args[i:i + len(sequence)] == sequence for i in range(len(args)))
               for sequence in engine.get("stream_flags", []))


def execute(command, env, feed, stream=True, timeout=None, slot_fd=None):
    """Stream output with bounded classification tails, and reap on cancellation."""
    tails = {"out": b"", "err": b""}
    with tempfile.TemporaryFile() as stdin, selectors.DefaultSelector() as selector:
        if feed is not None:
            stdin.write(feed)
            stdin.seek(0)
        proc = subprocess.Popen(command, env=env, stdin=stdin if feed is not None else None,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                pass_fds=() if slot_fd is None else (slot_fd,))
        try:
            selector.register(proc.stdout, selectors.EVENT_READ, ("out", sys.stdout))
            selector.register(proc.stderr, selectors.EVENT_READ, ("err", sys.stderr))
            deadline = time.monotonic() + timeout if timeout is not None else None
            while selector.get_map():
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(0.1):
                    data = os.read(key.fd, 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    label, target = key.data
                    tails[label] = (tails[label] + data)[-65536:]
                    if stream:
                        emit(data, target)
            return proc.wait(), tails["out"].decode(errors="replace"), tails["err"].decode(errors="replace")
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            proc.stdout.close()
            proc.stderr.close()


def safe_relative(root, path):
    """Reject symbolic links at every component, including the configured root."""
    relative = path.relative_to(root)
    current = root
    if current.is_symlink():
        return False
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def transfer_session(pool, accounts, target, session):
    """Copy only configured transcript matches and their regular sidecar files."""
    pattern = pool.engines[target["engine"]].get("transcript_glob")
    if not pattern:
        return False
    sources = []
    for account in accounts:
        for path in account["path"].glob(pattern.format(session=session)):
            if safe_relative(account["path"], path) and path.is_file():
                sources.append((path.stat().st_mtime_ns, path, account["path"]))
    if not sources:
        return False
    _, source, root = max(sources, key=lambda item: item[0])
    files = [source]
    side = source.with_suffix("")
    if safe_relative(root, side) and side.is_dir():
        files.extend(p for p in side.rglob("*") if safe_relative(root, p) and p.is_file())
    for src in files:
        dst = target["path"] / src.relative_to(root)
        if not safe_relative(target["path"], dst):
            raise ValueError("session destination contains a symbolic link")
        if src == dst or (dst.exists() and dst.stat().st_mtime_ns >= src.stat().st_mtime_ns):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never follow a source link swapped in after the directory scan.
        fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("session source is not a regular file")
            atomic(dst, stream.read())
        os.utime(dst, ns=(src.stat().st_atime_ns, src.stat().st_mtime_ns))
    return True


def run(pool, args):
    engine = pool.engines[args.engine]
    accounts = pool.selected(args.engine, args.account)
    if args.session and not SESSION.fullmatch(args.session):
        raise ValueError("invalid session identifier")
    if args.resume and not SESSION.fullmatch(args.resume):
        raise ValueError("invalid resume identifier")
    common = []
    for key in ("model", "effort"):
        if getattr(args, key):
            if not engine.get(key + "_flag"):
                raise ValueError(f"engine does not declare {key}_flag")
            common += pool.argv(engine, key + "_flag", **{key: getattr(args, key)})
    if args.session and not engine.get("session_flag"):
        raise ValueError("engine cannot assign a new session identifier; use --resume for an existing session")
    if (args.resume or args.session) and not engine.get("resume_flag"):
        raise ValueError("engine does not declare resume_flag")
    extra = args.args[1:] if args.args[:1] == ["--"] else args.args
    feed = None if sys.stdin.isatty() else sys.stdin.buffer.read()
    tried = set()
    while True:
        account, fd = pool.acquire(accounts, tried)
        if account is None:
            with pool.locked() as state:
                now = time.time()
                times = []
                for candidate in accounts:
                    record = state.get(candidate["key"], {})
                    if record.get("manual") or record.get("state") == "signed-out":
                        continue
                    if record.get("state") == "cooling":
                        times.append(max(now + 1, record.get("until", now + 60)))
                    elif record.get("state") == "unlicensed":
                        times.append(max(now + 1, record.get("last_auth_fail", now) + pool.settings["unlicensed_recheck"]))
                    else:
                        times.append(now + 60)
                retry = int(min(times, default=now + 60))
            retry_file = os.environ.get("AI_POOL_RETRY_FILE")
            if retry_file:
                atomic(Path(retry_file), f"{retry}\n".encode())
            emit(f"ai-pool: no account available; AI_POOL_RETRY_AT={retry}\n".encode(), sys.stderr)
            return 75
        tried.add(account["key"])
        started = time.time()
        try:
            session = args.resume or args.session
            existing = transfer_session(pool, pool.selected(args.engine), account, session) if session else False
            resume = bool(args.resume or existing)
            command = engine.get("resume_command", engine["command"]) if resume else engine["command"]
            flags = pool.argv(engine, "resume_flag" if resume else "session_flag", session=session) if session else []
            attempt_feed = feed
            if resume:
                continuation = engine.get("continue_prompt", "Continue the same task from this session's checkpoint. "
                    "Inspect the current state first; preserve completed work and do not repeat completed side effects.\n")
                attempt_feed = continuation.encode() + (feed or b"")
            code, out, err = execute(command + common + flags + extra, pool.environment(account), attempt_feed, slot_fd=fd)
            verdict, evidence = classify(engine, code, out, err, is_stream(engine, extra))
            with pool.locked() as state:
                pool.mark(state, account, verdict, started, reset_time(evidence, pool.settings["cool_seconds"]))
            if verdict not in ("cooling", "signed-out", "unlicensed"):
                return 0 if verdict == "ok" else (code if code > 0 else (128 - code if code < 0 else 1))
        finally:
            os.close(fd)
            with pool.locked() as state:
                pool.settle(state, accounts)


def probe(pool, accounts, force):
    for account in accounts:
        started = time.time()
        with pool.locked() as state:
            pool.settle(state, accounts)
            record = state.get(account["key"], {})
            interval = pool.settings["unlicensed_recheck"] if record.get("state") == "unlicensed" else pool.settings["probe_interval"]
            if record.get("manual") in ("off", "drain") or not account["path"].is_dir():
                continue
            if not force and (pool.effective(record) == "cooling" or started - record.get("checked", 0) < interval):
                continue
            fd = pool.slot(account)
        if fd is None:
            continue
        try:
            try:
                code, out, err = execute(pool.engines[account["engine"]]["probe_command"],
                                         pool.environment(account), b"", stream=False,
                                         timeout=pool.settings["probe_timeout"], slot_fd=fd)
                verdict, evidence = classify(pool.engines[account["engine"]], code, out, err)
            except (OSError, subprocess.TimeoutExpired):
                verdict, evidence = "unknown", ""
            with pool.locked() as state:
                pool.mark(state, account, verdict, started, reset_time(evidence, pool.settings["cool_seconds"]))
            emit(f"{account['key']} {verdict}\n".encode(), sys.stdout)
        finally:
            os.close(fd)
            with pool.locked() as state:
                pool.settle(state, accounts)
    return 0


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config-dir", type=Path, default=Path(os.environ.get("HARNESS_CONFIG_DIR", "~/.config/agent-harness")).expanduser())
    cli.add_argument("--state-dir", type=Path, default=Path(os.environ.get("HARNESS_STATE_DIR", "~/.local/state/agent-harness/pool")).expanduser())
    subs = cli.add_subparsers(dest="command", required=True)
    for cmd in ("run", "status", "healthy", "probe", "on", "off", "drain", "cool"):
        sub = subs.add_parser(cmd)
        sub.add_argument("--engine", required=cmd not in ("status", "healthy", "probe"))
        if cmd == "run":
            sub.add_argument("--account")
            sub.add_argument("--model")
            sub.add_argument("--effort")
            sessions = sub.add_mutually_exclusive_group()
            sessions.add_argument("--session")
            sessions.add_argument("--resume")
            sub.add_argument("args", nargs=argparse.REMAINDER)
        elif cmd in ("on", "off", "drain", "cool"):
            sub.add_argument("account")
            if cmd == "cool":
                sub.add_argument("--seconds", type=float, default=None)
        elif cmd == "probe":
            sub.add_argument("--account")
            sub.add_argument("--force", action="store_true")
        elif cmd == "healthy":
            sub.add_argument("--names", action="store_true")
        else:
            sub.add_argument("--json", action="store_true")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    old_handler = signal.getsignal(signal.SIGTERM)
    def terminate(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        pool = Pool(args.config_dir, args.state_dir)
        accounts = pool.selected(args.engine, getattr(args, "account", None))
        if args.command == "run":
            return run(pool, args)
        if args.command == "probe":
            return probe(pool, accounts, args.force)
        if args.command in ("on", "off", "drain", "cool"):
            if args.command == "cool" and args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
                raise ValueError("cool --seconds must be finite and positive")
            with pool.locked() as state:
                for account in accounts:
                    record = state.setdefault(account["key"], {})
                    if args.command == "cool":
                        seconds = pool.settings["cool_seconds"] if args.seconds is None else args.seconds
                        pool.mark(state, account, "cooling", until=time.time() + seconds)
                    else:
                        old = record.get("manual", "on")
                        record["manual"] = args.command if args.command != "on" else ""
                        pool.event(account, old or "on", args.command)
                pool.settle(state, accounts)
            return probe(pool, accounts, True) if args.command == "on" else 0
        rows = []
        with pool.locked() as state:
            pool.settle(state, accounts)
            for account in accounts:
                record = state.get(account["key"], {})
                rows.append(dict(engine=account["engine"], name=account["name"],
                                 state=pool.effective(record), manual=record.get("manual", ""),
                                 busy=pool.busy(account), slots=account["slots"],
                                 configured=account["path"].is_dir(), checked=record.get("checked"),
                                 until=record.get("until"), uncertain=record.get("uncertain", False)))
        if args.command == "healthy":
            # Unknown and inconclusive results are never counted as verified healthy.
            rows = [r for r in rows if r["state"] == "ok" and not r["manual"] and not r["uncertain"]
                    and r["configured"] and r["busy"] < r["slots"]]
            output = "\n".join(f"{r['engine']}/{r['name']}" for r in rows) if args.names else str(len(rows))
        elif args.json:
            output = json.dumps(rows, sort_keys=True)
        else:
            output = "\n".join(f"{r['engine']}/{r['name']} {r['manual'] or r['state']} "
                               f"{r['busy']}/{r['slots']}" + (" uncertain" if r["uncertain"] else "") +
                               (" missing-directory" if not r["configured"] else "") for r in rows)
        emit((output + "\n").encode(), sys.stdout)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Do not print configuration data or exception strings containing command arguments.
        emit(f"ai-pool: configuration or execution error ({type(exc).__name__}); check configuration and executable paths\n".encode(), sys.stderr)
        return 64
    finally:
        signal.signal(signal.SIGTERM, old_handler)
