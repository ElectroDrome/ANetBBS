"""Regression test: web/docs.py (the in-app /docs/ documentation
browser) had ZERO access control at all -- no login_required, no
before_request, no config flag -- reachable by anyone, including
SECURITY.md/CHANGELOG.md and every sysop/developer-facing manual.
DOCS_PUBLIC_ENABLED (config.py) defaults to True (unchanged existing
public behavior); when False, /docs/ requires an admin account.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class DocsPublicToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.docs_toggle_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        with cls.app.app_context():
            db.create_all()
            admin = User(username='docstoggleadmin', email='docstoggleadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            regular = User(username='docstoggleregular', email='docstoggleregular@example.com',
                           password_hash='x', access_level=10)
            db.session.add_all([admin, regular])
            db.session.commit()
            cls.admin_id = admin.id
            cls.regular_id = regular.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _anon_client(self):
        return self.app.test_client()

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def test_default_is_public_no_login_needed(self):
        resp = self._anon_client().get('/docs/')
        self.assertEqual(resp.status_code, 200)

    def test_disabled_blocks_anonymous_visitors(self):
        self.app.config['DOCS_PUBLIC_ENABLED'] = False
        try:
            resp = self._anon_client().get('/docs/')
        finally:
            self.app.config['DOCS_PUBLIC_ENABLED'] = True
        self.assertEqual(resp.status_code, 403)

    def test_disabled_blocks_a_regular_logged_in_user(self):
        self.app.config['DOCS_PUBLIC_ENABLED'] = False
        try:
            resp = self._client_as(self.regular_id).get('/docs/')
        finally:
            self.app.config['DOCS_PUBLIC_ENABLED'] = True
        self.assertEqual(resp.status_code, 403)

    def test_disabled_still_allows_an_admin(self):
        self.app.config['DOCS_PUBLIC_ENABLED'] = False
        try:
            resp = self._client_as(self.admin_id).get('/docs/')
        finally:
            self.app.config['DOCS_PUBLIC_ENABLED'] = True
        self.assertEqual(resp.status_code, 200)

    def test_disabled_also_blocks_a_single_doc_page(self):
        self.app.config['DOCS_PUBLIC_ENABLED'] = False
        try:
            resp = self._anon_client().get('/docs/00-overview')
        finally:
            self.app.config['DOCS_PUBLIC_ENABLED'] = True
        self.assertEqual(resp.status_code, 403)

    def test_tutorial_route_is_public_by_default(self):
        # /tutorial is registered under main_bp, not docs_bp, but is
        # conceptually part of the same docs surface (docs.py's own
        # module docstring lists it) -- confirm it's still covered.
        resp = self._anon_client().get('/tutorial')
        self.assertEqual(resp.status_code, 200)

    def test_disabled_also_blocks_the_tutorial_route(self):
        self.app.config['DOCS_PUBLIC_ENABLED'] = False
        try:
            resp = self._anon_client().get('/tutorial')
        finally:
            self.app.config['DOCS_PUBLIC_ENABLED'] = True
        self.assertEqual(resp.status_code, 403)


if __name__ == '__main__':
    unittest.main()
