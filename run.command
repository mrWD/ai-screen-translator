#!/usr/bin/env bash
# Double-clickable launcher for macOS Finder. Double-clicking a .command file opens
# Terminal and runs it; this just hands off to run.sh in the same folder (which
# creates the venv + installs deps on first run, then launches the tray app).
# The Terminal window shows the live log; closing it quits the app.
cd "$(dirname "$0")" || exit 1
exec ./run.sh
