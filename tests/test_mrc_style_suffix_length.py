"""The MRC style suffix holds up to 32 chars (pipe codes included),
matching umrc-client's chatterNameSuffix, on the bridge and on both
ANetBBS clients. The style prefix stays at 16.
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401,E402  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat  # noqa: E402
from mrc.bridge.main import BridgeApp, MRC_STYLE_SUFFIX_MAX_LEN  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SUFFIX_40 = '|12<' + '=' * 36   # 40 chars
PREFIX_40 = '|11!' + '-' * 36   # 40 chars


def _run(coro):
    return asyncio.run(coro)


class _FakeWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def _make_bridge(tmp_dir):
    app = object.__new__(BridgeApp)
    app.config = {"bridge_bbs": "TestBBS"}
    from mrc.bridge.db import BridgeDB
    app.db = BridgeDB(tmp_dir)
    app.websockets = {}
    app.mrc_tcp_clients = {}
    app._ws_remote_ip = {}
    app.mrc = AsyncMock()
    app.mrc.connected = True
    app.join_packet_delay_ms = 0
    app.announce_join_part = False
    app.request_banners_on_join = False
    app.request_motd_on_join = False
    app.join_message_tpl = "- {handle} has arrived."
    app.exit_message_tpl = "- {handle} has left chat."
    app.ctcp_room = "ctcp_echo_channel"
    app.userlist_refresh_on_server_events = False
    app.identify_required_mode = False
    app.post_identify_auto_join = False
    app.default_style_prefix = ""
    app.default_style_suffix = ""
    app.default_style_color = "07"
    app.rate_limiter = {}
    app.pending_disconnects = {}
    return app


class BridgeSuffixLengthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 444
        self.app.websockets[self.ws_id] = _FakeWs()
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))

    def test_limit_matches_umrc_chatter_name_suffix(self):
        self.assertEqual(MRC_STYLE_SUFFIX_MAX_LEN, 32)

    def test_set_style_keeps_32_char_suffix_and_16_char_prefix(self):
        _run(self.app._handle_set_style(
            self.ws_id, {"prefix": PREFIX_40, "suffix": SUFFIX_40}))
        sess = self.app.db.get_session(str(self.ws_id))
        self.assertEqual(sess["style_suffix"], SUFFIX_40[:32])
        self.assertEqual(sess["style_prefix"], PREFIX_40[:16])
        prof = self.app.db.get_profile("Alice")
        self.assertEqual(prof["style_suffix"], SUFFIX_40[:32])

    def test_32_char_suffix_survives_rejoin(self):
        _run(self.app._handle_set_style(self.ws_id, {"suffix": SUFFIX_40[:32]}))
        ws2 = 445
        self.app.websockets[ws2] = _FakeWs()
        _run(self.app._handle_join_room(ws2, {"handle": "Alice", "room": "lobby"}))
        sess = self.app.db.get_session(str(ws2))
        self.assertEqual(sess["style_suffix"], SUFFIX_40[:32])
        self.assertIn(SUFFIX_40[:32], self.app._session_display_handle(sess))


class TerminalSetSuffixTests(unittest.TestCase):
    def _chat(self):
        class _S:
            user = {'username': 'tester'}
            written = []

            async def write(self, text):
                self.written.append(text)
        chat = MRCChat(_S())
        chat._split_screen = False
        chat._handle = 'Alice'
        chat.sent = []

        async def _fake_send_json(obj):
            chat.sent.append(obj)
        chat._send_json = _fake_send_json
        return chat

    def test_set_suffix_sends_up_to_32_chars(self):
        chat = self._chat()
        _run(chat._handle_slash(f'/set suffix {SUFFIX_40}'))
        self.assertEqual(chat.sent[-1]['suffix'], SUFFIX_40[:32])

    def test_set_prefix_still_sends_up_to_16_chars(self):
        chat = self._chat()
        _run(chat._handle_slash(f'/set prefix {PREFIX_40}'))
        self.assertEqual(chat.sent[-1]['prefix'], PREFIX_40[:16])


class WebSuffixInputTests(unittest.TestCase):
    def test_both_web_clients_allow_32_char_suffix(self):
        for rel in ('mrc/web/index.html', 'anetbbs/templates/mrc/index.html'):
            html = (REPO / rel).read_text(encoding='utf-8')
            self.assertIn('id="style-suffix" maxlength="32"', html, rel)
            self.assertIn('id="style-prefix" maxlength="16"', html, rel)


if __name__ == '__main__':
    unittest.main()
