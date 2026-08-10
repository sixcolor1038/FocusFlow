# -*- coding: utf-8 -*-
"""
FocusFlow 数据库模块（年度归档版 · 键鼠统计）

核心设计：
1. 按年份拆分数据库文件：data/focusflow_YYYY.db
2. 当前年份 DB 为活跃库（写入目标）
3. 启动时检测年份变化，自动归档上一年数据
4. 跨年查询使用 ATTACH DATABASE 聚合多个年度库
5. SQLite WAL 模式 + synchronous=NORMAL + busy_timeout
6. 单写线程 + Queue，避免并发写
7. 今日计数内存缓存（跨天自动从 DB 重载）
8. 自动备份 + VACUUM + 清理旧数据
9. 批量写入失败自动重试 + 逐条兜底，保证数据不丢

v1.2 变更：重新启用键鼠统计，鼠标操作（点击/滚轮）与键盘按键统一
           写入 key_log 表（key_name 区分），无需独立 mouse_stats 表。
"""

import os
import re
import time
import sqlite3
import threading
import queue
import shutil
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Dict, List

from config import config, get_app_dir, get_data_dir, APP_NAME
from logger import get_logger

log = get_logger('db')


DATA_DIR = get_data_dir()
BACKUP_DIR = os.path.join(get_app_dir(), 'backup')


def _year_db_path(year: int) -> str:
    """获取指定年份的数据库路径"""
    return os.path.join(DATA_DIR, f'focusflow_{year}.db')


def get_current_year_db_path() -> str:
    """当前年份的数据库路径"""
    return _year_db_path(datetime.now().year)


def get_available_years() -> List[int]:
    """获取所有有数据的年份列表（降序）"""
    years = []
    try:
        for f in os.listdir(DATA_DIR):
            if f.startswith('focusflow_') and f.endswith('.db'):
                try:
                    year = int(f[len('focusflow_'):-len('.db')])
                    years.append(year)
                except ValueError:
                    pass
    except Exception as e:
        log.warning('列出年度数据库失败: %s', e)
    return sorted(years, reverse=True)


# ==================== 连接上下文管理器 ====================
class DBConnection:
    """SQLite 连接上下文管理器

    - 默认连接当前年份库
    - 可指定年份
    - 自动 commit / rollback / close
    - WAL 模式 + busy_timeout，提升并发读写的健壮性
    """

    def __init__(self, year: Optional[int] = None, path: Optional[str] = None):
        if path is not None:
            self.path = path
        elif year is not None:
            self.path = _year_db_path(year)
        else:
            self.path = get_current_year_db_path()
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(
            self.path,
            timeout=15.0,           # 加长锁等待时间，减少 OperationalError
            isolation_level=None,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        # 健壮性 PRAGMA
        self.conn.execute('PRAGMA journal_mode=WAL;')
        self.conn.execute('PRAGMA synchronous=NORMAL;')
        self.conn.execute('PRAGMA foreign_keys=ON;')
        self.conn.execute('PRAGMA busy_timeout=15000;')   # 15s 锁等待
        self.conn.execute('PRAGMA cache_size=-8000;')      # 8MB 缓存
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn is None:
            return
        try:
            if exc_type is None:
                self.conn.execute('COMMIT;')
            else:
                self.conn.execute('ROLLBACK;')
        except Exception:
            # 提交/回滚失败也不能阻止 close
            pass
        finally:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


# ==================== 初始化 ====================
def init_db():
    """初始化当前年份数据库表结构，并启动写入线程"""
    year = datetime.now().year
    with DBConnection(year=year) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS key_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_name TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON key_log(timestamp)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_key_name ON key_log(key_name)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        # 记录该年度库的年份
        conn.execute(
            'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
            ('year', str(year))
        )
        # 兼容清理：若旧版本（v1.0 之前）存在独立的 mouse_stats 表，安全删除，
        # 不影响 key_log（键鼠数据现在统一写入 key_log，用 key_name 区分）
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mouse_stats'"
            )
            if cur.fetchone() is not None:
                conn.execute('DROP TABLE IF EXISTS mouse_stats')
                log.info('已移除遗留的 mouse_stats 表（键鼠数据统一存入 key_log）')
        except Exception as me:
            log.debug('mouse_stats 清理检查: %s', me)
    log.info('数据库初始化完成: %s (年份=%d)', get_current_year_db_path(), year)

    # 年度归档检查
    _check_yearly_archive()

    # v1.2.1 数据迁移：拆分旧版组合键（Ctrl+D → Ctrl 与 D 各自独立计数）
    try:
        _migrate_combo_keys()
    except Exception as e:
        log.warning('组合键数据迁移异常: %s', e)

    # 显式启动写入线程
    get_writer()


