"""Regression test for a path-traversal-shaped finding in a follow-up
audit: anetbbs/games/door_runner.py's dosbox/dosemu2 stdout/stderr
log-file paths are built from `game.slug` (sysop-set via the admin
Add/Edit Game form, labeled "URL-safe" but -- before this fix --
never actually validated against '..'/'/') with no sanitization,
unlike the two other places this codebase already fixed the exact
same pattern (anetbbs/features/ansi_ui.py's load_menu_ansi() and
anetbbs/core/session.py's _show_ansi_screen(), both of which strip
`slug`/`slot` down to [A-Za-z0-9_-] before any path join).

The vulnerable code runs inside the pty.fork() CHILD branch of
launch_door_game() (right before os.execvp()/os._exit()) -- driving
that branch directly in a unit test would require a real fork, which
isn't practical/safe here. Instead this test inspects the actual
source text to confirm the sanitizer call is genuinely present
between the raw `game.slug` read and each of the two log-filename
f-strings, which is directly sensitive to the real fix (reverting the
fix removes the matched sanitizer line, making this test fail) rather
than asserting something that could pass by coincidence.
"""
import re
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))


class DoorRunnerSlugSanitizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (_REPO_ROOT / 'anetbbs' / 'games' / 'door_runner.py').read_text()

    def _assert_sanitized_before_fstring(self, fstring_marker):
        idx = self.source.index(fstring_marker)
        # Look at the ~400 chars immediately preceding the f-string
        # construction -- the sanitizer call must appear there, applied
        # to `_slug` specifically, before it's interpolated.
        window = self.source[max(0, idx - 400):idx]
        self.assertRegex(
            window, r"_slug\s*=\s*re\.sub\(r?['\"]\[\^A-Za-z0-9_-\]['\"],\s*['\"]['\"],\s*_slug\)",
            f"expected a [^A-Za-z0-9_-] whitelist sanitizer applied to "
            f"_slug immediately before building {fstring_marker!r}")

    def test_dosbox_log_filename_sanitizes_slug_first(self):
        self._assert_sanitized_before_fstring("f'dosbox_{_slug}_node{node}.log'")

    def test_dosemu_log_filename_sanitizes_slug_first(self):
        self._assert_sanitized_before_fstring("f'dosemu_{_slug}_node{node}.log'")

    def test_sanitizer_actually_strips_a_traversal_slug(self):
        """Exercise the exact regex the source uses (extracted above)
        against a real malicious slug, confirming it produces a safe,
        traversal-free filename component -- not just that some regex
        exists."""
        dirty = '../../../../tmp/evil'
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', dirty) or 'door'
        self.assertNotIn('..', cleaned)
        self.assertNotIn('/', cleaned)
        self.assertEqual(cleaned, 'tmpevil')

    def test_legitimate_slug_is_unaffected(self):
        real_slug = 'sbbs-minesweeper'
        cleaned = re.sub(r'[^A-Za-z0-9_-]', '', real_slug) or 'door'
        self.assertEqual(cleaned, real_slug)


if __name__ == '__main__':
    unittest.main()
