@echo off
REM Soterian Clock Widget — Windows Build
REM
REM Prerequisites:
REM   - Python 3.10+ installed
REM   - pip install pyinstaller pystray Pillow requests
REM
REM Usage:
REM   cd engines\calendar
REM   installer\build_windows.bat
REM

echo === Soterian Clock Widget — Windows Build ===

python --version >nul 2>&1 || (echo ERROR: Python not found & exit /b 1)
python -c "import PyInstaller" 2>nul || (echo ERROR: PyInstaller not found. Run: pip install pyinstaller & exit /b 1)

REM Clean
if exist dist\soterian-clock rmdir /s /q dist\soterian-clock
if exist build\soterian-clock rmdir /s /q build\soterian-clock

REM Build
echo Building...
python -m PyInstaller installer\soterian_clock.spec --noconfirm --clean

if not exist dist\soterian-clock\soterian-clock.exe (
    echo ERROR: Build failed
    exit /b 1
)

echo.
echo Build successful!
dir dist\soterian-clock\soterian-clock.exe

REM Package
echo Packaging...
powershell -Command "Compress-Archive -Path dist\soterian-clock -DestinationPath dist\soterian-clock-2.0.0-windows-x64.zip -Force"
echo Archive: dist\soterian-clock-2.0.0-windows-x64.zip

echo.
echo === Done ===
echo.
echo To install: extract the zip, run soterian-clock.exe
echo To autostart: place a shortcut in shell:startup
pause