# ==================== 数据迁移 ====================
_CTRL_SINGLE_RE = re.compile(r'^Ctrl\+([A-Za-z0-9])$')


def _migrate_combo_keys() -> int:
    """拆分旧版"Ctrl+X"组合键记录，返回更新的记录数

    v1.2.0 及之前，按住 Ctrl 按字母时会被记录成 'Ctrl+D' 这样的组合键名；
    v1.2.1 起改为 Ctrl 键与字母键各自独立计数（左Ctrl + D 各 1 次）。
    本函数把历史库中已存在的 'Ctrl+X' 记录改名为对应物理键 'X'
    （'Ctrl+127' → 'Delete'），与新统计口径一致，同类计数自动合并。

    幂等：已转换的记录不再匹配该模式，可安全重复执行（每次启动自动运行）。
    """
    migrated = 0
    for year in get_available_years():
        try:
            with DBConnection(year=year) as conn:
                cur = conn.execute(
                    "SELECT DISTINCT key_name FROM key_log WHERE key_name LIKE 'Ctrl+%'"
                )
                names = [r[0] for r in cur.fetchall()]
                for old_name in names:
                    new_name = None
                    m = _CTRL_SINGLE_RE.match(old_name)
                    if m:
                        new_name = m.group(1).upper()
                    elif old_name == 'Ctrl+127':
                        new_name = 'Delete'
                    if new_name and new_name != old_name:
                        conn.execute(
                            'UPDATE key_log SET key_name=? WHERE key_name=?',
                            (new_name, old_name)
                        )
                        migrated += 1
        except Exception as e:
            log.warning('组合键数据迁移失败（%d 年）: %s', year, e)
    if migrated:
        log.info('组合键数据迁移完成：%d 条 Ctrl+X 记录已按物理键拆分', migrated)
    return migrated


def _check_yearly_archive():
    """检查是否需要执行年度归档

    逻辑：如果当前年份库中存在上一年的数据（跨年时未及时归档），则迁移到上一年库。
    """
    if not config.getbool('database', 'yearly_archive', True):
        return

    current_year = datetime.now().year
    prev_year = current_year - 1

    # 检查当前年份库中是否有上一年的数据
    try:
        with DBConnection(year=current_year) as conn:
            year_start_ts = int(time.mktime(datetime(current_year, 1, 1).timetuple()))
            cur = conn.execute(
                'SELECT COUNT(*) FROM key_log WHERE timestamp < ?', (year_start_ts,)
            )
            count = cur.fetchone()[0]
    except Exception as e:
        log.warning('年度归档检查失败: %s', e)
        return

    if count == 0:
        return

    log.info('检测到 %d 条 %d 年数据在当前库中，开始归档...', count, prev_year)
    _archive_year_data(prev_year, current_year)


