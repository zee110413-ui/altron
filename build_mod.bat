@echo off
chcp 65001 >nul
title Сборка мода Альтрона / Building the Altron mod
set "ROOT=%~dp0"
rem JDK 17: tools\jdk-17* next to this file if there is one, otherwise the JAVA_HOME already set on this PC
for /d %%J in ("%ROOT%tools\jdk-17*") do set "JAVA_HOME=%%~fJ"
if exist "%ROOT%tools" set "GRADLE_USER_HOME=%ROOT%tools\gradle-home"
if not defined JAVA_HOME (
    echo Нужна Java 17 ^(JDK^). Установи её или положи в папку tools\jdk-17...
    echo Java 17 ^(JDK^) is required. Install it or put it into tools\jdk-17...
    pause
    exit /b 1
)

echo Собираю мод Альтрона (первый раз 5-15 минут: скачиваются файлы Forge)...
echo Building the Altron mod (the first time takes 5-15 minutes: Forge is downloaded)...
cd /d "%ROOT%mod"
call gradlew.bat --no-daemon --console=plain build
if errorlevel 1 (
    echo.
    echo ОШИБКА СБОРКИ / BUILD FAILED
    pause
    exit /b 1
)
echo.
echo Готово. Мозг Альтрона сам поставит мод в выбранную сборку при запуске.
echo Done. Altron's brain installs the mod into the chosen modpack when it starts.
pause
