"""Regression tests for five new bold web themes Jerry asked for ("go
crazy" -- something distinctly different from what ANetBBS already had,
not another palette swap): Ceefax Teletext, Brass & Oscilloscope,
Mahogany Study, Cyanotype Blueprint, and Riso Pop-Art Arcade. Each uses
the same "--theme-stylesheet" escape hatch Graphite Teal/Ivory
Editorial/Retro Web/Command Deck already use (see test_new_themes.py),
so this file mirrors that one's shape exactly for the five new themes.

Covers: the themes seed correctly, their css_variables are valid JSON
with the full schema, each one's declared colors pass WCAG AA contrast
(matching this project's own stated bar), and picking one as a user's
theme loads the correct override stylesheet and no other.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anetbbs.config as cfg_mod

NEW_THEME_NAMES = (
    'ceefax-teletext', 'brass-oscilloscope', 'mahogany-study',
    'cyanotype-blueprint', 'riso-pop-art',
)

STYLESHEETS = {
    'ceefax-teletext': 'ceefax_teletext_theme.css',
    'brass-oscilloscope': 'brass_oscilloscope_theme.css',
    'mahogany-study': 'mahogany_study_theme.css',
    'cyanotype-blueprint': 'cyanotype_blueprint_theme.css',
    'riso-pop-art': 'riso_pop_art_theme.css',
}


# Same dependency-free WCAG 2.1 contrast-ratio implementation as
# test_new_themes.py -- kept as its own copy per this project's
# established per-file-fixture convention rather than a shared import.
def _luminance(hex_color):
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def chan(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = chan(r), chan(g), chan(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    l1, l2 = _luminance(fg), _luminance(bg)
    l1, l2 = max(l1, l2), min(l1, l2)
    return (l1 + 0.05) / (l2 + 0.05)


class FiveNewConceptThemesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_db_uri = cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI
        cls._tmp_db = str(Path(__file__).resolve().parent / '.five_new_themes_test.db')
        if os.path.exists(cls._tmp_db):
            os.remove(cls._tmp_db)
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = f'sqlite:///{cls._tmp_db}'
        os.environ['FLASK_ENV'] = 'testing'

        from anetbbs.web_app import create_app
        from anetbbs.models import User, Theme, db
        cls.app = create_app('testing')
        cls.app.config['TESTING'] = True
        with cls.app.app_context():
            cls.themes = {}
            for name in NEW_THEME_NAMES:
                t = Theme.query.filter_by(name=name).first()
                cls.themes[name] = {
                    'id': t.id if t else None,
                    'css_variables': t.css_variables if t else None,
                }
            user = User(username='fivethemeuser', email='fivethemeuser@example.com',
                       is_active=True)
            user.set_password('password12345')
            db.session.add(user)
            db.session.commit()
            cls.user_id = user.id

    @classmethod
    def tearDownClass(cls):
        cfg_mod.TestingConfig.SQLALCHEMY_DATABASE_URI = cls._orig_db_uri
        for suffix in ('', '-wal', '-shm'):
            path = cls._tmp_db + suffix
            if os.path.exists(path):
                os.remove(path)

    def _client_with_theme(self, theme_id):
        from anetbbs.models import db, User
        with self.app.app_context():
            u = db.session.get(User, self.user_id)
            u.theme_id = theme_id
            db.session.commit()
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(self.user_id)
            sess['_fresh'] = True
        return client

    def test_all_five_themes_seeded(self):
        for name in NEW_THEME_NAMES:
            self.assertIsNotNone(self.themes[name]['id'], f'{name} was not seeded')

    def test_css_variables_are_valid_json_with_full_schema(self):
        required_keys = {
            '--theme-bg', '--theme-bg-dark', '--theme-primary',
            '--theme-primary-dark', '--theme-text', '--theme-text-muted',
            '--theme-card-bg', '--theme-input-bg', '--theme-input-focus',
            '--theme-border', '--theme-stylesheet',
        }
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            missing = required_keys - data.keys()
            self.assertFalse(missing, f'{name} missing keys: {missing}')
            self.assertEqual(data['--theme-stylesheet'], name)

    def test_body_text_and_muted_text_pass_wcag_aa(self):
        """AA requires >= 4.5:1 for normal body text against the page
        background -- checked against both --theme-bg and
        --theme-card-bg, since real content sits on both surfaces."""
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            for surface in ('--theme-bg', '--theme-card-bg'):
                ratio = _contrast(data['--theme-text'], data[surface])
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f'{name}: --theme-text on {surface} only {ratio:.2f}:1')
            ratio = _contrast(data['--theme-text-muted'], data['--theme-bg'])
            self.assertGreaterEqual(
                ratio, 4.5,
                f'{name}: --theme-text-muted on --theme-bg only {ratio:.2f}:1')

    def test_primary_accent_readable_against_background(self):
        for name, theme in self.themes.items():
            data = json.loads(theme['css_variables'])
            ratio = _contrast(data['--theme-primary'], data['--theme-bg'])
            self.assertGreaterEqual(
                ratio, 3.0,
                f'{name}: --theme-primary on --theme-bg only {ratio:.2f}:1')

    def test_each_theme_loads_its_own_stylesheet_and_none_other(self):
        for name, css_file in STYLESHEETS.items():
            body = self._client_with_theme(self.themes[name]['id']).get('/').data.decode()
            self.assertIn(css_file, body, f'{name} did not load {css_file}')
            others = set(STYLESHEETS.values()) - {css_file}
            for other in others:
                self.assertNotIn(other, body,
                                 f'{name} incorrectly also loaded {other}')

    def test_stylesheet_files_exist_and_declare_their_fonts(self):
        """Sanity check the actual CSS files, not just the DB rows."""
        css_dir = Path(__file__).resolve().parent.parent / 'anetbbs' / 'static' / 'css'
        expected_fonts = {
            'ceefax_teletext_theme.css': 'VT323',
            'brass_oscilloscope_theme.css': 'Oswald',
            'mahogany_study_theme.css': 'Playfair Display',
            'cyanotype_blueprint_theme.css': 'Big Shoulders Display',
            'riso_pop_art_theme.css': 'Anton',
        }
        for filename, font in expected_fonts.items():
            css = (css_dir / filename).read_text()
            self.assertIn(font, css, f'{filename} missing expected display font {font}')
            self.assertIn(':root', css)

    def test_docs_theme_count_includes_all_five(self):
        """docs/08-themes.md and docs/00-overview.md must both name
        every one of the five new themes and the updated total count --
        same drift this project already guards against in
        test_docs_facts_match_code.py."""
        repo_root = Path(__file__).resolve().parent.parent
        themes_doc = (repo_root / 'docs' / '08-themes.md').read_text()
        for name in ('Ceefax Teletext', 'Brass & Oscilloscope', 'Mahogany Study',
                    'Cyanotype Blueprint', 'Riso Pop-Art Arcade'):
            self.assertIn(name, themes_doc, f'{name} missing from docs/08-themes.md')

        import re
        src = (repo_root / 'anetbbs' / 'web_app.py').read_text()
        m = re.search(r'default_themes = \[(.*?)\n    \]', src, re.DOTALL)
        real_count = len(re.findall(r"'name':", m.group(1)))

        overview = (repo_root / 'docs' / '00-overview.md').read_text()
        doc_m = re.search(r'pick from (\d+) built-in', overview)
        self.assertEqual(int(doc_m.group(1)), real_count)


if __name__ == '__main__':
    unittest.main()
