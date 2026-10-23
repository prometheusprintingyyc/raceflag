@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-raceflag.ps1" %*
pause
