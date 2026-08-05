# -*- coding: utf-8 -*-
"""
FocusFlow - 效率追踪器 v3.11
模块化重构 + 全面优化 + 年度归档 + 纯键盘统计

v3.11 变更：全新 DeepSeek 风格界面（液态玻璃卡片 + 渐变背景 + 顶部导航 +
           主卡片 + 无 emoji）；窗口与弹窗居中；移除插件内嵌视图；
           Edge 历史趋势图改用 Tk Canvas（不再依赖 matplotlib）。

v3.10 变更：按键长按自动重复过滤；新增"清除今日按键"；修复界面卡顿
           （非阻塞 flush + flush 事件残留 bug）；记账本恢复"距今多久"(多选)、
           月度汇总分类明细按净值统计、分页每 10 条、日期范围筛选生效。

模块结构：
- config.py        配置管理（config.ini）
- logger.py        日志（RotatingFileHandler 5MB×3 + 全局异常钩子）
- database.py      数据库（年度归档/WAL/单写线程/缓存/备份/VACUUM/清理）
- stats.py         CPM 计算（带锁+缓存）
- listener.py      键盘监听（暂停/过滤）
- autostart.py     开机自启（注册表）
- hotkey.py        全局热键（Ctrl+Shift+F）
- tray.py          系统托盘（暂停态图标/tooltip）
- floating_window.py 悬浮窗
- exporter.py      数据导出（CSV/HTML）
- gui.py           主界面（现代主题/汉化/分组/趋势图）
- cli.py           命令行接口
- shutdown.py      优雅退出（独立模块，避免循环导入）
- key_counter.py   主入口（本文件）

v3.9 变更：移除 mouse_counter.py 模块及全部鼠标统计功能。
"""

import os
import sys
import signal
import importlib
import importlib.util

# ====================================================================
# PyInstaller 打包后的模块加载器
# 解决问题：PyInstaller 有时不会把某些 .py 模块编译进 PYZ 归档，
# 导致运行时 ModuleNotFoundError。
#
# 解决方案：如果 .py 文件作为数据文件存在于 _MEIPASS（通过 --add-data），
# 这里会动态加载它们并注册到 sys.modules，使后续 import 语句能找到。
# ====================================================================
def _load_local_modules():
    """从 _MEIPASS 或当前目录加载所有本地 .py 模块"""
    # 确定搜索目录
    search_dirs = []
    if getattr(sys, 'frozen', False):
        # PyInstaller onefile 模式：资源解压到 _MEIPASS
        _meipass = getattr(sys, '_MEIPASS', None)
        if _meipass:
            search_dirs.append(_meipass)
        # exe 所在目录（--add-data 可能放到这里）
        _base_dir = os.path.dirname(sys.executable)
        search_dirs.append(_base_dir)
    else:
        # 开发模式：脚本所在目录
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))

    # 需要加载的本地模块列表
    local_modules = [
        'config', 'logger', 'database', 'stats', 'listener',
        'autostart', 'hotkey', 'tray', 'floating_window',
        'exporter', 'gui', 'cli', 'shutdown',
    ]

    for mod_name in local_modules:
        # 如果模块已经在 sys.modules 中，跳过
        if mod_name in sys.modules:
            continue

        # 在搜索目录中查找 .py 文件
        for search_dir in search_dirs:
            mod_path = os.path.join(search_dir, mod_name + '.py')
            if os.path.exists(mod_path):
                try:
                    # 用 importlib 动态加载模块
                    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[mod_name] = mod
                        spec.loader.exec_module(mod)
                        # 把搜索目录加入 sys.path，以便模块内部的 import 能找到其他本地模块
                        if search_dir not in sys.path:
                            sys.path.insert(0, search_dir)
                        break
                except Exception as _e:
                    # 加载失败不阻塞，后续 import 会给出更清晰的错误
                    pass

# 在任何 import 之前执行模块加载
_load_local_modules()

# 确保搜索路径包含当前目录（开发模式）和 _MEIPASS（打包模式）
if getattr(sys, 'frozen', False):
    _base_dir = os.path.dirname(sys.executable)
    if _base_dir not in sys.path:
        sys.path.insert(0, _base_dir)
    _meipass = getattr(sys, '_MEIPASS', None)
    if _meipass and _meipass not in sys.path:
        sys.path.insert(0, _meipass)
else:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)

from logger import log, install_global_excepthook, get_logger
from config import config, APP_NAME, APP_DISPLAY_NAME

log_main = get_logger('main')

# ====================================================================
# 显式导入所有自定义模块
# 目的：让 PyInstaller 静态分析器能发现这些模块，确保打包时全部收集
# 如果不在这里显式 import，PyInstaller 可能遗漏某些模块（如 stats.py）
# 导致运行时 ModuleNotFoundError
#
# 注意：用 try/except 包裹依赖第三方库的模块，这样在缺少依赖时
# 不会阻塞启动，但 PyInstaller 仍能通过静态分析发现这些 import 语句
# ====================================================================
import database          # noqa: E402
import stats             # noqa: E402
import exporter          # noqa: E402
import shutdown          # noqa: E402
import edge_history      # noqa: E402
import accounting        # noqa: E402
import scheduler         # noqa: E402
import plugins           # noqa: E402

# 以下模块依赖第三方库，用 try 包裹
# 这些 import 主要是给 PyInstaller 静态分析用的
# 运行时会在 main() 中按需导入
try:
    import autostart         # noqa: E402
