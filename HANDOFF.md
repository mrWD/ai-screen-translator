# AI Screen Translator — Handoff / Project State

Self-contained context so a fresh chat (with no memory of the prior conversation)
can continue this project. Read this + `README.md` + the code, then continue.

---

## What it is

A hotkey-driven screen OCR + translation overlay for games / films / anything
on screen. User **holds a key** → the whole screen is OCR'd, translated, and each
translation drawn in a box over the original, **while held**. Everything is also
**saved to disk** so the user can review (and select/copy) original + translation
**after closing the game**.

Use case (the bar to meet):
1. User plays a game / watches a film.
2. Sees foreign text, holds the full-screen key (default F6).
3. Translations appear over the foreign text while held; release to hide.
4. The screen + text is saved to disk, openable AFTER closing the game.
5. User can see ORIGINAL + TRANSLATION and SELECT/COPY the text.

Translation is pluggable — free Google (no API key) is the default, with an
optional offline Argos backend. (Region/part-of-screen + live modes were removed;
full-screen hold is the only translate mode now.)

---

## Where it lives & how to run

- Project root: `/Users/viktor/Projects/ai-screen-translator` (this folder).
- Run: `./run.sh` (creates `.venv`, installs `requirements.txt`, launches the
  menu-bar app). **Do NOT use `sudo`** — it breaks macOS permission prompts.
- Headless core check: `./.venv/bin/python tools/smoke_test.py`
- macOS permissions required for the launching Terminal/iTerm:
  - **Screen Recording** (capture) and **Accessibility** (global hotkeys).
  - Settings → Privacy & Security → … ; relaunch after granting.

Platform: developed/tested on **macOS (Apple Silicon, Python 3.12)** — the exercised
target. **Windows/Linux** are wired end-to-end (RapidOCR auto-installs instead of
Vision, mss instead of Quartz with logical→physical dpr scaling, X11 hotkeys, a
`run.bat` launcher) and pass the simulated-non-darwin tests, but still need real-
hardware validation — see README "Quick start (Windows / Linux)" and the
cross-platform gotchas below.

---

## Features & hotkeys (current defaults)

| Hotkey | Action |
|---|---|
| `F6` (hold) | Translate the whole screen **while held**, hide on release |
| `Cmd+Shift+H` | Hide the overlay |

- **Menu**: a `Translator` menu (top-left menu bar) + tray icon `文`. Items:
  Translate full screen / Hide overlay / Copy last result / Open translation log /
  Open history folder / Source & Target language / Settings / Quit.
- **Settings dialog**: languages (25), Fast OCR, translation engine + offline-model
  download, overlay font/opacity, save_history/save_screenshots, Suppress, and
  **click-to-record hotkeys** for the hold + hide keys (single keys like F6 work,
  chords too). Some config fields are deliberately NOT exposed (kept at safe
  defaults): `ocr_engine` (auto routes correctly), `accessory_mode` (always on — a
  footgun as a toggle). The region/live modes, `overlay_inplace`, and the DeepL
  backend were removed.
- **History**: every capture → `~/Library/Application Support/ai-screen-translator/history/<session>/session.jsonl` (+ downscaled screenshot PNG). "Open translation log" builds `index.html` (original|translation pairs, selectable/copyable, works with the app closed).

---

## Architecture (file map, all under `screen_translator/`)

- `app.py` — tray/menu UI shell + wiring. Hold-mode handlers, history persistence,
  settings apply, hotkey setup, menu building. Delegates the heavy lifting:
  - `jobs.py` — `ScreenJob` (full-screen) runs OCR+translate off the UI thread and
    emits results via signals. It fans per-block translate calls out concurrently
    (bounded executor; lock-guarded cache), or one batched call for Argos.
  - `pipeline.py` — pure, Qt-free logic the job calls (scale/box mapping, junk
    filter). Unit-tested in `tests/`.
  - `gating.py` — `BusyGate`, the single-in-flight (`gate.busy`) + hold-retry state
    machine. Unit-tested in `tests/`.
