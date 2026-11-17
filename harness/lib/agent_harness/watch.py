"""Read local health signals and send deduplicated alerts."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
from .notify import config_path, send


def collect(config, *, now=None, run=subprocess.run, disk_usage=shutil.disk_usage, acknowledgements=None):
    now = time.time() if now is None else now
    alerts = {}
    stalled = config.get("stalled_seconds", 3600)
    unread = config.get("unread_seconds", 3600)
    acknowledgements = acknowledgements or {}
    jobs_dir = Path(config.get("jobs_dir", os.environ.get("HARNESS_DISPATCH_DIR", "~/.local/state/agent-harness/dispatch"))).expanduser()
    paths = list(jobs_dir.glob("*.json")) + list(jobs_dir.glob("*/meta.json"))
    for path in sorted(paths):
        try:
            job = json.loads(path.read_text())
            name = path.parent.name if path.name == "meta.json" else path.stem
            updated = float(job.get("updated_at", job.get("created", job.get("started_at", path.stat().st_mtime))))
            attempt = Path(job["attempt"]) if job.get("attempt") else None
            if attempt and job.get("status") in ("running", "starting"):
                for log in (attempt / "output.log", attempt / "stderr.log"):
                    if log.is_file():
                        updated = max(updated, log.stat().st_mtime)
            if job.get("status") in ("running", "starting") and now - float(updated) >= stalled:
                alerts[f"stalled:{name}"] = f"Dispatch job {name} has stalled."
            if job.get("status") in ("completed", "failed", "stopped", "done", "lost") and not job.get("read_at"):
                ended = float(job.get("finished_at", job.get("finished", updated)))
                if now - ended >= unread and acknowledgements.get(name, -1) < ended:
                    alerts[f"unread:{name}"] = f"Dispatch job {name} has an unread result."
        except (OSError, ValueError, TypeError, AttributeError):
            alerts[f"job-state:{path.name}"] = f"Cannot read dispatch state {path.name}."

    def capture(command):
        return run(command, capture_output=True, text=True, timeout=20, check=True).stdout

    for scope in ("system", "user"):
        try:
            command = ["systemctl", "--" + scope, "--failed", "--no-legend", "--plain", "--no-pager"]
            for line in capture(command).splitlines():
                if line.strip():
                    unit = line.split()[0]
                    alerts[f"unit:{scope}:{unit}"] = f"Failed {scope} unit: {unit}."
        except (OSError, subprocess.SubprocessError):
            alerts[f"check:systemd:{scope}"] = f"Cannot inspect {scope} systemd units."
    pool = config.get("pool_command", ["ai-pool"])
    try:
        healthy = int(capture([*pool, "healthy"]).strip())
        if healthy < config.get("minimum_healthy", 2):
            alerts["pool:healthy"] = f"Only {healthy} healthy pool accounts remain."
        rows = json.loads(capture([*pool, "status", "--json"]))
        for row in rows:
            if row["state"] == "signed-out":
                # 'checked' is the latest observation, not the sign-out onset.
                alerts[f"signin:{row['engine']}:{row['name']}"] = f"Sign-in required: {row['engine']}/{row['name']}."
            elif now - float(row.get("checked") or 0) >= config.get("signin_stale_seconds", 86400):
                alerts[f"signin-stale:{row['engine']}:{row['name']}"] = f"Sign-in health is stale: {row['engine']}/{row['name']}."
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        alerts["check:pool"] = "Cannot inspect account pool health."
    for directory in config.get("disk_paths", [str(Path.home())]):
        try:
            usage = disk_usage(str(Path(directory).expanduser()))
            percent = 100 * usage.used / usage.total
            if percent >= config.get("disk_percent", 90):
                alerts[f"disk:{directory}"] = f"Disk usage at {directory}: {percent:.1f}%."
        except (OSError, ZeroDivisionError):
            alerts[f"check:disk:{directory}"] = f"Cannot inspect disk usage at {directory}."
    return alerts


def deliver(alerts, state_path, notify_config, *, now=None, repeat_seconds=86400, sender=send):
    """Serialize runs; remember only successfully delivered active alerts."""
    now = time.time() if now is None else now
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        pending = {key: value for key, value in alerts.items()
                   if key not in state or now - state[key] >= repeat_seconds}
        state = {key: value for key, value in state.items() if key in alerts}
        if pending:
            sender(notify_config, "\n".join(pending.values()))
            state.update({key: now for key in pending})
        temporary = path.with_name(path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(state, output, sort_keys=True)
        os.replace(temporary, path)
        return len(pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config_path("watch.toml"))
    parser.add_argument("--notify-config", type=Path, default=config_path("notify.toml"))
    parser.add_argument("--state", type=Path, default=Path(os.environ.get("HARNESS_STATE_DIR", "~/.local/state/agent-harness")).expanduser() / "watch.json")
    parser.add_argument("--ack", metavar="JOB", action="append", default=[], help="acknowledge current unread result (repeatable)")
    args = parser.parse_args(argv)
    try:
        ack_path = args.state.with_name(args.state.name + ".acks.json")
        if args.ack:
            for name in args.ack:
                if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
                    raise ValueError("invalid job name")
            args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock_fd = os.open(str(ack_path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(lock_fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                acks = json.loads(ack_path.read_text()) if ack_path.exists() else {}
                acks.update({name: time.time() for name in args.ack})
                temporary = ack_path.with_name(ack_path.name + ".tmp")
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as output:
                    json.dump(acks, output)
                os.replace(temporary, ack_path)
            return 0
        acks = json.loads(ack_path.read_text()) if ack_path.exists() else {}
        with args.config.open("rb") as source:
            config = tomllib.load(source)
        with args.notify_config.open("rb") as source:
            notifications = tomllib.load(source)
        deliver(collect(config, acknowledgements=acks), args.state, notifications,
                repeat_seconds=config.get("repeat_seconds", 86400))
        return 0
    except Exception:
        parser.exit(1, "harness-watch: configuration, inspection or delivery failed\n")
