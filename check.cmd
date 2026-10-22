@echo off
cd /d "%~dp0"
if exist "runtime\app\YutoNight.exe" goto portable
if exist ".venv\Scripts\python.exe" goto source
echo No portable application or source environment found. Copy the whole folder, or run install.cmd.
pause
exit /b 1

:portable
set "REPORT=%~dp0portable-self-check.json"
if exist "%REPORT%" del /q "%REPORT%"
start /wait "" "runtime\app\YutoNight.exe" --self-check "%REPORT%"
set "STATUS=%ERRORLEVEL%"
if exist "%REPORT%" (
  type "%REPORT%"
) else (
  echo Self-check did not produce a report.
)
pause
exit /b %STATUS%

:source
".venv\Scripts\python.exe" -m yutonight.doctor
set "STATUS=%ERRORLEVEL%"
pause
exit /b %STATUS%