- `capture.py` — screen capture. **macOS: native Quartz** `CGDisplayCreateImageForRect`
  (full Retina res). Else mss. Logical-coords in; returns RGB PIL image. `is_black()`.
- `ocr.py` — pluggable OCR. `VisionOCR` (Apple Vision via ocrmac) + `RapidOCRBackend`.
  `recognize()` → text; `recognize_blocks()` → `Block(text,x,y,w,h)` in image pixels.
- `translate.py` — pluggable: `GoogleFreeBackend` / `ArgosBackend`
  behind `make_translator`, mirroring `ocr.make_ocr`. Per-backend (source,target,text)
  cache + uniform `TranslateError`. Module-level `translate()` kept for the smoke test.
- `overlay.py` — the small "⏳ Translating…" indicator panel (click-through),
  shown near the cursor while a full-screen translate runs.
- `screen_overlay.py` — full-screen click-through overlay; translucent boxes
  grow-to-fit + de-overlap, drawn over the text.
- `hotkeys.py` — **single** pynput Listener driving chord HotKeys + hold key.
- `hotkey_edit.py` — click-to-record hotkey field; Qt key → pynput string.
- `history.py` — JSONL + PNG persistence + `index.html` renderer.
- `settings_dialog.py`, `languages.py`, `config.py`, `macos.py` (NSWindow
  float-over-fullscreen tweaks + activation-policy / accessory helpers).
- `tests/` — `unittest` coverage for `pipeline.py` + `gating.py` (stdlib only).
- `tools/smoke_test.py` — headless OCR+translate verification.

---

## Key decisions & gotchas (the non-obvious stuff)

- **mss captures at 1× (logical) on macOS** → soft text → garbled OCR. We switched
  to **Quartz** for native 2× Retina pixels (much better OCR). Fallback to mss off-mac.
- **NEVER assume devicePixelRatio when mapping OCR coords.** Self-calibrate:
  `scale = captured_image_size / logical_screen_size`. (Assuming dpr=2 once caused
  translations to land at half-height.) `pipeline.compute_scale`/`map_block` do
  this, called from `jobs.ScreenJob`.
