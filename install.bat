@echo off
rem POPME install: double-click this file. Runs install.ps1 with the execution policy relaxed for this run only.
if not exist "%~dp0install.ps1" (
  echo [POPME] install.ps1 not found. Extract the whole zip first, then run install.bat in the extracted folder.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
