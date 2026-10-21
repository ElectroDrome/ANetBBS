"""Regression test: a new/edited RSS feed's URL used to only ever be
validated at the NEXT background fetch (rss/poller.py's own scheme/host
check), with the result stashed in feed.last_error -- no immediate
feedback on save (rss_admin.py's _save() calls fetch_one_now() wrapped
in a bare `except: pass`). A non-http(s) URL (webcal://, feed://, or a
bare domain with no scheme at all -- all common on real "subscribe"
buttons) silently failed with the only sign being a field a sysop has
to go check afterward. Fixed by validating scheme + host at save time,
with an immediate flash error, using the exact same check
rss/poller.py's own _import_one_feed() already uses.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class RssFeedUrlSaveTimeValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.rss_save_validation_test.db')
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
            admin = User(username='rssvalidationadmin',
                        email='rssvalidationadmin@example.com',
                        password_hash='x', is_admin=True, access_level=100)
            db.session.add(admin)
            db.session.commit()
            cls.admin_id = admin.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def _post_add(self, name, url):
        # Distinct name per test -- this class shares one DB across all
        # methods (setUpClass, not setUp), so a shared name would let an
        # earlier test's leftover row (e.g. one that saved successfully
        # under a reverted/broken fix) silently satisfy a later test's
        # own filter_by(name=...) lookup, masking a real failure.
        client = self._client()
        return client.post('/admin/rss/add',
                           data={'name': name, 'url': url},
                           follow_redirects=True)

    def test_bare_domain_with_no_scheme_is_rejected_immediately(self):
        resp = self._post_add('Bare Domain Feed', 'example.com/feed.xml')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('must start with http', body)
        with self.app.app_context():
            from anetbbs.models import RssFeed
            self.assertIsNone(RssFeed.query.filter_by(name='Bare Domain Feed').first(),
                             'an invalid-scheme feed must never be saved at all')

    def test_webcal_scheme_is_rejected_immediately(self):
        resp = self._post_add('Webcal Feed', 'webcal://example.com/feed.ics')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('must start with http', body)
        with self.app.app_context():
            from anetbbs.models import RssFeed
            self.assertIsNone(RssFeed.query.filter_by(name='Webcal Feed').first(),
                             'an invalid-scheme feed must never be saved at all')

    def test_valid_https_url_is_accepted(self):
        # _save() also triggers an immediate real fetch_one_now() call on
        # success (so the sysop sees items right away) -- mocked out here
        # since this test is only about the save-time URL format check,
        # not the actual network fetch (already covered by
        # test_rss_feed_url_ssrf.py and test_rss_poller_fetch_timeout.py).
        from unittest.mock import patch
        with patch('anetbbs.rss.poller.fetch_one_now', return_value=0):
            resp = self._post_add('Valid Https Feed', 'https://example.com/feed.xml')
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from anetbbs.models import RssFeed
            feed = RssFeed.query.filter_by(name='Valid Https Feed').first()
            self.assertIsNotNone(feed)
            self.assertEqual(feed.url, 'https://example.com/feed.xml')


if __name__ == '__main__':
    unittest.main()
