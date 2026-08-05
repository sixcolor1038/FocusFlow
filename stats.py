# -*- coding: utf-8 -*-
"""
实时统计模块
- CPM (每分钟按键数) 计算，带锁 + 缓存
- 跨天自动重置
"""

import time
import threading
from collections import deque
from typing import Optional

from config import config
from logger import get_logger

log = get_logger('stats')


class CPMCalculator:
    """实时打字速度计算器

    优化点：
    - deque + 锁，线程安全
    - 查询时惰性清理过期数据
    - 结果缓存（避免每次查询都遍历）
    """

    def __init__(self, window: int):
        self.window = window
        self._timestamps: deque = deque()
        self._lock = threading.Lock()
        self._cached_cpm = 0
        self._cached_at = 0.0
        self._cache_ttl = 0.5  # 500ms 缓存

    def record(self):
        """记录一次按键时间戳"""
        now = time.time()
        with self._lock:
            self._timestamps.append(now)
            # 顺便清理一下头部过期数据，控制 deque 长度
            cutoff = now - self.window
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            # 写入时缓存失效
            self._cached_at = 0.0

    def get_cpm(self) -> int:
        """获取当前 CPM"""
        # 命中缓存直接返回
        now = time.time()
        if now - self._cached_at < self._cache_ttl:
            return self._cached_cpm

        cutoff = now - self.window
        with self._lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            count = len(self._timestamps)
        # 缓存结果
        self._cached_cpm = count
        self._cached_at = now
        return count

    def reset(self):
        with self._lock:
            self._timestamps.clear()
            self._cached_at = 0.0


# 全局实例（延迟创建，便于配置变更）
_cpm: Optional[CPMCalculator] = None
_cpm_lock = threading.Lock()


def get_cpm_calculator() -> CPMCalculator:
    global _cpm
    if _cpm is None:
        with _cpm_lock:
            if _cpm is None:
                window = config.getint('stats', 'cpm_window', 60)
                _cpm = CPMCalculator(window)
                log.info('CPM 计算器初始化 (window=%ds)', window)
    return _cpm


def record_cpm():
    get_cpm_calculator().record()


def get_current_cpm() -> int:
    return get_cpm_calculator().get_cpm()


def reset_cpm():
    """重置 CPM 计数器（清除当前按键时间戳）

    用于清除某按键今日次数后，立即清掉可能虚高的实时速度。
    """
    try:
        get_cpm_calculator().reset()
    except Exception as e:
        log.debug('重置 CPM 失败: %s', e)
