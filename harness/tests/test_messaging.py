import contextlib
import io
import json
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from agent_harness import messaging


class MessagingTests(unittest.TestCase):
    def test_https_origin_and_mailbox_restrictions(self):
        with patch.dict(os.environ, {'AGENT_MSG_TOKEN': 'fixture'}):
            for url in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path', 'https://example.com?secret=x'):
                with self.assertRaises(ValueError):
                    messaging.request({'url': url}, 'GET', 'host-a')
            with self.assertRaises(ValueError):
                messaging.request({'url': 'https://example.com'}, 'GET', '../host-b')

    def test_redirects_are_refused(self):
        self.assertIsNone(messaging.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))

    def test_transport_uses_headers_and_bounded_response(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"messages": []}'
        with patch.dict(os.environ, {'AGENT_MSG_TOKEN': 'fixture'}), \
                patch.object(messaging.urllib.request, 'build_opener', return_value=opener):
            result = messaging.request({'url': 'https://example.com'}, 'GET', 'host-a')
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://example.com/v1/mailboxes/host-a')
        self.assertEqual(req.get_header('Authorization'), 'Bearer fixture')
        self.assertEqual(result, {'messages': []})

    def test_send_peek_ack_and_input_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg = Path(temporary) / 'messaging.toml'
            cfg.write_text('[messaging]\nurl="https://example.com"\nmailbox="host-a"\n')
            message = Path(temporary) / 'message'
            message.write_text('bounded information')
            with patch.object(messaging, 'request', return_value={'ok': True}) as request, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                prefix = ['--config', str(cfg)]
                self.assertEqual(messaging.main(prefix + ['send', 'host-b', '--file', str(message)]), 0)
                uuid = request.call_args.args[3]['id']
                self.assertEqual(len(uuid), 36)
                self.assertEqual(messaging.main(prefix + ['receive']), 0)
                self.assertEqual(request.call_args.args[1], 'GET')
                self.assertEqual(messaging.main(prefix + ['ack', uuid]), 0)
                self.assertEqual(request.call_args.args[1], 'DELETE')
                message.write_bytes(b'x' * 8193)
                request.reset_mock()
                self.assertEqual(messaging.main(prefix + ['send', 'host-b', '--file', str(message)]), 64)
                request.assert_not_called()
