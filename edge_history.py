# -*- coding: utf-8 -*-
"""
FocusFlow Edge 浏览器历史记录模块

功能：
- 查询 Edge 浏览器总历史记录条数
- 查询指定日期的历史记录条数
- 按日期存储历史记录条数到本地数据库
- 支持查询近 N 天的趋势

Edge 历史记录存储在 SQLite 数据库中：
  %LOCALAPPDATA%\\Microsoft\\Edge\\User Data\\Default\\History
表 urls 记录所有访问的 URL，字段 last_visit_time 是 Chrome 时间戳
（从 1601-01-01 起的微秒数）
"""

import os
import sys
import sqlite3
import shutil
from datetime import datetime, date, timedelta, timezone
from typing import Optional, List, Tuple

from config import get_data_dir
from logger import get_logger

log = get_logger('edge_history')


def get_edge_history_path() -> str:
    """获取 Edge 历史记录数据库路径"""
    if sys.platform == 'win32':
        local_app = os.environ.get('LOCALAPPDATA', '')
        if local_app:
            return os.path.join(local_app, 'Microsoft', 'Edge', 'User Data', 'Default', 'History')
    return os.path.expanduser('~/.config/microsoft-edge/Default/History')


def _chrome_time_to_datetime(chrome_time: int) -> datetime:
    """Chrome 时间戳（微秒，从 1601-01-01 起）转 datetime"""
    epoch_start = datetime(1601, 1, 1, tzinfo=timezone.utc)
    return epoch_start + timedelta(microseconds=chrome_time)


def _datetime_to_chrome_time(dt: datetime) -> int:
    """datetime 转 Chrome 时间戳"""
    epoch_start = datetime(1601, 1, 1, tzinfo=timezone.utc)
    delta = dt - epoch_start
    return int(delta.total_seconds() * 1000000)


def _open_edge_history() -> Tuple[Optional[sqlite3.Connection], Optional[str]]:
    """打开 Edge 历史库，返回 (连接, 临时文件路径)

    策略：
    1. 优先只读直连（`file:...?mode=ro`）：Edge 未运行或允许读时，
       完全不复制大文件（Edge 历史库可达几十上百 MB），最快。
    2. 失败（Edge 运行中持有文件锁）则复制 主库+WAL+SHM 到临时文件后打开，
       保证包含 WAL 中未 checkpoint 的数据（旧版只复制主库会漏最近记录）。

    调用方负责：用完关闭连接，若临时文件非 None 则删除。
    """
    history_path = get_edge_history_path()
    if not os.path.exists(history_path):
        log.debug('Edge 历史记录文件不存在: %s', history_path)
        return None, None

    # 1) 只读直连
    try:
        conn = sqlite3.connect(f'file:{history_path}?mode=ro', uri=True)
        return conn, None
    except Exception as e:
        log.debug('Edge 历史库只读直连失败（可能被 Edge 锁定），改走复制: %s', e)

    # 2) 复制主库 + WAL + SHM（保证数据完整）
    temp_path = os.path.join(get_data_dir(), '_edge_history_temp.db')
    try:
        shutil.copy2(history_path, temp_path)
        for suffix in ('-wal', '-shm'):
            src = history_path + suffix
            if os.path.exists(src):
                try:
                    shutil.copy2(src, temp_path + suffix)
                except Exception:
                    pass
        conn = sqlite3.connect(temp_path)
        return conn, temp_path
    except Exception as e:
        log.error('复制 Edge 历史记录失败: %s', e)
        return None, None


def _close_edge_connection(conn, temp_path):
    """关闭连接并清理临时文件"""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    if temp_path:
        for suffix in ('', '-wal', '-shm'):
            try:
                if os.path.exists(temp_path + suffix):
                    os.remove(temp_path + suffix)
            except Exception:
                pass


def query_edge_total_count() -> int:
    """查询 Edge 浏览器总历史记录条数

    Returns: 总历史记录条数
    """
    conn, temp_path = _open_edge_history()
    if conn is None:
        return 0
    try:
        cur = conn.execute('SELECT COUNT(*) FROM urls')
        return cur.fetchone()[0]
    except Exception as e:
        log.error('查询 Edge 总历史记录失败: %s', e)
        return 0
    finally:
        _close_edge_connection(conn, temp_path)


def query_edge_history_count(target_date: Optional[date] = None) -> int:
    """查询指定日期的 Edge 历史记录条数

    Args:
        target_date: 指定日期（None=今天）

    Returns: 该日期的历史记录条数
    """
    if target_date is None:
        target_date = date.today()

    conn, temp_path = _open_edge_history()
    if conn is None:
        return 0
    try:
        # 按本地时区计算日期范围
        local_tz = datetime.now().astimezone().tzinfo
        day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=local_tz)
        day_end = day_start + timedelta(days=1)
        chrome_start = _datetime_to_chrome_time(day_start)
        chrome_end = _datetime_to_chrome_time(day_end)

        cur = conn.execute(
            'SELECT COUNT(*) FROM urls WHERE last_visit_time >= ? AND last_visit_time < ?',
            (chrome_start, chrome_end)
        )
        return cur.fetchone()[0]
    except Exception as e:
        log.error('查询 Edge 历史记录失败: %s', e)
        return 0
    finally:
        _close_edge_connection(conn, temp_path)


def save_edge_history_count(target_date: date, count: int):
    """保存指定日期的 Edge 历史记录条数到本地数据库"""
    db_path = os.path.join(get_data_dir(), 'focusflow_edge_history.db')
    try:
        conn = sqlite3.connect(db_path)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS edge_history (
                date TEXT PRIMARY KEY,
                count INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        conn.execute(
            'INSERT OR REPLACE INTO edge_history (date, count, updated_at) VALUES (?, ?, ?)',
            (target_date.isoformat(), count, int(datetime.now().timestamp()))
        )
        conn.commit()
        conn.close()
        log.info('已保存 Edge 历史记录: %s = %d 条', target_date, count)
    except Exception as e:
        log.error('保存 Edge 历史记录失败: %s', e)


def get_edge_history_counts(days: int = 30) -> List[Tuple[str, int]]:
    """获取最近 N 天的 Edge 历史记录条数

    Returns: [(日期字符串, 条数), ...]
    """
    db_path = os.path.join(get_data_dir(), 'focusflow_edge_history.db')
    if not os.path.exists(db_path):
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        now = datetime.now()
        start = now - timedelta(days=days - 1)
        cur = conn.execute(
            'SELECT date, count FROM edge_history WHERE date >= ? ORDER BY date',
            (start.date().isoformat(),)
        )
        result = [(row['date'], row['count']) for row in cur.fetchall()]
        conn.close()
        return result
    except Exception as e:
        log.error('查询 Edge 历史记录统计失败: %s', e)
        return []


def update_today_edge_history() -> Tuple[int, int]:
    """查询并保存今天的 Edge 历史记录

    Returns: (今日条数, 总条数)
    """
    today = date.today()
    today_count = query_edge_history_count(today)
    save_edge_history_count(today, today_count)
    total_count = query_edge_total_count()
    return today_count, total_count
