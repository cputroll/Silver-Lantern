@echo off
setlocal
cd /d "%~dp0"
title HeyListen Get
if exist "%~dp0SilverLantern.exe" (
  "%~dp0SilverLantern.exe" --cli %*
) else (
  echo SilverLantern.exe is missing. Extract the complete release archive first.
  pause
  exit /b 1
)
if errorlevel 1 pause
