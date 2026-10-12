"""Regression tests for a real MRC protocol-compliance audit against the
published MRC protocol documentation (and cross-checked against a real
reference client implementation, not named here -- see RELEASE.md).

Two live-reported bugs plus what auditing them against the spec turned
up:

1. "Loses its color once it hits the second line, then tries the 1 of 2
   continuation" -- a long room-chat message that needs to be split
   into multiple wire chunks (anetbbs/features/mrc_chat.py's
   _split_for_wire) only had the sender's active color pipe-code at the
   very START of the original, pre-split string. Each chunk is sent as
   its own fully independent send_message -- a separate MRC message on
   the wire, not a client-side word-wrap of one received message -- so
   only the FIRST chunk ever carried the color; every later chunk
   arrived with none and rendered in whatever default/leftover color
   happened to be active on each recipient's own client.

2. "Not being able to type/send the full 140 character limit, it's
   less than that" -- /me and /broadcast budgeted their outgoing text
   against the bare 140-char hub limit with ZERO reservation for the
   wrapper the bridge adds before transmission (a fixed-color "* Nick"
   wrapper for actions, a literal "BROADCAST " prefix for broadcasts).
   Anything within that wrapper's length of the 140 limit had its tail
   silently cut off server-side with no warning -- the exact class of
   bug _chat_wire_cap()/_dm_wire_cap() already existed to prevent for
   plain chat and DMs, just never extended to these two commands.

3. USERNICK: parsing bug found auditing the wire format directly
   against the spec: the real packet carries exactly ONE nick value
   (Request: SERVER~~~CLIENT~~~USERNICK:nick~), not an "old new" pair.
   raw.split(None, 1) on a single token always produced parts=[nick]
   (nothing to split on), so the code's "new_nick" was permanently
   empty and "old_nick" (actually just the one real value) was
   unconditionally discarded from the local roster every single time,
   never re-added -- a slow leak silently shrinking tab-completion/
   mention coverage.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat, _split_for_wire, MAX_OUTGOING_CHARS


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _make_chat(handle='StingRay'):
    chat = MRCChat(_FakeSession())
    chat._split_screen = False
    chat._handle = handle
    chat.sent = []

    async def _fake_send_json(obj):
        chat.sent.append(obj)
    chat._send_json = _fake_send_json
    return chat


def _run(coro):
    return asyncio.run(coro)


class SplitForWireColorCarryoverTests(unittest.TestCase):
    """Direct unit tests of the fixed helper itself."""

    def test_no_prefix_behavior_is_unchanged(self):
        chunks = _split_for_wire('word ' * 60, cap=40)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertFalse(c.startswith('|'))

    def test_single_chunk_gets_prefix_once(self):
        chunks = _split_for_wire('short message', cap=140, repeat_prefix='|08')
        self.assertEqual(chunks, ['|08short message'])

    def test_every_multichunk_piece_carries_the_prefix(self):
        chunks = _split_for_wire('word ' * 60, cap=40, repeat_prefix='|12')
        self.assertGreater(len(chunks), 1,
                          'test setup must actually force a split')
        for c in chunks:
            self.assertTrue(c.startswith('|12'),
                           f'chunk missing color prefix: {c!r}')

    def test_chunks_still_respect_the_cap_including_the_prefix(self):
        chunks = _split_for_wire('word ' * 60, cap=40, repeat_prefix='|12')
        for c in chunks:
            self.assertLessEqual(len(c), 40)

    def test_continuation_tag_still_present_alongside_prefix(self):
        chunks = _split_for_wire('word ' * 60, cap=40, repeat_prefix='|12')
        t = len(chunks)
        for i, c in enumerate(chunks):
            self.assertIn(f'({i+1}/{t})', c)


class RoomChatSplitColorCarryoverTests(unittest.TestCase):
    """End-to-end through the real _chat_loop() call site."""

    def test_every_chunk_of_a_split_room_message_keeps_the_color(self):
        chat = _make_chat()
        chat._connected = True
        chat._color_idx = 2  # some non-default color
        color = chat._current_color_pipe()
        # 300 typed chars -- well past the 140-char typed-text budget,
        # so the line must split.
        long_line = 'word ' * 60

        lines = iter([long_line, None])

        async def fake_read_chat_line():
            return next(lines)
        chat._read_chat_line = fake_read_chat_line

        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()):
            _run(chat._chat_loop())

        self.assertGreater(len(chat.sent), 1,
                          'test setup must actually force a multi-chunk send')
        for i, sent in enumerate(chat.sent):
            self.assertTrue(sent['message'].startswith(color),
                           f'chunk {i} lost its color: {sent["message"]!r}')

    def test_single_chunk_room_message_unaffected(self):
        chat = _make_chat()
        chat._connected = True
        chat._color_idx = 0
        color = chat._current_color_pipe()
        lines = iter(['just a short message', None])

        async def fake_read_chat_line():
            return next(lines)
        chat._read_chat_line = fake_read_chat_line

        _run(chat._chat_loop())

        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['message'],
                         color + 'just a short message')


class ActionAndBroadcastOverheadTests(unittest.TestCase):
    def test_me_action_gets_full_typed_budget_regardless_of_overhead(self):
        # The bridge carries its "|15* |13{nick} ...|07" wrapper on top
        # of the 140-char typed-text budget, so a 140-char action goes
        # out as one chunk however large the reported overhead is.
        chat = _make_chat()
        chat._connected = True
        chat._action_overhead = 100
        action = ('word ' * 28)[:139] + 'x'

        _run(chat._handle_slash(f'/me {action}'))

        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['message'], '* ' + action)

    def test_long_me_action_splits_at_typed_budget(self):
        chat = _make_chat()
        chat._connected = True
        long_action = 'word ' * 60

        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()):
            _run(chat._handle_slash(f'/me {long_action}'))

        self.assertGreater(len(chat.sent), 1)
        for sent in chat.sent:
            # The client adds "* " on top of each typed chunk; the bridge
            # swaps it for its own wrapper.
            self.assertTrue(sent['message'].startswith('* '))
            self.assertLessEqual(len(sent['message']) - 2, MAX_OUTGOING_CHARS)

    def test_me_action_with_zero_overhead_behaves_like_before(self):
        chat = _make_chat()
        chat._connected = True
        chat._action_overhead = 0
        _run(chat._handle_slash('/me waves hello'))
        self.assertEqual(len(chat.sent), 1)
        self.assertEqual(chat.sent[0]['message'], '* waves hello')

    def test_broadcast_respects_broadcast_prefix_overhead(self):
        chat = _make_chat()
        chat._connected = True
        long_text = 'word ' * 60

        with patch('anetbbs.features.mrc_chat.asyncio.sleep', new=AsyncMock()):
            _run(chat._handle_slash(f'/broadcast {long_text}'))

        broadcasts = [s for s in chat.sent if s.get('type') == 'server_cmd']
        self.assertGreater(len(broadcasts), 0)
        for s in broadcasts:
            self.assertLessEqual(len(s['command']), MAX_OUTGOING_CHARS,
                                'BROADCAST + chunk must never exceed the '
                                'real wire limit once the literal '
                                '"BROADCAST " prefix is included')
            self.assertTrue(s['command'].startswith('BROADCAST '))


class StatusBarActionCounterTests(unittest.TestCase):
    def test_me_counter_counts_typed_text_only(self):
        # The counter measures the typed action text against 140 (the
        # bridge's action wrapper isn't charged against it). Past 140,
        # it counts down what's left of the 280-char line and shows the
        # 280 in red -- see _buffer_countdown.
        chat = _make_chat()
        chat._split_screen = True
        chat._term_columns = 100
        chat._action_overhead = 50
        chat._input_buf = list('/me ' + ('x' * 145))  # 149 typed in all

        _run(chat._draw_status_line())

        written = ''.join(chat.session.written)
        self.assertIn('131\x1b[0m/\x1b[1;91m280\x1b[0m', written,
                      f'expected 131/280 with a red 280, got: {written!r}')


class UsernickParsingTests(unittest.TestCase):
    def test_single_value_usernick_is_added_not_discarded(self):
        chat = _make_chat()
        chat._known_users = set()
        _run(chat._handle_event({'type': 'mrc_message',
                                 'body': 'USERNICK:StingRay2'}))
        self.assertIn('StingRay2', chat._known_users,
                      'the announced nick must end up in the known-users '
                      'roster, not be discarded')

    def test_usernick_never_erroneously_discards_an_existing_entry(self):
        chat = _make_chat()
        chat._known_users = {'SomeOtherUser'}
        _run(chat._handle_event({'type': 'mrc_message',
                                 'body': 'USERNICK:StingRay2'}))
        self.assertIn('SomeOtherUser', chat._known_users,
                      'processing an unrelated USERNICK must not remove '
                      'other known users from the roster')

    def test_usernick_with_domain_suffix_strips_it(self):
        chat = _make_chat()
        chat._known_users = set()
        _run(chat._handle_event({'type': 'mrc_message',
                                 'body': 'USERNICK:StingRay2@somebbs'}))
        self.assertIn('StingRay2', chat._known_users)


if __name__ == '__main__':
    unittest.main()
