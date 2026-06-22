@echo off
REM Same as run.bat, but with verbose (debug) logging in the console window.
setlocal
cd /d "%~dp0"
set "ST_LOG=debug"
call "%~dp0run.bat"
