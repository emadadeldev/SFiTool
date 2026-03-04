@echo off
chcp 65001 >nul
REM Build Font Image Editor exe using PyInstaller
REM This script creates a standalone executable

SETLOCAL

if NOT EXIST ".venv\Scripts\activate.bat" (
    echo Creating virtual environment .venv...
    python -m venv .venv
    echo Upgrading pip...
    .venv\Scripts\python -m pip install --upgrade pip >nul
)

call .venv\Scripts\activate.bat

echo Installing build dependencies...
.venv\Scripts\python -m pip install PyInstaller PyQt6 Pillow PyYAML -q

echo.
echo Building executable...
.venv\Scripts\pyinstaller font_editor.spec --clean

echo.
echo ============================================
echo Build complete!
echo.
echo The executable is located at:
echo   dist\Smart Fontimage.exe
echo.
echo You can now run it without needing Python installed.
echo ============================================

ENDLOCAL
pause
