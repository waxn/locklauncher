@echo off
REM build.bat — build LockLauncher.exe
REM
REM Before running:
REM   1. Edit config.ini with the real server URL, API key, and Excel filename.
REM   2. Run this script on Windows with Python installed.
REM
REM Output: dist\LockLauncher.exe — copy this into the shared drive folder.

echo Installing dependencies...
py -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo.
echo Building LockLauncher.exe...
py -m PyInstaller ^
    --onefile ^
    --windowed ^
    --add-data "config.ini;." ^
    --name LockLauncher ^
    launcher.py

if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo Build complete: dist\LockLauncher.exe
echo Copy it into the shared Proton Drive folder alongside the Excel file.
pause