def _archive_year_data(target_year: int, source_year: int):
    """将 source_year 库中属于 target_year 的数据迁移到 target_year 库

    步骤：
    1. 确保 target_year 库存在且有表结构
    2. 从 source_year 库导出 target_year 数据
    3. 导入到 target_year 库
    4. 从 source_year 库删除已迁移数据
    5. VACUUM source_year 库
    """
    year_start_ts = int(time.mktime(datetime(target_year, 1, 1).timetuple()))
    year_end_ts = int(time.mktime(datetime(target_year + 1, 1, 1).timetuple()))

    # 1. 确保 target_year 库有表结构
    with DBConnection(year=target_year) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS key_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_name TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON key_log(timestamp)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_key_name ON key_log(key_name)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        conn.execute(
            'INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)',
            ('year', str(target_year))
        )

    # 2-4. 使用 ATTACH 进行迁移
    source_path = _year_db_path(source_year)
    target_path = _year_db_path(target_year)

    try:
        # 导出到 target_year 库
        with DBConnection(year=target_year) as conn:
            conn.execute(f'ATTACH DATABASE ? AS source', (source_path,))
            conn.execute('BEGIN;')
            conn.execute('''
                INSERT INTO key_log (key_name, timestamp)
                SELECT key_name, timestamp FROM source.key_log
                WHERE timestamp >= ? AND timestamp < ?
            ''', (year_start_ts, year_end_ts))
            # 从 source 删除
            conn.execute('''
                DELETE FROM source.key_log
                WHERE timestamp >= ? AND timestamp < ?
            ''', (year_start_ts, year_end_ts))
            conn.execute('COMMIT;')
            conn.execute('DETACH DATABASE source;')
        log.info('归档完成：%d 年数据已迁移到 %s', target_year, target_path)

        # 5. VACUUM source 库
        _vacuum_db(source_path)
        log.info('源库已压缩: %s', source_path)
    except Exception as e:
        log.error('年度归档失败: %s', e, exc_info=True)


