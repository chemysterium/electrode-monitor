@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.cmd once before starting the app.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" electrode_monitor.py
if errorlevel 1 pause
