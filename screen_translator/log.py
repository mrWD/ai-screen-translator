"""App-wide logging to stderr + a rotating file in the config dir.

The GUI/hotkey/capture path can only be exercised by the user on the real machine,
so a persistent log is the main diagnostic channel: it shows every hotkey event,
gate transition, capture, job result and overlay action. The file lives next to the
config (macOS: ~/Library/Application Support/ai-screen-translator/app.log) so the
user can hand it over when something misbehaves.

Modules log via `logging.getLogger(__name__)`; everything propagates up to the
"screen_translator" logger configured here. Set ST_LOG=debug for verbose output
(e.g. every key event).
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_ROOT = "screen_translator"
_configured = False


def _log_dir() -> str:
    # Mirror config._config_dir() without importing it (avoid import cycles).
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, "ai-screen-translator")


def log_path() -> str:
    return os.path.join(_log_dir(), "app.log")


def setup() -> logging.Logger:
    """Configure the root app logger once. Safe to call repeatedly."""
    global _configured
    logger = logging.getLogger(_ROOT)
    if _configured:
        return logger
    level = logging.DEBUG if os.environ.get("ST_LOG", "").lower() == "debug" else logging.INFO
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        os.makedirs(_log_dir(), exist_ok=True)
        rotating = RotatingFileHandler(
            log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        logger.addHandler(rotating)
    except Exception:
        pass  # stderr logging still works even if the file can't be opened

    _configured = True
    logger.info("=== logging started (level=%s) -> %s", logging.getLevelName(level), log_path())
    return logger
