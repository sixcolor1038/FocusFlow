# -*- coding: utf-8 -*-
"""
FocusFlow 定时任务模块（v3.1 增强）

功能：
- 定时启动指定程序（如 WeaselServer.exe）
- 支持三种调度类型：
  • once      一次性（YYYY-MM-DD HH:MM）
  • daily     每日定时（HH:MM）
  • interval  每日间隔执行（格式：HH:MM-HH:MM|间隔分钟数）
              例如 "07:00-23:00|60" 表示每天 07:00-23:00 期间每 60 分钟执行一次
- 支持启用/禁用/删除/编辑
- 后台线程检查，到点自动执行

数据库表 scheduled_tasks:
  id, name, target_path, args, schedule_type, schedule_time,
  enabled, last_run, created_at
"""

import os
import sys
import sqlite3
import threading
import subprocess
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Tuple

from config import get_data_dir
from logger import get_logger

log = get_logger('scheduler')


DB_PATH = os.path.join(get_data_dir(), 'focusflow_scheduler.db')
_db_lock = threading.Lock()
_check_thread = None
_stop_event = threading.Event()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    return conn


def init_db():
    """初始化定时任务数据库"""
    with _db_lock:
        conn = _get_conn()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_path TEXT NOT NULL,
                args TEXT,
                schedule_type TEXT NOT NULL DEFAULT 'daily',
                schedule_time TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    # 启动检查线程
    start_check_thread()


def add_task(name: str, target_path: str, args: str = '',
             schedule_type: str = 'daily', schedule_time: str = '09:00',
             enabled: bool = True) -> int:
    """添加定时任务

    Args:
        name: 任务名称
        target_path: 目标程序路径
        args: 启动参数
        schedule_type: 'daily' / 'once' / 'interval'
        schedule_time:
            daily    -> 'HH:MM'（如 '09:00'）
            once     -> 'YYYY-MM-DD HH:MM'
            interval -> 'HH:MM-HH:MM|N'（如 '07:00-23:00|60' 表示 07:00-23:00 期间每 60 分钟一次）
        enabled: 是否启用
    """
    created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''INSERT INTO scheduled_tasks
               (name, target_path, args, schedule_type, schedule_time, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (name, target_path, args, schedule_type, schedule_time,
             1 if enabled else 0, created_at)
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
    log.info('添加定时任务: %s -> %s %s (%s)', name, target_path, schedule_time, schedule_type)
    return task_id


def update_task(task_id: int, name: Optional[str] = None,
                target_path: Optional[str] = None, args: Optional[str] = None,
                schedule_type: Optional[str] = None,
                schedule_time: Optional[str] = None,
                enabled: Optional[bool] = None) -> bool:
    """更新定时任务（任何字段为 None 表示不修改）"""
    updates: List[str] = []
    params: List = []
    if name is not None:
        updates.append('name=?')
        params.append(name)
    if target_path is not None:
        updates.append('target_path=?')
        params.append(target_path)
    if args is not None:
        updates.append('args=?')
        params.append(args)
    if schedule_type is not None:
        updates.append('schedule_type=?')
        params.append(schedule_type)
    if schedule_time is not None:
        updates.append('schedule_time=?')
        params.append(schedule_time)
        # 修改调度时间时重置 last_run，避免立即触发或长时间不触发
        updates.append('last_run=?')
        params.append(None)
    if enabled is not None:
        updates.append('enabled=?')
        params.append(1 if enabled else 0)
    if not updates:
        return False
    params.append(task_id)
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            f'UPDATE scheduled_tasks SET {", ".join(updates)} WHERE id=?', params
        )
        conn.commit()
        success = cur.rowcount > 0
        conn.close()
    if success:
        log.info('更新定时任务: id=%d, 字段=%s', task_id,
                 [u.split('=?')[0] for u in updates])
    return success


def delete_task(task_id: int) -> bool:
    """删除定时任务"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute('DELETE FROM scheduled_tasks WHERE id = ?', (task_id,))
        deleted = cur.rowcount > 0
        conn.commit()
        conn.close()
    if deleted:
        log.info('删除定时任务: id=%d', task_id)
    return deleted


def toggle_task(task_id: int, enabled: bool):
    """启用/禁用任务"""
    with _db_lock:
        conn = _get_conn()
        conn.execute('UPDATE scheduled_tasks SET enabled = ? WHERE id = ?',
                      (1 if enabled else 0, task_id))
        conn.commit()
        conn.close()


def get_all_tasks() -> List[Dict]:
    """获取所有定时任务"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute('SELECT * FROM scheduled_tasks ORDER BY id')
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


