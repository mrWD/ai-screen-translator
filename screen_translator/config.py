"""Persistent settings: languages, hotkeys, OCR/translation engines.

Stored as JSON in the user config dir (NOT in the repo), so the app remembers the
language and other choices between runs.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_MOD = "<cmd>" if sys.platform == "darwin" else "<ctrl>"


def _config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "ai-screen-translator"


CONFIG_PATH = _config_dir() / "config.json"


def history_dir() -> Path:
    """Where translation history (JSONL + screenshots + index.html) is written."""
    return _config_dir() / "history"


@dataclass
class Region:
    x: int
    y: int
    w: int
    h: int
    dpr: float = 1.0  # devicePixelRatio at selection time (Retina = 2.0)


@dataclass
class Config:
    source: str = "en"
    target: str = "ru"
    ocr_engine: str = "auto"  # "auto" | "vision" | "rapidocr"
    ocr_fast: bool = True     # Apple Vision "fast" recognition (≈2x faster); off = "accurate"
    translate_engine: str = "offline"  # "offline" (default, on-device) | "google" (free, needs net)
    offline_model_dir: str = ""       # optional Argos package dir; "" = library default
    hotkey_hide: str = f"{_MOD}+<shift>+h"
    hotkey_hold: str = "<f6>"  # HOLD to show the full-screen translation; release hides it.
    # A single, modifier-free key works best for hold. Pair with suppress_hotkeys so
    # the key's own action (F6 = brightness/etc.) is swallowed while you hold it.
    suppress_hotkeys: bool = False  # swallow a single-key hotkey's normal action
    # (e.g. F6's default). Only single, modifier-free keys; macOS needs Accessibility.
    overlay_font_pt: int = 18
    overlay_opacity: float = 0.85
    save_history: bool = True
    save_screenshots: bool = True
    history_keep_sessions: int = 20
    accessory_mode: bool = True  # macOS: menu-bar-only (no Dock icon); needs relaunch.
    # Default on: an accessory app's overlay floats over other apps' native-fullscreen
    # Spaces (GeForce Now games) without switching Space — a Regular (Dock) app can't.

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()
        # Drop unknown keys so older/newer config files don't crash construction
        # (e.g. the removed region/live/deepl fields from an earlier version).
        known = set(cls.__dataclass_fields__)
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        if cfg.translate_engine not in ("google", "offline"):
            cfg.translate_engine = "offline"  # fall back to the default; e.g. a stale "deepl"
        if sys.platform != "darwin" and cfg.ocr_engine == "vision":
            # Apple Vision is macOS-only; a config.json carried from a Mac would
            # otherwise force an engine that can't build here. Let make_ocr route to
            # the cross-platform RapidOCR instead of erroring on startup.
            cfg.ocr_engine = "auto"
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8"
        )
