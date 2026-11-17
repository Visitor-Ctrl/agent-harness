"""Deliver a single broker value to one child without displaying it."""
import argparse
import base64
import os
import re
import socket
import subprocess
from .broker import MAX_SECRET, NAME, peer_uid, read_line


def request_secret(path, name):
    import json
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(40)
        connection.connect(path)
        if peer_uid(connection) != 0:
            raise ValueError("broker must be root")
        connection.sendall(json.dumps({"name": name}).encode() + b"\n")
        response = read_line(connection, MAX_SECRET * 2)
    if response.get("ok") is not True:
        raise ValueError("broker refused request")
    return base64.b64decode(response["value"], validate=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("HARNESS_PROFILE"))
    parser.add_argument("--socket", help="override broker socket path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--env", metavar="NAME")
    mode.add_argument("--stdin", action="store_true")
    parser.add_argument("name")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not NAME.fullmatch(args.name):
        parser.error("a secret name and child command are required")
    if args.env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.env):
        parser.error("invalid environment variable name")
    if not args.socket and (not args.profile or not NAME.fullmatch(args.profile)):
        parser.error("a profile or socket is required")
    try:
        value = request_secret(args.socket or f"/run/agent-harness/{args.profile}.sock", args.name)
        environment = os.environ.copy()
        if args.env:
            environment[args.env] = value.decode("utf-8")
            result = subprocess.run(command, env=environment)
        else:
            result = subprocess.run(command, env=environment, input=value)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "secret-run: request or child launch failed\n")
