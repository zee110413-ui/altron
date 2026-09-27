@echo off
chcp 65001 >nul
title Альтрон
cd /d "%~dp0brain"
".venv\Scripts\python.exe" altron.py
pause
