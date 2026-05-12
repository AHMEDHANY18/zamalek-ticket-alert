@echo off
REM Zamalek ticket alert - Windows launcher.
cd /d "%~dp0"

REM Find a working Python: try 'py' launcher first (most reliable on Windows),
REM then 'python'. The MS Store alias prints to stdout, so we check version.
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)

if not defined PYCMD (
    echo.
    echo  [X] Python is not installed on this computer.
    echo.
    echo  Install it from:  https://www.python.org/downloads/
    echo.
    echo  IMPORTANT during install:
    echo    -^> Check the box "Add python.exe to PATH" on the first screen
    echo    -^> Then click "Install Now"
    echo.
    echo  After installing, close this window and run this file again.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PYCMD%

if not exist .venv (
    echo Creating Python virtual environment...
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo Installing dependencies...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

if not exist .env (
    echo.
    echo  [!] .env file not found.
    echo      Copy .env.example to .env and fill in your Telegram details first.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting Zamalek ticket monitor. Press Ctrl+C to stop.
echo.
python monitor.py
pause
