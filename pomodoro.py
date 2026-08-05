# -*- coding: utf-8 -*-
"""
FocusFlow 番茄工作法模块（v1.1 新增）
- 工作/休息定时器（后台线程，秒级计时）
- 每个番茄钟自动记录按键数据（与统计联动）
- 历史记录持久化到 data/focusflow_pomodoro.db
- 支持暂停/继续/停止/跳过

数据库表 pomodoro_sessions:
  id, type (work/break), start_time, end_time,
  planned_seconds, actual_seconds, key_count, created_at
"""

import os
import time
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Dict, Callable

from config import get_data_dir
from logger import get_logger

log = get_logger('pomodoro')


DB_PATH = os.path.join(get_data_dir(), 'focusflow_pomodoro.db')
_db_lock = threading.Lock()

# 状态常量
STATE_IDLE = 'idle'
STATE_WORK = 'work'
STATE_BREAK = 'break'


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    return conn


def init_db():
    """初始化番茄钟数据库"""
    with _db_lock:
        conn = _get_conn()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pomodoro_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                planned_seconds INTEGER NOT NULL,
                actual_seconds INTEGER NOT NULL,
                key_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pomo_type ON pomodoro_sessions(type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pomo_created ON pomodoro_sessions(created_at)')
        conn.commit()
        conn.close()


init_db()


class PomodoroTimer:
    """番茄钟定时器（后台线程驱动）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = STATE_IDLE
        self._paused = False
        self._remaining = 0          # 剩余秒数
        self._planned = 0            # 本次计划秒数
        self._elapsed = 0            # 本次已执行秒数（不含暂停）
        self._key_count = 0          # 本次按键数
        self._work_minutes = 25      # 工作时长（分钟）
        self._break_minutes = 5      # 休息时长（分钟）
        self._work_finished = 0      # 今日已完成的番茄钟数
        self._auto_break = True      # 工作结束后自动进入休息
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._tick_callbacks: List[Callable[[str, int, int], None]] = []
        self._done_callbacks: List[Callable[[Dict], None]] = []

    # ---------- 配置 ----------
    def set_durations(self, work_minutes: int, break_minutes: int):
        with self._lock:
            self._work_minutes = max(1, int(work_minutes))
            self._break_minutes = max(1, int(break_minutes))

    def set_auto_break(self, enabled: bool):
        self._auto_break = bool(enabled)

    # ---------- 回调 ----------
    def add_tick_callback(self, cb: Callable[[str, int, int], None]):
        """注册秒级回调 (state, remaining, key_count)"""
        self._tick_callbacks.append(cb)

    def add_done_callback(self, cb: Callable[[Dict], None]):
        """注册完成回调（返回本次会话数据 dict）"""
        self._done_callbacks.append(cb)

    # ---------- 状态查询 ----------
    def get_state(self) -> str:
        with self._lock:
            return self._state

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def get_remaining(self) -> int:
        with self._lock:
            return self._remaining

    def get_key_count(self) -> int:
        with self._lock:
            return self._key_count

    def get_work_finished(self) -> int:
        with self._lock:
            return self._work_finished

    def get_state_info(self) -> Dict:
        """返回当前状态快照（供 GUI 使用）"""
        with self._lock:
            return {
                'state': self._state,
                'paused': self._paused,
                'remaining': self._remaining,
                'planned': self._planned,
                'key_count': self._key_count,
                'work_finished': self._work_finished,
                'work_minutes': self._work_minutes,
                'break_minutes': self._break_minutes,
                'auto_break': self._auto_break,
            }

    # ---------- 控制 ----------
    def start_work(self):
        """开始一个工作番茄钟"""
        with self._lock:
            if self._state == STATE_WORK:
                return
            # 保存尚未结束的会话
            self._save_current_locked()
            self._state = STATE_WORK
            self._paused = False
            self._planned = self._work_minutes * 60
            self._remaining = self._planned
            self._elapsed = 0
            self._key_count = 0
        self._ensure_thread()
        self._notify_tick()
        log.info('番茄钟开始工作 (%d 分钟)', self._work_minutes)

    def start_break(self):
        """开始休息"""
        with self._lock:
            if self._state == STATE_BREAK:
                return
            self._save_current_locked()
            self._state = STATE_BREAK
            self._paused = False
            self._planned = self._break_minutes * 60
            self._remaining = self._planned
            self._elapsed = 0
            self._key_count = 0
        self._ensure_thread()
        self._notify_tick()
        log.info('番茄钟开始休息 (%d 分钟)', self._break_minutes)

    def toggle_pause(self) -> bool:
        """暂停/继续，返回新的暂停状态"""
        with self._lock:
            if self._state == STATE_IDLE:
                return False
            self._paused = not self._paused
        self._notify_tick()
        return self._paused

    def skip(self):
        """跳过当前阶段（不保存记录）"""
        with self._lock:
            self._state = STATE_IDLE
            self._paused = False
            self._remaining = 0
            self._elapsed = 0
            self._key_count = 0
        self._notify_tick()
        log.info('番茄钟跳过当前阶段')

    def stop(self):
        """停止定时器（保存当前阶段记录）"""
        with self._lock:
            self._save_current_locked()
            self._state = STATE_IDLE
            self._paused = False
            self._remaining = 0
            self._elapsed = 0
            self._key_count = 0
        self._notify_tick()
        log.info('番茄钟已停止')

    def record_key(self, key_name: str):
        """按键回调：仅在工作中计数"""
        if self.get_state() == STATE_WORK:
            with self._lock:
                self._key_count += 1

    # ---------- 内部 ----------
    def _save_current_locked(self) -> Optional[Dict]:
        """保存当前阶段记录（须持有锁）。工作阶段才记录按键统计。"""
        if self._state == STATE_IDLE or self._planned <= 0:
            return None
        end_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        start_time = (datetime.now()).strftime('%Y-%m-%d %H:%M:%S')
        record = {
            'type': self._state,
            'start_time': start_time,
            'end_time': end_time,
            'planned_seconds': self._planned,
            'actual_seconds': self._elapsed,
            'key_count': self._key_count,
        }
        if record['actual_seconds'] <= 0:
            record['actual_seconds'] = max(1, int(self._planned - self._remaining))
        try:
            _save_session(record)
        except Exception as e:
            log.error('保存番茄钟记录失败: %s', e)
        if self._state == STATE_WORK and record['actual_seconds'] >= 1:
            self._work_finished += 1
            # 通知完成回调
            for cb in self._done_callbacks:
                try:
                    cb(record)
                except Exception as e:
                    log.debug('番茄钟完成回调异常: %s', e)
        log.info('番茄钟阶段结束: %s 实际 %d 秒 按键 %d',
                 record['type'], record['actual_seconds'], record['key_count'])
        return record

    def _tick_loop(self):
        while not self._stop_event.wait(1.0):
            try:
                _transitioned = False
                with self._lock:
                    if self._state == STATE_IDLE or self._paused:
                        continue
                    self._remaining -= 1
                    self._elapsed += 1
                    if self._remaining <= 0:
                        # 阶段完成
                        self._save_current_locked()
                        if self._state == STATE_WORK and self._auto_break:
                            self._state = STATE_BREAK
                            self._planned = self._break_minutes * 60
                            self._remaining = self._planned
                            self._elapsed = 0
                            self._key_count = 0
                        else:
                            self._state = STATE_IDLE
                            self._paused = False
                            self._remaining = 0
                            self._elapsed = 0
                            self._key_count = 0
                        _transitioned = True
                self._notify_tick()
                if _transitioned:
                    # 阶段切换后立即停止，避免继续倒计时
                    if self.get_state() == STATE_IDLE:
                        continue
            except Exception as e:
                log.error('番茄钟 tick 异常: %s', e, exc_info=True)

    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._tick_loop,
                                        name='pomodoro', daemon=True)
        self._thread.start()

    def _notify_tick(self):
        info = self.get_state_info()
        for cb in self._tick_callbacks:
            try:
                cb(info['state'], info['remaining'], info['key_count'])
            except Exception as e:
                log.debug('番茄钟 tick 回调异常: %s', e)

    def shutdown(self):
        """关闭：保存当前记录并停止线程"""
        try:
            with self._lock:
                self._save_current_locked()
                self._state = STATE_IDLE
        except Exception:
            pass
        self._stop_event.set()
        log.info('番茄钟已关闭')


def _save_session(record: Dict):
    """保存一条番茄钟会话记录"""
    with _db_lock:
        conn = _get_conn()
        conn.execute(
            '''INSERT INTO pomodoro_sessions
               (type, start_time, end_time, planned_seconds, actual_seconds, key_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (record.get('type', 'work'),
             record.get('start_time', ''),
             record.get('end_time', ''),
             record.get('planned_seconds', 0),
             record.get('actual_seconds', 0),
             record.get('key_count', 0),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()


def get_today_work_sessions() -> List[Dict]:
    """查询今日工作番茄钟记录"""
    today = datetime.now().strftime('%Y-%m-%d')
    return get_sessions_by_date(today)


def get_sessions_by_date(date_str: str, limit: int = 100) -> List[Dict]:
    """按日期查询番茄钟记录"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT * FROM pomodoro_sessions
               WHERE start_time >= ? AND start_time < ?
               ORDER BY id DESC LIMIT ?''',
            (f'{date_str} 00:00:00', f'{date_str} 23:59:59', limit)
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_recent_sessions(limit: int = 50) -> List[Dict]:
    """查询最近番茄钟记录"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            'SELECT * FROM pomodoro_sessions ORDER BY id DESC LIMIT ?', (limit,))
        rows = cur.fetchall()
        conn.close()
    return [dict(r) for r in rows]


def get_today_summary() -> Dict:
    """今日番茄钟汇总"""
    today = datetime.now().strftime('%Y-%m-%d')
    sessions = get_sessions_by_date(today, limit=1000)
    work = [s for s in sessions if s.get('type') == 'work']
    return {
        'count': len(work),
        'total_keys': sum(s.get('key_count', 0) for s in work),
        'total_seconds': sum(s.get('actual_seconds', 0) for s in work),
    }


# 全局单例
_timer: Optional[PomodoroTimer] = None
_timer_lock = threading.Lock()


def get_pomodoro() -> PomodoroTimer:
    global _timer
    if _timer is None:
        with _timer_lock:
            if _timer is None:
                init_db()
                _timer = PomodoroTimer()
    return _timer


def shutdown():
    """关闭番茄钟模块"""
    try:
        if _timer:
            _timer.shutdown()
    except Exception as e:
        log.warning('番茄钟关闭异常: %s', e)
