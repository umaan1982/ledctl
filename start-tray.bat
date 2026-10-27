@echo off
REM Starts the tray icon with screen sync on, without a console window. Safe to close this window.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0ledctl_tray.pyw" --sync
