@echo off
chcp 65001 >nul
REM Simple launcher for My tool Font Image Editor (Windows)
REM Place this file inside the "My tool" folder and double-click to run.

SETLOCAL
if NOT EXIST ".venv\Scripts\activate.bat" (
    echo Creating virtual environment .venv...
    python -m venv .venv
    echo Upgrading pip and installing requirements...
    .venv\Scripts\python -m pip install --upgrade pip >nul
    if exist requirements.txt (
        .venv\Scripts\python -m pip install -r requirements.txt
    )
)

call .venv\Scripts\activate.bat
echo Running Font Image Editor...
python run_my_tool.py

ENDLOCAL
pause
