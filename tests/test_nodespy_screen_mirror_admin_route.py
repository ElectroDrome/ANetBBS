"""Regression tests for the admin-only NodeSpy screen.json poll route
(anetbbs/web/control.py's nodespy_screen_json()) -- the web side of the
live screen mirror BBSSession._spy_tee() populates. Confirms: a live
node with a persisted screen returns it, a live node with no screen
yet (or a stale/gone node) returns ok:false rather than erroring, and
the route is admin-gated like every other NodeSpy route.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class NodeSpyScreenMirrorAdminRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.nodespy_screen_route_test.db')
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

    _counter = 0

    def _make_user(self, is_admin=False):
        from anetbbs.models import User
        NodeSpyScreenMirrorAdminRouteTests._counter += 1
        n = NodeSpyScreenMirrorAdminRouteTests._counter
        with self.app.app_context():
            u = User(username=f'spyroute{n}', email=f'spyroute{n}@example.com',
                     is_active=True, is_admin=is_admin)
            u.set_password('x')
            from anetbbs.models import db as _db
            _db.session.add(u)
            _db.session.commit()
            return u.id

    def _logged_in_client(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _make_node(self, slot, last_screen=None, stale=False):
        from anetbbs.models import db, NodeActivity
        with self.app.app_context():
            row = NodeActivity(
                slot=slot, username='spyroutetest', protocol='telnet',
                peer='1.2.3.4', last_screen=last_screen)
            if stale:
                row.last_seen = datetime.utcnow() - timedelta(minutes=10)
            db.session.add(row)
            db.session.commit()

    def test_admin_sees_the_persisted_screen(self):
        admin_id = self._make_user(is_admin=True)
        self._make_node(slot=11, last_screen='<span>hi</span>')
        client = self._logged_in_client(admin_id)

        resp = client.get('/admin/control/nodespy/11/screen.json')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertIn('hi', data['html'])

    def test_no_screen_yet_returns_ok_false_not_an_error(self):
        admin_id = self._make_user(is_admin=True)
        self._make_node(slot=12, last_screen=None)
        client = self._logged_in_client(admin_id)

        resp = client.get('/admin/control/nodespy/12/screen.json')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['ok'])

    def test_stale_node_returns_ok_false(self):
        admin_id = self._make_user(is_admin=True)
        self._make_node(slot=13, last_screen='<span>old</span>', stale=True)
        client = self._logged_in_client(admin_id)

        resp = client.get('/admin/control/nodespy/13/screen.json')

        self.assertFalse(resp.get_json()['ok'])

    def test_unknown_slot_returns_ok_false(self):
        admin_id = self._make_user(is_admin=True)
        client = self._logged_in_client(admin_id)

        resp = client.get('/admin/control/nodespy/999/screen.json')

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['ok'])

    def test_non_admin_is_denied(self):
        self._make_node(slot=14, last_screen='<span>secret</span>')
        regular_id = self._make_user(is_admin=False)
        client = self._logged_in_client(regular_id)

        resp = client.get('/admin/control/nodespy/14/screen.json')

        self.assertNotEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
