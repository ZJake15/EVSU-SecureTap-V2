@echo off
REM Double-click entry point for EVSU SecureTap. Opens the launcher, which
REM starts the backend and lets you pick the dashboard or the entry agent -
REM no terminal commands needed.
REM
REM Uses python.exe rather than pythonw.exe on purpose: if the launcher fails
REM to start at all (missing venv, broken install), pythonw would fail
REM silently with no window and no message. The console window here is where
REM that error shows up, and the pause below keeps it readable.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Could not find .venv\Scripts\python.exe
    echo.
    echo   The project's virtual environment is missing. Follow the setup
    echo   steps in README.md once, then run this file again.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" launcher.py
if errorlevel 1 (
    echo.
    echo   The launcher exited with an error - the message above says why.
    echo.
    pause
)
