@echo off
rem Double-click to install ShapeShifter for the current user.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
