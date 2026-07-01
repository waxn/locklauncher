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
echo Reading exe name from config.ini...
for /f "usebackq delims=" %%A in (`py -c "import configparser; c=configparser.ConfigParser(); c.read('config.ini'); print(c.get('build','exe_name',fallback='LockLauncher'))"`) do set EXE_NAME=%%A
echo Exe name: %EXE_NAME%

echo.
echo Building %EXE_NAME%.exe...
py -m PyInstaller ^
    --onefile ^
    --windowed ^
    --add-data "config.ini;." ^
    --name "%EXE_NAME%" ^
    launcher.py

if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo Build complete: dist\%EXE_NAME%.exe
echo Copy it into the shared Proton Drive folder alongside the Excel file.
pause
