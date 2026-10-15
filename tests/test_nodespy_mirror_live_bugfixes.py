"""Regression tests for two real live bugs in the NodeSpy live screen
mirror (v1.1.39), reported directly by the sysop testing it on the
real BBS:

1. "It stays on the screen previously viewed and not the current
   screen, no matter how much time goes by." Root cause: a screen is
   normally drawn as a tight burst of several write() calls (clear,
   banner, content lines, prompt) with essentially no real delay
   between them -- all well under _spy_tee()'s persist throttle
   window. Only the first call in a burst that happens to clear the
   throttle actually persists; later calls in the SAME burst (often
   the most complete, final content) get throttled away. If the next
   screen's burst ALSO starts soon enough after that lone persist,
   its calls can ALSO all land inside the throttle window -- nothing
   from that whole screen reaches the database, and the session then
   blocks waiting for input with nothing left to trigger a re-check.
   Fixed by _spy_flush(): forces an immediate, unthrottled persist of
   the CURRENT shadow screen state, called right before
   read_key()/read_key_arrow()/read_line() block waiting for input --
   the one moment a screen has actually settled.

2. "The alignment needs work" -- confirmed as a real CP437-vs-latin-1
   decode bug: _show_ansi_screen()'s non-enhanced _send_part() and
   _play_screen_animation() feed sysop-authored ANSI-art content
   (box-drawing/block characters, stored as latin-1 mojibake of the
   original CP437 bytes -- see _show_ansi_screen's own docstring) into
   _spy_tee() by re-decoding with latin-1 (a no-op round-trip back to
   the SAME mojibake), instead of cp437 (which recovers the real
   Unicode box-drawing/block glyphs a browser's monospace font needs
   -- exactly what the 'enhanced' branch right next to it already does
   correctly). Latin-1 mojibake renders as the wrong, non-box-drawing
   characters in the web mirror.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeWriter:
    def __init__(self):
        self.written = bytearray()
        self._closing = False

    def get_extra_info(self, key):
        return ('1.2.3.4', 1234) if key == 'peername' else None

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        pass


class _InstantReader:
    """Yields nothing -- any blocking read on this raises CarrierLost,
    which is fine for these tests: we only care what happened BEFORE
    the blocking read was attempted."""
    async def read(self, n=1):
        return b''


class _DbBackedNodeSpyTestCase(unittest.TestCase):
    """Shared setup: a real NodeActivity row + Flask app/db context, so
    _spy_tee()/_spy_flush()'s _heartbeat_node() calls actually persist,
    matching tests/test_session_nodespy_screen_mirror.py's pattern."""

    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodespy_live_bugfixes_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def _make_session_with_node_activity(self, slot=1, **kwargs):
        from anetbbs.core.session import BBSSession
        from anetbbs.models import db, NodeActivity
        row = NodeActivity(slot=slot, user_id=None, username='spytest',
                            protocol='telnet', peer='1.2.3.4')
        db.session.add(row)
        db.session.commit()
        writer = _FakeWriter()
        reader = _InstantReader()
        session = BBSSession(
            reader, writer, {'server': {'host': '127.0.0.1', 'port': 0}}, **kwargs)
        session._node_activity_id = row.id
        return session, row.id


