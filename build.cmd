@echo off
REM FocusFlow-rs build helper. Loads MSVC env then runs cargo.
REM Usage:  build.cmd check   / build.cmd build / build.cmd run

setlocal

if not defined VCINSTALLDIR (
    call "C:\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul
)

set CARGO_HOME=E:\software\rust\.cargo
set RUSTUP_HOME=E:\software\rust\.rustup
set PATH=E:\software\rust\.cargo\bin;%PATH%

cargo %*
exit /b %ERRORLEVEL%