# ==================== 写入：单写线程 + Queue ====================
class _DBWriter:
    """单一后台写入线程"""

    def __init__(self, batch_size: int, flush_interval: int):
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        # 有界队列：极端情况下（磁盘慢/写入阻塞）也不会让内存无限增长
        self._max_queue = 5000
        self._queue: 'queue.Queue[Tuple[str, int]]' = queue.Queue(maxsize=self._max_queue)
        self._stop_event = threading.Event()
        self._flush_event = threading.Event()  # 立即 flush 信号
        self._flush_done = threading.Event()   # flush 完成信号
        self._thread: Optional[threading.Thread] = None
        # 今日计数缓存
        self._today_count = 0
        self._today_date: Optional[date] = None
        self._today_count_lock = threading.Lock()
        self._today_count_last_sync = 0.0

    @property
    def name(self):
        return self._thread.name if self._thread else 'db-writer(unstarted)'

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name='db-writer', daemon=True)
        self._thread.start()
        log.info('DB 写入线程已启动 (batch=%d, interval=%ds)',
                 self.batch_size, self.flush_interval)

    def stop(self):
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def put(self, key_name: str, timestamp: int):
        try:
            self._queue.put_nowait((key_name, timestamp))
        except queue.Full:
            # 队列已满（写入线程暂时阻塞）：丢弃最旧的一条，防止内存无限增长
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait((key_name, timestamp))
            except queue.Full:
                pass
        except Exception as e:
            log.error('投递按键到队列失败: %s', e)
        self._increment_today_count()

    def _run(self):
        last_flush = time.time()
        batch = []
        while not self._stop_event.is_set():
            # 健壮性：单次异常不终止线程，避免写入线程意外退出导致队列无限堆积
            try:
                # 用较短的超时轮询，以便及时响应 flush 信号
                timeout = 0.5
                try:
                    item = self._queue.get(timeout=timeout)
                    if item is None:
                        # 哨兵：退出信号
                        break
                    batch.append(item)
                except queue.Empty:
                    pass

                # 检查 flush 信号
                flush_requested = self._flush_event.is_set()

                # v1.0 修复：flush 时先排空队列，避免遗漏未取出的按键
                if flush_requested:
                    while True:
                        try:
                            item = self._queue.get_nowait()
                            if item is None:
                                break
                            batch.append(item)
                        except queue.Empty:
                            break

                now = time.time()
                if (len(batch) >= self.batch_size
                    or (batch and now - last_flush >= self.flush_interval)
                    or (batch and flush_requested)):
                    self._write_batch(batch)
                    batch = []
                    last_flush = now
                    if flush_requested:
                        self._flush_event.clear()
                        self._flush_done.set()
                elif flush_requested:
                    # v1.0 修复：队列为空时无需写入，但仍要清除信号，
                    # 否则 _flush_event 一直置位，后续 flush_now(wait=True) 会误等 2 秒超时
                    self._flush_event.clear()
                    self._flush_done.set()
            except Exception as e:
                log.error('DB 写入线程异常（已忽略继续运行）: %s', e, exc_info=True)
                # 避免异常导致死循环，稍作停顿
                import time as _t
                _t.sleep(0.2)

        # 退出前 flush 残留
        if batch:
            self._write_batch(batch)
        log.info('DB 写入线程已停止')

    def _write_batch(self, batch: List[Tuple[str, int]]):
        """批量写入按键记录（带重试，防止数据丢失）"""
        if not batch:
            return
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with DBConnection() as conn:
                    # 使用事务保证原子性：要么全部写入，要么全部回滚
                    conn.executemany(
                        'INSERT INTO key_log (key_name, timestamp) VALUES (?, ?)',
                        batch
                    )
                log.debug('写入 %d 条记录', len(batch))
                return  # 成功，退出重试循环
            except Exception as e:
                if attempt < max_retries - 1:
                    log.warning('批量写入失败 (第%d次, %d条), 重试中: %s',
                               attempt + 1, len(batch), e)
                    time.sleep(0.5 * (attempt + 1))  # 递增退避
                else:
                    log.error('批量写入最终失败 (%d 条, 已重试%d次): %s',
                             len(batch), max_retries, e, exc_info=True)
                    # 最后一次失败：尝试逐条写入，尽量减少丢失
                    self._write_one_by_one(batch)

    def _write_one_by_one(self, batch: List[Tuple[str, int]]):
        """逐条写入（批量写入全部失败时的兜底方案）"""
        ok = 0
        for key_name, ts in batch:
            try:
                with DBConnection() as conn:
                    conn.execute(
                        'INSERT INTO key_log (key_name, timestamp) VALUES (?, ?)',
                        (key_name, ts)
                    )
                ok += 1
            except Exception:
                pass  # 单条失败不影响其他
        log.warning('逐条写入完成: %d/%d 成功', ok, len(batch))

    def flush_now(self, wait: bool = True):
        """通知写入线程立即写入。

        Args:
            wait: True 时等待写入完成（最多 2 秒，适合退出/备份等需要立即一致性的场景）；
                  False 时只发信号不等待（适合 GUI 后台刷新，避免阻塞界面）。
        """
        self._flush_done.clear()
        self._flush_event.set()
        if wait:
            self._flush_done.wait(timeout=2.0)

    # ---------- 今日计数缓存 ----------
    def _increment_today_count(self):
        """按键时递增今日计数（纯内存，不查 DB）

        v1.0 修复：跨天/首次初始化时不再 return 而跳过递增，
        否则会导致首次按键丢失（计数少 1）。
        """
        today = date.today()
        with self._today_count_lock:
            if self._today_date != today:
                # 跨天或首次：从 DB 加载新一天的初始值
                self._today_date = today
                self._today_count = _query_today_count()
                self._today_count_last_sync = time.time()
                # 不 return，继续递增（当前按键属于新的一天）
            self._today_count += 1

    def get_today_count(self) -> int:
        """获取今日计数（优先用内存值，避免数字跳动）

        修复方案：不再周期性从 DB 重新查询（会导致数字先降后升）。
        只在跨天时从 DB 加载一次初始值，之后纯内存递增。
        """
        today = date.today()
        with self._today_count_lock:
            if self._today_date != today:
                # 跨天，需要从 DB 加载
                pass
            else:
                # 同一天，直接返回内存值（可能包含未 flush 的按键）
                return self._today_count

        # 跨天或首次，从 DB 查询
        count = _query_today_count()
        with self._today_count_lock:
            self._today_date = today
            self._today_count = count
            self._today_count_last_sync = time.time()
        return count


    def reset_today_count_cache(self):
        """重置今日计数缓存（下次查询时从 DB 重新加载）"""
        with self._today_count_lock:
            self._today_date = None

    def purge_key_from_queue(self, key_name: str, start_ts: Optional[int] = None) -> int:
        """从待写入队列中移除指定按键的记录（start_ts 限制时间范围），返回移除条数

        用于"清除今日某按键次数"：未写入数据库的队列数据也要一并剔除。
        """
        removed = 0
        kept: List[Tuple[str, int]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                kept.append(item)
                continue
            k, ts = item
            if k == key_name and (start_ts is None or ts >= start_ts):
                removed += 1
            else:
                kept.append(item)
        for item in kept:
            try:
                self._queue.put_nowait(item)
            except Exception:
                pass
        if removed:
            log.info('从写入队列剔除 %d 条 %s 记录', removed, key_name)
        return removed


# 全局 writer 实例
_writer: Optional[_DBWriter] = None
_writer_lock = threading.Lock()


def get_writer() -> _DBWriter:
    global _writer
    if _writer is None:
        with _writer_lock:
            if _writer is None:
                _writer = _DBWriter(
                    batch_size=config.getint('database', 'batch_size', 100),
                    flush_interval=config.getint('database', 'flush_interval', 30),
                )
                _writer.start()
    return _writer


def record_key(key_name: str):
    """记录一次按键"""
    ts = int(time.time())
    get_writer().put(key_name, ts)


def flush_now(wait: bool = True):
    """立即刷新写入线程。

    Args:
        wait: True 时等待写入完成（最多 2 秒）；False 时只发信号不等待。
    """
    if _writer:
        _writer.flush_now(wait=wait)


def delete_key_today(key_name: str) -> int:
    """删除今日指定按键的所有记录（含待写入队列中未入库的数据），返回删除条数

    用途：长按自动重复（游戏/输入框按住某键）导致该键今日次数虚高时，
         一键清除该键今日计数，不影响历史和其他按键。

    步骤：
    1. 从写入队列剔除该键今日数据
    2. 阻塞 flush 确保其余数据落库
    3. 删除数据库中该键今日记录
    4. 重置今日计数缓存（下次从 DB 重新加载）
    """
    key_name = (key_name or '').strip()
    if not key_name:
        return 0
    today = date.today()
    start_ts = int(time.mktime(today.timetuple()))
    end_ts = start_ts + 86400

    # 1. 先从待写入队列中剔除该按键今日的数据
    purged = 0
    if _writer:
        purged = _writer.purge_key_from_queue(key_name, start_ts)

    # 2. 确保其余数据已写入数据库
    flush_now(wait=True)

    # 3. 删除数据库中该按键今日的记录
    deleted = 0
    with DBConnection() as conn:
        cur = conn.execute(
            'DELETE FROM key_log WHERE key_name=? AND timestamp >= ? AND timestamp < ?',
            (key_name, start_ts, end_ts)
        )
        deleted = cur.rowcount

    # 4. 重置今日计数缓存
    if _writer:
        _writer.reset_today_count_cache()

    total = purged + deleted
    log.info('已删除今日按键 [%s] 的记录 %d 条（队列 %d + 数据库 %d）',
             key_name, total, purged, deleted)
    return total


# ==================== 查询 API ====================
def _query_today_count() -> int:
    """查询今日按键数"""
    today = date.today()
    start_ts = int(time.mktime(today.timetuple()))
    end_ts = start_ts + 86400
    # 今日数据一定在当前年份库
    try:
        with DBConnection() as conn:
            cur = conn.execute(
                'SELECT COUNT(*) FROM key_log WHERE timestamp >= ? AND timestamp < ?',
                (start_ts, end_ts)
            )
            return cur.fetchone()[0]
    except Exception as e:
        log.error('查询今日计数失败: %s', e)
        return 0


def get_today_count() -> int:
    """获取今日按键数（带缓存）"""
    if _writer:
        return _writer.get_today_count()
    return _query_today_count()


def _get_query_years(days: Optional[int], target_date: Optional[date] = None) -> List[int]:
    """根据查询范围确定需要查询的年份列表

    Args:
        days: 天数（None 表示全部）
        target_date: 指定日期查询模式
    """
    if target_date is not None:
        return [target_date.year]

    if days is None:
        return get_available_years()

    # 计算 days 天前到现在的年份范围
    now = datetime.now()
    start = now - timedelta(days=days)
    years = list(range(start.year, now.year + 1))
    # 只返回实际存在数据库的年份
    available = set(get_available_years())
    return [y for y in years if y in available] or [now.year]


def get_stats(days: Optional[int] = None, year: Optional[int] = None) -> Tuple[int, Dict[str, int]]:
    """获取统计：返回 (总数, {按键: 次数})

    优化：不调用 flush_now()（会阻塞 2 秒），直接查询 DB。
    未 flush 的数据（最多 100 条）不影响统计准确性。

    Args:
        days: 天数（None=全部）
        year: 指定年份（None=自动）
    """
    # 不再调用 flush_now()，避免 UI 卡顿
    # 未写入的数据最多 batch_size 条（100条），对统计影响极小

    if year is not None:
        return _query_stats_single_year(year, days)
    return _query_stats_multi_year(days)


def _query_stats_single_year(year: int, days: Optional[int]) -> Tuple[int, Dict[str, int]]:
    """查询单个年份库"""
    try:
        with DBConnection(year=year) as conn:
            if days is None:
                cur = conn.execute('SELECT COUNT(*) FROM key_log')
                total = cur.fetchone()[0]
                cur = conn.execute(
                    'SELECT key_name, COUNT(*) as cnt FROM key_log '
                    'GROUP BY key_name ORDER BY cnt DESC'
                )
            else:
                cutoff = int(time.time()) - days * 86400
                cur = conn.execute(
                    'SELECT COUNT(*) FROM key_log WHERE timestamp >= ?', (cutoff,)
                )
                total = cur.fetchone()[0]
                cur = conn.execute(
                    'SELECT key_name, COUNT(*) as cnt FROM key_log '
                    'WHERE timestamp >= ? GROUP BY key_name ORDER BY cnt DESC',
                    (cutoff,)
                )
            stats_dict = {row[0]: row[1] for row in cur.fetchall()}
        return total, stats_dict
    except Exception as e:
        log.error('查询 %d 年统计失败: %s', year, e)
        return 0, {}


def _query_stats_multi_year(days: Optional[int]) -> Tuple[int, Dict[str, int]]:
    """跨年查询（使用 ATTACH）"""
    years = _get_query_years(days)
    if len(years) == 1:
        return _query_stats_single_year(years[0], days)

    # 多年：用第一个年份库作为主库，ATTACH 其他
    main_year = years[0]
    other_years = years[1:]
    try:
        with DBConnection(year=main_year) as conn:
            # ATTACH 其他年份库
            aliases = []
            for i, y in enumerate(other_years):
                alias = f'y{y}'
                path = _year_db_path(y)
                if os.path.exists(path):
                    conn.execute(f'ATTACH DATABASE ? AS {alias}', (path,))
                    aliases.append((y, alias))

            # 构建 UNION ALL 查询
            if days is None:
                where_clause = ''
                params = ()
            else:
                cutoff = int(time.time()) - days * 86400
                where_clause = 'WHERE timestamp >= ?'
                params = (cutoff,)

            # 主库
            sql_parts = [f'SELECT key_name, COUNT(*) as cnt FROM key_log {where_clause}']
            for _, alias in aliases:
                sql_parts.append(
                    f'SELECT key_name, COUNT(*) as cnt FROM {alias}.key_log {where_clause}'
                )
            union_sql = ' UNION ALL '.join(sql_parts)

            # 总数
            count_sql = f'SELECT SUM(cnt) FROM ({union_sql})'
            # 参数需要重复 N 次
            all_params = params * (1 + len(aliases))
            cur = conn.execute(count_sql, all_params)
            total = cur.fetchone()[0] or 0

            # 分组统计
            group_sql = f'SELECT key_name, SUM(cnt) as total FROM ({union_sql}) GROUP BY key_name ORDER BY total DESC'
            cur = conn.execute(group_sql, all_params)
            stats_dict = {row[0]: row[1] for row in cur.fetchall()}

            # DETACH
            for _, alias in aliases:
                try:
                    conn.execute(f'DETACH DATABASE {alias}')
                except Exception:
                    pass

        return total, stats_dict
    except Exception as e:
        log.error('跨年查询失败: %s', e, exc_info=True)
        return 0, {}


def get_stats_by_date(target_date: date) -> Tuple[int, Dict[str, int]]:
    """查询指定日期的统计"""
    # 不调用 flush_now()，避免卡顿
    start_ts = int(time.mktime(target_date.timetuple()))
    end_ts = start_ts + 86400
    # 检查该年份库是否存在
    db_path = _year_db_path(target_date.year)
    if not os.path.exists(db_path):
        return 0, {}
    try:
        with DBConnection(year=target_date.year) as conn:
            # 检查表是否存在
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='key_log'"
            )
            if cur.fetchone() is None:
                return 0, {}
            cur = conn.execute(
                'SELECT COUNT(*) FROM key_log WHERE timestamp >= ? AND timestamp < ?',
                (start_ts, end_ts)
            )
            total = cur.fetchone()[0]
            cur = conn.execute(
                'SELECT key_name, COUNT(*) as cnt FROM key_log '
                'WHERE timestamp >= ? AND timestamp < ? '
                'GROUP BY key_name ORDER BY cnt DESC',
                (start_ts, end_ts)
            )
            stats_dict = {row[0]: row[1] for row in cur.fetchall()}
        return total, stats_dict
    except Exception as e:
        log.error('查询 %s 统计失败: %s', target_date, e)
        return 0, {}


