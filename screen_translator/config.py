"""Persistent settings: languages, capture region, hotkeys, OCR engine.

Stored as JSON in the user config dir (NOT in the repo), so the app remembers
the last region and language choice between runs.
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
    translate_engine: str = "google"  # "google" | "deepl" | "offline"
    deepl_api_key: str = ""           # required for the "deepl" engine
    offline_model_dir: str = ""       # optional Argos package dir; "" = library default
    region: Region | None = None
    hotkey_translate: str = f"{_MOD}+<shift>+t"
    hotkey_fullscreen: str = f"{_MOD}+<shift>+f"
    hotkey_reselect: str = f"{_MOD}+<shift>+r"
    hotkey_hide: str = f"{_MOD}+<shift>+h"
    hotkey_live: str = f"{_MOD}+<shift>+l"
    hotkey_hold: str = "<f8>"  # hold to show full-screen translation, release to hide
    live_interval_ms: int = 800
    overlay_font_pt: int = 18
    overlay_opacity: float = 0.85
    overlay_inplace: bool = False  # erase original + draw translation in place (full-screen)
    save_history: bool = True
    save_screenshots: bool = True
    history_keep_sessions: int = 20
    accessory_mode: bool = False  # macOS: menu-bar-only (no Dock icon); needs relaunch

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()
        region = data.pop("region", None)
        # Drop unknown keys so older/newer config files don't crash construction.
        known = {f for f in cls.__dataclass_fields__ if f != "region"}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        if region:
            region_fields = Region.__dataclass_fields__
            cfg.region = Region(**{k: v for k, v in region.items() if k in region_fields})
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8"
        )
