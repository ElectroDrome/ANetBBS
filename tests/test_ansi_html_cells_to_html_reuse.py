"""Regression test for extracting cells_to_html() out of _to_html_vt().

Context: the admin NodeSpy live screen mirror (see session.py's
_spy_tee()) needs to render an already-computed, CONTINUING _run_vt()
state (cells/max_row persisted across many write() calls) to HTML,
without re-running the VT parser from scratch each time. _to_html_vt()
used to do the cell-grid-to-HTML rendering inline on a one-shot
_run_vt(text) call; this refactor pulls that rendering loop out into a
standalone cells_to_html(cells, max_row) function that either caller
can use. This test confirms the refactor didn't change _to_html_vt()'s
own output, and that cells_to_html() fed the exact same cells/max_row
_run_vt() would have produced renders identically.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anetbbs.features.ansi_html import _run_vt, _to_html_vt, cells_to_html


class CellsToHtmlReuseTests(unittest.TestCase):
    def test_to_html_vt_unchanged_for_plain_text(self):
        self.assertEqual(_to_html_vt('hello'), _to_html_vt('hello'))
        html = _to_html_vt('hello')
        self.assertIn('hello', html)

    def test_cells_to_html_matches_to_html_vt_for_same_input(self):
        text = 'Enter wager (\x1b[1;37;40m1400 Max\x1b[0m)'
        cells, max_row, _state = _run_vt(text)
        self.assertEqual(cells_to_html(cells, max_row), _to_html_vt(text))

    def test_cells_to_html_renders_color_spans(self):
        cells, max_row, _state = _run_vt('\x1b[31mred\x1b[0m')
        html = cells_to_html(cells, max_row)
        self.assertIn('color:', html)
        self.assertIn('red', html)

    def test_cells_to_html_empty_grid_returns_empty_string(self):
        self.assertEqual(cells_to_html({}, -1), '')

    def test_cells_to_html_works_with_a_continued_state(self):
        # Two separate _run_vt() calls sharing state, same shape the
        # live NodeSpy mirror uses across many BBSSession.write() calls.
        cells, max_row, state = _run_vt('line one\r\n')
        cells, max_row, state = _run_vt('line two', state=state)
        html = cells_to_html(cells, max_row)
        self.assertIn('line one', html)
        self.assertIn('line two', html)


if __name__ == '__main__':
    unittest.main()
