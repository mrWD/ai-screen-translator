"""CLI: install the offline (Argos) language pack for the configured languages.

`offline` is the default translate engine, so the setup scripts run this once
after installing dependencies, giving the user a working on-device translator out
of the box instead of a "download model" round-trip on first launch.

    python -m screen_translator.download_offline [SRC TGT]

With no arguments it uses the saved config (or the en→ru defaults). It is
best-effort: progress is printed, it exits 0 on success and non-zero on failure,
so the setup script can warn without aborting the whole install (the user can
still download later from Settings → Offline model).
"""

from __future__ import annotations

import sys

from .config import Config
from .offline_models import download_model


def _force_utf8_output() -> None:
    """Make stdout/stderr UTF-8 so the progress messages (which contain '→', '…',
    '✓') don't crash with a cp1252 'charmap' UnicodeEncodeError when the setup
    script redirects output to a file on Windows."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 1:
        print("usage: python -m screen_translator.download_offline [SRC TGT]", flush=True)
        return 2
    if len(argv) >= 2:
        src, tgt, model_dir = argv[0], argv[1], ""
    else:
        cfg = Config.load()
        src, tgt, model_dir = cfg.source, cfg.target, cfg.offline_model_dir

    print(f"Offline translation model: {src} -> {tgt}", flush=True)
    try:
        download_model(src, tgt, model_dir, log=lambda m: print(m, flush=True))
    except Exception as exc:  # noqa: BLE001 — surface any failure as a non-zero exit
        print(f"ERROR: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
