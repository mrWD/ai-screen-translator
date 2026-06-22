@echo off
REM Create the venv on first run, install deps, then launch the tray app (Windows).
setlocal
cd /d "%~dp0"

if not exist .venv (
    python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)

.venv\Scripts\python -m screen_translator
