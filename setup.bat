@echo off
REM One-time setup for Windows: ensure Python exists, create the venv, install deps.
REM Pass --from-run when called by run.bat (skips the final "press a key" pause).
REM
REM This script NEVER uninstalls, replaces or downgrades Python. If any Python 3.x
REM is already present (on PATH or in a standard install folder) it is reused as-is
REM - including versions NEWER than 3.12. winget is invoked only when no Python can
REM be found at all, and even then it just *adds* 3.12 (it removes nothing).
REM
REM The venv lives at %LOCALAPPDATA%\ai-screen-translator\venv (a SHORT path) on
REM purpose: PySide6 ships very long internal paths, and putting the venv inside a
REM deep project folder blows past Windows' 260-char MAX_PATH limit during pip
REM install. A short, fixed location avoids that with no admin / long-path tweak.
setlocal enabledelayedexpansion
cd /d "%~dp0"
title AI Screen Translator - Setup

set "VENV=%LOCALAPPDATA%\ai-screen-translator\venv"
set "VPY=%VENV%\Scripts\python.exe"
REM UTF-8 mode so Python output (progress arrows, Cyrillic) doesn't hit cp1252 errors.
set "PYTHONUTF8=1"

echo(
echo ============================================================
echo   AI Screen Translator - one-time setup
echo ============================================================
echo(

REM ---- 1. Create the virtual environment (find / install Python) ----
if exist "%VPY%" (
  echo Virtual environment already exists - skipping Python setup.
  goto deps
)

echo Looking for Python 3...
set "VENV_DONE="

REM Try the py launcher, then python on PATH. The Microsoft Store stub
REM fails here (exit code 49/9009), so it is filtered out automatically.
py -3 -m venv "%VENV%" >nul 2>&1 && set "VENV_DONE=1"
if not defined VENV_DONE python -m venv "%VENV%" >nul 2>&1 && set "VENV_DONE=1"

if defined VENV_DONE (
  echo Found an existing Python on PATH. Virtual environment created.
  goto deps
)

REM Not on PATH? Look for an already-installed Python BEFORE installing anything,
REM so winget is never re-run when Python already exists (any 3.x version is fine).
call :find_python
if defined PYPATH (
  echo Found an installed Python: !PYPATH!
  "!PYPATH!" -m venv "%VENV%"
  if exist "%VPY%" goto deps
  set "ERRMSG=Found Python at !PYPATH! but creating the venv failed - see the messages above. Make sure ensurepip works and antivirus isn't blocking %VENV%."
  goto fail
)

echo No Python found anywhere. Installing Python 3.12 (per-user, no admin)...
where winget >nul 2>&1
if errorlevel 1 (
  set "ERRMSG=winget is not available. Install Python 3.12 from https://www.python.org/downloads/windows/ - tick 'Add python.exe to PATH' - then run setup again."
  goto fail
)

winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
set "WINGET_RC=%ERRORLEVEL%"

REM PATH is stale in this window, so locate the freshly installed python.exe.
call :find_python
if not defined PYPATH (
  if "%WINGET_RC%"=="0" (
    set "ERRMSG=Python was installed but could not be located automatically. Close this window, open a NEW one, and run setup.bat again."
  ) else (
    set "ERRMSG=winget could not install Python - cancelled, offline, or unavailable. Install Python 3.12 from https://www.python.org/downloads/windows/ with 'Add python.exe to PATH', then re-run setup."
  )
  goto fail
)

echo Using Python: !PYPATH!
"!PYPATH!" -m venv "%VENV%"
if not exist "%VPY%" (
  set "ERRMSG=Failed to create the virtual environment."
  goto fail
)

:deps
echo(
echo Installing dependencies (the first time can take several minutes)...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  set "ERRMSG=Dependency installation failed. Check your internet connection and run setup again."
  goto fail
)

REM Mark deps as fully installed. run.bat gates on this file, so an interrupted
REM ~1GB pip install (which still leaves python.exe behind) isn't mistaken for a
REM ready environment - the next launch re-runs setup instead of failing to import.
>"%VENV%\.setup_ok" echo ok

REM ---- 2. Download the offline (Argos) language pack for the default languages ----
REM 'offline' is the default translate engine, so fetch its model now. Best-effort:
REM a failure here only warns (the app still runs; the pack can be fetched later
REM from Settings -> Offline model).
echo(
echo Downloading the offline translation model...
"%VPY%" -m screen_translator.download_offline
if errorlevel 1 (
  echo(
  echo NOTE: the offline model did not download. The app still works - retry from
  echo       Settings -^> Offline model, or just re-run setup.bat later.
)

echo(
echo ============================================================
echo   Setup complete. Double-click run.bat to start the app.
echo ============================================================
if /i not "%~1"=="--from-run" pause
exit /b 0

REM ---- helper: set PYPATH to an installed python.exe, or leave it empty ----
REM Looks only at the TOP of each PythonNNN folder; a recursive search would also
REM match the venv template at Lib\venv\scripts\nt\python.exe (the wrong one).
:find_python
set "PYPATH="
REM Prefer the py launcher - it always resolves the NEWEST installed Python 3.x,
REM so a newer-than-3.12 install is reused (never downgraded).
set "_PYL=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
if not exist "%_PYL%" set "_PYL=%WINDIR%\py.exe"
if exist "%_PYL%" for /f "delims=" %%P in ('"%_PYL%" -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYPATH=%%P"
if defined PYPATH goto :eof
REM Fallback: scan standard install folders (top level only).
for /f "delims=" %%I in ('dir /b /ad "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
  if exist "%LOCALAPPDATA%\Programs\Python\%%I\python.exe" set "PYPATH=%LOCALAPPDATA%\Programs\Python\%%I\python.exe"
)
if not defined PYPATH for /f "delims=" %%I in ('dir /b /ad "%ProgramFiles%\Python\Python3*" 2^>nul') do (
  if exist "%ProgramFiles%\Python\%%I\python.exe" set "PYPATH=%ProgramFiles%\Python\%%I\python.exe"
)
if not defined PYPATH if exist "%ProgramFiles%\Python312\python.exe" set "PYPATH=%ProgramFiles%\Python312\python.exe"
goto :eof

:fail
echo(
echo ============================================================
echo   ERROR: !ERRMSG!
echo ============================================================
if /i not "%~1"=="--from-run" pause
exit /b 1
