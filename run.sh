#!/usr/bin/env bash
# Create the venv on first run, install deps, then launch the tray app.
set -e
cd "$(dirname "$0")"

# Gate on a setup-complete marker, NOT just the .venv dir: `python -m venv` creates
# the dir before the (now ~1GB, offline-default) pip install, so a dir-only check
# would treat an interrupted install as done forever. Gating on the marker also means
# users upgrading an existing .venv pick up the now-required argostranslate + model.
if [ ! -f .venv/.setup_ok ]; then
  python3 -m venv .venv
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
