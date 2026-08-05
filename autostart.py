# -*- coding: utf-8 -*-
"""
FocusFlow 开机自启模块（Windows）
- 写入 HKCU\\...\\Run 启动项
- 同时维护 StartupApproved\\Run 标记，避免 Windows 11 将启动项标记为"已禁用"导致不生效
- 读取时校验注册表值、exe 路径、启用标记三者是否一致
"""

import os
import sys
import winreg
from datetime import datetime, timedelta

from logger import get_logger

log = get_logger('autostart')

AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_KEY_NAME = "FocusFlow"

# Windows 11 的"启动应用"标记：2=禁用，3=启用
STARTUP_APPROVED_PATH = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
_STATE_ENABLED = b'\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
_STATE_DISABLED = b'\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'


def get_exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])


def _get_startup_command() -> str:
    return f'"{get_exe_path()}" --hidden'


def _read_startup_approved_state() -> bytes:
    """读取 StartupApproved 标记：返回原始字节，不存在返回 b''"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_PATH, 0, winreg.KEY_READ
        )
        try:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
            return bytes(val)
        except FileNotFoundError:
            return b''
        finally:
            winreg.CloseKey(key)
    except OSError as e:
        log.warning('读取 StartupApproved 失败: %s', e)
        return b''


def _write_startup_approved(state: bytes):
    """写入 StartupApproved 标记（启用=3 / 禁用=2）"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_PATH, 0,
            winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(key, AUTOSTART_KEY_NAME, 0, winreg.REG_BINARY, state)
        finally:
            winreg.CloseKey(key)
    except OSError as e:
        log.warning('写入 StartupApproved 失败: %s', e)


def _is_startup_approved_enabled() -> bool:
    """StartupApproved 是否允许启动（0x03=启用，0x02=禁用，无记录=视为启用）"""
    state = _read_startup_approved_state()
    if not state:
        return True
    return state[0] == 0x03


def _registry_command_matches() -> bool:
    """Run 键中的命令是否仍指向当前 exe（路径可能已变更）"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ
        )
        try:
            val, _ = winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
            return bool(val) and val == _get_startup_command()
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def _exe_exists() -> bool:
    try:
        return os.path.isfile(get_exe_path())
    except Exception:
        return False


def is_autostart_enabled() -> bool:
    """是否已启用且可用：Run 键存在 + 路径有效 + 未被 Windows 标记为禁用"""
    if not _registry_command_matches():
        return False
    if not _exe_exists():
        return False
    return _is_startup_approved_enabled()


def enable_autostart() -> tuple:
    """启用开机自启，返回 (success, message)"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(
                key, AUTOSTART_KEY_NAME, 0, winreg.REG_SZ, _get_startup_command()
            )
        finally:
            winreg.CloseKey(key)
        # 关键：同步把 Windows 11"启动应用"标记设为启用，
        # 否则即使 Run 键存在，任务管理器里也会显示"已禁用"导致开机不启动
        _write_startup_approved(_STATE_ENABLED)
        log.info('已启用开机自启: %s', _get_startup_command())
        return True, '已启用开机自启，开机后将自动后台运行'
    except PermissionError:
        msg = '权限不足，请以管理员身份运行或手动添加到启动项'
        log.error(msg)
        return False, msg
    except OSError as e:
        msg = f'写入注册表失败: {e}'
        log.error(msg)
        return False, msg


def disable_autostart() -> tuple:
    """取消开机自启，返回 (success, message)"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, AUTOSTART_KEY_NAME)
        except FileNotFoundError:
            pass  # 本来就没有，视为成功
        finally:
            winreg.CloseKey(key)
        # 同步移除/禁用 StartupApproved 标记
        _write_startup_approved(_STATE_DISABLED)
        log.info('已取消开机自启')
        return True, '已取消开机自启'
    except OSError as e:
        msg = f'删除注册表项失败: {e}'
        log.error(msg)
        return False, msg
