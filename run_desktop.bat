@echo off
cd /d "%~dp0"
echo Starting WSJT-X Operator Console (Desktop Mode)...
.\.venv\Scripts\python.exe desktop.py
if %ERRORLEVEL% NEQ 0 pause
