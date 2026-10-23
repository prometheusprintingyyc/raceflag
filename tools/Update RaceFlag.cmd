@echo off
if not exist "%~dp0update-raceflag.ps1" (
    echo Extract all the files first, then double-click Update RaceFlag.cmd again.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-raceflag.ps1" %*
pause
