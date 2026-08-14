@echo off
REM FocusFlow Tauri build script.
REM Builds release and assembles dist-tauri\FocusFlow\ (portable folder).
REM NOTE: no longer deploys to the install dir (E:\software\aFocusFlow is the
REM production folder and must not be overwritten).
setlocal

call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%

echo [1/3] Building Tauri release...
cargo build --release -p focusflow-desktop || goto :err

echo [2/3] Assembling dist-tauri folder...
set DIST=E:\mydata\DeepSeekdata\code\FocusFlow\dist-tauri\FocusFlow
if not exist "%DIST%" mkdir "%DIST%"
if not exist "%DIST%\plugins" mkdir "%DIST%\plugins"
if not exist "%DIST%\data" mkdir "%DIST%\data"
if exist "%DIST%\FocusFlow.exe" del /q "%DIST%\FocusFlow.exe"
copy /y target\release\focusflow-desktop.exe "%DIST%\FocusFlow.exe" >nul
if not exist "%DIST%\config.ini" copy /y config.ini "%DIST%\config.ini" >nul
copy /y crates\core\plugins\*.lua "%DIST%\plugins\" >nul

echo [3/3] Done!
echo.
echo Portable folder: %DIST%
echo Run: %DIST%\FocusFlow.exe
goto :eof

:err
echo Build failed!
exit /b 1
