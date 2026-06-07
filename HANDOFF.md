# AI Screen Translator — Handoff / Project State

Self-contained context so a fresh chat (with no memory of the prior conversation)
can continue this project. Read this + `README.md` + the code, then continue.

---

## What it is

A hotkey-driven screen OCR + translation overlay for games / films / anything
on screen. User presses a hotkey → the foreign text on screen is translated and
shown **in place** over the original. Everything is also **saved to disk** so the
user can review (and select/copy) original + translation **after closing the game**.

Original use case (the bar to meet):
1. User plays a game / watches a film.
2. Sees foreign text, presses a hotkey.
3. Translation appears in place of the foreign text.
4. The screen with text is saved to disk, openable AFTER closing the game.
5. User can see ORIGINAL + TRANSLATION and SELECT/COPY the text.

All five are implemented. Translation is pluggable — free Google (no API key) is
the default, with optional DeepL (API key) and offline Argos backends.

---

## Where it lives & how to run

- Project root: `/Users/viktor/Projects/ai-screen-translator` (this folder).
- Run: `./run.sh` (creates `.venv`, installs `requirements.txt`, launches the
  menu-bar app). **Do NOT use `sudo`** — it breaks macOS permission prompts.
- Headless core check: `./.venv/bin/python tools/smoke_test.py`
- macOS permissions required for the launching Terminal/iTerm:
  - **Screen Recording** (capture) and **Accessibility** (global hotkeys).
  - Settings → Privacy & Security → … ; relaunch after granting.

Platform: developed/tested on **macOS (Apple Silicon, Python 3.12)**. Cross-platform
is a design goal (Windows/Linux) but only macOS is exercised so far.

---

## Features & hotkeys (current defaults)

| Hotkey | Action |
|---|---|
| `Cmd+Shift+T` | Translate the saved region once (panel appears beside it) |
| `Cmd+Shift+F` | Translate the whole screen, in place |
| Right Option `⌥` (hold) | Show whole-screen translation **while held**, hide on release |
| `Cmd+Shift+L` | Toggle live mode (auto re-translate the region on change) |
| `Cmd+Shift+R` | Re-select the region |
| `Cmd+Shift+H` | Hide overlays |

- **Menu**: a `Translator` menu (top-left menu bar) + tray icon `文`. Items:
  Translate now / Translate full screen / Live mode / Select region / Hide /
  Copy last result / Open translation log / Open history folder / Source &
  Target language / Settings / Quit.
- **Settings dialog**: languages, OCR engine, live interval, overlay font/opacity,
  save_history/save_screenshots, and **click-to-record hotkeys** (press a key; single
  keys like F6 work, chords too).
- **History**: every capture → `~/Library/Application Support/ai-screen-translator/history/<session>/session.jsonl` (+ downscaled screenshot PNG). "Open translation log" builds `index.html` (original|translation pairs, selectable/copyable, works with the app closed).

---

## Architecture (file map, all under `screen_translator/`)

- `app.py` — tray/menu app + orchestration. Single in-flight job (`_busy`), worker
  jobs (`_Job` region/live, `_ScreenJob` full-screen) run OCR+translate off the UI
  thread and emit results via signals. Hold-mode handlers, history persistence,
  settings apply, hotkey setup.
- `capture.py` — screen capture. **macOS: native Quartz** `CGDisplayCreateImageForRect`
  (full Retina res). Else mss. Logical-coords in; returns RGB PIL image. `is_black()`.
- `ocr.py` — pluggable OCR. `VisionOCR` (Apple Vision via ocrmac) + `RapidOCRBackend`.
  `recognize()` → text; `recognize_blocks()` → `Block(text,x,y,w,h)` in image pixels.
- `translate.py` — pluggable: `GoogleFreeBackend` / `DeepLBackend` / `ArgosBackend`
  behind `make_translator`, mirroring `ocr.make_ocr`. Per-backend (source,target,text)
  cache + uniform `TranslateError`. Module-level `translate()` kept for the smoke test.
- `region_selector.py` — drag-to-select region (frameless top-most overlay).
- `overlay.py` — region-mode translucent click-through panel, anchored BESIDE the
  region (never over it → no self-capture feedback loop).
- `screen_overlay.py` — full-screen click-through overlay; legacy boxes grow-to-fit +
  de-overlap, OR in-place mode that erases each block with a sampled fill anchored on
  the original.
