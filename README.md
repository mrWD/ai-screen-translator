# AI Screen Translator

Press a global hotkey while playing a game (or reading anything on screen) and a
translation of the selected on-screen text appears in an overlay over it.

**Pipeline:** hotkey → capture screen region → OCR → translate → overlay.

This is the v1 prototype: Python + PySide6, Apple Vision OCR on macOS, and the
**free Google Translate** endpoint (no API key). It is architected so the OCR
and translation engines are pluggable for a later cross-platform build.

---

## Quick start (macOS)

```bash
cd ai-screen-translator
./run.sh            # creates .venv, installs deps, launches the menu-bar app
```

First, verify the core works without any GUI/permissions:

```bash
.venv/bin/python tools/smoke_test.py
# Expect: OCR reads "Hello, world", translate prints the Russian text.
```

### Required macOS permissions

The app needs two permissions for the terminal/app you launch it from
(System Settings → Privacy & Security):

1. **Screen Recording** — to capture the screen region. Without it, captures
   come back black.
2. **Accessibility** (and possibly **Input Monitoring**) — for the global hotkey
   (pynput). Without it, use the menu-bar icon's actions instead.

You'll be prompted on first use; you may need to quit and relaunch after granting.

---

## How to use

1. Launch the app — a 文 icon appears in the menu bar.
2. Pick **Source language** (what's on screen) and **Target language** (what you
   want) from the menu, or open **Settings…** to set everything at once.
3. Press the **translate hotkey** (default `Cmd+Shift+T`). The first time it asks
   you to **drag-select the region** where the game text appears (e.g. the
   subtitle/dialogue box).
4. After that, each press of `Cmd+Shift+T` re-captures that same region and shows
   the translation in a panel just below it. A **⏳ Translating…** placeholder
   appears instantly so you know it's working; it's replaced by the result when
   OCR + translation finish (a network/first-offline call takes a moment). The
   offline engine is pre-warmed in the background so its first translate is quick.

