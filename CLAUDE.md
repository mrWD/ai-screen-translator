# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hotkey-driven screen OCR + translation overlay (games / films / anything on
screen): **hotkey → capture screen region → OCR → translate → overlay**, with
every result saved to disk for review/copy after the game closes. Python +
PySide6 menu-bar app. Developed and exercised only on **macOS (Apple Silicon,
Python 3.12)**; cross-platform is a design goal but not yet tested.

`README.md` (user-facing) and `HANDOFF.md` (deep design notes, gotchas, open
items) are kept current — read both before substantial work.

## Commands

```bash
./run.sh                                   # create .venv, install deps, launch the app. Do NOT sudo — it breaks macOS permission prompts.
./.venv/bin/python tools/smoke_test.py     # headless core check: real Vision OCR + Google translate, no GUI/permissions
./.venv/bin/python -m py_compile screen_translator/*.py   # fast syntax check
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "from screen_translator.app import App; App()"  # build widgets/menus/logic without a display
./.venv/bin/python -m screen_translator    # run directly (skips the venv-setup step in run.sh)
```

There is **no test suite, linter, or formatter** configured. Verification is:
`py_compile` → `smoke_test.py` → offscreen construction → the user runs `./run.sh`
for the interactive flow (region select, overlay over a game, real hotkeys). That
last step is the standing feedback loop — the GUI/capture/hotkey path can only be
validated by the user on the real machine.

To enable the Cyrillic/cross-platform OCR engine, uncomment `rapidocr-onnxruntime`
in `requirements.txt` and reinstall (see OCR routing below).

## Architecture

All code is in `screen_translator/`; entry point is `__main__.py` → `app.main()`.
The README "Project layout" section maps every file. The big-picture pieces that
span files:

**Orchestration & threading (`app.py`).** `App` wires hotkeys/menu to the
pipeline. OCR + network translation run **off the UI thread** as `QRunnable`s on
the global `QThreadPool`, reporting back via Qt `Signal`s:
- `_Job` — region/live single-region translate (with live-mode dedup).
- `_ScreenJob` — full-screen: OCR every block, translate each, map boxes to screen.

