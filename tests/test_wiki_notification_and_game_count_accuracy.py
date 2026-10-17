"""
Regression test for a docs/wiki accuracy audit finding: anetbbs/wiki/
seed.py's 'notifications', 'troubleshooting', and 'sysop-control-panel'
pages claimed there were "six" admin-review notification kinds (naming
a 6th, "social post queued for review", that has no actual toggle in
anetbbs/web/notifications.py's _ADMIN_NOTIFY_KINDS -- which has exactly
five). The 'features' page separately understated the Game Center's
built-in browser game count as "20" when anetbbs/games/web_games.py's
WEB_GAMES list actually has 21. This test pins both corrected counts so
a future wiki edit can't silently reintroduce either drift.
"""
import unittest

from anetbbs.wiki.seed import SEED


def _page_body(slug):
    for s, _title, body in SEED:
        if s == slug:
            return body
    raise AssertionError(f'no seed page with slug {slug!r}')


class AdminNotificationCountAccuracyTests(unittest.TestCase):

    def test_notifications_page_says_five_not_six(self):
        body = _page_body('notifications')
        self.assertIn('five', body)
        self.assertNotIn('six', body)
        self.assertNotIn('Social post queued for review', body)

    def test_sysop_guide_page_says_five_not_six(self):
        body = _page_body('sysop-guide')
        self.assertIn('five', body)
        self.assertNotIn('six', body)
        self.assertNotIn('social posts queued for', body)

    def test_sysop_control_panel_page_says_five_not_six(self):
        body = _page_body('sysop-control-panel')
        self.assertIn('five', body)
        self.assertNotIn('six', body)


class GameCenterCountAccuracyTests(unittest.TestCase):

    def test_features_page_says_21_not_20_games(self):
        body = _page_body('features')
        self.assertIn('21 built-in browser games', body)
        self.assertNotIn('20 built-in', body)

    def test_web_games_list_actually_has_21_entries(self):
        from anetbbs.games.web_games import WEB_GAMES
        self.assertEqual(len(WEB_GAMES), 21)


if __name__ == '__main__':
    unittest.main()
