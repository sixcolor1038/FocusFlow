# -*- coding: utf-8 -*-
"""
FocusFlow 开机自启模块（Windows）
- 注册表写入失败时给出友好提示
"""

import os
import sys
import winreg

from logger import get_logger

log = get_logger('autostart')

AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_KEY_NAME = "FocusFlow"


def get_exe_path() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])


def is_autostart_enabled() -> bool:
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except OSError as e:
        log.warning('读取注册表失败: %s', e)
        return False


def enable_autostart() -> tuple:
    """启用开机自启，返回 (success, message)"""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0, winreg.KEY_SET_VALUE
        )
        try:
            winreg.SetValueEx(
                key, AUTOSTART_KEY_NAME, 0, winreg.REG_SZ,
                f'"{get_exe_path()}" --hidden'
            )
        finally:
            winreg.CloseKey(key)
        log.info('已启用开机自启')
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
            # 本来就没有，视为成功
            pass
        finally:
            winreg.CloseKey(key)
        log.info('已取消开机自启')
        return True, '已取消开机自启'
    except OSError as e:
        msg = f'删除注册表项失败: {e}'
        log.error(msg)
        return False, msg
