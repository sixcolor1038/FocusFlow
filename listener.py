# -*- coding: utf-8 -*-
"""
FocusFlow 键盘监听模块
- 暂停/恢复
- 修饰键过滤
- 全局异常保护
"""

import time
import threading
from typing import Optional, Callable, List, Dict

from pynput import keyboard

from config import config
from logger import get_logger

# 延迟导入 database 和 stats，避免启动时强耦合
# 这两个模块在 _on_press 中按需导入
log = get_logger('listener')


# 修饰键集合（用于过滤）
_MODIFIER_KEYS = {
    'Shift', '左Shift', '右Shift',
    'Ctrl', '左Ctrl', '右Ctrl',
    'Alt', '左Alt', '右Alt',
    'Win', '左Win', '右Win',
}
_FUNCTION_KEYS = {f'F{i}' for i in range(1, 13)}


def normalize_key(key) -> str:
    """规范化按键名

    pynput 的按键对象有三种情况：
    1. KeyCode（普通字符键）：有 char 属性，如 'a' '1'
    2. Key（特殊键）：如 Key.space Key.enter Key.ctrl_l
    3. 组合键状态下的字符：char 可能是控制字符（如 Ctrl+C 返回 '\x03'）

    本函数将所有按键统一映射为可读的中文名。
    """
    # 特殊键映射表（pynput Key.name -> 显示名）
    SPECIAL_KEY_MAP = {
        'space': '空格', 'enter': '回车', 'return': '回车',
        'backspace': '退格', 'tab': 'Tab',
        'shift': 'Shift', 'shift_l': '左Shift', 'shift_r': '右Shift',
        'ctrl': 'Ctrl', 'ctrl_l': '左Ctrl', 'ctrl_r': '右Ctrl',
        'alt': 'Alt', 'alt_l': '左Alt', 'alt_r': '右Alt', 'alt_gr': 'AltGr',
        'cmd': 'Win', 'cmd_l': '左Win', 'cmd_r': '右Win',
        'caps_lock': 'CapsLock', 'esc': 'Esc', 'escape': 'Esc',
        'delete': 'Delete', 'home': 'Home', 'end': 'End',
        'page_up': 'PageUp', 'page_down': 'PageDown', 'insert': 'Insert',
        'num_lock': 'NumLock', 'scroll_lock': 'ScrollLock',
        'print_screen': 'PrintScreen', 'pause': 'Pause', 'menu': 'Menu',
        'up': '↑', 'down': '↓', 'left': '←', 'right': '→',
        'f1': 'F1', 'f2': 'F2', 'f3': 'F3', 'f4': 'F4',
        'f5': 'F5', 'f6': 'F6', 'f7': 'F7', 'f8': 'F8',
        'f9': 'F9', 'f10': 'F10', 'f11': 'F11', 'f12': 'F12',
        'media_play_pause': '播放/暂停', 'media_volume_mute': '静音',
        'media_volume_up': '音量+', 'media_volume_down': '音量-',
        'media_previous': '上一曲', 'media_next': '下一曲',
    }

    # 1. 先检查是否是 pynput 的 Key 枚举（特殊键）
    key_name = getattr(key, 'name', None)
    if key_name:
        return SPECIAL_KEY_MAP.get(key_name, key_name)

    # 2. 尝试获取 char 属性（普通字符键）
    try:
        char = key.char
        if char is not None and len(char) == 1:
            # 检查是否是控制字符（Ctrl+字母组合时 char 是控制字符）
            if ord(char) < 32 or ord(char) == 127:
                # 控制字符，不记录为普通字符
                # 返回 Ctrl+对应字母，避免和普通按键混淆
                ctrl_map = {
                    '\x01': 'Ctrl+A', '\x02': 'Ctrl+B', '\x03': 'Ctrl+C',
                    '\x04': 'Ctrl+D', '\x05': 'Ctrl+E', '\x06': 'Ctrl+F',
                    '\x07': 'Ctrl+G', '\x08': 'Ctrl+H', '\x09': 'Ctrl+I',
                    '\x0a': 'Ctrl+J', '\x0b': 'Ctrl+K', '\x0c': 'Ctrl+L',
                    '\x0d': 'Ctrl+M', '\x0e': 'Ctrl+N', '\x0f': 'Ctrl+O',
                    '\x10': 'Ctrl+P', '\x11': 'Ctrl+Q', '\x12': 'Ctrl+R',
                    '\x13': 'Ctrl+S', '\x14': 'Ctrl+T', '\x15': 'Ctrl+U',
                    '\x16': 'Ctrl+V', '\x17': 'Ctrl+W', '\x18': 'Ctrl+X',
                    '\x19': 'Ctrl+Y', '\x1a': 'Ctrl+Z',
                }
                return ctrl_map.get(char, f'Ctrl+{ord(char)}')
            # 普通可见字符
            return char.upper()
    except (AttributeError, TypeError):
        pass

    # 3. 兜底：用字符串表示
    key_str = str(key)
    # 去掉 Key. 前缀
    if key_str.startswith('Key.'):
        key_str = key_str[4:]
    return SPECIAL_KEY_MAP.get(key_str, key_str)


