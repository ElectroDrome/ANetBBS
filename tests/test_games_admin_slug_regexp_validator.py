"""Regression test for a follow-up audit finding: GameForm.slug and
GameCategoryForm.slug (anetbbs/web/games_admin.py) were labeled "URL-
safe" but only validated DataRequired()/Length() -- no character
whitelist -- leaving the field an open invitation for the next future
code path that joins game.slug into a filesystem path or URL without
re-deriving the sanitizer itself (see the sibling fix in
anetbbs/games/door_runner.py's dosbox/dosemu log-filename construction,
which already had exactly this gap). Adds Regexp(r'^[A-Za-z0-9_-]+$')
to both fields as defense-in-depth.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.config as cfg_mod


class GamesAdminSlugRegexpValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.games_admin_slug_regexp_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'

        from anetbbs.web_app import create_app
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def test_game_form_rejects_a_traversal_shaped_slug(self):
        from anetbbs.web.games_admin import GameForm
        with self.app.test_request_context('/admin/games/add', method='POST', data={
            'name': 'Evil Door', 'slug': '../../../../tmp/evil',
            'game_type': 'door_native', 'max_nodes': '1',
        }):
            form = GameForm(meta={'csrf': False})
            self.assertFalse(form.validate())
            self.assertIn('slug', form.errors)

    def test_game_form_accepts_a_real_slug(self):
        from anetbbs.web.games_admin import GameForm
        with self.app.test_request_context('/admin/games/add', method='POST', data={
            'name': 'Minesweeper', 'slug': 'sbbs-minesweeper',
            'game_type': 'door_native', 'max_nodes': '1',
        }):
            form = GameForm(meta={'csrf': False})
            self.assertTrue(form.validate(), form.errors)

    def test_game_category_form_rejects_a_traversal_shaped_slug(self):
        from anetbbs.web.games_admin import GameCategoryForm
        with self.app.test_request_context('/admin/games/categories/add', method='POST', data={
            'name': 'Evil Category', 'slug': '../../etc',
        }):
            form = GameCategoryForm(meta={'csrf': False})
            self.assertFalse(form.validate())
            self.assertIn('slug', form.errors)

    def test_game_category_form_accepts_a_real_slug(self):
        from anetbbs.web.games_admin import GameCategoryForm
        with self.app.test_request_context('/admin/games/categories/add', method='POST', data={
            'name': 'Card Games', 'slug': 'card_games',
        }):
            form = GameCategoryForm(meta={'csrf': False})
            self.assertTrue(form.validate(), form.errors)


if __name__ == '__main__':
    unittest.main()
