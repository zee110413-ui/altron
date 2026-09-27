@echo off
title Altron polygon
cd /d "%~dp0brain"
".venv\Scripts\python.exe" polygon.py %*
pause
