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
if not errorlevel 1 goto built
rem The Forge server did not answer (it is often slow or down): the plugin and Minecraft were downloaded by an
rem earlier build, so build from what is already on this PC.
echo.
echo Не достучался до сервера Forge - собираю из того, что уже скачано раньше...
echo Could not reach the Forge server - building from what was downloaded before...
call gradlew.bat --no-daemon --console=plain --offline build
if not errorlevel 1 goto built
if defined GRADLE_USER_HOME (
    set "GRADLE_USER_HOME="
    echo Пробую ещё раз с общим кэшем Gradle в папке пользователя...
    echo Once more with the common Gradle cache in the user folder...
    call gradlew.bat --no-daemon --console=plain --offline build
    if not errorlevel 1 goto built
)
echo.
echo ОШИБКА СБОРКИ / BUILD FAILED
echo Если выше "could not resolve" или "was not found" - нет связи с maven.minecraftforge.net, а раньше мод здесь не
echo собирался. Проверь: curl.exe -I https://maven.minecraftforge.net/  - или скачай готовый мод со страницы
echo GitHub Actions ^(Mod build and release, файл altron-0.1.0^) и положи jar в mod\build\libs.
echo "could not resolve" / "was not found" above: maven.minecraftforge.net is not reachable. Or take the built jar
echo from GitHub Actions ^(Mod build and release^) and put it into mod\build\libs.
pause
exit /b 1

:built
echo.
echo Готово. Мозг Альтрона сам поставит мод в выбранную сборку при запуске.
echo Done. Altron's brain installs the mod into the chosen modpack when it starts.
pause
