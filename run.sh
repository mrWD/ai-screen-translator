#!/usr/bin/env bash
# Create the venv on first run, install deps, then launch the tray app.
set -e
cd "$(dirname "$0")"

# Gate on a setup-complete marker, NOT just the .venv dir: `python -m venv` creates
# the dir before the (now ~1GB, offline-default) pip install, so a dir-only check
# would treat an interrupted install as done forever. Gating on the marker also means
# users upgrading an existing .venv pick up the now-required argostranslate + model.
if [ ! -f .venv/.setup_ok ]; then
  # Find a usable Python 3 (macOS doesn't ship one by default — fail with guidance,
  # not a raw "command not found"). Prefer python3, then python; require 3.9+.
  PY=""
  for _cand in python3 python; do
    if command -v "$_cand" >/dev/null 2>&1 \
        && "$_cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      PY="$_cand"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.9+ is required but was not found."
    echo
    if [ "$(uname)" = "Darwin" ]; then
      echo "Install it one of these ways, then run this again:"
      echo "  - Homebrew:     brew install python@3.12"
      echo "  - Xcode tools:  xcode-select --install   (provides python3)"
      echo "  - Or download:  https://www.python.org/downloads/macos/"
    else
      echo "Install Python 3 from your package manager or https://www.python.org/downloads/, then run this again."
    fi
    exit 1
  fi
  echo "Using $("$PY" --version 2>&1)"
  "$PY" -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
  touch .venv/.setup_ok   # deps are in; marker set before the best-effort model fetch
  # 'offline' is the default translate engine — fetch its language pack now.
  # Best-effort: don't abort the launch if the download fails (no network etc.);
  # the app can still fetch it later from Settings -> Offline model.
  ./.venv/bin/python -m screen_translator.download_offline \
    || echo "warning: offline model download failed; download later from Settings -> Offline model"
fi

exec ./.venv/bin/python -m screen_translator