def get_daily_counts(days: int, year: Optional[int] = None) -> List[Tuple[str, int]]:
    """获取最近 N 天的每日按键数（用于趋势图）

    Returns: [(日期字符串, 次数), ...]
    """
    # 不调用 flush_now()，避免卡顿
    now = datetime.now()
    start = now - timedelta(days=days - 1)

    if year is not None:
        years_to_query = [year]
    else:
        years_to_query = list(range(start.year, now.year + 1))
        available = set(get_available_years())
        years_to_query = [y for y in years_to_query if y in available] or [now.year]

    # 初始化所有日期为 0
    daily_map = {}
    for i in range(days):
        d = (start + timedelta(days=i)).date()
        daily_map[d] = 0

    for y in years_to_query:
        # 检查该年份库是否存在
        db_path = _year_db_path(y)
        if not os.path.exists(db_path):
            continue
        try:
            with DBConnection(year=y) as conn:
                # 检查表是否存在
                cur = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='key_log'"
                )
                if cur.fetchone() is None:
                    continue
                start_ts = int(time.mktime(start.timetuple()))
                end_ts = int(time.mktime(now.timetuple())) + 86400
                cur = conn.execute(
                    '''SELECT timestamp, COUNT(*) as cnt FROM key_log
                       WHERE timestamp >= ? AND timestamp < ?
                       GROUP BY date(timestamp, 'unixepoch', 'localtime')''',
                    (start_ts, end_ts)
                )
                for row in cur.fetchall():
                    ts = row[0]
                    cnt = row[1]
                    d = datetime.fromtimestamp(ts).date()
                    if d in daily_map:
                        daily_map[d] += cnt
        except Exception as e:
            log.error('查询 %d 年每日统计失败: %s', y, e)

    return [(d.isoformat(), c) for d, c in sorted(daily_map.items())]