- `hotkeys.py` — **single** pynput Listener driving chord HotKeys + hold key.
- `hotkey_edit.py` — click-to-record hotkey field; Qt key → pynput string.
- `history.py` — JSONL + PNG persistence + `index.html` renderer.
- `settings_dialog.py`, `languages.py`, `config.py`, `macos.py` (NSWindow
  float-over-fullscreen tweaks + activation-policy / accessory helpers).
- `tools/smoke_test.py` — headless OCR+translate verification.

---

## Key decisions & gotchas (the non-obvious stuff)

- **mss captures at 1× (logical) on macOS** → soft text → garbled OCR. We switched
  to **Quartz** for native 2× Retina pixels (much better OCR). Fallback to mss off-mac.
- **NEVER assume devicePixelRatio when mapping OCR coords.** Self-calibrate:
  `scale = captured_image_size / logical_screen_size`. (Assuming dpr=2 once caused
  translations to land at half-height.) `_ScreenJob` does this.
- **Apple Vision can't read Cyrillic** (ru/uk). `make_ocr` routes Cyrillic source to
  RapidOCR and errors clearly if it's not installed (rather than returning garbage).
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
  fullscreen Spaces — needed for GeForce Now in macOS fullscreen.
- **GeForce Now works** (it's a composited window). **DRM video = black frame** by
  design (unfixable). **True exclusive-fullscreen games** bypass the overlay → tell
  the user to switch to Borderless. The app detects all-black and messages it.
- **Accessory mode** (`accessory_mode`, opt-in, default off) sets
  `NSApplicationActivationPolicyAccessory` (no Dock icon / app menu — tray only).
  Accessory apps don't get keyboard focus for a frameless overlay, so
  `_selector_focus_acquire/release` briefly restore Regular policy + `activate_app()`
  around region selection (restored on BOTH the selected and cancelled paths).
  Applied at launch; toggling asks for a relaunch.
- **History JSONL had silently broken**: the `session.jsonl` write had drifted into
  dead code after a `return` in `HistoryWriter._downscaled`, so nothing was logged
  (screenshots saved, but "Open translation log" was always empty). Fixed — the write
  is back in `add()`.
- **In-place fill is sampled OFF the UI thread** in `_ScreenJob` from the captured PIL
  image (the click-through overlay can't read screen pixels at paint time) and passed
  through as plain `(r,g,b)` tuples — `QColor` is built only in the overlay's paint on
  the UI thread. `_ScreenJobSignals.done` now carries 5-tuples
  `(rect, orig, translated, fill_rgb, text_rgb)`; in-place layout anchors on the
  original (no grow/de-overlap) so the erase aligns.
- **Translation backend mirrors OCR**: built lazily on the UI thread
  (`_get_translator`), reset to None on engine/key change, and the instance is passed
  into the worker job — never call a module global from the worker. NOT reset on a
  source change (its cache is keyed by source, so one instance serves all).
  `make_translator` surfaces a clear error for explicit `deepl` with no key (no silent
  fallback to Google). DeepL targets map Chinese to ZH-HANS/ZH-HANT (bare ZH target =
  Simplified). On macOS, `capture._grab_quartz` subtracts the chosen display's bounds
  origin because `CGDisplayCreateImageForRect`'s rect is display-LOCAL, not global.

---

## Known open items / next steps

**Implemented since the last handoff — need live confirmation on the real machine:**
- **Multi-display capture** — `capture._display_id_for_region` picks the display under
  the region (was `CGMainDisplayID()` only). Verify on a real two-display rig.
- **Accessory mode** — opt-in (Settings → Dock; needs relaunch). VERIFY the region
  selector still gets keyboard focus (Esc to cancel + mouse drag) with it on, and that
  the Dock icon does NOT reappear afterward — this is the exact focus risk it was
  deferred over.
- **In-place text replacement** — opt-in (`overlay_inplace`). Confirm the erase aligns
  and looks right over a real game (flat fill won't match textured/gradient bgs).
- **Pluggable translation** — DeepL (needs a key) and offline Argos (needs
  `pip install argostranslate` + an explicit source). Confirm a real DeepL key and an
  installed Argos pack actually translate.

**Still open:**
- **Verify Right Option (`<alt_r>`) hold + hotkey recording on the real machine** — the
  two-listener crash fix still needs a live confirmation.
- Windows/Linux native capture backends (currently mss fallback there).
- LLM / context-aware translation tiers.
- Package as a signed `.app` (so permissions stick to the app, not Terminal).

---

## How changes were verified (no full GUI in headless)

- `./.venv/bin/python -m py_compile screen_translator/*.py`
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
