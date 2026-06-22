"""Unit tests for the pure package-planning logic (offline_models.plan_packages).

Run from the repo root:
    ./.venv/bin/python -m unittest discover -s tests -t .
No third-party deps — argostranslate is never imported by plan_packages.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from screen_translator import offline_models  # noqa: E402


class PlanPackagesTest(unittest.TestCase):
    def test_direct_pair(self):
        self.assertEqual(
            offline_models.plan_packages([("en", "ru"), ("ru", "en")], "en", "ru"),
            [("en", "ru")],
        )

    def test_strips_regional_variant(self):
        # zh-CN collapses to the bare Argos code "zh".
        self.assertEqual(
            offline_models.plan_packages([("zh", "en")], "zh-CN", "en"),
            [("zh", "en")],
        )

    def test_pivot_through_english(self):
        # No direct ja->ru pack; Argos pivots via English when both halves exist.
        self.assertEqual(
            offline_models.plan_packages([("ja", "en"), ("en", "ru")], "ja", "ru"),
            [("ja", "en"), ("en", "ru")],
        )

    def test_prefers_direct_over_pivot(self):
        avail = [("ja", "ru"), ("ja", "en"), ("en", "ru")]
        self.assertEqual(
            offline_models.plan_packages(avail, "ja", "ru"), [("ja", "ru")]
        )

    def test_no_route_raises(self):
        with self.assertRaises(RuntimeError):
            offline_models.plan_packages([("ja", "en")], "ja", "ru")  # missing en->ru

    def test_same_language_raises(self):
        with self.assertRaises(RuntimeError):
            offline_models.plan_packages([("en", "ru")], "en", "en")

    def test_auto_source_raises(self):
        with self.assertRaises(RuntimeError):
            offline_models.plan_packages([("en", "ru")], "auto", "ru")


if __name__ == "__main__":
    unittest.main()
