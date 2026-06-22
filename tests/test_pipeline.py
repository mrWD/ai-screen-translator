"""Unit tests for the framework-free pipeline logic (screen_translator/pipeline.py).

Run from the repo root:
    ./.venv/bin/python -m unittest discover -s tests -t .
These are pure geometry/text checks — no third-party deps needed.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screen_translator import pipeline  # noqa: E402


class TestComputeScale(unittest.TestCase):
    def test_retina_2x(self):
        # A 1800x1169 logical screen captured at native Retina (3600x2338).
        self.assertEqual(pipeline.compute_scale(3600, 2338, 1800, 1169), (2.0, 2.0))

    def test_1x(self):
        self.assertEqual(pipeline.compute_scale(800, 600, 800, 600), (1.0, 1.0))

    def test_zero_region_falls_back_to_one(self):
        self.assertEqual(pipeline.compute_scale(100, 100, 0, 0), (1.0, 1.0))


class TestMapBlock(unittest.TestCase):
    def test_2x_maps_back_to_logical(self):
        # The dpr=2 half-height regression: a block at pixel y=200 with a 2x scale
        # must land at logical y=100, not y=200.
        x, y, w, h = pipeline.map_block(100, 200, 60, 40, 0, 0, 2.0, 2.0)
        self.assertEqual((x, y, w, h), (50, 100, 30, 20))

    def test_geom_offset_applied(self):
        # Secondary display at logical origin (1800, 0).
        x, y, w, h = pipeline.map_block(0, 0, 100, 50, 1800, 0, 1.0, 1.0)
        self.assertEqual((x, y), (1800, 0))

    def test_dimensions_clamped_to_one(self):
        _, _, w, h = pipeline.map_block(0, 0, 1, 1, 0, 0, 4.0, 4.0)
        self.assertEqual((w, h), (1, 1))


class TestIsJunkBlock(unittest.TestCase):
    def test_real_text_kept(self):
        self.assertFalse(pipeline.is_junk_block(10, 200, 120, 30, geom_y=0, is_macos=True))

    def test_too_small_dropped(self):
        self.assertTrue(pipeline.is_junk_block(10, 200, 4, 30, geom_y=0, is_macos=False))
        self.assertTrue(pipeline.is_junk_block(10, 200, 120, 5, geom_y=0, is_macos=False))

    def test_macos_menu_bar_dropped_on_primary(self):
        self.assertTrue(pipeline.is_junk_block(10, 5, 120, 18, geom_y=0, is_macos=True))

    def test_menu_bar_kept_off_primary_origin(self):
        # Same y, but the capture started below the menu bar (geom_y != 0).
        self.assertFalse(pipeline.is_junk_block(10, 5, 120, 18, geom_y=100, is_macos=True))

    def test_menu_bar_not_applied_off_macos(self):
        self.assertFalse(pipeline.is_junk_block(10, 5, 120, 18, geom_y=0, is_macos=False))


if __name__ == "__main__":
    unittest.main()
