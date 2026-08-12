@echo off
REM FocusFlow Tauri 版构建打包脚本。
REM 构建 release 并组装 dist-tauri\FocusFlow\（保留 data/plugins）。
setlocal

call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%

echo [1/2] Building Tauri release...
cargo build --release -p focusflow-desktop || goto :err

echo [2/2] Assembling dist-tauri folder...
set DIST=E:\mydata\DeepSeekdata\code\FocusFlow\dist-tauri\FocusFlow
if not exist "%DIST%" mkdir "%DIST%"
if not exist "%DIST%\plugins" mkdir "%DIST%\plugins"
if not exist "%DIST%\data" mkdir "%DIST%\data"
copy /y target\release\focusflow-desktop.exe "%DIST%\FocusFlow.exe" >nul
REM Keep existing config.ini (user settings); write default only on first deploy
if not exist "%DIST%\config.ini" copy /y config.ini "%DIST%\config.ini" >nul
copy /y crates\core\plugins\*.lua "%DIST%\plugins\" >nul

echo.
echo Done! Run: %DIST%\FocusFlow.exe
goto :eof

:err
echo Build failed!
exit /b 1
