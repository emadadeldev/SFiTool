# Smart Fontimage

Source repository for the **Smart Fontimage** Font Atlas editor.

This folder is prepared for public Git hosting so others can run, build, and improve the tool.

## Included

- `editor.py` main application
- `utils.py` helpers
- `run_my_tool.py` Python launcher
- `run.bat` one-click local run script (Windows)
- `build.bat` one-click EXE build script (Windows)
- `font_editor.spec` PyInstaller build config
- `requirements.txt` dependencies
- `smart_fontimage.ico` app icon

## Run (development)

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python run_my_tool.py
```

## Build EXE (Windows)

```powershell
.\build.bat
```

Output:

- `dist/Smart Fontimage.exe`