class KeyListener:
    """键盘监听器，支持暂停/恢复和按键过滤"""

    def __init__(self):
        self._listener: Optional[keyboard.Listener] = None
        self._paused = False
        self._pause_lock = threading.Lock()
        self._pause_callbacks: List[Callable[[bool], None]] = []
        # 加载过滤配置
        self._ignore_modifiers = config.getbool('listener', 'ignore_modifier_keys', False)
        self._ignore_functions = config.getbool('listener', 'ignore_function_keys', False)
        # 长按自动重复过滤（游戏/输入框长按某键时，避免每秒几十次错误累加）
        self._ignore_key_repeat = config.getbool('listener', 'ignore_key_repeat', True)
        # 记录当前处于按下状态的按键: {按键名: 按下时间戳}
        self._pressed: Dict[str, float] = {}
        self._pressed_lock = threading.Lock()
        # 超过该时长未收到 release 的按键视为已释放（防止 release 事件丢失导致漏计）
        # 正常长按的自动重复间隔远小于此值，因此长按期间的重复事件都会被过滤；
        # 仅当 release 丢失且超过此值后才允许重新计数，兼顾抑制虚高与避免漏计。
        self._hold_stale_seconds = config.getfloat('listener', 'key_repeat_stale_seconds', 15.0)
        log.info('监听器初始化 (ignore_modifiers=%s, ignore_functions=%s, ignore_key_repeat=%s, stale=%.0fs)',
                 self._ignore_modifiers, self._ignore_functions, self._ignore_key_repeat,
                 self._hold_stale_seconds)

    def add_pause_callback(self, cb: Callable[[bool], None]):
        """注册暂停状态变化回调"""
        self._pause_callbacks.append(cb)

    def is_paused(self) -> bool:
        with self._pause_lock:
            return self._paused

    def set_paused(self, paused: bool):
        with self._pause_lock:
            if self._paused == paused:
                return
            self._paused = paused
        # 暂停时清空按下状态，避免恢复后误判长按
        if paused:
            with self._pressed_lock:
                self._pressed.clear()
        log.info('监听已 %s', '暂停' if paused else '恢复')
        for cb in self._pause_callbacks:
            try:
                cb(paused)
            except Exception as e:
                log.error('暂停回调异常: %s', e)

    def toggle_pause(self) -> bool:
        new_state = not self._paused
        self.set_paused(new_state)
        return new_state

    def _should_filter(self, key_name: str) -> bool:
        """是否过滤该按键"""
        if self._ignore_modifiers and key_name in _MODIFIER_KEYS:
            return True
        if self._ignore_functions and key_name in _FUNCTION_KEYS:
            return True
        return False

    def _is_new_press(self, key_name: str) -> bool:
        """判断是否为一次新的按下（非长按自动重复）

        处理逻辑：
        - 按键不在按下集合中 = 新按下，记录并计数
        - 按键已在按下集合中 = 属于按住期间的自动重复事件，不计数
        - 距上次按下超过 hold_stale_seconds = 视为上次 release 事件丢失，允许重新计数
        """
        now = time.time()
        with self._pressed_lock:
            last = self._pressed.get(key_name)
            if last is None or now - last > self._hold_stale_seconds:
                self._pressed[key_name] = now
                return True
            return False

    def _on_press(self, key):
        try:
            if self.is_paused():
                return
            key_name = normalize_key(key)
            if self._should_filter(key_name):
                return
            # 长按自动重复过滤：按住期间重复触发的事件不计入按键数
            if self._ignore_key_repeat and not self._is_new_press(key_name):
                return
            # 延迟导入，避免循环依赖和启动耦合
            import database
            import stats
            database.record_key(key_name)
            stats.record_cpm()
        except Exception as e:
            log.error('on_press 异常: %s', e, exc_info=True)

    def _on_release(self, key):
        try:
            key_name = normalize_key(key)
            with self._pressed_lock:
                self._pressed.pop(key_name, None)
        except Exception as e:
            log.debug('on_release 异常: %s', e)

    def start(self):
        if self._listener and self._listener.is_alive():
            return
        self._listener = keyboard.Listener(on_press=self._on_press,
                                           on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        log.info('键盘监听已启动')

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception as e:
                log.warning('停止监听异常: %s', e)
            self._listener = None


# 全局实例
_listener: Optional[KeyListener] = None


def get_listener() -> KeyListener:
    global _listener
    if _listener is None:
        _listener = KeyListener()
    return _listener


def start_listener():
    get_listener().start()
