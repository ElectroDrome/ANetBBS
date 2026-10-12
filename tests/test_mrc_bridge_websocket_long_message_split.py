"""Regression tests for the WebSocket chat-send path's long-message
handling (mrc/bridge/main.py's _handle_send_message/
_handle_direct_message) -- the same bug shape found live on the
native-TCP umrc-client path (github.com/codefenix-dev/uMRC issue #27,
see test_mrc_bridge_tcp_listener.py's MrcTcpLongMessageSplitTests for
the full writeup): a message exceeding the bridge's 140-char wire cap
had its tail silently dropped by _truncate_wire_message() instead of
being split across multiple packets. Both WebSocket handlers hit the
exact same _truncate_wire_message() call before the fix, so they get
the same dedicated coverage rather than relying only on the TCP path's
tests to prove the fix generally.

Uses the same lightweight mock-BridgeApp harness as
test_mrc_bridge_identify_gate.py (object.__new__(BridgeApp) + a fake
upstream-hub AsyncMock) rather than real loopback sockets, since these
two handlers take a plain dict payload directly -- no real network
layer to exercise.
"""
import asyncio
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrc.bridge.main import (  # noqa: E402
    BridgeApp, MRCProtocol, MRC_CHAT_PACKET_SPACING, MRC_MAX_MESSAGE_LEN,
    _dm_wrapper_prefix,
)


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
    app.announce_join_part = True
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


def _sent_messages(mock_mrc):
    return [MRCProtocol.parse_packet(c.args[0]) for c in mock_mrc.send_packet.call_args_list]


class WebSocketLongMessageSplitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 111
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        self.app.mrc.send_packet.reset_mock()
        self.handle = self.app._session_display_handle(
            self.app.db.get_session(str(self.ws_id)))

    def test_long_room_broadcast_is_split_not_truncated(self):
        sentences = ' '.join(f"This is sentence {i}." for i in range(1, 15))
        long_text = (sentences + ' ').ljust(280, 'E')[:280]
        self.assertEqual(len(long_text), 280)

        _run(self.app._handle_send_message(self.ws_id, {"message": long_text}))

        sent = _sent_messages(self.app.mrc)
        self.assertGreater(len(sent), 1, "a 280-char message must split into more than one packet")
        prefix = self.handle + " "
        for m in sent:
            # The display handle travels on top of the typed-text budget.
            self.assertTrue(m["message"].startswith(prefix))
            self.assertLessEqual(len(m["message"]) - len(prefix), MRC_MAX_MESSAGE_LEN)

        reassembled = re.sub(r'\(\d+/\d+\)\s*', '', ' '.join(m["message"] for m in sent))
        for word in long_text.split(' '):
            if word:
                self.assertIn(word, reassembled)

    def test_short_room_broadcast_is_unaffected(self):
        """The common case -- a message well under the cap -- must
        still go out as exactly one packet, same as before this fix."""
        _run(self.app._handle_send_message(self.ws_id, {"message": "hello there"}))
        sent = _sent_messages(self.app.mrc)
        self.assertEqual(len(sent), 1)
        self.assertIn("hello there", sent[0]["message"])

    def test_long_direct_message_is_split_not_truncated(self):
        long_text = ' '.join(f"word{i}" for i in range(1, 60))
        self.assertGreater(len(long_text), 140)

        _run(self.app._handle_direct_message(
            self.ws_id, {"to_user": "NightOwl", "message": long_text}))

        sent = [m for m in _sent_messages(self.app.mrc) if m["to_user"] == "NightOwl"]
        self.assertGreater(len(sent), 1)
        prefix = _dm_wrapper_prefix(self.handle)
        for m in sent:
            self.assertTrue(m["message"].startswith(prefix))
            self.assertLessEqual(len(m["message"]) - len(prefix), MRC_MAX_MESSAGE_LEN)
            self.assertEqual(m["to_room"], "")

        reassembled = re.sub(
            r'\(\d+/\d+\)\s*', '', ' '.join(m["message"] for m in sent))
        for word in long_text.split(' '):
            self.assertIn(word, reassembled)

    def test_long_action_is_split_not_truncated(self):
        action_text = ' '.join(f"dances{i}" for i in range(1, 40))
        self.assertGreater(len(action_text), 140)

        _run(self.app._handle_send_message(self.ws_id, {"message": f"* {action_text}"}))

        sent = _sent_messages(self.app.mrc)
        self.assertGreater(len(sent), 1)
        prefix = "|15* |13Alice "
        for m in sent:
            self.assertTrue(m["message"].startswith(prefix))
            self.assertTrue(m["message"].endswith("|07"))
            self.assertLessEqual(len(m["message"]) - len(prefix) - len("|07"),
                                 MRC_MAX_MESSAGE_LEN)


