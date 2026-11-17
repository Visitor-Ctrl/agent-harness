"""Configurable notification transports using only the standard library."""
import argparse
from email.message import EmailMessage
import json
import os
from pathlib import Path
import subprocess
import tomllib
import urllib.request


def send(config, message, *, urlopen=urllib.request.urlopen, run=subprocess.run):
    """Deliver to every configured backend; raise if any delivery fails."""
    failures = []
    for backend in config.get("backends", []):
        try:
            kind = backend["type"]
            if kind in ("ntfy", "webhook"):
                url = backend["url"]
                if not url.startswith("https://"):
                    raise ValueError("HTTPS required")
                body = message.encode() if kind == "ntfy" else json.dumps({"message": message}).encode()
                headers = {"Content-Type": "text/plain; charset=utf-8" if kind == "ntfy" else "application/json"}
                request = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urlopen(request, timeout=15) as response:
                    if not 200 <= response.status < 300:
                        raise ValueError("delivery failed")
            elif kind == "email":
                mail = EmailMessage()
                mail["From"] = backend["from"]
                mail["To"] = backend["to"]
                mail["Subject"] = backend.get("subject", "Agent harness alert")
                mail.set_content(message)
                # -t derives recipients from validated headers; -i prevents dot truncation.
                run([backend.get("sendmail", "/usr/sbin/sendmail"), "-i", "-t"],
                    input=mail.as_bytes(), capture_output=True, timeout=15, check=True)
            else:
                raise ValueError("unknown backend")
        except Exception:
            failures.append(True)
    if failures or not config.get("backends"):
        raise ValueError("notification delivery failed")


def config_path(name):
    return Path(os.environ.get("HARNESS_CONFIG_DIR", "~/.config/agent-harness")).expanduser() / name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config_path("notify.toml"))
    parser.add_argument("message")
    args = parser.parse_args(argv)
    try:
        with args.config.open("rb") as source:
            send(tomllib.load(source), args.message)
        return 0
    except Exception:
        parser.exit(1, "notify: configuration or delivery failed\n")
