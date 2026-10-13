@echo off
rem POPME uninstall: stops POPME and removes its shortcuts (asks before deleting data).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
echo.
pause
