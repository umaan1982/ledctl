@echo off
REM Usage: ledctl info | color #FF8000 | brightness 128 | calibrate | sync | tray | off
"%~dp0.venv\Scripts\python" -m ledctl %*