- **Windows/Linux capture must scale logical→physical (`_grab_mss`).** mss wants
  **physical** pixels there, but the region is in logical Qt points — so multiply by
  `region.dpr` (`scale = 1.0 if darwin else region.dpr`). Without it, a display scaled
  >100% (125/150/200%, common on Windows) grabs a wrong/truncated area. macOS keeps
  mss at 1× (it's just the Quartz fallback). Cross-platform invariant.
- **Off-macOS OCR is RapidOCR, not Vision.** `rapidocr-onnxruntime` auto-installs via a
  `; sys_platform != "darwin"` marker; `make_ocr` skips `vision` on non-darwin; and
  `Config.load` coerces a macOS-origin `ocr_engine="vision"` back to `"auto"` so a
  carried-over config.json still launches. These are the cross-platform launch invariants.
- **Apple Vision support is level-dependent.** `fast` (default) reads ~30 scripts
  incl. Cyrillic/CJK/Arabic/Thai; `accurate` only six Latin langs. `VisionOCR` queries
  the supported set and only passes a `language_preference` hint that's in it, so
  `accurate` + a non-Latin source degrades gracefully instead of `ocrmac` raising. A
  `languages` entry with `vision_code=None` would route to RapidOCR as a source.
- Vision bbox is normalized 0-1, **origin bottom-left** → flip Y: `(1-y-h)*H`.
- **Two pynput listeners crash macOS** (segfault + `AXIsProcessTrusted` lazy-import
  race). Use ONE Listener for both chords (pynput `HotKey` objects) and hold
  (vk/char normalization via `_norm`). `_prewarm_trust()` resolves the trust
  constant on the main thread first.
- **Single in-flight job** (`_busy`) serializes OCR/translate; hold-while-busy queues
  a retry (`_hold_pending`) instead of being dropped.
- **Hold mode** doesn't persist to history (avoids flooding with big 2× PNGs) and is
  suppressed if the key is released before the async result arrives.
- **Overlay click-through** (`Qt.WindowTransparentForInput`) means overlay text is NOT
  selectable — selection/copy is delivered via the history `index.html` (and "Copy
  last result"). This is intentional, not a bug.
- **macOS NSWindow collectionBehavior** (`macos.py`) lets overlays float over
  fullscreen Spaces — needed for GeForce Now in macOS fullscreen. The fix that made
  it actually appear over another app's fullscreen Space (not just on Desktop) was
  `setHidesOnDeactivate_(False)`: `Qt.Tool` = NSPanel utility panel, which hides when
  another app is active (the GFN game owns the foreground there). Re-applied on every
  show. **Verify live** with a GFN game in a fullscreen Space: hold F6, the
  translation must appear over the game, not only on the Desktop Space.
- **GeForce Now works** (it's a composited window). **DRM video = black frame** by
  design (unfixable). **True exclusive-fullscreen games** bypass the overlay → tell
  the user to switch to Borderless. The app detects all-black and messages it.
- **Accessory mode** (`accessory_mode`, **default ON**, no UI toggle) sets
  `NSApplicationActivationPolicyAccessory` (no Dock icon / app menu — tray only).
  Default on because a **Regular (Dock) app switches Spaces** when it orders a window
  front, so the full-screen overlay would appear on the Desktop Space instead of over
  a GeForce Now game's fullscreen Space — an accessory app floats over it in place.
  Applied at launch.
- **History JSONL had silently broken**: the `session.jsonl` write had drifted into
  dead code after a `return` in `HistoryWriter._downscaled`, so nothing was logged
  (screenshots saved, but "Open translation log" was always empty). Fixed — the write
  is back in `add()`.
- **Full-screen overlay**: `ScreenJob`'s `done` signal carries
  `(rect, orig, translated)` 3-tuples; `screen_overlay.show_blocks` takes
  `(rect, text)` and draws translucent boxes (grow-to-fit + de-overlap). `QColor` is
  built only in the overlay's paint on the UI thread (never off it). (The old in-place
  fill mode + `pipeline.sample_block_colors` were removed.)
- **Translation backend mirrors OCR**: built lazily on the UI thread
  (`_get_translator`), reset to None on engine/model-dir change, and the instance is
  passed into the worker job — never call a module global from the worker. NOT reset on a
  source change (its cache is keyed by source, so one instance serves all). Engines:
  free Google (default) and offline Argos. On macOS, `capture._grab_quartz` subtracts
  the chosen display's bounds origin because `CGDisplayCreateImageForRect`'s rect is
  display-LOCAL, not global.

---

## Known open items / next steps

**Implemented since the last handoff — need live confirmation on the real machine:**
- **Multi-display capture** — `capture._display_id_for_region` picks the display under
  the cursor (was `CGMainDisplayID()` only). Verify on a real two-display rig.
- **Accessory mode** — **default ON**, no UI toggle. App is always menu-bar-only.
- **Offline translation** — Argos (needs an Argos pack + an explicit source).
  **Settings → Offline model → Download** (`offline_models.download_model`, run on a
  `QRunnable` so the modal dialog stays responsive) pip-installs argostranslate if
  missing and fetches the pack — direct if Argos has one, else pivoting through
  English (`offline_models.plan_packages`, unit-tested in
  `tests/test_offline_models.py`). Confirm a freshly-downloaded Argos pack translates.

  **Gotcha — Argos must run in a subprocess.** argostranslate pulls in
  stanza → PyTorch, and torch SEGFAULTs when its GIL is acquired from a Qt
  `QThreadPool` worker thread (`take_gil` ← `gil_scoped_acquire` in
  `libtorch_python`, while stanza inits a torch model). The job layer always runs
  translation off the UI thread, so calling argos in-process is guaranteed to
  crash. `ArgosBackend` therefore never imports argostranslate; it spawns
  `python -m screen_translator.argos_proc` and exchanges newline-delimited JSON
  (one request/response per line) over stdin/stdout. torch lives on the child's
  main thread, our worker thread only does blocking pipe I/O. The child is
  lazy-spawned on first translate, persists across calls, and exits on stdin EOF
  when the app quits. **Do not "optimize" this back to an in-process call.**

**Still open:**
- **Capture uses the deprecated `CGDisplayCreateImageForRect`** (`capture._grab_quartz`).
  Deprecated since macOS 14 but still works (and is fast, ~85ms) on macOS 26. When
  Apple removes it, migrate to **ScreenCaptureKit** (`pyobjc-framework-ScreenCaptureKit`,
  not currently a dep): `SCShareableContent.getShareableContent…` → `SCContentFilter`
  (cache the per-display filter so it isn't rebuilt every grab) → `SCStreamConfiguration`
  → `SCScreenshotManager.captureImageWithFilter:configuration:completionHandler:`,
  blocking on a semaphore for the async result, then reuse `_cgimage_to_pil`. Make it
  the primary path with the current Quartz path as fallback. Deferred for now — no
  point taking the async-rewrite + dependency risk while the deprecated call still works.
- **Verify Right Option (`<alt_r>`) hold + hotkey recording on the real machine** — the
  two-listener crash fix still needs a live confirmation.
- **Verify hotkey suppression (`suppress_hotkeys`).** macOS: needs Accessibility
  trust (the active event tap) — confirm a plain function key (e.g. F6, or F1 with
  "standard function keys" on) both translates AND no longer triggers its default;
  confirm the tap isn't disabled by timeout under load. Windows is **untested** —
  the `win32_event_filter` dispatch path (`hotkeys.py`) needs a real run. macOS
  F1/F2/media keys can't be caught (system events, not key events) — expected.
- **macOS 26 TSM/keyboard-layout crash (FIXED, verify live).** Once Accessibility
  was granted and keys reached pynput, the app SIGTRAP'd: pynput resolves the
  keyboard layout via Carbon TSM APIs on its listener thread, and macOS 26 asserts
  those are main-thread-only. `hotkeys._patch_darwin_keycode_context()` pre-resolves
  the layout on the main thread and caches it for the listener. Confirm hotkeys now
  fire on the real machine without the "Python quit unexpectedly" SIGTRAP.
- Windows/Linux native capture backends (currently mss fallback there).
- LLM / context-aware translation tiers.
- Package as a signed `.app` (so permissions stick to the app, not Terminal).

---

## How changes were verified (no full GUI in headless)

- `./.venv/bin/python -m py_compile screen_translator/*.py`
- `./.venv/bin/python -m unittest discover -s tests -t .` (pipeline + gating logic)
- `./.venv/bin/python tools/smoke_test.py` (real Vision OCR + Google translate)
- `QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "from screen_translator.app import App; App()..."`
  for constructing widgets/menus and unit-testing logic without a display.
- Real screen capture + coordinate mapping tested by grabbing the actual screen.
- The interactive flow (region select, overlay over a game, real hotkeys) can only be
  validated by the user running `./run.sh` — that's the standing feedback loop.

Several adversarial code-review passes were run (concurrency, Qt/macOS, capture
coords, history, hotkeys); fixes applied.

---

## To continue in a NEW chat

1. Open the new chat with the working directory set to **this folder**
   (`/Users/viktor/Projects/ai-screen-translator`).
2. Tell it: *"Read HANDOFF.md and README.md, then continue."*
3. The immediate next step is usually: confirm Right Option hold + hotkey-recording
   work on the machine, then pick from "open items" above.
