@echo off
REM Launch the tray app. First run auto-installs everything via setup.bat.
REM The console window stays open and shows the live log; closing it quits the app.
REM The venv lives at %LOCALAPPDATA%\ai-screen-translator\venv (see setup.bat).
setlocal
cd /d "%~dp0"
title AI Screen Translator

set "VENV=%LOCALAPPDATA%\ai-screen-translator\venv"
set "VPY=%VENV%\Scripts\python.exe"
REM Setup writes this marker only after pip fully succeeds; gating on it (not just
REM python.exe, which venv creates before the ~1GB pip install) means an interrupted
REM install re-runs setup next launch instead of crashing on a missing import.
set "READY=%VENV%\.setup_ok"
REM UTF-8 mode so the live console log renders Cyrillic / arrows correctly.
set "PYTHONUTF8=1"

if not exist "%READY%" (
  echo First run or incomplete setup detected - installing. Please wait...
  echo(
  call "%~dp0setup.bat" --from-run
  if not exist "%READY%" (
    echo(
    echo Setup did not finish. Run setup.bat and read the messages there.
    pause
    exit /b 1
  )
)

echo Starting AI Screen Translator...
echo (This window shows the live log. Set ST_LOG=debug before running for verbose logs.)
echo (Closing this window quits the app.)
echo(
"%VPY%" -m screen_translator