except Exception as _e:
    log_main.warning('导入 autostart 失败: %s', _e)
try:
    import hotkey            # noqa: E402
except Exception as _e:
    log_main.warning('导入 hotkey 失败: %s', _e)
try:
    import listener          # noqa: E402
except Exception as _e:
    log_main.warning('导入 listener 失败: %s', _e)
try:
    import tray              # noqa: E402
except Exception as _e:
    log_main.warning('导入 tray 失败: %s', _e)
try:
    import floating_window   # noqa: E402
except Exception as _e:
    log_main.warning('导入 floating_window 失败: %s', _e)
try:
    import gui               # noqa: E402
except Exception as _e:
    log_main.warning('导入 gui 失败: %s', _e)
try:
    import cli               # noqa: E402
except Exception as _e:
    log_main.warning('导入 cli 失败: %s', _e)


# ==================== 单实例检查（Windows）====================
def check_single_instance() -> bool:
    """检查是否已有实例在运行"""
    try:
        import ctypes
        from ctypes import wintypes
        mutex_name = "Global\\FocusFlowAppMutex"
        CreateMutex = ctypes.windll.kernel32.CreateMutexW
        CreateMutex.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        CreateMutex.restype = wintypes.HANDLE
        ERROR_ALREADY_EXISTS = 183
        mutex = CreateMutex(None, True, mutex_name)
        if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        global _mutex_handle
        _mutex_handle = mutex
        return True
    except Exception as e:
        log_main.warning('单实例检查失败（非 Windows 平台？）: %s', e)
        return True


_mutex_handle = None


def _signal_handler(signum, frame):
    log_main.info('收到信号 %d，开始优雅退出', signum)
    from shutdown import graceful_shutdown
    graceful_shutdown()


def main():
    install_global_excepthook()
    log_main.info('=== %s 启动 ===', APP_DISPLAY_NAME)

    # 信号捕获（Linux/Mac 的 SIGTERM/SIGINT）
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        pass  # Windows 子线程不支持

    # CLI 模式
    from cli import run_cli
    cli_ret = run_cli()
    if cli_ret != -1:
        sys.exit(cli_ret)

    # GUI 模式
    if not check_single_instance():
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, "FocusFlow 已经在运行了，在系统托盘里哦~",
            "FocusFlow", 64
        )
        sys.exit(0)

    import database
    database.init_db()

    from listener import start_listener
    start_listener()

    from gui import FocusFlowApp
    hidden = '--hidden' in sys.argv or config.getbool('gui', 'start_to_tray', False)
    app = FocusFlowApp(hidden=hidden)

    from tray import init_tray
    init_tray(app)

    # 注册全局热键
    from hotkey import register_default_hotkey
    register_default_hotkey(app.toggle_window_visibility)

    # 首次启动提示
    if not hidden and config.getbool('gui', 'show_first_run_tip', True):
        app.root.after(500, lambda: _show_first_run_tip(app))

    try:
        app.run()
    except Exception as e:
        log_main.error('主循环异常: %s', e, exc_info=True)
        from shutdown import graceful_shutdown
        graceful_shutdown()


def _show_first_run_tip(app):
    """首次启动提示，带"不再显示"复选框"""
    from tkinter import Toplevel, Checkbutton, BooleanVar, ttk, Label, Frame

    top = Toplevel(app.root)
    top.title("FocusFlow - 使用提示")
    top.geometry("480x360")
    top.resizable(False, False)
    top.transient(app.root)
    top.grab_set()

    # 弹窗居中显示
    try:
        top.update_idletasks()
        sw = top.winfo_screenwidth()
        sh = top.winfo_screenheight()
        x = max(0, (sw - 480) // 2)
        y = max(0, (sh - 360) // 3)
        top.geometry(f"+{x}+{y}")
    except Exception:
        pass

    msg = (
        "欢迎使用 FocusFlow！程序已启动，开始记录键盘活跃数据。\n\n"
        "• 关闭窗口 = 最小化到托盘后台运行\n"
        "• 悬停托盘图标 = 查看今日活跃与速度\n"
        "• 双击托盘图标 = 打开统计面板\n"
        "• 快捷键 Ctrl+Shift+F = 显示/隐藏窗口\n"
        "• 托盘菜单可暂停记录（隐私保护）\n"
        "• 支持导出 CSV/HTML、清理旧数据、压缩数据库\n"
        "• 可在操作区开启悬浮窗实时显示\n"
        "• 数据按年度归档，自动管理文件大小\n\n"
        "数据完全本地存储，不上传任何信息。"
    )

    Label(top, text=msg, justify='left', font=("Segoe UI", 10),
          padx=20, pady=15).pack(fill='both', expand=True)

    dont_show = BooleanVar(value=False)
    cb_frame = Frame(top)
    cb_frame.pack(fill='x', padx=20)
    Checkbutton(cb_frame, text="不再显示此提示", variable=dont_show,
                font=("Segoe UI", 9)).pack(side='left')

    def on_close():
        if dont_show.get():
            config.set('gui', 'show_first_run_tip', 'false')
        top.destroy()

    btn_frame = Frame(top)
    btn_frame.pack(fill='x', padx=20, pady=10)
    ttk.Button(btn_frame, text="我知道了", command=on_close).pack(side='right')


if __name__ == '__main__':
    main()
