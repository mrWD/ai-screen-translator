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
./.venv/bin/python -m unittest discover -s tests -t .   # unit tests for pipeline.py + gating.py (stdlib only)
./.venv/bin/python -m py_compile screen_translator/*.py   # fast syntax check
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "from screen_translator.app import App; App()"  # build widgets/menus/logic without a display
./.venv/bin/python -m screen_translator    # run directly (skips the venv-setup step in run.sh)
```

There is **no linter or formatter** configured, and no GUI test harness. The pure
logic (`pipeline.py`, `gating.py`) has `unittest` coverage under `tests/`;
everything Qt/capture/hotkey-bound is still verified by:
`py_compile` → `unittest` → `smoke_test.py` → offscreen construction → the user
runs `./run.sh` for the interactive flow (region select, overlay over a game, real
hotkeys). That last step is the standing feedback loop — the GUI/capture/hotkey path can only be
validated by the user on the real machine.

To enable the Cyrillic/cross-platform OCR engine, uncomment `rapidocr-onnxruntime`
in `requirements.txt` and reinstall (see OCR routing below).

## Architecture

All code is in `screen_translator/`; entry point is `__main__.py` → `app.main()`.
The README "Project layout" section maps every file. The big-picture pieces that
span files:

**Orchestration & threading.** `App` (`app.py`) is the UI shell + wiring: it
connects hotkeys/menu to the pipeline. The work is split across modules so the
non-Qt logic is testable:
- `jobs.py` — `Job` (region/live single-region translate, with live-mode dedup)
  and `ScreenJob` (full-screen: OCR every block, translate each, map boxes to
  screen). These `QRunnable`s run **off the UI thread** on the global
  `QThreadPool`, reporting back via Qt `Signal`s. `ScreenJob` fans its per-block
  translate calls out concurrently (bounded `ThreadPoolExecutor`); the translator
  cache is lock-guarded and a backend opts out via `parallel_safe = False` (Argos).
- `pipeline.py` — **pure, Qt-free** functions used by the jobs: `compute_scale`,
  `map_block`, `is_junk_block`, `dedup_outcome`, `sample_block_colors`. Unit-tested.
- `gating.py` — `BusyGate`, the single-in-flight-job + hold-retry **state machine**
  (no Qt). Unit-tested.

There is **one in-flight job at a time** (`App.gate.busy`). A new trigger while
busy is dropped, *except* the hold key, which `BusyGate` remembers (`hold_pending`)
and replays when free — only if still held. Always keep a Python ref to the running
`QRunnable` (`self._current_job`) — the pool only stores a C++ pointer, so a GC'd
wrapper tears down its C++ object. The same applies to menus/action-groups (see
`_rebuild_menu`'s explicit ref lists).

**Full-screen hold latency.** Offline (Argos) full-screen is slow — ~76 ms/block
on-device, serial, so a text-heavy screen (50+ blocks) takes several seconds.
`ScreenJob._translate_all` sends the whole batch to a `parallel_safe=False` backend
in ONE call (`translate_batch`, one subprocess round-trip instead of N); network
backends still fan out across `_MAX_TRANSLATE_WORKERS`. Because the result often
arrives *after* the user lets go of the hold key, `_on_screen_done` no longer
discards a released-hold result — it shows it and auto-hides after `_HOLD_LINGER_MS`
(`_fs_linger`), cancelled by `_on_hold_end`/`_hide_overlays`/a new hold.

**Perceived latency.** OCR + translate run off-thread, so on an *explicit* press
the app shows a `⏳ Translating…` placeholder immediately (`_show_loading` /
`_clear_loading`, tracked by `_loading`) — region in the panel, full-screen near
the cursor — replaced in place by the result (`_on_job_done`) or hidden on
failure/hold-release. Live ticks never show it. The offline (Argos) backend is
**pre-warmed** in the background (`_warm_translator`, at launch and on engine
switch) so its slow first call — subprocess spawn + model/torch load — happens
before the user's first real translate, not during it.

**Capture coordinate self-calibration — the load-bearing invariant.** Capture
takes **logical (Qt) point** coordinates. On macOS, `capture.grab` uses Quartz
(`CGDisplayCreateImageForRect`) returning **native Retina pixels** (~2×); other
platforms fall back to `mss` (1× on macOS, hence worse OCR). **Never assume a
fixed devicePixelRatio when mapping OCR pixel coords back to the screen.** Derive
it from the actual returned image size vs. the logical region:
`scale = captured_image_size / logical_region_size` (see `pipeline.compute_scale`
/ `pipeline.map_block`, called from `jobs.ScreenJob.run`).
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
**Argos runs in a subprocess (`argos_proc.py`), not in-process.** argostranslate
pulls in stanza → PyTorch, and torch segfaults when its GIL is acquired from a Qt
`QThreadPool` worker thread (`take_gil` ← `gil_scoped_acquire`) — and translation
*always* runs on a worker thread. So `ArgosBackend` never imports argostranslate;
it spawns `python -m screen_translator.argos_proc` and talks newline-delimited
JSON over stdin/stdout (lock-serialized; the child persists and exits on stdin
EOF). Do not move this back in-process. The Settings button that installs
argostranslate + the language pack lives in `offline_models.py` (`plan_packages`
pivots through English when there's no direct pack; the download runs on a
`QRunnable`).

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
**Full-screen is hold-to-show only**: `hotkey_hold` (default `<f6>`) shows the
full-screen overlay while held and hides on release (`_on_hold_start`/`_on_hold_end`).
There is **no** persistent full-screen hotkey (`hotkey_fullscreen` was removed;
`Config.load` drops the stale key from old config files). The menu "Translate full
screen" still calls `translate_fullscreen(is_hold=False)` for a deliberate one-off
persistent capture. A hotkey field can be cleared (Backspace in `hotkey_edit.py`);
`start()` skips unparseable/empty specs.
**Callbacks must never raise:** pynput's `_emitter` *stops the whole listener* on
any unhandled exception in `on_press`/intercept (then no hotkey works), so every
handler swallows its own errors. **Event-tap watchdog:** macOS disables a tap whose
process was slow (`kCGEventTapDisabledByTimeout`) and pynput NEVER re-enables it
(it calls `CGEventTapEnable` once at startup) — so after one heavy translate the
hotkeys go dead. `_install_tap_capture_patch` stashes the tap on the listener and a
`QTimer` (`_check_tap`, plus the disable-event path in `_darwin_intercept`)
re-enables it. **Logging** (`log.py`, file at `<config dir>/app.log`, `ST_LOG=debug`
for verbose) traces hotkey fires, hold up/down, gate transitions, capture, job
results and the overlay float tweak — the main remote-diagnosis channel. **Accessibility:** the event tap only gets
hardware key events when the process is trusted, and a tap created while the app
runs won't receive events until relaunch — `App._check_accessibility()` prompts
(`macos.accessibility_trusted(prompt=True)`) and notifies, so "hotkeys do nothing"
is surfaced instead of silent.
**macOS 26 TSM crash workaround:** pynput resolves the keyboard layout via Carbon
TSM/TIS APIs inside `Listener._run` — on its background thread — and recent macOS
hard-asserts those are main-thread-only (`dispatch_assert_queue` → SIGTRAP in
`islGetInputSourceListWithAdditions`). `_patch_darwin_keycode_context()` resolves
the layout once on the main thread (in `start()`) and monkeypatches
`pynput.keyboard._darwin.keycode_context` to yield that cached value, so the
listener thread only ever calls the thread-safe `UCKeyTranslate`. Keep this; the
active event tap (suppression) reliably trips the crash without it.
**Optional suppression** (`config.suppress_hotkeys`, default off) swallows a
single-key hotkey's normal OS/app action (e.g. F1=Help) via the SAME listener's
per-platform hooks — never a second listener. macOS `darwin_intercept`: pynput
calls `on_press` first (sets `_suppress_current`), then the intercept returns
`None` to drop the event (needs Accessibility; the active tap; F1/F2/media keys
arrive as system events it can't catch). Windows `win32_event_filter`: runs
*before* `on_press` and suppressing skips it, so the filter dispatches the action
itself (off `data.vkCode`, with auto-repeat de-dup) then returns `False`. Only
single, modifier-free keys qualify (`_build_suppress_tables`); chords pass through.

**Live mode (`app.py` + `changes.py`).** A `QTimer` re-captures the saved region;
`changes.signature`/`changes.changed` skip OCR on frozen frames, and `Job`'s dedup
(`pipeline.dedup_outcome`) skips re-translating when the OCR text is unchanged — so
an animated background doesn't cause constant re-translation.

**Overlays.** `overlay.py` (region panel, anchored *beside* the region so it's
never re-captured) and `screen_overlay.py` (full-screen). Both are click-through
(`Qt.WindowTransparentForInput`), so overlay text is **deliberately not
selectable** — selection/copy is delivered via the history `index.html` and "Copy
last result", not the overlay. `screen_overlay` has two modes: the default
translucent boxes (grow-to-fit + de-overlap) and opt-in **in-place** mode
(`overlay_inplace`) that paints an opaque, colour-sampled fill *over* the original
text and draws the translation in place, boxes anchored on the original so the
erase aligns. The fill/text colours are sampled from the captured PIL image by
`pipeline.sample_block_colors`, called in `ScreenJob` **on the worker thread**, and
passed through as plain `(r,g,b)` tuples (`ScreenJob`'s `done` signal carries
5-tuples) — `QColor` is constructed only in the overlay's paint on the UI thread
(never build `QtGui` objects off the UI thread).

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
  Spaces via `macos.make_overlay_join_all_spaces`. The full recipe on the NSPanel
  behind each overlay (`Qt.Tool` → `QNSPanel`):
  - `collectionBehavior = CanJoinAllSpaces|FullScreenAuxiliary|Stationary` — appears
    on every Space, including the game's fullscreen one.
  - **NON-ACTIVATING panel** (`NSWindowStyleMaskNonactivatingPanel` + `setFloatingPanel_`
    + `setBecomesKeyOnlyIfNeeded_`) — **the load-bearing fix**: without it, ordering
    the overlay front *activates our app*, and macOS switches to our app's Space
    (Desktop) instead of drawing over the game. Verify: `window.isKeyWindow()` is
    False right after show.
  - `setHidesOnDeactivate_(False)` — NSPanel utility panels otherwise hide whenever
    another app (the game) is active.
  - `setLevel_(NSScreenSaverWindowLevel)` set **LAST** — `setFloatingPanel_` resets
    the level to `NSFloatingWindowLevel` (3), which isn't above a fullscreen game.
  Applied at overlay **construction**, **before** every `show()`, and after it
  (before-show so `show()` doesn't bind the window to the Desktop Space first; Qt's
  per-show setup can also drop the tweaks). `accessory_mode` (default ON) further
  keeps the app out of the Dock so it has no "home" Space to pull forward.
- **DRM video / true exclusive-fullscreen** capture as a black frame by design
  (unfixable in software). `capture.is_black` detects this and the app tells the
  user to switch the game to Borderless Windowed.
- Multi-display: the Quartz path captures from the **display under the region**
  (`_display_id_for_region`); a region straddling two displays only yields the
  chosen display's portion.
- **Accessory mode** (`accessory_mode`, **default ON**): sets
  `NSApplicationActivationPolicyAccessory` (no Dock icon / app menu — tray only) in
  `macos.py`. Default on so the full-screen overlay floats over other apps'
  fullscreen Spaces (a Regular/Dock app switches Spaces on window-order-front).
  Accessory apps don't get keyboard focus for a frameless overlay, so
  `_selector_focus_acquire/release` briefly restore Regular policy + `activate_app()`
  around region selection (restored on **both** the selected and cancelled paths).
  Applied at launch; toggling asks for a relaunch.
