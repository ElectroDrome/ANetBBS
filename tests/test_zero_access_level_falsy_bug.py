"""Regression test for https://github.com/anetonline/ANetBBS/issues/30 --
reported by ElectroDrome: setting a Board's Min Read Level to 0 (public/
everyone) in the Web Admin UI silently reset it to 10 (registered) instead.

Root cause confirmed in admin.py's add_board()/edit_board(): both used
`form.min_access_level.data or 10` to fill in a default when the field is
blank. Python's `or` treats 0 as falsy, so a deliberately-entered 0 was
indistinguishable from "field left blank" and always fell through to 10.
The correct idiom (`x if x is not None else default`) was already present
two lines away in edit_board()'s GET branch -- only the save path had the
bug.

A sweep for the same shape across the rest of the admin/gating code found
three more sites with the identical bug (file_areas_admin()'s update
action, and manage_user()'s access_level save) plus two READ-side sites
(echomail.py's _accessible_areas_query()/compose(), games.py's
_user_access()/lobby()) that compute `getattr(current_user,
'access_level', 10) or 10` -- if a sysop ever sets a real user's own
access_level to a deliberate 0 (a valid, documented value: the field's
own model comment says "Mystic/Synchronet-style numeric access level
(0-255)"), those two read sites silently promoted that user to level 10
for every area/game-visibility check, a privilege increase rather than
the admin-form annoyance the other four sites have.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class ZeroAccessLevelFalsyBugTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.zero_access_level_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _admin_client(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            admin = User.query.filter_by(username='zeroacllevadmin').first()
            if not admin:
                admin = User(username='zeroacllevadmin', is_admin=True,
                            access_level=255,
                            email='zeroacllevadmin@example.com')
                admin.set_password('x')
                db.session.add(admin)
                db.session.commit()
            admin_id = admin.id
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(admin_id)
            sess['_fresh'] = True
        return client

    def _ensure_user(self, username, access_level):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, is_admin=False,
                         access_level=access_level,
                         email=f'{username}@example.com')
                u.set_password('x')
                db.session.add(u)
            else:
                u.access_level = access_level
            db.session.commit()
            return u.id

    # --- save-path sites (admin forms) ---

    def test_add_board_with_access_level_zero_is_saved_as_public(self):
        from anetbbs.models import Board
        client = self._admin_client()
        resp = client.post('/admin/boards/add', data={
            'name': 'ZeroLevelBoardA', 'description': '', 'category': '',
            'order': '1', 'min_access_level': '0', 'min_write_level': '',
            'is_active': 'y', 'submit': 'Save Board',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            board = Board.query.filter_by(name='ZeroLevelBoardA').first()
            self.assertIsNotNone(board)
            self.assertEqual(board.min_access_level, 0,
                             'a deliberately-entered 0 (public access) must not '
                             'silently fall back to 10 (registered)')

    def test_edit_board_with_access_level_zero_is_saved_as_public(self):
        from anetbbs.models import db, Board
        with self.app.app_context():
            board = Board(name='ZeroLevelBoardB', min_access_level=10, order=1)
            db.session.add(board)
            db.session.commit()
            board_id = board.id

        client = self._admin_client()
        resp = client.post(f'/admin/boards/{board_id}/edit', data={
            'name': 'ZeroLevelBoardB', 'description': '', 'category': '',
            'order': '1', 'min_access_level': '0', 'min_write_level': '',
            'is_active': 'y', 'submit': 'Save Board',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            refreshed = Board.query.get(board_id)
            self.assertEqual(refreshed.min_access_level, 0)

    def test_file_area_update_with_access_level_zero_is_saved_as_public(self):
        from anetbbs.models import db, FileArea
        with self.app.app_context():
            fa = FileArea(tag='ZEROLVL', name='Zero Level Area',
                          min_access_level=10, is_active=True, is_subscribed=True)
            db.session.add(fa)
            db.session.commit()
            area_id = fa.id

        client = self._admin_client()
        resp = client.post('/admin/file-areas', data={
            'action': 'update', 'area_id': str(area_id),
            'name': 'Zero Level Area', 'storage_path': '',
            'description': '', 'upload_permission': 'admin_only',
            'min_access_level': '0', 'min_write_level': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            refreshed = FileArea.query.get(area_id)
            self.assertEqual(refreshed.min_access_level, 0)

    def test_manage_user_access_level_zero_is_saved(self):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = User(username='zeroacltarget', access_level=10,
                     email='zeroacltarget@example.com')
            u.set_password('x')
            db.session.add(u)
            db.session.commit()
            uid = u.id

        client = self._admin_client()
        resp = client.post(f'/admin/users/{uid}/manage', data={
            'action': 'save', 'email': 'zeroacltarget@example.com',
            'display_name': '', 'access_level': '0',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            refreshed = User.query.get(uid)
            self.assertEqual(refreshed.access_level, 0)

    # --- read-path sites (gating against a real access_level of 0) ---

    def test_echomail_accessible_areas_does_not_promote_a_real_zero_level(self):
        from anetbbs.models import db, EchomailNetwork, EchoArea
        from anetbbs.web.echomail import _accessible_areas_query

        with self.app.app_context():
            net = EchomailNetwork(name='ZeroLevelNet', network_type='binkp',
                                  our_address='1:1/1', hub_address='1:1/1',
                                  is_active=True)
            db.session.add(net)
            db.session.commit()
            public_area = EchoArea(network_id=net.id, tag='PUBZERO', name='Public',
                                   min_access_level=0, is_active=True, is_subscribed=True)
            gated_area = EchoArea(network_id=net.id, tag='GATED5', name='Gated',
                                  min_access_level=5, is_active=True, is_subscribed=True)
            db.session.add_all([public_area, gated_area])
            db.session.commit()
            net_id = net.id

        self._ensure_user('zeroacluser', access_level=0)
        with self.app.test_request_context():
            from flask_login import login_user
            from anetbbs.models import User
            user = User.query.filter_by(username='zeroacluser').first()
            login_user(user)
            tags = [a.tag for a in _accessible_areas_query(net_id).all()]
        self.assertIn('PUBZERO', tags)
        self.assertNotIn('GATED5', tags,
                         'a user whose real access_level is 0 must not be '
                         'silently promoted to level 10 and see areas gated '
                         'above their actual level')

    def test_games_lobby_does_not_promote_a_real_zero_level(self):
        from anetbbs.models import db, Game, User
        from flask_login import login_user

        self._ensure_user('zeroacluser', access_level=0)
        with self.app.app_context():
            open_game = Game.query.filter_by(slug='zerolvl-open-game').first()
            if not open_game:
                open_game = Game(name='Zero Level Open', slug='zerolvl-open-game',
                                 game_type='builtin_web', min_access_level=0,
                                 is_active=True, web_enabled=True)
                db.session.add(open_game)
            gated_game = Game.query.filter_by(slug='zerolvl-gated-game').first()
            if not gated_game:
                gated_game = Game(name='Zero Level Gated', slug='zerolvl-gated-game',
                                  game_type='builtin_web', min_access_level=5,
                                  is_active=True, web_enabled=True)
                db.session.add(gated_game)
            db.session.commit()

        with self.app.test_request_context():
            user = User.query.filter_by(username='zeroacluser').first()
            login_user(user)
            from anetbbs.web.games import _user_access
            self.assertEqual(_user_access(), 0,
                             "a real access_level of 0 must be returned as 0, "
                             "not silently promoted to 10")
