@echo off
setlocal
cd /d "%~dp0"
python tools\setup.py %*
if errorlevel 1 (
  echo Setup did not finish. See the error above. Your saves have not been replaced.
  pause
  exit /b 1
)
pause
