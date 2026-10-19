@echo off
setlocal
cd /d "%~dp0"
python -m envmanager_v3 %*
if errorlevel 1 pause
