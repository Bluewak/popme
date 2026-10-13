@echo off
rem POPME install: double-click this file (runs install.ps1 without changing the PC execution policy)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
