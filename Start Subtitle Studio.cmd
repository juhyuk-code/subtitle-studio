@echo off
setlocal
cd /d "%~dp0"
title Subtitle Studio
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1"
if errorlevel 1 (
  echo.
  echo Subtitle Studio could not start.
  pause
)
