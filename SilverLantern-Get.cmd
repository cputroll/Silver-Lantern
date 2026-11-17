@echo off
setlocal
cd /d "%~dp0"
title HeyListen Get
if exist "%~dp0HeyListen-Get.exe" (
  "%~dp0HeyListen-Get.exe" %*
) else (
  echo HeyListen-Get.exe is missing. Extract the complete release archive first.
  pause
  exit /b 1
)
if errorlevel 1 pause
