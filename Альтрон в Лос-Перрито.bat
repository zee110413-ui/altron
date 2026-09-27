@echo off
chcp 65001 >nul
title Альтрон — Лос-Перрито (со сторожем)
cd /d "%~dp0brain"
set PYTHONIOENCODING=utf-8
echo Запускаю игру с копией Los Perrito и Альтрона. Окно игры откроется само, играй в нём.
echo Сторож сам перезапустит мозг Альтрона, если тот зависнет или упадёт. Закроешь игру — всё выключится.
".venv\Scripts\python.exe" -u supervisor.py "Los Perrito"
pause