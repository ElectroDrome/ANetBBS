"""Regression test: irc/index.html's "Save" button fetch() POSTed to
/irc/save with a FormData body and NO CSRF token at all. CSRFProtect
is initialized app-wide with no exemption for the irc blueprint, so
with CSRF protection actually enabled (the real production posture),
every settings save was silently rejected -- the JS only showed
"[Save failed.]" with no indication why. A real, live functional bug
(the opposite of a vulnerability -- the app's own protection was
correctly blocking the unauthenticated-looking request), found in a
follow-up security audit.

Fixed by reading the token from the same <meta name="csrf-token"> tag
base.html already renders on every page and sending it as
X-CSRFToken -- the same working pattern already used elsewhere in
this app (e.g. admin/addons.html, and the identical class of bug
already fixed for the web games' score-submission fetch() calls, see
test_web_game_score_csrf.py).

Verifies with REAL Flask-WTF CSRF protection enabled (most of this
suite disables it for convenience) that: (1) a save with no token is
rejected and the settings row is unchanged (proves the endpoint is
genuinely protected, not accidentally exempted), and (2) a save using
the real token from the rendered page -- exactly how the fixed JS now
behaves -- succeeds and the settings are actually persisted.
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class IrcWebSaveSettingsCsrfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.irc_web_save_csrf_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import db, User
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            db.create_all()
            u = User(username='irccsrftester', email='irccsrftester@example.com',
                    password_hash='x', access_level=0)
            db.session.add(u)
            db.session.commit()
            cls.user_id = u.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_as(self, user_id):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True
        return client

    def _saved_server(self):
        from anetbbs.models import IrcServerConfig
        with self.app.app_context():
            cfg = IrcServerConfig.query.filter_by(user_id=self.user_id).first()
            return cfg.server if cfg else None

    def test_save_with_no_csrf_token_is_rejected(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        try:
            client = self._client_as(self.user_id)
            resp = client.post('/irc/save', data={
                'server': 'irc.example.test', 'port': '6667',
                'nick': 'tester', 'channels': '#test',
            })
            self.assertNotEqual(resp.status_code, 204,
                                'a save with no CSRF token must not succeed')
            self.assertIsNone(self._saved_server(),
                              'no settings must be persisted without a valid CSRF token')
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = False

    def test_save_with_the_real_page_token_succeeds(self):
        """Mirrors exactly what the fixed JS now does: GET the page
        that renders the <meta name="csrf-token"> tag, extract the
        token, send it back as X-CSRFToken on the save POST."""
        self.app.config['WTF_CSRF_ENABLED'] = True
        try:
            client = self._client_as(self.user_id)
            page = client.get('/irc/')
            self.assertEqual(page.status_code, 200)
            m = re.search(r'<meta name="csrf-token" content="([^"]+)">',
                          page.get_data(as_text=True))
            self.assertIsNotNone(m, 'the IRC page must render the '
                                    'csrf-token meta tag (base.html)')
            token = m.group(1)

            resp = client.post('/irc/save',
                               data={'server': 'irc.example.test', 'port': '6667',
                                     'nick': 'tester', 'channels': '#test'},
                               headers={'X-CSRFToken': token})
            self.assertEqual(resp.status_code, 204,
                             'a save with the real page CSRF token must succeed')
            self.assertEqual(self._saved_server(), 'irc.example.test',
                             'settings must actually be persisted for a '
                             'correctly-authenticated save')
        finally:
            self.app.config['WTF_CSRF_ENABLED'] = False

    def test_template_sends_the_csrf_header_on_save(self):
        """Confirms the actual fix is present in the template's own
        JS source, independent of the end-to-end request test above."""
        template_path = (Path(__file__).resolve().parents[1]
                         / 'anetbbs' / 'templates' / 'irc' / 'index.html')
        src = template_path.read_text()
        save_section = src[src.index('btnSave.addEventListener'):]
        save_section = save_section[:save_section.index('btnSend')]
        self.assertIn('csrf-token', save_section)
        self.assertIn('X-CSRFToken', save_section)


if __name__ == '__main__':
    unittest.main()