class WebSocketTypedTextBudgetTests(unittest.TestCase):
    """MRC_MAX_MESSAGE_LEN is the typed-text budget on the WebSocket
    path too, matching umrc-client (umrc-client/main.c,
    sendChatMessage()): the display handle, DM or /me wrapper, and the
    client's leading typing color travel on top of it, while every
    typed char -- color codes typed inside the text included -- counts.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 333
        self.app.websockets[self.ws_id] = _FakeWs()
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        self.app.mrc.send_packet.reset_mock()
        self.handle = self.app._session_display_handle(
            self.app.db.get_session(str(self.ws_id)))

    @staticmethod
    def _text(n):
        return ('abcd ' * 60)[:n - 1] + 'z'

    def test_full_budget_room_message_is_one_packet(self):
        text = self._text(MRC_MAX_MESSAGE_LEN)
        _run(self.app._handle_send_message(self.ws_id, {"message": text}))
        sent = _sent_messages(self.app.mrc)
        self.assertEqual([m["message"] for m in sent], [f"{self.handle} {text}"])

    def test_typing_color_rides_on_top_of_the_budget(self):
        text = self._text(MRC_MAX_MESSAGE_LEN)
        _run(self.app._handle_send_message(self.ws_id, {"message": "|14" + text}))
        sent = _sent_messages(self.app.mrc)
        self.assertEqual([m["message"] for m in sent], [f"{self.handle} |14{text}"])

    def test_over_budget_room_message_repeats_handle_and_color(self):
        text = self._text(MRC_MAX_MESSAGE_LEN + 1)
        _run(self.app._handle_send_message(self.ws_id, {"message": "|14" + text}))
        sent = _sent_messages(self.app.mrc)
        self.assertGreater(len(sent), 1)
        prefix = f"{self.handle} |14"
        for m in sent:
            self.assertTrue(m["message"].startswith(prefix))
            self.assertLessEqual(len(m["message"]) - len(prefix), MRC_MAX_MESSAGE_LEN)

    def test_color_codes_typed_inside_the_text_count(self):
        # 28 x "|12ab" = 140 typed chars, 84 of them color codes. Only
        # the client's single leading typing color ("|14") is free.
        text = "|12ab" * 28
        _run(self.app._handle_send_message(self.ws_id, {"message": "|14" + text}))
        self.assertEqual(len(_sent_messages(self.app.mrc)), 1)
        self.app.mrc.send_packet.reset_mock()
        _run(self.app._handle_send_message(self.ws_id, {"message": "|14" + text + "c"}))
        self.assertGreater(len(_sent_messages(self.app.mrc)), 1)

    def test_full_budget_direct_message_is_one_packet(self):
        text = self._text(MRC_MAX_MESSAGE_LEN)
        _run(self.app._handle_direct_message(
            self.ws_id, {"to_user": "Bob", "message": text}))
        sent = [m for m in _sent_messages(self.app.mrc) if m["to_user"] == "Bob"]
        self.assertEqual([m["message"] for m in sent],
                         [_dm_wrapper_prefix(self.handle) + text])

    def test_full_budget_action_is_one_packet(self):
        text = self._text(MRC_MAX_MESSAGE_LEN)
        _run(self.app._handle_send_message(self.ws_id, {"message": f"* {text}"}))
        sent = _sent_messages(self.app.mrc)
        self.assertEqual([m["message"] for m in sent], [f"|15* |13Alice {text}|07"])


class WebSocketChatPacingTests(unittest.TestCase):
    """ANetBBS's own web and terminal MRC clients go through these
    WebSocket handlers. The hub's flood control discards a chat line
    that arrives too soon after the same sender's previous one, so the
    chunks of a line the bridge splits itself -- and back-to-back
    /broadcast commands -- must leave at least MRC_CHAT_PACKET_SPACING
    apart, same as the native-TCP path (see
    test_mrc_bridge_tcp_listener.py's MrcTcpChatPacingTests)."""

    SLACK = 0.02  # timer granularity

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app = _make_bridge(self._tmp.name)
        self.ws_id = 222
        self.ws = _FakeWs()
        self.app.websockets[self.ws_id] = self.ws
        _run(self.app._handle_join_room(self.ws_id, {"handle": "Alice", "room": "lobby"}))
        self.app.mrc.send_packet.reset_mock()
        self.times = []

        async def _record(_pkt):
            self.times.append(time.monotonic())
        self.app.mrc.send_packet.side_effect = _record

    def _assert_spaced(self, min_packets=2):
        self.assertGreaterEqual(len(self.times), min_packets)
        for a, b in zip(self.times, self.times[1:]):
            self.assertGreaterEqual(b - a, MRC_CHAT_PACKET_SPACING - self.SLACK)

    def test_split_room_message_chunks_are_spaced(self):
        long_text = ' '.join(f"word{i}" for i in range(1, 60))
        _run(self.app._handle_send_message(self.ws_id, {"message": long_text}))
        self._assert_spaced()

    def test_split_direct_message_chunks_are_spaced(self):
        long_text = ' '.join(f"word{i}" for i in range(1, 60))
        _run(self.app._handle_direct_message(
            self.ws_id, {"to_user": "Bob", "message": long_text}))
        self._assert_spaced()

    def test_back_to_back_broadcast_commands_are_spaced(self):
        async def _two():
            await self.app._handle_server_cmd(self.ws_id, {"command": "BROADCAST part one"})
            await self.app._handle_server_cmd(self.ws_id, {"command": "BROADCAST part two"})
        _run(_two())
        sent = _sent_messages(self.app.mrc)
        self.assertEqual([m["message"] for m in sent],
                         ["BROADCAST part one", "BROADCAST part two"])
        self._assert_spaced()


if __name__ == '__main__':
    unittest.main()
