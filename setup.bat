@echo off
REM One-time setup: creates a private virtualenv in .venv and installs dependencies.
cd /d "%~dp0"
python -m venv .venv || (echo Install Python 3.10+ from python.org first & exit /b 1)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install dxcam
echo.
echo Done. Close DX Light, plug in the strip, then run:  ledctl info
