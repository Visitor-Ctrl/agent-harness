"""Linux peer-authenticated, OCI instance-principal secret broker."""
import argparse
import base64
from collections import defaultdict, deque
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import time
import tomllib

MAX_REQUEST = 4096
MAX_SECRET = 1024 * 1024
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


def peer_uid(connection):
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def read_line(connection, limit):
    data = bytearray()
    while len(data) <= limit:
        chunk = connection.recv(min(4096, limit + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if len(data) > limit or not data.endswith(b"\n") or b"\n" in data[:-1]:
        raise ValueError("invalid frame")
    return json.loads(data)


class Broker:
    def __init__(self, profiles, audit, *, oci="/usr/bin/oci", rate_limit=30,
                 rate_window=60, clock=time.time):
        self.profiles = profiles
        self.audit = Path(audit)
        self.oci = oci
        self.rate_limit = rate_limit
        self.rate_window = rate_window
        self.clock = clock
        self.requests = defaultdict(deque)

    def fetch(self, ocid):
        result = subprocess.run([self.oci, "secrets", "secret-bundle", "get", "--auth",
                                 "instance_principal", "--secret-id", ocid],
                                capture_output=True, timeout=30, check=True,
                                env={"PATH": "/usr/bin:/bin", "HOME": "/root",
                                     "OCI_CLI_SUPPRESS_FILE_PERMISSIONS_WARNING": "True"})
        value = json.loads(result.stdout)["data"]["secret-bundle-content"]["content"]
        if len(value) > MAX_SECRET * 2:
            raise ValueError("oversize secret")
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) > MAX_SECRET:
            raise ValueError("oversize secret")
        return decoded

    def log(self, uid, profile, name, result):
        line = dict(time=self.clock(), uid=uid, profile=profile, secret=name, result=result)
        fd = os.open(self.audit, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as output:
            info = os.fstat(output.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise OSError("unsafe audit file")
            output.write(json.dumps(line, sort_keys=True) + "\n")

    def handle(self, connection, profile):
        uid, name, outcome = -1, "-", "denied"
        response = {"ok": False}
        connection.settimeout(5)
        try:
            uid = peer_uid(connection)
            policy = self.profiles[profile]
            if uid != policy["uid"]:
                raise ValueError("unauthorized")
            now = self.clock()
            recent = self.requests[uid]
            while recent and recent[0] <= now - self.rate_window:
                recent.popleft()
            if len(recent) >= self.rate_limit:
                outcome = "rate-limited"
                raise ValueError("rate limited")
            recent.append(now)
            request = read_line(connection, MAX_REQUEST)
            candidate = request.get("name", "")
            if not isinstance(candidate, str) or not NAME.fullmatch(candidate):
                raise ValueError("invalid name")
            name = candidate
            if name not in policy["secrets"]:
                raise ValueError("unauthorized")
            value = self.fetch(policy["secrets"][name])
            response = {"ok": True, "value": base64.b64encode(value).decode("ascii")}
            outcome = "ok"
        except Exception:
            # Exceptions can contain OCI output; never stringify them.
            pass
        try:
            self.log(uid, profile, name, outcome)
        except OSError:
            response = {"ok": False}  # Fail closed when auditing is unavailable.
        try:
            connection.sendall(json.dumps(response).encode() + b"\n")
        except OSError:
            pass


def load_config(path):
    info = os.lstat(path)
    if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
        raise ValueError("configuration must be a root-owned private policy")
    with open(path, "rb") as source:
        config = tomllib.load(source)
    profiles = config["profiles"]
    for name, policy in profiles.items():
        if not NAME.fullmatch(name) or type(policy["uid"]) is not int or policy["uid"] < 0:
            raise ValueError("invalid profile")
        for secret, ocid in policy["secrets"].items():
            if not NAME.fullmatch(secret) or not isinstance(ocid, str) or not ocid.startswith("ocid1.vaultsecret."):
                raise ValueError("invalid secret mapping")
    return profiles


def serve(profiles, runtime, audit):
    import selectors
    runtime = Path(runtime)
    runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("unsafe runtime directory")
    broker = Broker(profiles, audit)
    selector = selectors.DefaultSelector()
    sockets = []
    try:
        for profile, policy in profiles.items():
            path = runtime / (profile + ".sock")
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != policy["uid"]:
                    raise ValueError("unsafe existing socket")
                path.unlink()
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(path))
            sockets.append((listener, path))
            os.chmod(path, 0o600)
            os.chown(path, policy["uid"], -1)
            listener.listen(16)
            selector.register(listener, selectors.EVENT_READ, profile)
        while True:
            for key, _ in selector.select():
                connection, _ = key.fileobj.accept()
                with connection:
                    broker.handle(connection, key.data)
    finally:
        selector.close()
        for listener, path in sockets:
            listener.close()
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/agent-harness/secrets.toml")
    parser.add_argument("--runtime-dir", default="/run/agent-harness")
    parser.add_argument("--audit", default="/var/log/agent-harness/secrets.jsonl")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise ValueError("root required")
        serve(load_config(args.config), args.runtime_dir, args.audit)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "secret-broker: policy or service error\n")
