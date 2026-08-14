@echo off
REM FocusFlow build-dist script (portable folder, main Tauri desktop build).
REM Output: dist\FocusFlow\
REM NOTE: legacy crates/app (egui) is excluded from the workspace; this
REM script builds the main focusflow-desktop (Tauri) into the dist folder.
setlocal

call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%

echo [1/3] Building release...
cargo build --release -p focusflow-desktop || goto :err

echo [2/3] Assembling dist folder...
set DIST=E:\mydata\DeepSeekdata\code\FocusFlow\dist\FocusFlow
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%"
mkdir "%DIST%\plugins"
mkdir "%DIST%\data"
if not exist "%DIST%\config.ini" copy /y config.ini "%DIST%\config.ini" >nul

copy /y target\release\focusflow-desktop.exe "%DIST%\FocusFlow.exe" >nul
copy /y crates\core\plugins\*.lua "%DIST%\plugins\" >nul

echo [3/3] Done!
echo.
echo Dist folder: %DIST%
echo Run: %DIST%\FocusFlow.exe
goto :eof

:err
echo Build failed!
exit /b 1
