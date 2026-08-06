# -*- coding: utf-8 -*-
"""
FocusFlow 开机自启模块（Windows）

方案：使用"启动文件夹"（Startup folder）快捷方式。
- 勾选开机自启 -> 在 启动文件夹 创建 FocusFlow.lnk（指向 exe，带 --hidden 参数）
- 取消勾选     -> 删除该快捷方式
- 任务管理器的"启动应用"里会显示为「已启用」，取消后即消失，符合用户直觉

说明：相比注册表 HKCU\\...\\Run 启动项，启动文件夹方式不会被
Windows 11 的 StartupApproved 机制标记为"已禁用"，更可靠。
"""

import os
import sys
import subprocess
import winreg

from logger import get_logger

log = get_logger('autostart')

AUTOSTART_KEY_NAME = "FocusFlow"
SHORTCUT_NAME = "FocusFlow.lnk"

# 注册表 Run 键（旧方案，用于清理兼容）
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
# 旧方案的启动标记（需一并清理，避免残留"已禁用"状态）
STARTUP_APPROVED_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


def get_exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])


def _get_startup_dir() -> str:
    """启动文件夹路径（用户级 shell:startup）"""
    return os.path.join(
        os.environ.get('APPDATA', os.path.expanduser('~')),
        r'Microsoft\Windows\Start Menu\Programs\Startup'
    )


def _get_shortcut_path() -> str:
    return os.path.join(_get_startup_dir(), SHORTCUT_NAME)


def _create_shortcut():
    """用 PowerShell WScript.Shell 创建启动快捷方式"""
    exe = get_exe_path()
    workdir = os.path.dirname(exe)
    lnk = _get_shortcut_path()
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    ps_cmd = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{exe}'; "
        f"$s.Arguments = '--hidden'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        f"$s.Description = 'FocusFlow - 效率追踪器'; "
        f"$s.Save()"
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f'PowerShell 创建快捷方式失败 (code={result.returncode})')
    return lnk


def _shortcut_points_to_exe() -> bool:
    """检查快捷方式是否指向当前 exe"""
    lnk = _get_shortcut_path()
    if not os.path.isfile(lnk):
        return False
    exe = get_exe_path()
    ps_cmd = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"[Console]::Write($s.TargetPath)"
    )
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
            capture_output=True, text=True
        )
        target = result.stdout.strip().strip('"')
        return os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(exe))
    except Exception:
        return False


# ---------- 清理旧的注册表方案（兼容/防止双重启动） ----------
def _remove_legacy_registry():
    """删除旧方案写入的 Run 键 与 StartupApproved 标记，避免残留冲突"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, AUTOSTART_KEY_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, AUTOSTART_KEY_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    """是否已启用：启动文件夹中有指向当前 exe 的快捷方式"""
    if _shortcut_points_to_exe():
        return True
    # 兼容旧版本：若 Run 键仍存在且指向当前 exe，视为已启用
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ
        )
        try:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
            return bool(val) and os.path.normcase(os.path.abspath(get_exe_path())) in os.path.normcase(val)
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def enable_autostart() -> tuple:
    """启用开机自启，返回 (success, message)"""
    try:
        lnk = _create_shortcut()
        # 清理旧的注册表方案，避免双重启动/残留禁用标记
        _remove_legacy_registry()
        log.info('已启用开机自启: %s', lnk)
        return True, '已启用开机自启，开机后将自动后台运行'
    except PermissionError:
        msg = '权限不足，请以管理员身份运行或手动添加到启动项'
        log.error(msg)
        return False, msg
    except OSError as e:
        msg = f'创建启动快捷方式失败: {e}'
        log.error(msg)
        return False, msg
    except Exception as e:
        msg = f'启用开机自启失败: {e}'
        log.error(msg)
        return False, msg


def disable_autostart() -> tuple:
    """取消开机自启，返回 (success, message)"""
    try:
        lnk = _get_shortcut_path()
        if os.path.isfile(lnk):
            os.remove(lnk)
        _remove_legacy_registry()
        log.info('已取消开机自启')
        return True, '已取消开机自启'
    except OSError as e:
        msg = f'删除启动快捷方式失败: {e}'
        log.error(msg)
        return False, msg
    except Exception as e:
        msg = f'取消开机自启失败: {e}'
        log.error(msg)
        return False, msg