There is **one in-flight job at a time** (`self._busy`). A new trigger while busy
is dropped, *except* the hold key, which sets `_hold_pending` to retry when free.
Always keep a Python ref to the running `QRunnable` (`self._current_job`) — the
pool only stores a C++ pointer, so a GC'd wrapper tears down its C++ object. The
same applies to menus/action-groups (see `_rebuild_menu`'s explicit ref lists).

**Capture coordinate self-calibration — the load-bearing invariant.** Capture
takes **logical (Qt) point** coordinates. On macOS, `capture.grab` uses Quartz
(`CGDisplayCreateImageForRect`) returning **native Retina pixels** (~2×); other
platforms fall back to `mss` (1× on macOS, hence worse OCR). **Never assume a
fixed devicePixelRatio when mapping OCR pixel coords back to the screen.** Derive
it from the actual returned image size vs. the logical region:
`scale = captured_image_size / logical_region_size` (see `_ScreenJob.run`).
Assuming dpr=2 once put translations at half-height. `_display_id_for_region`
picks the display under the region; `CGDisplayCreateImageForRect`'s rect is in
that display's **local** space, so `_grab_quartz` subtracts the display's bounds
origin (no-op on the main display at (0,0), correct on secondary ones). **Any
CoreGraphics display call aborts (`CGS_REQUIRE_INIT`) in a bare non-GUI shell**,
so don't call `capture.grab` in headless checks; it only works inside the running
app (or a real GUI session).

**Translation routing (`translate.py`).** Mirrors the OCR plugin: backends behind
`make_translator(engine, ...)` — `GoogleFreeBackend` (free, default, no key),
`DeepLBackend` (needs `deepl_api_key`), `ArgosBackend` (offline, optional dep).
The base class centralizes the empty-text guard, the (source,target,text) cache,
and the `TranslateError` contract. An **explicit** engine is built as-is so its
failure surfaces (e.g. `deepl` with no key errors instead of silently using
Google); `auto` skips DeepL when there's no key. Like OCR, the backend is built
lazily on the **UI thread** (`_get_translator`), reset to `None` on engine/key
change, and the **instance is passed into the worker job** — never call a module
global from the `QRunnable`. Unlike OCR it is **not** reset on a source change:
its cache is keyed by `(source, target, text)`, so one instance serves every
source. The module-level `translate()` is kept only for `smoke_test.py`.

**OCR routing (`ocr.py`).** Pluggable backends behind `make_ocr(engine, source)`:
`VisionOCR` (Apple Vision via `ocrmac`, macOS, default) and `RapidOCRBackend`
(cross-platform ONNX, optional dep). Vision **cannot read Cyrillic** — `make_ocr`
routes a Cyrillic source to RapidOCR and raises a clear "install rapidocr" error
rather than ever silently falling back to Vision (which would return garbage).
Vision bboxes are normalized 0–1, **origin bottom-left** → flip Y with
`(1 - y - h) * H`. The OCR backend is lazily built and reset to `None` whenever
the source language or engine changes, forcing a rebuild.

**Hotkeys (`hotkeys.py`).** Exactly **one** pynput `Listener` drives both chord
hotkeys (pynput `HotKey` objects) and the hold key. Two listeners segfault macOS
(an `AXIsProcessTrusted` lazy-import race) — do not add a second. `hotkey_edit.py`
records a Qt keypress and converts it to the pynput string format used in config.

**Live mode (`app.py` + `changes.py`).** A `QTimer` re-captures the saved region;
`changes.signature`/`changes.changed` skip OCR on frozen frames, and `_Job` dedup
skips re-translating when the OCR text is unchanged — so an animated background
doesn't cause constant re-translation.

**Overlays.** `overlay.py` (region panel, anchored *beside* the region so it's
never re-captured) and `screen_overlay.py` (full-screen). Both are click-through
(`Qt.WindowTransparentForInput`), so overlay text is **deliberately not
selectable** — selection/copy is delivered via the history `index.html` and "Copy
last result", not the overlay. `screen_overlay` has two modes: the default
translucent boxes (grow-to-fit + de-overlap) and opt-in **in-place** mode
(`overlay_inplace`) that paints an opaque, colour-sampled fill *over* the original
text and draws the translation in place, boxes anchored on the original so the
erase aligns. The fill/text colours are sampled from the captured PIL image in
`_ScreenJob` **on the worker thread** and passed through as plain `(r,g,b)` tuples
(`_ScreenJobSignals.done` carries 5-tuples) — `QColor` is constructed only in the
overlay's paint on the UI thread (never build `QtGui` objects off the UI thread).

**Config & history.** `config.py` persists a `Config` dataclass as JSON to the OS
config dir (macOS: `~/Library/Application Support/ai-screen-translator/`), **not**
the repo; `Config.load` drops unknown keys (and tolerates missing ones), so
version skew never crashes startup — new config fields need no migration code.
`history.py` writes one `session.jsonl` per run (+ optional screenshots) and
renders an `index.html` of original|translation pairs that works with the app
closed. (Watch `HistoryWriter.add`: the JSONL write must stay in `add()`, not
drift below the `return` in `_downscaled` — that exact bug silently disabled the
log once.)

## Platform notes that constrain design

- **GeForce Now works** (composited window); overlays float over native-fullscreen
  Spaces via NSWindow `collectionBehavior` tweaks in `macos.py`.
- **DRM video / true exclusive-fullscreen** capture as a black frame by design
  (unfixable in software). `capture.is_black` detects this and the app tells the
  user to switch the game to Borderless Windowed.
- Multi-display: the Quartz path captures from the **display under the region**
  (`_display_id_for_region`); a region straddling two displays only yields the
  chosen display's portion.
- **Accessory mode** (`accessory_mode`, opt-in, default off): sets
  `NSApplicationActivationPolicyAccessory` (no Dock icon / app menu — tray only) in
  `macos.py`. Accessory apps don't get keyboard focus for a frameless overlay, so
  `_selector_focus_acquire/release` briefly restore Regular policy + `activate_app()`
  around region selection (restored on **both** the selected and cancelled paths).
  Applied at launch; toggling asks for a relaunch.
