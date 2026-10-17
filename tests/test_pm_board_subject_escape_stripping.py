"""Regression test for a real High finding from a follow-up security
audit (2026-10-16): BBSMenuUI.list_pm_inbox() and the board thread
list (_list_threads_v2()) in anetbbs/features/bbs_ui.py rendered
PrivateMessage.subject / Post.subject into a lightbar row WITHOUT the
file's own _strip_untrusted() treatment -- unlike echomail subject/
from_name and InterBBS instant-message sender, which already get it
(see this file's module docstring + read_echo_area()/list_imsg_inbox()).

Both subjects are local-user-authored free text with no charset
restriction when composed via the web UI (web/pm.py and web/boards.py
only run word_filter.apply(), a profanity filter, not a control-
character sanitizer) -- so a subject typed via the web compose form
could carry raw CSI/OSC escape bytes straight to another user's real
terminal the moment they open their PM inbox or that board's thread
list (screen-clear/spoof, cursor tricks, OSC window-title injection).

Fixed by wrapping both fields with the file's existing
_strip_untrusted() helper at the point each row tuple is built --
same one-line treatment already used for the two network-peer-data
call sites in this file.

Drives the REAL list_pm_inbox()/_list_threads_v2() against a seeded DB
containing a message/post whose subject carries a raw ESC[2J (screen-
clear) sequence, and confirms the rendered lightbar output no longer
contains a raw ESC byte.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod

_EVIL_SUBJECT = 'Hi\x1b[2J\x1b[Hgotcha'


class _FakeSession:
    def __init__(self, user_id, keys=None):
        self.user = {'id': user_id, 'username': 'testuser',
                      'access_level': 100, 'is_admin': True}
        self.window_size = (80, 24)
        self.written = []
        self._keys = list(keys or ['Q'])

    async def write(self, text):
        self.written.append(text)

    async def read_key_arrow(self):
        return self._keys.pop(0) if self._keys else 'Q'

    async def read_line(self, prompt=''):
        if prompt:
            await self.write(prompt)
        return ''

    def transcript(self):
        return ''.join(self.written)


class PmSubjectEscapeStrippingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.pm_board_subject_escape_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User, PrivateMessage, Board, Post
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            recipient = User(username='escsubjtest', email='est@example.com',
                             password_hash='x')
            sender = User(username='escsubjsender', email='ess@example.com',
                         password_hash='x')
            db.session.add_all([recipient, sender])
            db.session.flush()
            db.session.add(PrivateMessage(
                sender_id=sender.id, recipient_id=recipient.id,
                subject=_EVIL_SUBJECT, body='body'))

            board = Board.query.filter_by(name='EscSubjTestBoard').first()
            if board is None:
                board = Board(name='EscSubjTestBoard', description='test')
                db.session.add(board)
                db.session.flush()
            db.session.add(Post(board_id=board.id, author_id=sender.id,
                                 subject=_EVIL_SUBJECT, content='body'))
            db.session.commit()
            cls.recipient_id = recipient.id
            cls.board_id = board.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _patched_app(self):
        return patch('anetbbs.features.bbs_ui._app', return_value=self.app)

    def test_pm_inbox_strips_escape_sequence_from_subject(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(self.recipient_id)
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.list_pm_inbox())
        out = session.transcript()
        self.assertNotIn('\x1b[2J\x1b[H', out[out.index('Subject'):],
                         'a raw screen-clear escape from a PM subject '
                         'must not reach the rendered lightbar row')
        self.assertIn('gotcha', out)

    def test_board_thread_list_strips_escape_sequence_from_subject(self):
        from anetbbs.features.bbs_ui import BBSMenuUI
        session = _FakeSession(self.recipient_id)
        ui = BBSMenuUI(session)
        with self._patched_app():
            asyncio.run(ui.list_threads(self.board_id, 'EscSubjTestBoard'))
        out = session.transcript()
        self.assertNotIn('\x1b[2J\x1b[H', out[out.index('Subject'):],
                         'a raw screen-clear escape from a Post subject '
                         'must not reach the rendered lightbar row')
        self.assertIn('gotcha', out)


if __name__ == '__main__':
    unittest.main()