def get_task(task_id: int) -> Optional[Dict]:
    """获取单个任务"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute('SELECT * FROM scheduled_tasks WHERE id=?', (task_id,))
        row = cur.fetchone()
        conn.close()
    return dict(row) if row else None


def _parse_interval(schedule_time: str) -> Optional[Tuple[int, int, int, int, int]]:
    """解析 interval 格式 'HH:MM-HH:MM|N'

    Returns: (start_h, start_m, end_h, end_m, interval_minutes) 或 None
    """
    try:
        if '|' not in schedule_time or '-' not in schedule_time:
            return None
        time_part, n_part = schedule_time.split('|')
        start_str, end_str = time_part.split('-')
        sh, sm = map(int, start_str.split(':'))
        eh, em = map(int, end_str.split(':'))
        n = int(n_part)
        if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
            return None
        if n <= 0:
            return None
        return (sh, sm, eh, em, n)
    except Exception:
        return None


def _should_run(task: Dict, now: datetime) -> bool:
    """检查任务是否应该执行"""
    if not task['enabled']:
        return False

    schedule_time = task['schedule_time'] or ''
    last_run = task.get('last_run')

    if task['schedule_type'] == 'daily':
        # 每日定时：格式 HH:MM
        try:
            hour, minute = schedule_time.split(':')
            target_time = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if now >= target_time:
                if last_run:
                    last_run_dt = datetime.strptime(last_run, '%Y-%m-%d %H:%M:%S')
                    if last_run_dt.date() >= now.date():
                        return False
                return True
        except Exception:
            return False

    elif task['schedule_type'] == 'once':
        # 一次性：格式 YYYY-MM-DD HH:MM
        try:
            target_time = datetime.strptime(schedule_time, '%Y-%m-%d %H:%M')
            if now >= target_time:
                if last_run:
                    return False
                return True
        except Exception:
            return False

    elif task['schedule_type'] == 'interval':
        # 每日间隔执行：格式 HH:MM-HH:MM|N
        parsed = _parse_interval(schedule_time)
        if not parsed:
            return False
        sh, sm, eh, em, n = parsed
        start_min_of_day = sh * 60 + sm
        end_min_of_day = eh * 60 + em
        now_min_of_day = now.hour * 60 + now.minute

        # 不在时间窗口内
        if not (start_min_of_day <= now_min_of_day <= end_min_of_day):
            return False

        if not last_run:
            # 首次：仅在达到 start_time 后触发
            return now_min_of_day >= start_min_of_day

        try:
            last_run_dt = datetime.strptime(last_run, '%Y-%m-%d %H:%M:%S')
        except Exception:
            return True  # last_run 格式错误，触发一次以重置

        # 如果上次运行不在今天，且当前已过 start_time，则触发
        if last_run_dt.date() < now.date():
            return now_min_of_day >= start_min_of_day

        # 上次运行在今天：检查是否已过 N 分钟
        elapsed = (now - last_run_dt).total_seconds() / 60.0
        if elapsed >= n:
            # 还要确保不会越过 end_time 窗口
            return True
        return False

    return False


def _execute_task(task: Dict):
    """执行定时任务"""
    try:
        target = task['target_path']
        args = task.get('args', '')
        log.info('执行定时任务: %s -> %s', task['name'], target)

        if sys.platform == 'win32':
            if args:
                subprocess.Popen([target] + args.split(), creationflags=subprocess.DETACHED_PROCESS)
            else:
                subprocess.Popen([target], creationflags=subprocess.DETACHED_PROCESS)
        else:
            if args:
                subprocess.Popen([target] + args.split())
            else:
                subprocess.Popen([target])

        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with _db_lock:
            conn = _get_conn()
            conn.execute('UPDATE scheduled_tasks SET last_run = ? WHERE id = ?',
                          (now_str, task['id']))
            conn.commit()
            conn.close()
        log.info('定时任务执行完成: %s', task['name'])
    except Exception as e:
        log.error('执行定时任务失败: %s -> %s', task['name'], e)


def _check_loop():
    """后台检查循环（每 30 秒检查一次）"""
    while not _stop_event.wait(30):
        try:
            tasks = get_all_tasks()
            now = datetime.now()
            for task in tasks:
                if _should_run(task, now):
                    _execute_task(task)
        except Exception as e:
            log.error('定时任务检查异常: %s', e)


def start_check_thread():
    """启动检查线程"""
    global _check_thread
    if _check_thread and _check_thread.is_alive():
        return
    _stop_event.clear()
    _check_thread = threading.Thread(target=_check_loop, name='scheduler', daemon=True)
    _check_thread.start()
    log.info('定时任务检查线程已启动')


def stop_check_thread():
    """停止检查线程"""
    _stop_event.set()
    log.info('定时任务检查线程已停止')


def shutdown():
    """关闭"""
    stop_check_thread()


# ==================== 工具函数：解析 schedule_time 用于显示 ====================
def describe_schedule(schedule_type: str, schedule_time: str) -> str:
    """返回人类可读的调度描述"""
    if schedule_type == 'daily':
        return f'每日 {schedule_time}'
    if schedule_type == 'once':
        return f'一次性 {schedule_time}'
    if schedule_type == 'interval':
        parsed = _parse_interval(schedule_time)
        if not parsed:
            return f'间隔执行（格式错误：{schedule_time}）'
        sh, sm, eh, em, n = parsed
        return f'每 {n} 分钟 ({sh:02d}:{sm:02d} ~ {eh:02d}:{em:02d})'
    return f'{schedule_type} {schedule_time}'


def validate_schedule(schedule_type: str, schedule_time: str) -> Tuple[bool, str]:
    """校验调度配置是否合法

    Returns: (ok, error_message)
    """
    schedule_time = (schedule_time or '').strip()
    if not schedule_time:
        return False, '执行时间不能为空'
    if schedule_type == 'daily':
        try:
            parts = schedule_time.split(':')
            if len(parts) != 2:
                return False, '每日定时格式应为 HH:MM'
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                return False, '时间超出范围'
        except Exception:
            return False, '每日定时格式应为 HH:MM'
    elif schedule_type == 'once':
        try:
            datetime.strptime(schedule_time, '%Y-%m-%d %H:%M')
        except Exception:
            return False, '一次性格式应为 YYYY-MM-DD HH:MM'
    elif schedule_type == 'interval':
        if not _parse_interval(schedule_time):
            return False, '间隔执行格式应为 HH:MM-HH:MM|分钟数（例如 07:00-23:00|60）'
    else:
        return False, f'未知调度类型: {schedule_type}'
    return True, ''
