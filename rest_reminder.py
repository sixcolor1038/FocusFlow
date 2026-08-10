# -*- coding: utf-8 -*-
"""
FocusFlow 小憩与护眼提醒模块（v1.1 新增）

功能：
- 检测连续高强度输入（如 30 分钟内按键超过阈值），触发护眼提醒
- 提醒有冷却时间，避免频繁打扰
- 通过回调通知 GUI 弹出"休息一下 / 继续工作"对话框

实现：
- 后台线程维护一个滚动时间窗口的按键时间戳队列
- 每 N 秒检查一次：窗口内按键数 >= 阈值 且距上次提醒超过冷却时间 → 触发回调
"""

import time
import threading
from collections import deque
from typing import Callable, Optional, List

from config import config
from logger import get_logger

log = get_logger('rest')


class RestReminder:
    """护眼提醒检测器"""

    def __init__(self):
        self._lock = threading.Lock()
        # maxlen 作为硬上限安全阀：正常情况下按 window_minutes 清空，极端输入也不会无限增长
        self._timestamps: deque = deque(maxlen=100000)
        self._enabled = config.getbool('rest', 'enabled', True)
        self._window_minutes = max(1, config.getint('rest', 'window_minutes', 30))
        self._threshold = max(1, config.getint('rest', 'key_threshold', 10000))
        self._cooldown_minutes = max(1, config.getint('rest', 'cooldown_minutes', 10))
        self._check_interval = max(2, config.getint('rest', 'check_interval', 10))
        self._last_remind_at = 0.0
        self._notified = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._callbacks: List[Callable[[int], None]] = []
        log.info('护眼提醒初始化 (window=%dmin, threshold=%d, cooldown=%dmin, enabled=%s)',
                 self._window_minutes, self._threshold, self._cooldown_minutes, self._enabled)

    # ---------- 回调 ----------
    def add_callback(self, cb: Callable[[int], None]):
        """注册提醒回调（参数为当前窗口内按键数）"""
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    # ---------- 配置 ----------
    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if not enabled:
            with self._lock:
                self._timestamps.clear()
        log.info('护眼提醒 %s', '启用' if enabled else '禁用')

    def is_enabled(self) -> bool:
        return self._enabled

    # ---------- 按键上报 ----------
    def record_key(self, key_name: str):
        """由键盘监听回调调用：记录一次按键时间戳"""
        if not self._enabled:
            return
        now = time.time()
        cutoff = now - self._window_minutes * 60
        with self._lock:
            self._timestamps.append(now)
            # 顺手清理窗口外的旧数据，控制队列长度
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

    # ---------- 查询 ----------
    def count_in_window(self) -> int:
        """当前窗口内按键数"""
        cutoff = time.time() - self._window_minutes * 60
        with self._lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)

    def get_status(self) -> str:
        """返回状态描述（供 GUI/托盘显示）"""
        if not self._enabled:
            return '护眼提醒已关闭'
        return f'{self._window_minutes} 分钟内 {self.count_in_window():,} 键 (阈值 {self._threshold:,})'

    # ---------- 检查循环 ----------
    def _check_loop(self):
        while not self._stop_event.wait(self._check_interval):
            try:
                if not self._enabled:
                    continue
                count = self.count_in_window()
                now = time.time()
                if (count >= self._threshold
                        and now - self._last_remind_at >= self._cooldown_minutes * 60):
                    self._last_remind_at = now
                    self._notified = True
                    log.info('检测到高强度输入：%d 分钟内 %d 键，触发护眼提醒', self._window_minutes, count)
                    for cb in self._callbacks:
                        try:
                            cb(count)
                        except Exception as e:
                            log.debug('护眼提醒回调异常: %s', e)
            except Exception as e:
                log.error('护眼提醒检查异常: %s', e, exc_info=True)

    # ---------- 生命周期 ----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._check_loop,
                                        name='rest-reminder', daemon=True)
        self._thread.start()
        log.info('护眼提醒检测线程已启动')

    def reset(self):
        """重置计数（用于提醒弹出后，避免立刻再次触发）"""
        with self._lock:
            self._timestamps.clear()

    def shutdown(self):
        self._stop_event.set()
        log.info('护眼提醒已关闭')


# 全局单例
_instance: Optional[RestReminder] = None
_instance_lock = threading.Lock()


def get_rest_reminder() -> RestReminder:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = RestReminder()
    return _instance


def shutdown():
    try:
        if _instance:
            _instance.shutdown()
    except Exception as e:
        log.warning('护眼提醒关闭异常: %s', e)
