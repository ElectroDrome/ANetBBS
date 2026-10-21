"""Tests for door-network federation (Phase 4 of the door-games
roadmap, 2026-10-20): DoorParty admin settings + add-game
(anetbbs/web/door_networks_admin.py), and the underlying
Game-kwargs builder (anetbbs/features/doorparty_import.py).

Real wire shape confirmed directly from DoorParty's own Mystic/Linux
setup HOWTO (pasted in full by Jerry): the client-user field is shaped
"<BBS_TAG>@USER@" (tag as a literal PREFIX with NO separator,
substituted via ANetBBS's existing @USER@ template token) and the
password is just a local shared secret -- deliberately NOT the same
shape as ANetBBS's own separate `rlogin_bbs_tag` field (a hyphen
SUFFIX, for a different target).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class BuildDoorpartyGameKwargsTests(unittest.TestCase):
    """Pure unit tests, no DB/Flask needed."""

    def test_wire_shape_matches_the_real_documented_convention(self):
        from anetbbs.features.doorparty_import import build_doorparty_game_kwargs
        kwargs = build_doorparty_game_kwargs(
            'DoorParty', local_port='9999', bbs_tag='MYTAG',
            shared_secret='s3cr3t')
        self.assertEqual(kwargs['command_line_args'], 'MYTAG@USER@ s3cr3t')
        self.assertEqual(kwargs['executable_path'], '127.0.0.1:9999')
        self.assertEqual(kwargs['game_type'], 'door_rlogin')
        # The tag is baked into command_line_args directly -- NOT
        # ANetBBS's own separate rlogin_bbs_tag field (different shape,
        # different target).
        self.assertEqual(kwargs['rlogin_bbs_tag'], '')

    def test_door_code_appends_xtrn_suffix(self):
        from anetbbs.features.doorparty_import import build_doorparty_game_kwargs
        kwargs = build_doorparty_game_kwargs(
            'TradeWars', door_code='tw', local_port='9999',
            bbs_tag='MYTAG', shared_secret='s3cr3t')
        self.assertEqual(kwargs['command_line_args'],
                         'MYTAG@USER@ s3cr3t xtrn=tw')

    def test_blank_door_code_omits_xtrn_entirely(self):
        from anetbbs.features.doorparty_import import build_doorparty_game_kwargs
        kwargs = build_doorparty_game_kwargs(
            'DoorParty', local_port='9999', bbs_tag='MYTAG',
            shared_secret='s3cr3t')
        self.assertNotIn('xtrn=', kwargs['command_line_args'])

    def test_slug_is_prefixed_and_derived_from_door_code_when_given(self):
        from anetbbs.features.doorparty_import import build_doorparty_game_kwargs
        kwargs = build_doorparty_game_kwargs(
            'Trade Wars 2002', door_code='tw2002', local_port='9999',
            bbs_tag='MYTAG', shared_secret='s3cr3t')
        self.assertEqual(kwargs['slug'], 'doorparty-tw2002')

    def test_slug_falls_back_to_name_when_no_door_code(self):
        from anetbbs.features.doorparty_import import build_doorparty_game_kwargs
        kwargs = build_doorparty_game_kwargs(
            'DoorParty', local_port='9999', bbs_tag='MYTAG',
            shared_secret='s3cr3t')
        self.assertEqual(kwargs['slug'], 'doorparty-doorparty')


class DoorNetworksAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.door_networks_admin_test.db')
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
            admin = User(username='doornetworksadmin',
                        email='doornetworksadmin@example.com',
                        password_hash='x', is_admin=True)
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

    def setUp(self):
        self._orig = {
            k: self.app.config.get(k) for k in (
                'DOORPARTY_LOCAL_PORT', 'DOORPARTY_BBS_TAG',
                'DOORPARTY_SHARED_SECRET')
        }
        self.addCleanup(self._restore)

        # Never write to the real project .env from a test -- see
        # test_fsxnet_admin.py's own setUp docstring for the real
        # incident this pattern prevents.
        import tempfile
        from anetbbs.web import door_networks_admin as _dn_admin_mod
        self._fake_env = tempfile.NamedTemporaryFile(
            mode='w', suffix='.env', delete=False)
        self._fake_env.close()
        self.addCleanup(lambda: os.path.exists(self._fake_env.name) and os.remove(self._fake_env.name))
        self._orig_env_path = _dn_admin_mod._env_path
        _dn_admin_mod._env_path = lambda: self._fake_env.name
        self.addCleanup(lambda: setattr(_dn_admin_mod, '_env_path', self._orig_env_path))

    def _restore(self):
        for k, v in self._orig.items():
            self.app.config[k] = v

    def _admin_client(self):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True
        return client

    def test_index_loads(self):
        client = self._admin_client()
        resp = client.get('/admin/door-networks/')
        self.assertEqual(resp.status_code, 200)

    def test_settings_round_trip(self):
        client = self._admin_client()
        resp = client.post('/admin/door-networks/doorparty/settings', data={
            'local_port': '9999', 'bbs_tag': 'MYTAG',
            'shared_secret': 's3cr3t',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.app.config['DOORPARTY_LOCAL_PORT'], '9999')
        self.assertEqual(self.app.config['DOORPARTY_BBS_TAG'], 'MYTAG')
        self.assertEqual(self.app.config['DOORPARTY_SHARED_SECRET'], 's3cr3t')

    def test_settings_rejects_a_non_numeric_port(self):
        client = self._admin_client()
        resp = client.post('/admin/door-networks/doorparty/settings', data={
            'local_port': 'not-a-port', 'bbs_tag': 'MYTAG',
            'shared_secret': 's3cr3t',
        }, follow_redirects=True)
        body = resp.get_data(as_text=True)
        self.assertIn('must be a number', body)

    def test_add_game_without_settings_configured_is_refused(self):
        client = self._admin_client()
        resp = client.post('/admin/door-networks/doorparty/add-game', data={
            'name': 'DoorParty',
        }, follow_redirects=True)
        body = resp.get_data(as_text=True)
        self.assertIn('Save your DoorParty connection settings first', body)

    def test_add_game_creates_a_real_game_row(self):
        from anetbbs.models import Game
        client = self._admin_client()
        client.post('/admin/door-networks/doorparty/settings', data={
            'local_port': '9999', 'bbs_tag': 'MYTAG',
            'shared_secret': 's3cr3t',
        })
        resp = client.post('/admin/door-networks/doorparty/add-game', data={
            'name': 'DoorParty', 'door_code': '',
        }, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            game = Game.query.filter_by(slug='doorparty-doorparty').first()
            self.assertIsNotNone(game)
            self.assertEqual(game.game_type, 'door_rlogin')
            self.assertEqual(game.command_line_args, 'MYTAG@USER@ s3cr3t')

    def test_add_game_duplicate_slug_is_refused(self):
        client = self._admin_client()
        client.post('/admin/door-networks/doorparty/settings', data={
            'local_port': '9999', 'bbs_tag': 'MYTAG',
            'shared_secret': 's3cr3t',
        })
        client.post('/admin/door-networks/doorparty/add-game',
                   data={'name': 'DoorParty', 'door_code': 'dupe'})
        resp = client.post('/admin/door-networks/doorparty/add-game', data={
            'name': 'DoorParty Again', 'door_code': 'dupe',
        }, follow_redirects=True)
        body = resp.get_data(as_text=True)
        self.assertIn('already exists', body)

    def test_delete_game_removes_only_doorparty_rows(self):
        from anetbbs.models import db, Game
        with self.app.app_context():
            other = Game(name='Not DoorParty', slug='some-other-game',
                        game_type='door_native', is_active=True)
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        client = self._admin_client()
        resp = client.post(
            f'/admin/door-networks/doorparty/game/{other_id}/delete',
            follow_redirects=True)
        # Not a doorparty-% slug -- must 404, not silently delete it.
        self.assertEqual(resp.status_code, 404)
        with self.app.app_context():
            self.assertIsNotNone(Game.query.get(other_id))


if __name__ == '__main__':
    unittest.main()
