@echo off
setlocal
cd /d "%~dp0"
python -m envmanager_v3 --backend file --data-dir "%LOCALAPPDATA%\EnvManagerV3-Isolated" %*
if errorlevel 1 pause
