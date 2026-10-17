"""Regression test for a path-traversal-shaped pattern found in a
follow-up security audit: menu_engine.py's _load_menu_art_from_disk()
joins `menu_name` (BbsMenu.name, sysop-set via Admin -> BBS Menus with
no character restriction -- just .strip() + a 50-char truncation, see
web/menu_admin.py) straight into a filesystem path with no validation.
Sibling of the already-fixed anetbbs/features/ansi_ui.py:141 bug
(load_menu_ansi()'s 'slot' parameter, see test_ansi_slot_path_traversal.py).

A menu named e.g. '../../outside/secret' would make
_load_menu_art_from_disk build os.path.join(menus_dir,
'../../outside/secret.ans'), escaping the intended menus directory.
Fixed by restricting menu_name to [A-Za-z0-9_-] -- every real menu name
already matches that character set.

Uses a REAL on-disk directory layout (not a mocked Path.is_file()) so
the traversal distance is exact and the test is genuinely sensitive to
the fix: menus_dir and outside_dir are deterministic siblings under the
same tempdir root, with a real secret.ans file planted in outside_dir.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.menu_engine import _load_menu_art_from_disk


class LoadMenuArtFromDiskPathTraversalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.menus_dir = root / 'data' / 'text' / 'menus'
        self.menus_dir.mkdir(parents=True)
        self.outside_dir = root / 'outside'
        self.outside_dir.mkdir()
        (self.outside_dir / 'secret.ans').write_bytes(b'TOP SECRET CONTENT')
        (self.outside_dir / 'secret.asc').write_bytes(b'TOP SECRET ASCII CONTENT')
        # Exact traversal distance from menus_dir back up to outside_dir.
        self.traversal_name = '../../../outside/secret'
        assert (self.menus_dir / f'{self.traversal_name}.ans').resolve() == \
            (self.outside_dir / 'secret.ans').resolve(), \
            'test setup bug: traversal distance does not actually reach outside_dir'

    def tearDown(self):
        self._tmp.cleanup()

    def test_traversal_menu_name_never_escapes_menus_dir_ansi_mode(self):
        screen, is_plain = _load_menu_art_from_disk(
            str(self.menus_dir), self.traversal_name, 'ansi')
        self.assertEqual(screen, '',
                          'sanitized menu_name must not resolve to the '
                          'planted file outside menus_dir')

    def test_traversal_menu_name_never_escapes_menus_dir_ascii_mode(self):
        screen, is_plain = _load_menu_art_from_disk(
            str(self.menus_dir), self.traversal_name, 'ascii')
        self.assertEqual(screen, '')

    def test_traversal_menu_name_never_escapes_menus_dir_wide_mode(self):
        screen, is_plain = _load_menu_art_from_disk(
            str(self.menus_dir), self.traversal_name, 'wide')
        self.assertEqual(screen, '')

    def test_legitimate_menu_name_still_loads_normally(self):
        """Sanity check: the fix must not regress the common case."""
        (self.menus_dir / 'mainmenu.ans').write_bytes(b'WELCOME SCREEN')
        screen, is_plain = _load_menu_art_from_disk(
            str(self.menus_dir), 'mainmenu', 'ansi')
        self.assertEqual(screen, 'WELCOME SCREEN')


if __name__ == '__main__':
    unittest.main()
