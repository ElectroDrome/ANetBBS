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
    ThemeLayout, load_theme_layout, art_char_at, _SCRIPTS_DIR,
)
from unittest import mock  # noqa: E402


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


class ArtCharAtOverlapTests(unittest.TestCase):
    """art_char_at() checks every art segment covering a row, not just
    the first, and the last-drawn one (BOTANSI after TOPANSI) wins --
    no bundled theme overlaps segments, so these use a synthetic
    layout."""

    LINES = {
        1: 'AAAA',      # TOPANSI line, drawn at (1, 5)
        2: '\x1b[1;31mBB',  # BOTANSI line, drawn at (3, 5) -- overlaps cols 3-4
    }

    def _layout(self):
        return ThemeLayout('synthetic.ans',
                           {'TOPANSI': (1, 1, 1, 5, 7), 'BOTANSI': (2, 1, 3, 5, 7)},
                           {})

    def _segment(self, _ansifile, first_line, num_lines):
        return [self.LINES[first_line + i] for i in range(num_lines)]

    def test_cell_only_in_first_segment_is_found(self):
        with mock.patch('mrc.mystic_client.theme_layout.extract_art_segment', self._segment):
            self.assertEqual(art_char_at(self._layout(), 1, 5), 'A')

    def test_cell_in_both_segments_uses_last_drawn(self):
        with mock.patch('mrc.mystic_client.theme_layout.extract_art_segment', self._segment):
            self.assertEqual(art_char_at(self._layout(), 3, 5), 'B')

    def test_cell_only_in_second_segment_is_found(self):
        self.LINES = {1: 'A', 2: '\x1b[1;31mBB'}
        with mock.patch('mrc.mystic_client.theme_layout.extract_art_segment', self._segment):
            self.assertEqual(art_char_at(self._layout(), 4, 5), 'B')

    def test_cell_no_segment_draws_is_none(self):
        with mock.patch('mrc.mystic_client.theme_layout.extract_art_segment', self._segment):
            self.assertIsNone(art_char_at(self._layout(), 9, 5))


if __name__ == '__main__':
    unittest.main()
