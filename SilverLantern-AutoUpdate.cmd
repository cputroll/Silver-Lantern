@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Watch-Game.ps1" %*
if errorlevel 1 pause
