@echo off
cd /d "%~dp0"
set LAUNCHER=
if exist adventure_time_launcher.py set LAUNCHER=adventure_time_launcher.py
if "%LAUNCHER%"=="" if exist ckd_launcher.py set LAUNCHER=ckd_launcher.py
if "%LAUNCHER%"=="" (
  echo Could not find the BMO launcher ^(adventure_time_launcher.py or ckd_launcher.py^) in this folder.
  pause
  exit /b 1
)
python %LAUNCHER%
if errorlevel 1 (
  echo.
  echo BMO closed with an error. If Python or a module was not found, run install.bat first.
  pause
)
