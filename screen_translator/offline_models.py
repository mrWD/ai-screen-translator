"""Offline (Argos Translate) model management.

The "offline" engine needs two things the app doesn't ship: the `argostranslate`
package itself, and a downloaded language pack for the chosen source→target pair.
This module installs both on demand so the user gets a one-click "Download offline
model" button in Settings instead of dropping to a terminal.

Kept Qt-free: the work (a pip subprocess + a network download) runs on a worker
thread and reports progress through a plain `log(str)` callback, so the modal
settings dialog never blocks. `plan_packages` is pure and unit-tested.

Argos ships English-centric packs (xx→en and en→xx). There is no direct pack for,
say, ja→ru, but Argos pivots through English at translate time when both ja→en and
en→ru are installed — so `plan_packages` expands a non-English pair into that pair
of packs (mirroring `ArgosBackend._translate`'s `source.split("-")[0]` code form).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Callable, Iterable

Logger = Callable[[str], None]


def _base(code: str) -> str:
    return code.split("-")[0]  # Argos uses bare ISO codes: "zh-CN" -> "zh"


def plan_packages(
    available_pairs: Iterable[tuple[str, str]], src: str, tgt: str
) -> list[tuple[str, str]]:
    """Which Argos packs to install to translate src→tgt, given what's available.

    Returns one pack for a direct pair, or two (src→en, en→tgt) for Argos's
    English pivot. Raises RuntimeError if no route exists. Pure — unit-tested.
    """
    src, tgt = _base(src), _base(tgt)
    if src == "auto":
        raise RuntimeError("Offline translation needs an explicit source language.")
    if src == tgt:
        raise RuntimeError("Source and target languages are the same.")
    avail = set(available_pairs)
    if (src, tgt) in avail:
        return [(src, tgt)]
    if src != "en" and tgt != "en" and (src, "en") in avail and ("en", tgt) in avail:
        return [(src, "en"), ("en", tgt)]  # pivot through English
    raise RuntimeError(f"No offline (Argos) language pack available for {src}→{tgt}.")


def argos_available() -> bool:
    try:
        import argostranslate.package  # noqa: F401
        import argostranslate.translate  # noqa: F401
    except ImportError:
        return False
    return True


def ensure_argos(log: Logger) -> None:
    """Make `argostranslate` importable, pip-installing it into the running
    interpreter's environment if it's missing. Raises RuntimeError on failure."""
    if argos_available():
        return
    log("Installing argostranslate (one-time, this can take a minute)…")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "argostranslate"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            "Couldn't install argostranslate automatically — run "
            "`pip install argostranslate` in the app's .venv.\n" + tail[-400:]
        )
    importlib.invalidate_caches()  # let the just-installed package be importable now
    if not argos_available():
        raise RuntimeError(
            "argostranslate installed but not importable yet — restart the app, "
            "then download again."
        )


def download_model(
    src: str, tgt: str, model_dir: str = "", log: Logger = lambda _m: None
) -> None:
    """Install the Argos pack(s) needed for src→tgt, installing argostranslate
    first if missing. `model_dir` (if set) is honoured as the install location,
    matching `ArgosBackend`. Progress is reported via `log`."""
    if model_dir:
        import os

        os.environ.setdefault("ARGOS_PACKAGES_DIR", model_dir)
    ensure_argos(log)

    import argostranslate.package as pkg

    log("Fetching the Argos package index…")
    pkg.update_package_index()
    by_pair = {(p.from_code, p.to_code): p for p in pkg.get_available_packages()}
    wanted = plan_packages(by_pair.keys(), src, tgt)
    installed = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}

    for pair in wanted:
        if pair in installed:
            log(f"Already installed: {pair[0]}→{pair[1]}.")
            continue
        log(f"Downloading {pair[0]}→{pair[1]}…")
        path = by_pair[pair].download()
        log(f"Installing {pair[0]}→{pair[1]}…")
        pkg.install_from_path(path)

    # Verify every wanted pack actually landed. A pivot pair (src→en, en→tgt) is
    # installed in two steps; a mid-way failure would otherwise leave the route
    # half-built and translation silently broken. Raising here keeps the caller's
    # non-zero exit / error reporting honest.
    have = {(p.from_code, p.to_code) for p in pkg.get_installed_packages()}
    missing = [p for p in wanted if p not in have]
    if missing:
        raise RuntimeError(
            "Offline model install incomplete: "
            + ", ".join(f"{a}→{b}" for a, b in missing)
        )
    log("✓ Offline model ready.")
