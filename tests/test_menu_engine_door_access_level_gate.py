"""Regression test for a real High finding from a follow-up security
audit (2026-10-16): menu_engine._act_door() (the 'door' menu action --
a hotkey pointed straight at a Game by id/slug) only checked
UserAccessFlags.no_games (global suspension) and Game.is_active before
launching -- it never checked Game.min_access_level against the user's
own access level.

Every OTHER door-launch path in this codebase DOES check it:
  - core/session.py's _launch_direct_door() (rlogin xtrn=)
  - features/games.py's show_door_menu() (the Game Center list, which
    filters min_access_level-restricted games out entirely)

So a door an admin set a high min_access_level on -- expecting it
hidden from the Game Center list, where it IS correctly filtered --
was still reachable by ANY logged-in user through a plain menu hotkey
(BbsMenuItem action_type='door', action_args=<id or slug>) pointed at
it directly, completely bypassing the access-level gate.

Fixed by adding the same (g.min_access_level or 0) > user_access check
_launch_direct_door() already uses, right after _act_door() resolves
the Game row.

Drives the REAL menu_engine._act_door() against a seeded DB with two
games: one gated at min_access_level=100 (should be denied to a
level-10 user) and one with the default min_access_level=0 (should
still launch normally for the same user -- confirms the fix doesn't
regress the common, unrestricted case).
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class _FakeSession:
    """Minimal stand-in for BBSSession -- _act_door only touches
    .session.user and .session.write() (plus optionally
    ._heartbeat_node(), which it already guards with hasattr())."""
    def __init__(self, user):
        self.user = user
        self.written = []

    async def write(self, text):
        self.written.append(text)


class _FakeUI:
    def __init__(self, user):
        self.session = _FakeSession(user)


class MenuEngineDoorAccessLevelGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent /
                           '.menu_door_access_level_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, Game
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            restricted = Game.query.filter_by(slug='audit-restricted-door').first()
            if restricted is None:
                restricted = Game(name='Restricted Door', slug='audit-restricted-door',
                                   game_type='door_native', min_access_level=100,
                                   is_active=True)
                db.session.add(restricted)
            open_door = Game.query.filter_by(slug='audit-open-door').first()
            if open_door is None:
                open_door = Game(name='Open Door', slug='audit-open-door',
                                  game_type='door_native', min_access_level=0,
                                  is_active=True)
                db.session.add(open_door)
            db.session.commit()
            cls.restricted_slug = restricted.slug
            cls.open_slug = open_door.slug

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_low_access_user_denied_a_restricted_door(self):
        from anetbbs.features import menu_engine
        with self.app.app_context():
            ui = _FakeUI({'id': 1, 'access_level': 10, 'is_admin': False})
            with mock.patch.object(menu_engine, '_app', return_value=self.app), \
                 mock.patch('anetbbs.games.door_runner.play_door_game_telnet',
                            new=mock.AsyncMock()) as launch:
                asyncio.run(menu_engine._act_door(ui, self.restricted_slug))

        launch.assert_not_called()
        out = ''.join(ui.session.written)
        self.assertIn("don't have access", out)

    def test_low_access_user_can_still_launch_an_unrestricted_door(self):
        """Sanity check: the fix must not regress the common case --
        a door with the default min_access_level=0/None launches fine
        for a low-access-level user, exactly as before."""
        from anetbbs.features import menu_engine
        with self.app.app_context():
            ui = _FakeUI({'id': 1, 'access_level': 10, 'is_admin': False})
            with mock.patch.object(menu_engine, '_app', return_value=self.app), \
                 mock.patch('anetbbs.games.door_runner.play_door_game_telnet',
                            new=mock.AsyncMock()) as launch:
                asyncio.run(menu_engine._act_door(ui, self.open_slug))

        launch.assert_called_once()
        out = ''.join(ui.session.written)
        self.assertNotIn("don't have access", out)


if __name__ == '__main__':
    unittest.main()
