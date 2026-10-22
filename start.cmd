@echo off
cd /d "%~dp0"
if exist "runtime\app\YutoNight.exe" (
  start "" "runtime\app\YutoNight.exe"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m yutonight
  exit /b 0
)
echo No portable application or source environment found. Copy the whole folder, or run install.cmd.
pause
exit /b 1
