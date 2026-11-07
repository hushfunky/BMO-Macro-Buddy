@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install 64-bit Python 3.10 or newer from python.org and tick Add Python to PATH.
  pause
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)"
if errorlevel 1 (
  echo BMO needs 64-bit Python 3.10 or newer. Install it from python.org, then run this again.
  pause
  exit /b 1
)
echo Installing BMO requirements...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo The install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Done. Double-click start_bmo.bat to launch.
pause
