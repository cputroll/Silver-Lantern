@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Apply-HeyListen-Preset.ps1" %*
pause
