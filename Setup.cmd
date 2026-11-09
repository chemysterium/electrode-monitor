@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install .
if errorlevel 1 goto failed
echo Setup complete. Open Start Electrode Monitor.cmd.
pause
exit /b 0
:failed
echo Setup failed. Python 3.10 or newer with Tkinter and internet access is required.
pause
exit /b 1
