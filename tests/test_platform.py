"""Cross-platform behavior that only triggers off macOS.

These run on the macOS dev box by faking `sys.platform`, so the Windows/Linux code
paths (RapidOCR routing, logical->physical capture scaling, config engine reset)
are exercised without that hardware. Stdlib + mocks only — no real capture/OCR.
"""

import json
import stat
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

    def test_offline_model_dir_rejects_traversal(self):
        cfg = self._load_with({"offline_model_dir": "/tmp/models/../../etc"}, "darwin")
        self.assertEqual(cfg.offline_model_dir, "")

    def test_offline_model_dir_rejects_relative(self):
        cfg = self._load_with({"offline_model_dir": "models"}, "darwin")
        self.assertEqual(cfg.offline_model_dir, "")

    def test_offline_model_dir_keeps_clean_absolute(self):
        cfg = self._load_with({"offline_model_dir": "/opt/argos-models"}, "darwin")
        self.assertEqual(cfg.offline_model_dir, "/opt/argos-models")


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


@unittest.skipIf(sys.platform == "win32", "POSIX permission semantics")
class TestSecurePermissions(unittest.TestCase):
    def test_secure_dir_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "x" / "y"
            config.secure_dir(sub)
            self.assertTrue(sub.is_dir())
            self.assertEqual(stat.S_IMODE(sub.stat().st_mode), 0o700)

    def test_restrict_file_is_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "f.txt"
            f.write_text("secret")
            config.restrict_file(f)
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)

    def test_history_files_owner_only(self):
        from screen_translator.history import HistoryWriter

        with tempfile.TemporaryDirectory() as d:
            w = HistoryWriter(keep_sessions=5, save_screenshots=False)
            w._root = Path(d) / "history"  # redirect off the real config dir
            w.add([("hello", "привет")], None, "en", "ru", "offline", "fullscreen")
            sess = w.session_dir
            self.assertEqual(stat.S_IMODE(sess.stat().st_mode), 0o700)
            jsonl = sess / "session.jsonl"
            self.assertTrue(jsonl.exists())
            self.assertEqual(stat.S_IMODE(jsonl.stat().st_mode), 0o600)


@unittest.skipIf(sys.platform == "win32", "symlink semantics")
class TestPruneSymlinkSafety(unittest.TestCase):
    def test_prune_ignores_symlinked_session_and_spares_target(self):
        from screen_translator.history import HistoryWriter

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "history"
            root.mkdir()
            victim = Path(d) / "victim"
            victim.mkdir()
            (victim / "important.txt").write_text("keep me")
            # a real session dir we created
            (root / "2026-06-23_10-00-00" / "shots").mkdir(parents=True)
            # a planted symlink with an early-sorting (would-be-pruned-first) name
            (root / "2000-01-01_00-00-00").symlink_to(victim, target_is_directory=True)

            w = HistoryWriter(keep_sessions=1)
            w._root = root
            w._prune_old_sessions()

            # the symlink is never treated as a prunable session, so its target's
            # contents survive (the old code would have _rmtree'd through it).
            self.assertTrue((victim / "important.txt").exists())


if __name__ == "__main__":
    unittest.main()
