@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch.ps1" -Offline
if errorlevel 1 pause
