"""Cross-platform behavior that only triggers off macOS.

These run on the macOS dev box by faking `sys.platform`, so the Windows/Linux code
paths (RapidOCR routing, logical->physical capture scaling, config engine reset)
are exercised without that hardware. Stdlib + mocks only — no real capture/OCR.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from screen_translator import capture, config, ocr
from screen_translator.config import Region


class _FakeShot:
    def __init__(self, w, h):
        self.width, self.height = w, h
        self.rgb = b"\x00\x00\x00" * (w * h)  # RGB, 3 bytes/pixel


class _FakeSct:
    """Stand-in for mss.mss(): a context manager whose grab() records the monitor."""

    def __init__(self):
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, monitor):
        self.last = monitor
        return _FakeShot(monitor["width"], monitor["height"])


class TestGrabMssScaling(unittest.TestCase):
    def _monitor_for(self, platform, dpr):
        region = Region(10, 20, 100, 50, dpr)
        fake = _FakeSct()
        with mock.patch.object(capture.sys, "platform", platform), \
                mock.patch.object(capture.mss, "mss", return_value=fake):
            capture._grab_mss(region)
        return fake.last

    def test_non_darwin_scales_logical_to_physical(self):
        # Windows 150% scaling: logical coords must be multiplied by dpr.
        self.assertEqual(
            self._monitor_for("linux", 1.5),
            {"left": 15, "top": 30, "width": 150, "height": 75},
        )

    def test_windows_2x(self):
        self.assertEqual(
            self._monitor_for("win32", 2.0),
            {"left": 20, "top": 40, "width": 200, "height": 100},
        )

    def test_darwin_stays_1x(self):
        # macOS uses Quartz primarily; the mss fallback keeps point coords (no dpr).
        self.assertEqual(
            self._monitor_for("darwin", 2.0),
            {"left": 10, "top": 20, "width": 100, "height": 50},
        )


class TestConfigLoad(unittest.TestCase):
    def _load_with(self, data, platform):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps(data), "utf-8")
            with mock.patch.object(config, "CONFIG_PATH", path), \
                    mock.patch.object(config.sys, "platform", platform):
                return config.Config.load()

    def test_vision_engine_reset_off_darwin(self):
        cfg = self._load_with({"ocr_engine": "vision"}, "linux")
        self.assertEqual(cfg.ocr_engine, "auto")

    def test_vision_engine_kept_on_darwin(self):
        cfg = self._load_with({"ocr_engine": "vision"}, "darwin")
        self.assertEqual(cfg.ocr_engine, "vision")

    def test_unknown_keys_dropped(self):
        # stale region/deepl/live fields from an old config must not crash load.
        cfg = self._load_with({"region": {"x": 1}, "live_interval_ms": 9, "source": "ja"}, "darwin")
        self.assertEqual(cfg.source, "ja")


class TestOcrRouting(unittest.TestCase):
    def test_auto_off_darwin_never_uses_vision(self):
        # On non-darwin, 'auto' must route to RapidOCR — never silently to Vision
        # (which can't run there). Either it builds RapidOCR, or (if the dep is
        # absent, as on the macOS dev box) it raises a clear rapidocr error.
        with mock.patch.object(ocr.sys, "platform", "linux"):
            try:
                backend = ocr.make_ocr("auto", "en")
            except RuntimeError as exc:
                self.assertIn("rapidocr", str(exc).lower())
            else:
                self.assertNotEqual(backend.name, "vision")


if __name__ == "__main__":
    unittest.main()
