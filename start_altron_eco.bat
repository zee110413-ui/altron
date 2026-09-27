@echo off
chcp 65001 >nul
title Альтрон — экономный режим
cd /d "%~dp0brain"
".venv\Scripts\python.exe" altron.py --profile eco
pause