def get_hourly_stats(target_date: Optional[date] = None) -> List[int]:
    """获取指定日期的每小时按键数（用于热力图/小时分布）

    Args:
        target_date: 指定日期（None=今天）

    Returns: 长度 24 的列表，索引 0-23 对应每小时的按键数
    """
    if target_date is None:
        target_date = date.today()

    db_path = _year_db_path(target_date.year)
    if not os.path.exists(db_path):
        return [0] * 24

    start_ts = int(time.mktime(target_date.timetuple()))
    end_ts = start_ts + 86400

    hourly = [0] * 24
    try:
        with DBConnection(year=target_date.year) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='key_log'"
            )
            if cur.fetchone() is None:
                return hourly
            cur = conn.execute(
                '''SELECT (timestamp - ?) / 3600 as hour, COUNT(*) as cnt
                   FROM key_log
                   WHERE timestamp >= ? AND timestamp < ?
                   GROUP BY hour''',
                (start_ts, start_ts, end_ts)
            )
            for row in cur.fetchall():
                hour = row[0]
                if 0 <= hour < 24:
                    hourly[hour] = row[1]
    except Exception as e:
        log.error('查询每小时统计失败: %s', e)
    return hourly


def get_weekday_stats(days: int = 30) -> Dict[int, int]:
    """获取最近 N 天按星期几的统计（用于星期分布）

    Returns: {0=周一, 1=周二, ..., 6=周日} -> 按键数
    """
    daily = get_daily_counts(days)
    weekday_counts = {i: 0 for i in range(7)}
    for date_str, count in daily:
        try:
            d = datetime.strptime(date_str, '%Y-%m-%d').date()
            # Python weekday(): 0=周一, 6=周日
            weekday_counts[d.weekday()] += count
        except Exception:
            pass
    return weekday_counts