class SpyFlushBeforeBlockingReadTests(_DbBackedNodeSpyTestCase):
    def test_read_key_flushes_pending_content_bypassing_throttle(self):
        """The exact live bug shape: two screens drawn back-to-back
        fast enough that the second screen's own write() calls all
        land inside the persist throttle window. Without the flush,
        last_screen would stay frozen on the FIRST screen forever once
        the session blocks on read_key() waiting for a keypress."""
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi')

        asyncio.run(session.write('First Screen\r\n'))
        row = NodeActivity.query.get(row_id)
        self.assertIn('First Screen', row.last_screen)

        # Simulate the second screen's burst landing entirely inside
        # the throttle window (real-world: a handful of write() calls
        # fired with no delay between them).
        asyncio.run(session.write('\x1b[2JSecond Screen\r\n'))
        row = NodeActivity.query.get(row_id)
        self.assertNotIn('Second Screen', row.last_screen,
                         'throttle should have suppressed this one -- '
                         'test setup assumption broken')

        # read_key() blocks waiting for input next -- this is the
        # moment the screen has "settled" and must flush regardless.
        with self.assertRaises(Exception):  # CarrierLost from the empty reader
            asyncio.run(session.read_key(''))

        from anetbbs.models import db
        db.session.expire_all()  # this test's own cached row, not a fix bug
        row = NodeActivity.query.get(row_id)
        self.assertIn('Second Screen', row.last_screen)
        self.assertNotIn('First Screen', row.last_screen)

    def test_read_line_also_flushes_before_blocking(self):
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi')

        asyncio.run(session.write('Username: '))
        with self.assertRaises(Exception):
            asyncio.run(session.read_line(''))

        row = NodeActivity.query.get(row_id)
        self.assertIn('Username:', row.last_screen)

    def test_flush_is_a_noop_with_no_prior_write(self):
        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi')
        session._spy_flush()  # must not raise with no shadow state yet


class ShowAnsiScreenCp437DecodeTests(_DbBackedNodeSpyTestCase):
    """CP437 double-line box-drawing byte 0xC9 (top-left corner, ╔ in
    real CP437) stored the way _show_ansi_screen expects -- latin-1
    mojibake of the raw CP437 byte, i.e. Python chr(0xC9) = 'É'."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data_dir = Path(self._tmp.name)
        (Path(self.data_dir) / 'mods' / 'text').mkdir(parents=True)
        (Path(self.data_dir) / 'text').mkdir(parents=True)

        # Unlike test_ansi_mods_text_override.py's bare-config fake app
        # (fine there -- it only checks writer.written bytes), this
        # test also needs _heartbeat_node()'s OWN separate _app() call
        # to get a REAL Flask app with a real app_context() for the DB
        # write to actually happen -- so patch _app() to return the
        # real test app (with DATA_DIR pointed at the tmpdir) instead.
        self._orig_data_dir = self.app.config.get('DATA_DIR')
        self.app.config['DATA_DIR'] = str(self.data_dir)
        self.addCleanup(lambda: self.app.config.__setitem__('DATA_DIR', self._orig_data_dir))
        self._patcher = patch('anetbbs.features.bbs_ui._app', return_value=self.app)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_box_drawing_byte_is_cp437_decoded_not_latin1_mojibake(self):
        from anetbbs.models import NodeActivity
        # chr(0xC9) is the latin-1-mojibake stand-in for raw CP437 byte
        # 0xC9 -- a double-line box top-left corner (╔, U+2554) on a
        # real CP437 terminal, but the Latin-1 letter 'É' if mis-decoded.
        art = '\x1b[1;36m' + chr(0xC9) + chr(0xCD) * 3 + chr(0xBB) + '\x1b[0m\r\n'
        (self.data_dir / 'text' / 'boxtest.ans').write_text(art, encoding='latin-1')

        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi', forced_width=80)
        asyncio.run(session._show_ansi_screen('boxtest'))

        row = NodeActivity.query.get(row_id)
        self.assertIsNotNone(row.last_screen)
        self.assertIn('╔', row.last_screen,
                      'expected real CP437 box-drawing glyph U+2554 (╔)')
        self.assertNotIn('É', row.last_screen,
                         'latin-1 mojibake glyph (Ã‰/É) leaked into the mirror')

    def test_plain_ascii_screen_content_unaffected(self):
        from anetbbs.models import NodeActivity
        (self.data_dir / 'text' / 'plaintest.ans').write_text(
            '\x1b[1;32mHello World\x1b[0m\r\n', encoding='latin-1')

        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi', forced_width=80)
        asyncio.run(session._show_ansi_screen('plaintest'))

        row = NodeActivity.query.get(row_id)
        self.assertIn('Hello World', row.last_screen)


if __name__ == '__main__':
    unittest.main()
