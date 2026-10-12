"""Dashboard "MRC Stats" widget (anetbbs/web/main.py's
/api/mrc-network-stats route + _fetch_mrc_network_stats()).

Pulls network-wide bbses/rooms/users/activity from the public MRC hub
status endpoint (the same 4 figures umrc-client's own stats file shows
-- see mrc/bridge/main.py's _parse_umrc_stats_reply docstring) and
caches them in-memory for MRC_NETWORK_STATS_CACHE_SECONDS, independent
of whether the local MRC bridge is enabled.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod


class _FakeResp:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError('simulated HTTP error')

    def json(self):
        return self._payload


class MrcNetworkStatsWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent
                          / '.mrc_network_stats_widget_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            from anetbbs.models import db
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def setUp(self):
        self.client = self.app.test_client()
        self.app.config['MRC_NETWORK_STATS_ENABLED'] = True
        self.app.config['MRC_NETWORK_STATS_CACHE_SECONDS'] = 30
        # Module-level cache is shared process-wide (deliberately, to cut
        # traffic to the real remote host) -- must be reset between tests
        # or an earlier test's cached reading leaks into a later one.
        import anetbbs.web.main as main_mod
        main_mod._mrc_stats_cache = None
        self.main_mod = main_mod

    def test_successful_fetch_returns_parsed_counts(self):
        payload = {'ok': True, 'stats': {'bbses': 192, 'rooms': 14, 'users': 56, 'activity': 2}, 'age': 10}
        with patch.object(self.main_mod.requests, 'get', return_value=_FakeResp(payload)):
            resp = self.client.get('/api/mrc-network-stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['bbses'], 192)
        self.assertEqual(data['rooms'], 14)
        self.assertEqual(data['users'], 56)
        self.assertEqual(data['activity'], 2)
        self.assertEqual(data['activity_label'], 'MED')
        self.assertFalse(data['stale'])

    def test_activity_label_matches_umrc_clients_0_to_3_scale(self):
        # Confirmed against docs/27-mrc-chat.md's mrcstats.dat section
        # (itself confirmed against real umrc-client behavior): activity
        # is a fixed 0-3 scale, none/low/medium/high -- not an
        # open-ended count. Out-of-range values clamp to the nearest end.
        cases = [(0, 'NONE'), (1, 'LOW'), (2, 'MED'), (3, 'HIGH'), (-1, 'NONE'), (9, 'HIGH')]
        for activity, expected in cases:
            self.main_mod._mrc_stats_cache = None
            payload = {'stats': {'bbses': 1, 'rooms': 1, 'users': 1, 'activity': activity}}
            with patch.object(self.main_mod.requests, 'get', return_value=_FakeResp(payload)):
                resp = self.client.get('/api/mrc-network-stats')
            self.assertEqual(resp.get_json()['activity_label'], expected,
                              f'activity={activity} should map to {expected}')

    def test_second_request_within_ttl_does_not_refetch(self):
        payload = {'stats': {'bbses': 1, 'rooms': 2, 'users': 3, 'activity': 1}}
        with patch.object(self.main_mod.requests, 'get',
                          return_value=_FakeResp(payload)) as mock_get:
            self.client.get('/api/mrc-network-stats')
            self.client.get('/api/mrc-network-stats')
            self.assertEqual(mock_get.call_count, 1,
                              'a second request inside the TTL window must be served from cache')

    def test_fetch_failure_falls_back_to_last_known_good_reading(self):
        good_payload = {'stats': {'bbses': 5, 'rooms': 6, 'users': 7, 'activity': 1}}
        with patch.object(self.main_mod.requests, 'get', return_value=_FakeResp(good_payload)):
            self.client.get('/api/mrc-network-stats')
        # Force the cache to look expired, then make the next fetch fail.
        data, _expires = self.main_mod._mrc_stats_cache
        self.main_mod._mrc_stats_cache = (data, 0)
        with patch.object(self.main_mod.requests, 'get', side_effect=RuntimeError('network down')):
            resp = self.client.get('/api/mrc-network-stats')
        self.assertEqual(resp.status_code, 200)
        out = resp.get_json()
        self.assertTrue(out['ok'])
        self.assertEqual(out['bbses'], 5)
        self.assertTrue(out['stale'])

    def test_fetch_failure_with_no_prior_cache_reports_not_ok(self):
        with patch.object(self.main_mod.requests, 'get', side_effect=RuntimeError('network down')):
            resp = self.client.get('/api/mrc-network-stats')
        self.assertEqual(resp.status_code, 502)
        self.assertFalse(resp.get_json()['ok'])

    def test_disabled_returns_404(self):
        self.app.config['MRC_NETWORK_STATS_ENABLED'] = False
        resp = self.client.get('/api/mrc-network-stats')
        self.assertEqual(resp.status_code, 404)

    def test_widget_card_absent_from_homepage_when_disabled(self):
        self.app.config['MRC_NETWORK_STATS_ENABLED'] = False
        resp = self.client.get('/')
        self.assertNotIn(b'mrc-network-stats', resp.data)

    def test_widget_card_present_on_homepage_when_enabled(self):
        self.app.config['MRC_NETWORK_STATS_ENABLED'] = True
        resp = self.client.get('/')
        self.assertIn(b'mrc-network-stats', resp.data)


if __name__ == '__main__':
    unittest.main()
