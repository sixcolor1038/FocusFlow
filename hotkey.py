# -*- coding: utf-8 -*-
"""
FocusFlow 全局热键模块
- 注册 Ctrl+Shift+F 显示/隐藏主窗口
- 支持自定义热键组合
"""

import threading
from typing import Optional, Callable

from pynput import keyboard

from config import config
from logger import get_logger

log = get_logger('hotkey')


# 修饰键名称映射表（config 中的名称 → pynput GlobalHotKeys 格式）
# pynput GlobalHotKeys 要求特殊键用 <name> 包裹，普通字符直接写
_SPECIAL_KEY_MAP = {
    'ctrl': '<ctrl>',
    'control': '<ctrl>',
    'shift': '<shift>',
    'alt': '<alt>',
    'option': '<alt>',
    'cmd': '<cmd>',
    'win': '<cmd>',
    'super': '<cmd>',
    'space': '<space>',
    'enter': '<enter>',
    'tab': '<tab>',
    'esc': '<esc>',
    'delete': '<delete>',
    'backspace': '<backspace>',
    'up': '<up>',
    'down': '<down>',
    'left': '<left>',
    'right': '<right>',
    'home': '<home>',
    'end': '<end>',
    'page_up': '<page_up>',
    'page_down': '<page_down>',
    'insert': '<insert>',
    'caps_lock': '<caps_lock>',
    'num_lock': '<num_lock>',
    'scroll_lock': '<scroll_lock>',
    'print_screen': '<print_screen>',
    'pause': '<pause>',
    'menu': '<menu>',
}


def _normalize_for_pynput(hotkey_str: str) -> str:
    """将 'ctrl+shift+k' 转换为 pynput GlobalHotKeys 接受的 '<ctrl>+<shift>+k' 格式

    pynput GlobalHotKeys 的格式要求：
    - 修饰键和特殊键用 <name> 包裹，如 <ctrl> <shift> <alt> <cmd> <f1>
    - 普通字符直接写，如 a b c 1 2 3
    """
    parts = [p.strip().lower() for p in hotkey_str.split('+')]
    result = []
    for p in parts:
        if not p:
            continue
        if p in _SPECIAL_KEY_MAP:
            result.append(_SPECIAL_KEY_MAP[p])
        elif len(p) == 1:
            # 普通字符，直接使用
            result.append(p)
        elif p.startswith('f') and p[1:].isdigit():
            # F1-F12
            result.append(f'<{p}>')
        else:
            # 未知的键名，尝试用 <> 包裹
            log.warning('无法识别的热键部分: %s，尝试作为特殊键处理', p)
            result.append(f'<{p}>')
    return '+'.join(result)


class GlobalHotkeyManager:
    """全局热键管理器"""

    def __init__(self):
        self._listener: Optional[keyboard.GlobalHotKeys.Listener] = None
        self._callbacks = {}
        self._lock = threading.Lock()

    def register(self, hotkey_str: str, callback: Callable[[], None]) -> bool:
        """注册一个全局热键

        Args:
            hotkey_str: 配置文件中的格式，如 'ctrl+shift+k'
            callback: 热键触发时的回调
        """
        try:
            # 转换为 pynput GlobalHotKeys 接受的格式
            pynput_str = _normalize_for_pynput(hotkey_str)
            self._callbacks[pynput_str] = callback
            ok = self._restart()
            if ok:
                log.info('已注册全局热键: %s (pynput格式: %s)', hotkey_str, pynput_str)
            return ok
        except Exception as e:
            log.error('注册热键失败: %s', e, exc_info=True)
            return False

    def _restart(self) -> bool:
        """重启 listener 以应用新的热键集合，返回是否成功启动"""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if not self._callbacks:
            return False
        try:
            self._listener = keyboard.GlobalHotKeys(self._callbacks)
            self._listener.daemon = True
            self._listener.start()
            return True
        except Exception as e:
            log.error('启动热键监听失败: %s', e, exc_info=True)
            return False

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# 全局实例
_manager: Optional[GlobalHotkeyManager] = None


def get_hotkey_manager() -> GlobalHotkeyManager:
    global _manager
    if _manager is None:
        _manager = GlobalHotkeyManager()
    return _manager


def register_default_hotkey(callback: Callable[[], None]):
    """注册默认的显示/隐藏窗口热键"""
    hotkey_str = config.get('hotkey', 'toggle_window', 'ctrl+shift+f')
    get_hotkey_manager().register(hotkey_str, callback)


def stop_hotkey():
    if _manager:
        _manager.stop()
