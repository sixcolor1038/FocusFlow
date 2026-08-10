@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   FocusFlow - Build Script v1.3 (onedir)
echo ========================================
echo.

echo [1/4] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    echo Please install Python 3.8+ and add it to PATH.
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
echo Python OK
echo.

echo [2/4] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__
echo Cleaned
echo.

echo [3/4] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install dependencies.
    echo Try: pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo.
    pause
    exit /b 1
)
echo Dependencies installed
echo.

echo [4/4] Building FocusFlow.exe...
echo.
echo Listing local modules to verify they exist:
if not exist config.py echo   WARNING: config.py not found!
if not exist logger.py echo   WARNING: logger.py not found!
if not exist database.py echo   WARNING: database.py not found!
if not exist stats.py echo   WARNING: stats.py not found!
if not exist listener.py echo   WARNING: listener.py not found!
if not exist autostart.py echo   WARNING: autostart.py not found!
if not exist hotkey.py echo   WARNING: hotkey.py not found!
if not exist tray.py echo   WARNING: tray.py not found!
if not exist floating_window.py echo   WARNING: floating_window.py not found!
if not exist exporter.py echo   WARNING: exporter.py not found!
if not exist gui.py echo   WARNING: gui.py not found!
if not exist cli.py echo   WARNING: cli.py not found!
if not exist shutdown.py echo   WARNING: shutdown.py not found!
if not exist key_counter.py echo   WARNING: key_counter.py not found!
if not exist pomodoro.py echo   WARNING: pomodoro.py not found!
if not exist rest_reminder.py echo   WARNING: rest_reminder.py not found!
echo.

pyinstaller --noconfirm FocusFlow.spec
if errorlevel 1 (
    echo.
    echo Build with spec failed, trying command line...
    echo.
    pyinstaller --noconfirm --onedir --windowed ^
        --name FocusFlow ^
        --icon focusflow.ico ^
        --version-file version_info.txt ^
        --hidden-import=config ^
        --hidden-import=logger ^
        --hidden-import=database ^
        --hidden-import=stats ^
        --hidden-import=listener ^
        --hidden-import=autostart ^
        --hidden-import=hotkey ^
        --hidden-import=tray ^
        --hidden-import=floating_window ^
        --hidden-import=exporter ^
        --hidden-import=gui ^
        --hidden-import=cli ^
        --hidden-import=shutdown ^
        --hidden-import=edge_history ^
        --hidden-import=accounting ^
        --hidden-import=scheduler ^
        --hidden-import=plugins ^
        --hidden-import=pomodoro ^
        --hidden-import=rest_reminder ^
        --hidden-import=pystray._win32 ^
        --hidden-import=PIL.Image ^
        --hidden-import=PIL.ImageDraw ^
        --hidden-import=PIL.ImageFont ^
        --hidden-import=PIL.ImageTk ^
        --collect-all pystray ^
        --collect-all pynput ^
        --additional-hooks-dir hooks ^
        --paths . ^
        --add-data "config.py;." ^
        --add-data "logger.py;." ^
        --add-data "database.py;." ^
        --add-data "stats.py;." ^
        --add-data "listener.py;." ^
        --add-data "autostart.py;." ^
        --add-data "hotkey.py;." ^
        --add-data "tray.py;." ^
        --add-data "floating_window.py;." ^
        --add-data "exporter.py;." ^
        --add-data "gui.py;." ^
        --add-data "cli.py;." ^
        --add-data "shutdown.py;." ^
        --add-data "edge_history.py;." ^
        --add-data "accounting.py;." ^
        --add-data "scheduler.py;." ^
        --add-data "plugins.py;." ^
        --add-data "pomodoro.py;." ^
        --add-data "rest_reminder.py;." ^
        --exclude-module matplotlib ^
        --exclude-module numpy ^
        --exclude-module pandas ^
        --exclude-module scipy ^
        key_counter.py
    if errorlevel 1 (
        echo.
        echo Build failed. See error messages above.
        echo.
        pause
        exit /b 1
    )
)
echo.
echo ========================================
echo   Build Complete!
echo   Output: dist\FocusFlow\FocusFlow.exe (文件夹模式)
echo ========================================
echo.
pause