**Live mode** (`Cmd+Shift+L`, or the menu): instead of pressing the hotkey for
every line, the app watches the selected region and re-translates automatically
whenever the text changes — ideal for subtitles / dialogue. It's efficient: it
skips frozen frames and only re-translates when the OCR'd text actually changes
(so an animated game background doesn't cause constant re-translation). The panel
is anchored next to the region, not over it, so the original text stays visible.

**Full-screen mode** (`Cmd+Shift+F`, or the menu): translates the *entire* screen
at once — it OCRs every block of text and draws each translation in place over the
original, like Google Lens. Each box grows to fit its translation, overlapping
boxes are nudged apart, and tiny/menu-bar noise is skipped.

**In-place replacement** (Settings → *In-place*, off by default): in full-screen /
hold mode, instead of a translucent box *over* the text, each original block is
painted out with a colour sampled from its surroundings and the translation is
drawn in its place — closer to a true "replace the text" look. Boxes stay anchored
on the original so the cover-up aligns; the overlay-opacity setting is ignored here
(the fill must be solid to erase).

**Hold to translate** (**Right Option `⌥`** by default): hold the key to see the
whole-screen translation, release to dismiss it — handy for a quick peek without
toggling. (Right Option is used because it does nothing on its own in macOS,
unlike the F-keys — F7/F8/F9 are media ⏮/⏯/⏭ and would launch Music.)

| Hotkey | Action |
|---|---|
| `Cmd+Shift+T` | Capture the saved region once, translate, show panel |
| **`F6` (hold)** | Show the whole-screen translation **only while held** — release to hide |
| `Cmd+Shift+L` | Toggle live (auto-translate) mode on the region |
| `Cmd+Shift+R` | Re-select the region |
| `Cmd+Shift+H` | Hide overlays |

Full-screen translation is **hold-to-show only**: hold the full-screen key
(reassignable under **Full screen: HOLD to show** in Settings) and the translation
stays only while you hold it — release to hide. There is no persistent full-screen
hotkey. The menu item **Translator → Translate full screen** still does a one-off
capture (it stays until you hide it) for when you can't hold a key.

**Changing hotkeys:** open **Settings**, click a hotkey field, and press the
key(s) you want — single keys (e.g. `F6`) work, as do chords like `Cmd+Shift+T`.
Press `⌫` (Backspace) while recording to clear a field (disables that hotkey).

**Global hotkeys need Accessibility** on macOS: the app prompts on first launch.
If hotkeys do nothing, enable the app (or your terminal) in *System Settings →
Privacy & Security → Accessibility* and **relaunch** — a tap granted while the app
is already running won't receive events until restart.

**Logs / troubleshooting:** the app writes a log to
`~/Library/Application Support/ai-screen-translator/app.log` (also printed to the
terminal). It records every hotkey, capture, translation and overlay action — open
it to see what happened, or run with `ST_LOG=debug` for verbose output. Note that the F-keys are
media/brightness keys by default; enable *System Settings → Keyboard → "Use F1,
F2, etc. keys as standard function keys"*, or turn on **Suppress** in Settings so
a single-key hotkey runs the translation instead of its default action.

## Saving & reviewing (after the game)

Every translation is saved to disk, so you can read the original + translation
**after closing the game** — and the text is fully selectable / copyable.

- Each app run is a **session** folder under
  `~/Library/Application Support/ai-screen-translator/history/<timestamp>/`,
  containing `session.jsonl` (one capture per line) and optional screenshots.
- Menu **Translator → Open translation log** generates and opens an `index.html`
  in your browser: every capture as an *Original | Translation* pair (plus the
  screenshot). It's plain HTML, so text is selectable, copyable, and `Cmd+F`
  searchable — and it keeps working with the app closed.
- **Open history folder** reveals the files in Finder.
- **Copy last result** puts the most recent original + translation on the clipboard.
- Toggle history / screenshots in **Settings** (`save_history`, `save_screenshots`).
  Old sessions are pruned to the most recent `history_keep_sessions` (default 20).

**Settings…** (menu) lets you change source/target language, OCR engine,
**translation engine**, live interval, overlay font size +
opacity, **in-place replacement**, the macOS **menu-bar-only (no Dock icon)**
toggle, and all hotkeys — applied live (the Dock-icon toggle needs a relaunch).
All of it also persists to the config file. On Windows/Linux the default modifier
is `Ctrl`.

On macOS the app runs **menu-bar-only (accessory) by default** — this is what lets
the full-screen translation float *over* a GeForce Now game's fullscreen Space; a
normal Dock app would switch you back to the Desktop Space instead. You can re-enable
the Dock icon under Settings, at the cost of that float-over-fullscreen behaviour.

**Suppress** (Settings) makes a single-key hotkey swallow its normal action — so
binding e.g. F1 runs the translation *instead of* opening Help. Only single,
modifier-free keys are affected (chords like `Cmd+Shift+T` always pass through).
On macOS it needs Accessibility permission, and the brightness/media keys
(F1/F2/F7–F12 in their default mode) can't be intercepted — pick a plain function
key, or enable “Use F1, F2 as standard function keys”.

**Translation engines:** the default is the free Google endpoint (no key, needs
internet). Or pick **offline** (Argos Translate) for on-device, no-network
translation. For offline, click **Settings → Offline
model → Download model for the selected languages** — it installs Argos Translate
(if missing) and downloads the language pack for your source/target (pivoting
through English when there's no direct pack). Offline can't auto-detect, so set an
explicit source.

---

## GeForce Now / games — what works

- **GeForce Now**: works well. The GFN stream is a normal composited window, so
  capture and overlay behave like any app — no exclusive-fullscreen or anti-cheat
  concerns. If GFN is in macOS **native fullscreen** (its own Space), the overlay
  still appears: it sets the NSWindow `collectionBehavior` to join all Spaces.
- **Native games**: capture/overlay work over **borderless / windowed-fullscreen**
  games (the common case in 2026). True *exclusive*-fullscreen bypasses the
  compositor — if the overlay doesn't show or capture is black, switch the game to
  **Borderless Windowed**.
- The overlay is a **separate top-level window** — it never injects into the game,
  so it is anti-cheat-safe (no VAC/EAC/BattlEye risk).
- **DRM video** (Netflix etc.) captures as a black frame by design — unfixable in
  any software; the app detects this and tells you.

---

## Configuration

Settings persist to:

- macOS: `~/Library/Application Support/ai-screen-translator/config.json`
- Linux: `~/.config/ai-screen-translator/config.json`
- Windows: `%APPDATA%\ai-screen-translator\config.json`

Keys: `source`, `target`, `ocr_engine` (`auto`/`vision`/`rapidocr`), `ocr_fast`,
`translate_engine` (`google`/`offline`), `offline_model_dir`, `region`,
`hotkey_translate`, `hotkey_hold`, `hotkey_reselect`, `hotkey_hide`, `hotkey_live`,
`suppress_hotkeys`, `live_interval_ms`, `overlay_font_pt`, `overlay_opacity`,
`overlay_inplace`, `save_history`, `save_screenshots`, `history_keep_sessions`,
`accessory_mode`.

---

## Project layout

```
screen_translator/
  app.py             # tray/UI shell + wiring (hotkey→capture→OCR→translate→overlay)
  jobs.py            # off-thread OCR+translate workers (QRunnables) on the thread pool
  pipeline.py        # pure logic: scale/box mapping, junk filter, dedup, colour sampling
  gating.py          # single-in-flight-job + hold-key-retry state machine (no Qt)
  config.py          # persisted settings
  capture.py         # screen capture: native Quartz (Retina 2x) on macOS, else mss
  hotkey_edit.py     # click-to-record hotkey field (Qt key -> pynput string)
  changes.py         # frozen-frame detection (live-mode OCR skip)
  ocr.py             # pluggable OCR: Apple Vision / RapidOCR
  translate.py       # pluggable translation: Google free / offline Argos + cache
  offline_models.py  # one-click Argos install + language-pack download (Settings button)
  argos_proc.py      # offline-translation subprocess (keeps torch off the Qt worker thread)
  region_selector.py # drag-to-select region UI
  overlay.py         # region-mode translucent click-through panel, anchored beside the region
  screen_overlay.py  # full-screen in-place overlay (boxes over text, or erase+replace)
  settings_dialog.py # Settings dialog (languages, engines, hotkeys, interval, overlay)
  hotkeys.py         # global hotkeys (pynput) bridged to Qt
  macos.py           # NSWindow tweaks (float over fullscreen) + activation policy
  languages.py       # language list for the UI
tests/               # unit tests for pipeline.py + gating.py + offline_models.py (stdlib unittest)
tools/smoke_test.py  # headless OCR+translate verification
```

---

## Known limitations / roadmap

- **OCR**: Apple Vision can't read Cyrillic. For a Russian/Ukrainian *source*,
  install `rapidocr-onnxruntime` and set `ocr_engine` to `rapidocr`.
- **Capture**: macOS uses native Quartz at full Retina resolution (crisp OCR);
  other platforms fall back to `mss`. Windows/Linux native backends
  (Windows.Graphics.Capture, PipeWire) are future work.
- **Multi-monitor**: macOS now captures from the display under the region; a
  region straddling two displays still only yields the chosen display's portion.
- **Linux/Wayland**: global hotkeys and always-on-top are restricted; X11 works
  better. A portal-based path is future work.
- **Next features**: LLM context-aware translation tiers, Windows/Linux native
  capture, and packaging as a signed `.app`.
