"""Explicit send, peek and acknowledgement for the optional HTTPS mailbox."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward bearer or Access credentials to another URL.


def identity(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', value):
        raise ValueError('invalid mailbox name')
    return value


def request(config, method, mailbox, data=None, message_id=None):
    url = config['url']
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('messaging.url must be an HTTPS origin without credentials')
    token = os.environ.get('AGENT_MSG_TOKEN', '')
    if not token or any(c in token for c in '\r\n'):
        raise ValueError('AGENT_MSG_TOKEN is required')
    endpoint = url.rstrip('/') + '/v1/mailboxes/' + identity(mailbox)
    if message_id:
        endpoint += '/' + str(uuid.UUID(message_id))
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    for env, header in (('CF_ACCESS_CLIENT_ID', 'CF-Access-Client-Id'),
                        ('CF_ACCESS_CLIENT_SECRET', 'CF-Access-Client-Secret')):
        if os.environ.get(env):
            headers[header] = os.environ[env]
    payload = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(endpoint, data=payload, method=method, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=15) as response:
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('mailbox response exceeds limit')
        return json.loads(body)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(os.environ.get(
        'HARNESS_CONFIG_DIR', '~/.config/agent-harness')).expanduser() / 'messaging.toml')
    sub = parser.add_subparsers(dest='command', required=True)
    send = sub.add_parser('send')
    send.add_argument('to')
    send.add_argument('--file', type=Path, help='UTF-8 message file; otherwise stdin')
    send.add_argument('--id', help='reuse a UUID on an explicit retry')
    sub.add_parser('receive', help='peek without removing messages')
    ack = sub.add_parser('ack')
    ack.add_argument('id')
    args = parser.parse_args(argv)
    try:
        with args.config.open('rb') as stream:
            cfg = tomllib.load(stream)['messaging']
        if args.command == 'send':
            if args.file:
                with args.file.open('rb') as stream:
                    raw = stream.read(8193)
            else:
                raw = sys.stdin.buffer.read(8193)
            if not raw.strip() or len(raw) > 8192:
                raise ValueError('message must contain 1..8192 UTF-8 bytes')
            message_id = str(uuid.UUID(args.id)) if args.id else str(uuid.uuid4())
            # Print retry identity before transport; delivery can succeed despite a timeout.
            print('message id: ' + message_id, file=sys.stderr)
            result = request(cfg, 'POST', args.to, {'id': message_id, 'body': raw.decode('utf-8')})
        elif args.command == 'receive':
            result = request(cfg, 'GET', cfg['mailbox'])
        else:
            result = request(cfg, 'DELETE', cfg['mailbox'], message_id=args.id)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except urllib.error.HTTPError as exc:
        print(f'agent-msg: HTTP {exc.code}; no automatic retry', file=sys.stderr)
    except (OSError, ValueError, KeyError):
        print('agent-msg: invalid configuration, input or transport failure; delivery may be uncertain', file=sys.stderr)
    return 64
