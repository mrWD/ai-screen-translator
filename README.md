# AI Screen Translator

Hold a key (default `F6`) while playing a game (or reading anything on screen) and
a translation of the whole screen appears in an overlay over it, while held.

**Pipeline:** hotkey → capture screen → OCR → translate → overlay.

This is the v1 prototype: Python + PySide6, Apple Vision OCR on macOS (RapidOCR
elsewhere), and **offline on-device translation** (Argos Translate) by default —
with the free **Google Translate** endpoint (no API key) available as a network
option. It is architected so the OCR and translation engines are pluggable.

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

## Quick start (Windows / Linux)

> Cross-platform support is **functional but not yet hardware-tested** — macOS is
> the primary, exercised target. On Windows/Linux the app uses the cross-platform
> **RapidOCR** engine (auto-installed from `requirements.txt`) instead of Apple
> Vision, and **mss** for capture. Expect rough edges; bug reports welcome.

**Windows** — just double-click **`run.bat`** in Explorer. The first run sets
everything up automatically (no manual Python install needed):

- **`setup.bat`** — installer, run once. Finds Python 3, or installs Python 3.12
  via `winget` if it's missing (per-user, no admin), then creates the virtual
  environment and installs deps (incl. rapidocr). Double-clickable. The venv is
  created at `%LOCALAPPDATA%\ai-screen-translator\venv` (a short path, on purpose:
  PySide6's long internal paths overflow Windows' 260-char limit inside a deep
  project folder).
- **`run.bat`** — launcher, double-click each time. Starts the tray app and keeps
  a console window open showing the live log. If setup hasn't run yet, it runs it
  first, so double-clicking `run.bat` alone is enough.
- **`run-debug.bat`** — same as `run.bat` but with verbose (`ST_LOG=debug`) logging.
- **`run-silent.vbs`** — starts the app with **no console window** (everyday use);
  the log still goes to `%APPDATA%\ai-screen-translator\app.log`. Run setup once first.

> **The first run is a large one-time download (~1 GB) and takes several minutes.**
> It installs Python (if missing), the GUI/OCR dependencies, and the default
> **offline** translation engine (Argos + ctranslate2/spacy/stanza/torch) plus the
> en→ru language pack. The console will sit on `Installing dependencies…` /
> `Downloading…` for a while — that's normal, not a hang. Later launches start in a
> couple of seconds. (Prefer network-only and a tiny install? Comment out
> `argostranslate` in `requirements.txt` and pick **Google** in Settings.)

From a terminal it's the same:

```bat
cd ai-screen-translator
run.bat            REM first run installs Python (if needed) + deps, then launches
```

For verbose logs, set `ST_LOG=debug` before launching (PowerShell:
`$env:ST_LOG="debug"; .\run.bat`).

**Linux** (X11 session, Python 3.12+):

```bash
cd ai-screen-translator
./run.sh           # creates .venv, installs deps (incl. rapidocr), launches the tray app
```

Notes:
- **OCR model**: the first run downloads the RapidOCR ONNX models (a few tens of MB).
- **Linux needs a system tray** (GNOME may need the AppIndicator extension) and the
  usual Qt xcb libs, e.g. on Debian/Ubuntu:
  `sudo apt install libxcb-xinerama0 libxcb-cursor0`.
- **Global hotkeys are X11-only.** Under **Wayland**, the hold-to-translate key won't
  fire (the app detects this and tells you) — use the tray menu's **Translate full
  screen**, or run an X11/XWayland session.
