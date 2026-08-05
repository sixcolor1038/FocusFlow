# -*- mode: python ; coding: utf-8 -*-
"""FocusFlow PyInstaller spec 文件（精简版 - 无 matplotlib）

用法：pyinstaller FocusFlow.spec
"""

import os
import sys

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in dir() else os.getcwd()

block_cipher = None

# 所有自定义模块 + 第三方隐藏依赖
hiddenimports = [
    # 自定义模块
    'config', 'logger', 'database', 'stats', 'listener',
    'autostart', 'hotkey', 'tray', 'floating_window',
    'exporter', 'gui', 'cli', 'shutdown', 'edge_history',
    'accounting', 'scheduler', 'plugins',
    'pomodoro', 'rest_reminder',
    # 第三方库
    'pystray._win32',
    'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageFont', 'PIL.ImageTk',
]

# 需要完整收集的包（移除 matplotlib，大幅减小体积）
collect_all_packages = ['pystray', 'pynput']

datas = []
binaries = []
for pkg in collect_all_packages:
    try:
        from PyInstaller.utils.hooks import collect_all
        pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hiddenimports
    except Exception as e:
        print(f'Warning: collect_all({pkg}) failed: {e}', file=sys.stderr)

# 添加所有本地 .py 文件作为数据文件
local_modules = [
    'config.py', 'logger.py', 'database.py', 'stats.py', 'listener.py',
    'autostart.py', 'hotkey.py', 'tray.py', 'floating_window.py',
    'exporter.py', 'gui.py', 'cli.py', 'shutdown.py',
    'edge_history.py', 'accounting.py', 'scheduler.py', 'plugins.py',
    'pomodoro.py', 'rest_reminder.py',
]
for mod in local_modules:
    mod_path = os.path.join(SPEC_DIR, mod)
    if os.path.exists(mod_path):
        datas.append((mod_path, '.'))

# 添加插件目录（包括内置的 accounting_data_maintenance.py 插件）
plugins_dir = os.path.join(SPEC_DIR, 'plugins')
if os.path.isdir(plugins_dir):
    for fname in os.listdir(plugins_dir):
        if fname.endswith('.py'):
            datas.append((os.path.join(plugins_dir, fname), 'plugins'))

# 添加图标
ico_path = os.path.join(SPEC_DIR, 'focusflow.ico')
if os.path.exists(ico_path):
    datas.append((ico_path, '.'))


a = Analysis(
    ['key_counter.py'],
    pathex=[SPEC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(SPEC_DIR, 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    # 排除不需要的大模块（减小体积）
    excludes=[
        'tkinter.test', 'unittest', 'test', 'pydoc.data',
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'IPython', 'jupyter', 'notebook',
        'email', 'http', 'urllib', 'xmlrpc',
        'pydoc', 'doctest', 'argparse',
        'distutils', 'setuptools', 'pip',
        'Cython', 'numpy.tests', 'pandas.tests',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FocusFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='focusflow.ico',
    version='version_info.txt',
)
