"""Every ANetBBS MRC client limits an input line to 280 chars, matching
umrc-client's input limit: the terminal client (all three input loops)
via MAX_CHAT_LINE_LEN, and both web pages via the message box's
maxlength plus a check in doSend() for text placed in the box another
way. A full line splits into at most three wire chunks.
"""
import asyncio
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401,E402  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import (  # noqa: E402
    MRCChat, MAX_CHAT_LINE_LEN, MAX_OUTGOING_CHARS, _split_for_wire,
)
from anetbbs.features.mrc_chat_ascii import AsciiMRCChat  # noqa: E402
from anetbbs.features.mrc_chat_petscii import PetsciiMRCChat  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
_OVER = b'B' * (MAX_CHAT_LINE_LEN + 20) + b'\r'


class _QueuedReader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, n=1):
        if self._pos >= len(self._data):
            return b''
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeSession:
    def __init__(self, reader_bytes=b''):
        self.user = {'username': 'tester'}
        self.written = []
        self.reader = _QueuedReader(reader_bytes)
        self.petscii_width = 40

    async def write(self, text):
        self.written.append(text)


def _run(coro):
    return asyncio.run(coro)


class TerminalLineLimitTests(unittest.TestCase):
    def test_limit_matches_umrc_input_limit(self):
        self.assertEqual(MAX_CHAT_LINE_LEN, 280)

    def test_base_client_stops_at_280(self):
        chat = MRCChat(_FakeSession(_OVER))
        chat._split_screen = False
        chat._handle = 'Alice'
        line = _run(chat._read_chat_line())
        self.assertEqual(line, 'B' * MAX_CHAT_LINE_LEN)

    def test_ascii_client_stops_at_280_and_stops_echoing(self):
        sess = _FakeSession(_OVER)
        chat = AsciiMRCChat(sess)
        chat._handle = 'Alice'
        line = _run(chat._read_chat_line())
        self.assertEqual(line, 'B' * MAX_CHAT_LINE_LEN)
        self.assertEqual(sum(1 for w in sess.written if w == 'B'), MAX_CHAT_LINE_LEN)

    def test_petscii_client_stops_at_280(self):
        chat = PetsciiMRCChat(_FakeSession(_OVER))
        chat._handle = 'Alice'
        line = _run(chat._read_chat_line())
        self.assertEqual(len(line), MAX_CHAT_LINE_LEN)

    def test_tab_complete_cannot_push_past_280(self):
        chat = MRCChat(_FakeSession())
        chat._split_screen = False
        chat._handle = 'Alice'
        chat._known_users = {'NightOwlNightOwl'}
        chat._input_buf = list('x' * (MAX_CHAT_LINE_LEN - 3) + ' Ni')
        chat._input_buf = chat._input_buf[-MAX_CHAT_LINE_LEN:]
        _run(chat._tab_complete())
        self.assertLessEqual(len(chat._input_buf), MAX_CHAT_LINE_LEN)

    def test_full_line_splits_into_at_most_three_chunks(self):
        line = ' '.join(['word'] * 60)[:MAX_CHAT_LINE_LEN]
        chunks = _split_for_wire(line, cap=MAX_OUTGOING_CHARS + 3, repeat_prefix='|14')
        self.assertLessEqual(len(chunks), 3)


class WebLineLimitTests(unittest.TestCase):
    def test_both_web_pages_limit_the_message_box_and_check_on_send(self):
        for rel in ('mrc/web/index.html', 'anetbbs/templates/mrc/index.html'):
            html = (REPO / rel).read_text(encoding='utf-8')
            self.assertIn('id="message-input" maxlength="280"', html, rel)
            self.assertIn('const MRC_MAX_LINE_LEN = 280;', html, rel)
            send = html[html.index('function doSend()'):]
            send = send[:send.index('parseUserInput(rawLine)')]
            self.assertTrue(re.search(r'rawLine\.length > MRC_MAX_LINE_LEN', send), rel)


if __name__ == '__main__':
    unittest.main()
