"""Regression tests for the admin NodeSpy live screen mirror.

Context: NodeActivity.last_screen already existed as a column (see
docs/29-watch-live.md) but nothing in the codebase ever populated it --
every _heartbeat_node() call site passed page=/action= only, never
screen=. The PUBLIC /watch page deliberately never reads this column
(no privacy allowlist exists for showing an anonymous stranger a
password prompt or a PM compose screen -- see web/watch.py's own
docstring), so it was left wired up but dead rather than filled in
unsafely.

This closes that gap for the ADMIN-ONLY NodeSpy view specifically (a
different trust boundary -- already gated by _require_admin(), the
sysop's own screen-spy tool, same as real DOS-era BBS node spy):
BBSSession._spy_tee() feeds visible screen text into a dedicated
shadow VT state (ansi_html._run_vt()) and persists a throttled,
rendered-HTML snapshot via _heartbeat_node(screen=...).
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

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
    async def read(self, n=1):
        return b''


class NodeSpyScreenMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodespy_screen_mirror_test.db')
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

    def _make_session_with_node_activity(self, slot=1, forced_term_mode=None):
        from anetbbs.core.session import BBSSession
        from anetbbs.models import db, NodeActivity
        row = NodeActivity(slot=slot, user_id=None, username='spytest',
                            protocol='telnet', peer='1.2.3.4')
        db.session.add(row)
        db.session.commit()
        writer = _FakeWriter()
        reader = _InstantReader()
        session = BBSSession(
            reader, writer, {'server': {'host': '127.0.0.1', 'port': 0}},
            forced_term_mode=forced_term_mode)
        session._node_activity_id = row.id
        return session, row.id

    def test_spy_tee_persists_rendered_html_to_last_screen(self):
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity()

        session._spy_tee('Hello \x1b[31mworld\x1b[0m')

        row = NodeActivity.query.get(row_id)
        self.assertIsNotNone(row.last_screen)
        self.assertIn('Hello', row.last_screen)
        self.assertIn('world', row.last_screen)
        self.assertIn('color:', row.last_screen)

    def test_spy_tee_is_throttled_on_rapid_successive_calls(self):
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity()

        session._spy_tee('first screen')
        row = NodeActivity.query.get(row_id)
        first_html = row.last_screen
        self.assertIn('first', first_html)

        # Immediately after -- within the throttle window -- a second,
        # DIFFERENT screen must NOT overwrite the persisted snapshot yet.
        session._spy_tee('second screen entirely different')
        row = NodeActivity.query.get(row_id)
        self.assertEqual(row.last_screen, first_html)
        self.assertNotIn('second', row.last_screen)

    def test_spy_tee_persists_again_after_throttle_window_elapses(self):
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity()

        session._spy_tee('first screen')
        session._spy_last_persist = None  # simulate throttle window elapsed
        session._spy_tee('second screen')

        row = NodeActivity.query.get(row_id)
        self.assertIn('second', row.last_screen)

    def test_spy_tee_continues_cursor_state_across_calls(self):
        """Two separate calls that each move the cursor up and
        reprint (the exact shape _play_screen_animation() uses) must
        compose onto the SAME shadow screen, not reset to row 0 each
        time -- otherwise the mirror would show only the last frame's
        single line instead of the whole redrawn block."""
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity()

        session._spy_tee('line one\r\nline two\r\n')
        session._spy_last_persist = None
        session._spy_tee('\x1b[2Aline one updated\r\nline two\r\n')

        row = NodeActivity.query.get(row_id)
        self.assertIn('line one updated', row.last_screen)
        self.assertIn('line two', row.last_screen)

    def test_spy_tee_noop_without_a_live_node_activity_row(self):
        """Pre-login / no node slot yet -- must be a silent no-op, not
        an exception, matching _heartbeat_node()'s own early-return."""
        from anetbbs.core.session import BBSSession
        writer = _FakeWriter()
        reader = _InstantReader()
        session = BBSSession(
            reader, writer, {'server': {'host': '127.0.0.1', 'port': 0}})
        session._spy_tee('some text')  # must not raise

    def test_empty_text_does_not_touch_last_screen(self):
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity()
        session._spy_tee('')
        row = NodeActivity.query.get(row_id)
        self.assertIsNone(row.last_screen)

    def test_write_tees_into_the_screen_mirror(self):
        """write() itself -- the normal non-enhanced path -- must feed
        _spy_tee() so ordinary menu/text output (not just the raw-
        writer.write() bypasses) shows up in the mirror."""
        from anetbbs.models import NodeActivity
        session, row_id = self._make_session_with_node_activity(
            forced_term_mode='ansi')

        asyncio.run(session.write('Main Menu\r\n'))

        row = NodeActivity.query.get(row_id)
        self.assertIsNotNone(row.last_screen)
        self.assertIn('Main Menu', row.last_screen)


if __name__ == '__main__':
    unittest.main()
