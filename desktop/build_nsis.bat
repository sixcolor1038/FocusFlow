@echo off
REM FocusFlow Tauri NSIS 安装包构建脚本。
setlocal
call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%
cd /d %~dp0
call npx --yes @tauri-apps/cli build
exit /b %ERRORLEVEL%
