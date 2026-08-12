@echo off
REM FocusFlow-rs build-dist script.
REM Builds release and assembles a portable folder.
REM Output: dist\FocusFlow\
setlocal

call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%

echo [1/3] Building release...
cargo build --release -p focusflow-app || goto :err

echo [2/3] Assembling dist folder...
set DIST=E:\mydata\DeepSeekdata\code\FocusFlow-rs\dist\FocusFlow
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%"
mkdir "%DIST%\plugins"
mkdir "%DIST%\data"

copy /y target\release\focusflow-app.exe "%DIST%\FocusFlow.exe" >nul
copy /y crates\core\plugins\*.lua "%DIST%\plugins\" >nul

echo [3/3] Done!
echo.
echo Dist folder: %DIST%
echo Run: %DIST%\FocusFlow.exe
goto :eof

:err
echo Build failed!
exit /b 1
