@echo off
title WSJT-X Operator Console 1.1
cd /d "%~dp0"
echo.
echo ============================================================
echo              WSJT-X OPERATOR CONSOLE 1.1
echo ============================================================
echo.
echo Keep this window open while using the program.
echo The first start may take a few minutes.
echo.
where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found.
  echo Install Python 3 from https://www.python.org/downloads/
  echo During installation, check "Add Python to PATH".
  echo Then run this file again.
  echo.
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating the private Python environment...
  py -m venv .venv
  if errorlevel 1 (
    echo ERROR: Python could not create the environment.
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate
echo Checking required Python packages...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo ERROR: Required packages could not be installed.
  echo Check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Starting Operator Console...
echo Browser address: http://127.0.0.1:8080
echo To stop the program, close this window or press Ctrl+C.
echo.
start "" http://127.0.0.1:8080
python -m uvicorn app:app --host 0.0.0.0 --port 8080
echo.
echo Operator Console has stopped.
pause
