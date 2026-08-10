# -*- coding: utf-8 -*-
"""
FocusFlow 键鼠监听模块
- 键盘监听：暂停/恢复、修饰键过滤、长按自动重复过滤
- 鼠标监听：左/右/中键、侧键点击，滚轮滚动（连续滚动合并，避免虚高）
- 全局异常保护
"""

import time
import threading
from typing import Optional, Callable, List, Dict

from pynput import keyboard, mouse

from config import config
from logger import get_logger

# 延迟导入 database 和 stats，避免启动时强耦合
# 这两个模块在 _record_event 中按需导入
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
            # 检查是否是控制字符（按住 Ctrl 时字母会变成控制字符，如 Ctrl+D → '\x04'）
            if ord(char) < 32 or ord(char) == 127:
                # 控制字符：还原为物理键本身，而不是记成 "Ctrl+X" 组合键。
                # 这样 Ctrl 键与字母键各自独立计数（按 Ctrl+D = 左Ctrl 1 次 + D 1 次）。
                ctrl_map = {
                    '\x01': 'A', '\x02': 'B', '\x03': 'C', '\x04': 'D',
                    '\x05': 'E', '\x06': 'F', '\x07': 'G', '\x08': 'H',
                    '\x09': 'I', '\x0a': 'J', '\x0b': 'K', '\x0c': 'L',
                    '\x0d': 'M', '\x0e': 'N', '\x0f': 'O', '\x10': 'P',
                    '\x11': 'Q', '\x12': 'R', '\x13': 'S', '\x14': 'T',
                    '\x15': 'U', '\x16': 'V', '\x17': 'W', '\x18': 'X',
                    '\x19': 'Y', '\x1a': 'Z', '\x7f': 'Delete',
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


# 鼠标按键名映射（pynput mouse.Button -> 显示名）
_MOUSE_BUTTON_MAP = {
    mouse.Button.left: '鼠标左键',
    mouse.Button.right: '鼠标右键',
    mouse.Button.middle: '鼠标中键',
    mouse.Button.x1: '鼠标侧键后退',
    mouse.Button.x2: '鼠标侧键前进',
}


def normalize_mouse_button(button) -> str:
    """规范化鼠标按键名"""
    return _MOUSE_BUTTON_MAP.get(button, f'鼠标{getattr(button, "name", button)}')


def scroll_direction(dy: int) -> str:
    """滚轮方向：dy>0 向上滚，dy<0 向下滚"""
    return '上' if dy > 0 else '下'


class InputListener:
    """键盘+鼠标监听器，支持暂停/恢复、按键过滤和滚轮合并"""

    def __init__(self):
        self._listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None
        self._paused = False
        self._pause_lock = threading.Lock()
        self._pause_callbacks: List[Callable[[bool], None]] = []
        self._key_callbacks: List[Callable[[str], None]] = []
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
        # 鼠标统计开关
        self._mouse_enabled = config.getbool('listener', 'mouse_enabled', True)
        # 滚轮连续滚动合并窗口（秒）：窗口内同方向的连续滚动只计 1 次，
        # 避免"滚轮滑一会儿"直接累加成十几次虚高
        self._scroll_burst_window = config.getfloat('listener', 'scroll_burst_window', 0.8)
        self._scroll_lock = threading.Lock()
        self._last_scroll_ts = 0.0
        self._last_scroll_dir = ''
        log.info('监听器初始化 (ignore_modifiers=%s, ignore_functions=%s, ignore_key_repeat=%s, '
                 'stale=%.0fs, mouse_enabled=%s, scroll_window=%.1fs)',
                 self._ignore_modifiers, self._ignore_functions, self._ignore_key_repeat,
                 self._hold_stale_seconds, self._mouse_enabled, self._scroll_burst_window)

    def add_pause_callback(self, cb: Callable[[bool], None]):
        """注册暂停状态变化回调"""
        self._pause_callbacks.append(cb)

    def add_key_callback(self, cb: Callable[[str], None]):
        """注册输入回调（每个有效键鼠事件触发一次，用于番茄钟计数 / 高强度输入检测等）"""
        if cb not in self._key_callbacks:
            self._key_callbacks.append(cb)

    def is_paused(self) -> bool:
        with self._pause_lock:
            return self._paused

    def set_paused(self, paused: bool):
        with self._pause_lock:
            if self._paused == paused:
                return
            self._paused = paused
        # 暂停时清空按下状态与滚轮合并状态，避免恢复后误判长按/误合并
        if paused:
            with self._pressed_lock:
                self._pressed.clear()
            with self._scroll_lock:
                self._last_scroll_ts = 0.0
                self._last_scroll_dir = ''
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
            # 安全阀：字典过大时，先清理超过"视为已释放"时长、且未收到 release 的残留按键
            if len(self._pressed) > 256:
                stale = [k for k, t in self._pressed.items()
                         if now - t > self._hold_stale_seconds]
                for k in stale:
                    self._pressed.pop(k, None)
            last = self._pressed.get(key_name)
            if last is None or now - last > self._hold_stale_seconds:
                self._pressed[key_name] = now
                return True
            return False

    def _is_new_scroll_burst(self, direction: str) -> bool:
        """判断是否开启一次新的滚轮滚动（连续滚动合并）

        窗口内同方向的连续滚动只计 1 次；换方向或超过窗口时长则算新一次。
        """
        now = time.time()
        with self._scroll_lock:
            is_new = (now - self._last_scroll_ts > self._scroll_burst_window) \
                     or (direction != self._last_scroll_dir)
            self._last_scroll_ts = now
            self._last_scroll_dir = direction
            return is_new

    def _record_event(self, key_name: str):
        """记录一次输入事件（键盘/鼠标统一入口）"""
        if self.is_paused():
            return
        # 延迟导入，避免循环依赖和启动耦合
        import database
        import stats
        database.record_key(key_name)
        stats.record_cpm()
        # 通知输入回调（番茄钟 / 高强度输入检测等）
        for cb in self._key_callbacks:
            try:
                cb(key_name)
            except Exception as e:
                log.debug('输入回调异常: %s', e)

    def _on_press(self, key):
        try:
            key_name = normalize_key(key)
            if self._should_filter(key_name):
                return
            # 长按自动重复过滤：按住期间重复触发的事件不计入按键数
            if self._ignore_key_repeat and not self._is_new_press(key_name):
                return
            self._record_event(key_name)
        except Exception as e:
            log.error('on_press 异常: %s', e, exc_info=True)

    def _on_release(self, key):
        try:
            key_name = normalize_key(key)
            with self._pressed_lock:
                self._pressed.pop(key_name, None)
        except Exception as e:
            log.debug('on_release 异常: %s', e)

    def _on_click(self, x, y, button, pressed):
        try:
            if not pressed:
                return
            key_name = normalize_mouse_button(button)
            self._record_event(key_name)
        except Exception as e:
            log.error('on_click 异常: %s', e, exc_info=True)

    def _on_scroll(self, x, y, dx, dy):
        try:
            if dy == 0:
                return
            direction = scroll_direction(dy)
            # 连续滚动合并：短时间内的连续滚动只计 1 次
            if not self._is_new_scroll_burst(direction):
                return
            self._record_event(f'滚轮{direction}滑')
        except Exception as e:
            log.error('on_scroll 异常: %s', e, exc_info=True)

    def start(self):
        if self._listener and self._listener.is_alive():
            return
        self._listener = keyboard.Listener(on_press=self._on_press,
                                           on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()
        if self._mouse_enabled:
            try:
                self._mouse_listener = mouse.Listener(on_click=self._on_click,
                                                      on_scroll=self._on_scroll)
                self._mouse_listener.daemon = True
                self._mouse_listener.start()
            except Exception as e:
                log.warning('鼠标监听启动失败: %s', e)
        log.info('键鼠监听已启动 (keyboard=%s, mouse=%s)',
                 self._listener.is_alive(),
                 bool(self._mouse_listener and self._mouse_listener.is_alive())
                 if self._mouse_enabled else False)

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception as e:
                log.warning('停止键盘监听异常: %s', e)
            self._listener = None
        if self._mouse_listener:
            try:
                self._mouse_listener.stop()
            except Exception as e:
                log.warning('停止鼠标监听异常: %s', e)
            self._mouse_listener = None


# 全局实例
_listener: Optional[InputListener] = None


def get_listener() -> InputListener:
    global _listener
    if _listener is None:
        _listener = InputListener()
    return _listener


def start_listener():
    get_listener().start()