# ==================== 维护操作 ====================
def cleanup_old_data(keep_days: int) -> int:
    """清理 keep_days 天前的数据，返回删除条数"""
    flush_now()
    cutoff = int(time.time()) - keep_days * 86400
    total_deleted = 0
    for year in get_available_years():
        try:
            with DBConnection(year=year) as conn:
                cur = conn.execute('SELECT COUNT(*) FROM key_log WHERE timestamp < ?', (cutoff,))
                count = cur.fetchone()[0]
                if count > 0:
                    conn.execute('DELETE FROM key_log WHERE timestamp < ?', (cutoff,))
                    total_deleted += count
                    log.info('从 %d 年库删除 %d 条旧数据', year, count)
        except Exception as e:
            log.error('清理 %d 年库失败: %s', year, e)
    if total_deleted > 0:
        log.info('共清理 %d 条旧数据', total_deleted)
    return total_deleted


def _vacuum_db(path: str):
    """VACUUM 指定数据库"""
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
        conn.execute('VACUUM;')
        conn.close()
    except Exception as e:
        log.error('VACUUM %s 失败: %s', path, e)


def vacuum():
    """压缩所有年度数据库"""
    flush_now()
    for year in get_available_years():
        path = _year_db_path(year)
        _vacuum_db(path)
        log.info('已压缩 %d 年数据库', year)


