"""Terminal MRC input countdown ("chatty mod"), shown on the plain
status bar and in the Mystic themes' BUFFER element as remaining/limit:
up to 140 typed, what's left of the 140-char typed-text budget over
140; past 140, what's left of the 280-char line over a red 280. The
first number turns yellow at 20 or fewer left and red at 5 or fewer
(umrc-client's thresholds; it counts up instead).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anetbbs.core  # noqa: F401,E402  (resolves a circular import if mrc_chat is imported first)
from anetbbs.features.mrc_chat import MRCChat  # noqa: E402
from mrc.mystic_client.theme_layout import (  # noqa: E402
    load_theme_layout, art_char_at, _SCRIPTS_DIR,
)


class _FakeSession:
    def __init__(self):
        self.user = {'username': 'tester'}
        self.written = []

    async def write(self, text):
        self.written.append(text)


def _chat(typed: str):
    chat = MRCChat(_FakeSession())
    chat._split_screen = False
    chat._handle = 'Alice'
    chat._input_buf = list(typed)
    return chat


class CountdownTests(unittest.TestCase):
    def test_empty_line(self):
        self.assertEqual(_chat('')._buffer_countdown(), (140, 140, 'normal', False))

    def test_yellow_at_20_left(self):
        self.assertEqual(_chat('x' * 119)._buffer_countdown()[2], 'normal')
        self.assertEqual(_chat('x' * 120)._buffer_countdown(), (20, 140, 'yellow', False))

    def test_red_at_5_left(self):
        self.assertEqual(_chat('x' * 134)._buffer_countdown()[2], 'yellow')
        self.assertEqual(_chat('x' * 135)._buffer_countdown(), (5, 140, 'red', False))
        self.assertEqual(_chat('x' * 140)._buffer_countdown(), (0, 140, 'red', False))

    def test_char_141_switches_to_280(self):
        self.assertEqual(_chat('x' * 141)._buffer_countdown(), (139, 280, 'normal', True))

    def test_second_stage_warnings_and_end(self):
        self.assertEqual(_chat('x' * 260)._buffer_countdown(), (20, 280, 'yellow', True))
        self.assertEqual(_chat('x' * 280)._buffer_countdown(), (0, 280, 'red', True))

    def test_dm_counts_message_part_then_whole_line(self):
        self.assertEqual(_chat('/t Bob ' + 'x' * 100)._buffer_countdown()[:2], (40, 140))
        line = '/t Bob ' + 'x' * 150
        self.assertEqual(_chat(line)._buffer_countdown()[:2], (280 - len(line), 280))


class MysticBufferTests(unittest.TestCase):
    def test_every_bundled_theme_has_slash_after_buffer(self):
        for ini in sorted(_SCRIPTS_DIR.glob('mrctheme-*.ini')):
            theme, mode = ini.name[len('mrctheme-'):-len('.ini')].split('.', 1)
            layout = load_theme_layout(theme, mode)
            x, y, length = layout.element('BUFFER')[:3]
            self.assertEqual(art_char_at(layout, x + length, y), '/', ini.name)

    def _placed(self, typed, layout):
        chat = _chat(typed)
        out = []
        chat._mystic_place_buffer(layout, out)
        return ''.join(out)

    def test_writes_both_numbers_on_a_bundled_theme(self):
        layout = load_theme_layout('minimal', 'default')
        x, y, length, fg = layout.element('BUFFER')[:4]
        normal = MRCChat._mystic_color_sgr(fg)
        text = self._placed('x' * 141, layout)
        self.assertIn(f'\x1b[{y};{x}H\x1b[{normal}m139\x1b[0m', text)
        self.assertIn(f'\x1b[{y};{x + length + 1}H\x1b[1;91m280\x1b[0m', text)

    def test_theme_without_slash_gets_one_number_counting_from_280(self):
        layout = load_theme_layout('minimal', 'default')
        x, y, length = layout.element('BUFFER')[:3]
        layout.elements['BUFFER'] = (x - 2, y, length, 7, 16)  # slot no longer before "/"
        text = self._placed('x' * 100, layout)
        self.assertIn('180\x1b[0m', text)
        self.assertNotIn('/', text.replace('\x1b[', ''))
        self.assertEqual(text.count('H'), 1)


if __name__ == '__main__':
    unittest.main()