- **Key suppression** (blocking a key's normal action) is **macOS/Windows only** —
  the option is disabled on Linux.
- High-DPI displays are handled (capture scales logical→physical coords), but the
  exact behavior on mixed-DPI multi-monitor setups is untested.

---

## How to use

1. Launch the app — a 文 icon appears in the menu bar.
2. Pick **Source language** (what's on screen) and **Target language** (what you
   want) from the menu, or open **Settings…** to set everything at once.
3. **Hold `F6`** (default; reassignable): the *entire* screen is translated — it
   OCRs every block of text and draws each translation in a translucent box over
   the original, like Google Lens. The translation shows **only while you hold the
   key** and disappears on release. A **⏳ Translating…** placeholder appears near
   the cursor while OCR + translation run.

Each box grows to fit its translation, overlapping boxes are nudged apart, and
tiny/menu-bar noise is skipped. The offline engine is pre-warmed in the background
so the first translate is quick.

| Hotkey | Action |
|---|---|
| **`F6` (hold)** | Translate the whole screen **only while held** — release to hide |
| `Cmd+Shift+H` | Hide the overlay |

The menu item **Translator → Translate full screen** does a one-off capture that
stays until you hide it (for when you can't hold a key).

**Changing hotkeys:** open **Settings**, click a hotkey field, and press the
key(s) you want — single keys (e.g. `F6`) work, as do chords like `Cmd+Shift+H`.
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
**translation engine**, Fast OCR, overlay font size + opacity, history, and the
hotkeys — applied live. All of it also persists to the config file. On
Windows/Linux the default modifier is `Ctrl`.

On macOS the app runs **menu-bar-only (no Dock icon)** — this is what lets the
full-screen translation float *over* a GeForce Now game's fullscreen Space; a
normal Dock app would switch you back to the Desktop Space instead. (It's no longer
a toggle: a Dock app can't do the float-over-fullscreen, so there's no useful
choice to expose.)

**Suppress** (Settings) makes a single-key hotkey swallow its normal action — so
binding e.g. F1 runs the translation *instead of* opening Help. Only single,
modifier-free keys are affected (chords like `Cmd+Shift+H` always pass through).
On macOS it needs Accessibility permission, and the brightness/media keys
(F1/F2/F7–F12 in their default mode) can't be intercepted — pick a plain function
key, or enable “Use F1, F2 as standard function keys”.

**Translation engines:** the default is **offline** (Argos Translate) — fully
on-device, no network. The setup scripts (`setup.bat` / `run.sh`) install it and
download the language pack for the default `source→target` (en→ru) automatically,
so it works out of the box; this is the ~1 GB the install pulls (ctranslate2 +
spacy/stanza/torch). Offline can't auto-detect, so set an explicit source. If you
switch to a source/target language whose pack isn't installed, the app **offers to
download it on the spot** and applies it without a restart. To use a
different language pair offline, click **Settings → Offline model → Download model
for the selected languages** (pivots through English when there's no direct pack).
Or switch to the free **Google** endpoint (no key, needs internet) in Settings —
and comment out `argostranslate` in `requirements.txt` to skip the heavy install.

**Privacy:** by default **nothing leaves your machine** — the offline engine
translates entirely on-device. The **Google** engine is the one exception: it sends
your on-screen text to Google's servers over the internet, so switching to it asks
for confirmation first. Translation history (text + screenshots) is stored **only
locally**, in your user config dir, and the files are created **owner-only**
(`0o600`/`0o700` on macOS/Linux); turn it off with `save_history` /
`save_screenshots` in Settings. Models, dependencies and (on Windows) Python are
downloaded from their official sources over HTTPS without app-side hash
verification — run the first-time setup on a network you trust.

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
`translate_engine` (`google`/`offline`), `offline_model_dir`,
`hotkey_hold`, `hotkey_hide`, `suppress_hotkeys`, `overlay_font_pt`,
`overlay_opacity`, `save_history`, `save_screenshots`, `history_keep_sessions`,
`accessory_mode`.

---

## Project layout

```
screen_translator/
  app.py             # tray/UI shell + wiring (hotkey→capture→OCR→translate→overlay)
  jobs.py            # off-thread full-screen OCR+translate worker (QRunnable)
  pipeline.py        # pure logic: scale/box mapping, junk filter
  gating.py          # single-in-flight-job + hold-key-retry state machine (no Qt)
  config.py          # persisted settings
  capture.py         # screen capture: native Quartz (Retina 2x) on macOS, else mss
  hotkey_edit.py     # click-to-record hotkey field (Qt key -> pynput string)
  ocr.py             # pluggable OCR: Apple Vision / RapidOCR
  translate.py       # pluggable translation: Google free / offline Argos + cache
  offline_models.py  # one-click Argos install + language-pack download (Settings button)
  argos_proc.py      # offline-translation subprocess (keeps torch off the Qt worker thread)
  overlay.py         # "Translating…" indicator panel (click-through, floats over fullscreen)
  screen_overlay.py  # full-screen overlay: translucent boxes drawn over the text
  settings_dialog.py # Settings dialog (languages, engines, hotkeys, overlay)
  hotkeys.py         # global hotkeys (pynput) bridged to Qt
  macos.py           # NSWindow tweaks (float over fullscreen) + activation policy
  languages.py       # language list for the UI
tests/               # unit tests for pipeline.py + gating.py + offline_models.py (stdlib unittest)
tools/smoke_test.py  # headless OCR+translate verification
```

---

## Known limitations / roadmap

- **OCR**: macOS uses Apple Vision (its *fast* level reads ~30 scripts incl.
  Cyrillic/CJK; *accurate* is Latin-only). Windows/Linux use **RapidOCR**
  (auto-installed), which has no per-language hint — accuracy varies by language.
- **Capture**: macOS uses native Quartz at full Retina resolution (crisp OCR);
  Windows/Linux fall back to `mss` (logical→physical coords scaled by DPI). Native
  backends (Windows.Graphics.Capture, PipeWire) are future work.
- **Multi-monitor**: macOS now captures from the display under the region; a
  region straddling two displays still only yields the chosen display's portion.
- **Linux/Wayland**: global hotkeys and always-on-top are restricted; X11 works
  better. A portal-based path is future work.
- **Next features**: LLM context-aware translation tiers, Windows/Linux native
  capture, and packaging as a signed `.app`.