def maybe_auto_vacuum():
    """按配置自动 VACUUM"""
    interval_days = config.getint('database', 'auto_vacuum_days', 7)
    if interval_days <= 0:
        return
    try:
        with DBConnection() as conn:
            cur = conn.execute(
                "SELECT value FROM meta WHERE key = 'last_vacuum'"
            )
            row = cur.fetchone()
            if row:
                last = datetime.fromisoformat(row[0])
                if (datetime.now() - last).days < interval_days:
                    return
        vacuum()
        with DBConnection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ('last_vacuum', datetime.now().isoformat())
            )
        log.info('自动 VACUUM 完成')
    except Exception as e:
        log.warning('自动 VACUUM 失败: %s', e)


def backup_database() -> Optional[str]:
    """备份所有年度数据库到 backup/ 目录"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    flush_now()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backed_up = []
    for year in get_available_years():
        src = _year_db_path(year)
        if not os.path.exists(src):
            continue
        # checkpoint WAL
        try:
            conn = sqlite3.connect(src, isolation_level=None)
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE);')
            conn.close()
        except Exception:
            pass
        dst = os.path.join(BACKUP_DIR, f'focusflow_{year}_{timestamp}.db')
        try:
            shutil.copy2(src, dst)
            backed_up.append(dst)
        except Exception as e:
            log.error('备份 %d 年库失败: %s', year, e)
    if backed_up:
        max_backups = config.getint('database', 'max_backups', 5)
        _rotate_backups(max_backups)
        log.info('已备份 %d 个年度库到 %s', len(backed_up), BACKUP_DIR)
        return backed_up[0]
    return None


def _rotate_backups(max_keep: int):
    """保留最近 N 个备份（按年份分组）"""
    try:
        from collections import defaultdict
        groups = defaultdict(list)
        for f in os.listdir(BACKUP_DIR):
            if f.startswith('focusflow_') and f.endswith('.db'):
                # focusflow_2026_20260723_075125.db
                parts = f.replace('.db', '').split('_')
                if len(parts) >= 3:
                    year = parts[1]
                    groups[year].append(os.path.join(BACKUP_DIR, f))
        for year, files in groups.items():
            files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            for old in files[max_keep:]:
                try:
                    os.remove(old)
                    log.debug('删除旧备份: %s', old)
                except Exception:
                    pass
    except Exception as e:
        log.warning('清理旧备份失败: %s', e)


def shutdown():
    """优雅关闭：flush + 备份"""
    log.info('数据库关闭中...')
    flush_now()
    if config.getbool('database', 'backup_on_exit', True):
        backup_database()
    if _writer:
        _writer.stop()
    log.info('数据库已关闭')

