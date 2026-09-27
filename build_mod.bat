@echo off
chcp 65001 >nul
title Сборка мода Альтрона
set "ROOT=%~dp0"
set "JAVA_HOME=%ROOT%tools\jdk-17.0.20.1+1"
set "GRADLE_USER_HOME=%ROOT%tools\gradle-home"
set "PACK_MODS=%APPDATA%\.minecraft\versions\Total War - TaCZ, SuperbWarfare, SurvivalInstict Total War-v1\mods"

echo Собираю мод Альтрона (первый раз 5-15 минут: скачиваются файлы Forge)...
cd /d "%ROOT%mod"
call gradlew.bat --no-daemon --console=plain build
if errorlevel 1 (
    echo.
    echo ОШИБКА СБОРКИ. Скопируй текст выше и покажи его Claude.
    pause
    exit /b 1
)
copy /y "%ROOT%mod\build\libs\altron-0.1.0.jar" "%PACK_MODS%\altron-0.1.0.jar" >nul
echo.
echo Готово: мод установлен в сборку Total War.
pause
