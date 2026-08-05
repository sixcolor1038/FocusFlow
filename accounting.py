# -*- coding: utf-8 -*-
"""
FocusFlow 记账模块（v3.1 完整版）

功能：
- 记录收入和支出
- 按日期/月/年/全部/分类/细分分类查询
- 计算距今多久（年+天）
- 分类盈亏统计（如游戏投入/赚取）
- 分页查询
- 分类与细分分类数据可由插件动态维护（DB 持久化）

数据库表：
  expenses        - 记账记录
  categories      - 顶级分类（type: expense/income/both）
  subcategories   - 细分分类（属于某个顶级分类）
"""

import os
import sqlite3
import threading
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Tuple

from config import get_data_dir
from logger import get_logger

log = get_logger('accounting')


DB_PATH = os.path.join(get_data_dir(), 'focusflow_accounting.db')
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA foreign_keys=ON;')
    return conn


# ==================== 预设分类（首次初始化用） ====================
# 游戏类作为顶级分类，梦幻西游作为其下的细分分类（修复用户反馈）
DEFAULT_CATEGORIES: Dict[str, Dict] = {
    '食品饮料': {
        'type': 'expense',
        'subs': ['早餐', '午餐', '晚餐', '零食', '饮料', '水果', '外卖', '其他'],
    },
    '日用百货': {
        'type': 'expense',
        'subs': ['清洁用品', '纸品', '厨房用品', '卫浴用品', '其他'],
    },
    '数码电子': {
        'type': 'expense',
        'subs': ['电脑配件', '手机配件', '耳机', '充电器', '存储设备', '其他'],
    },
    '服饰鞋包': {
        'type': 'expense',
        'subs': ['上衣', '裤子', '鞋子', '包', '配饰', '其他'],
    },
    '家居家电': {
        'type': 'expense',
        'subs': ['家具', '小家电', '灯具', '装饰', '其他'],
    },
    '图书文具': {
        'type': 'expense',
        'subs': ['书籍', '文具', '办公用品', '其他'],
    },
    '交通出行': {
        'type': 'expense',
        'subs': ['公交', '地铁', '打车', '加油', '停车', '其他'],
    },
    '医疗健康': {
        'type': 'expense',
        'subs': ['药品', '保健品', '医疗器械', '其他'],
    },
    '娱乐休闲': {
        'type': 'expense',
        'subs': ['电影', '音乐', '运动', '其他'],
    },
    '游戏': {
        'type': 'both',
        'subs': ['梦幻西游', '充值', '道具', '账号', '装备', '其他'],
    },
    '工资收入': {
        'type': 'income',
        'subs': ['月薪', '奖金', '兼职', '其他'],
    },
    '其他收入': {
        'type': 'income',
        'subs': ['退款', '红包', '投资收益', '其他'],
    },
    '其他': {
        'type': 'both',
        'subs': ['其他'],
    },
}


def init_db():
    """初始化记账数据库（含分类表 + 旧表迁移）"""
    with _db_lock:
        conn = _get_conn()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL DEFAULT '支出',
                item_name TEXT NOT NULL,
                store TEXT,
                purchase_date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT,
                subcategory TEXT,
                delivery_date TEXT,
                record_time TEXT NOT NULL,
                note TEXT
            )
        ''')
        # 迁移：如果旧表没有 type 列，添加并默认'支出'
        try:
            conn.execute('SELECT type FROM expenses LIMIT 1')
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE expenses ADD COLUMN type TEXT NOT NULL DEFAULT '支出'")
            log.info('已迁移：添加 type 列')

        # 分类表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL DEFAULT 'both',
                sort_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS subcategories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(name, category_id),
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            )
        ''')

        conn.execute('CREATE INDEX IF NOT EXISTS idx_purchase_date ON expenses(purchase_date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_category ON expenses(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_type ON expenses(type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_subcat_cat ON subcategories(category_id)')

        # 如果分类表为空，初始化默认分类
        cur = conn.execute('SELECT COUNT(*) FROM categories')
        if cur.fetchone()[0] == 0:
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            sort_idx = 0
            for cat_name, info in DEFAULT_CATEGORIES.items():
                sort_idx += 1
                conn.execute(
                    'INSERT INTO categories (name, type, sort_order, created_at) VALUES (?, ?, ?, ?)',
                    (cat_name, info['type'], sort_idx, now_str)
                )
                cat_id = cur = conn.execute(
                    'SELECT id FROM categories WHERE name=?', (cat_name,)
                ).fetchone()[0]
                for i, sub_name in enumerate(info['subs'], 1):
                    conn.execute(
                        'INSERT OR IGNORE INTO subcategories (name, category_id, sort_order, created_at) VALUES (?, ?, ?, ?)',
                        (sub_name, cat_id, i, now_str)
                    )
            log.info('已初始化默认分类数据')

        conn.commit()
        conn.close()
    log.info('记账数据库初始化完成: %s', DB_PATH)


# ==================== 分类查询 ====================
def get_categories(record_type: Optional[str] = None) -> Dict[str, List[str]]:
    """获取分类字典 {分类名: [细分分类1, 细分分类2, ...]}

    Args:
        record_type: 'expense' / 'income' / None(全部)
                     若指定，则只返回该类型对应的分类（'both' 始终包含）
    """
    result: Dict[str, List[str]] = {}
    with _db_lock:
        conn = _get_conn()
        if record_type:
            cur = conn.execute(
                '''SELECT id, name FROM categories
                   WHERE type = ? OR type = 'both'
                   ORDER BY sort_order, id''',
                (record_type,)
            )
        else:
            cur = conn.execute(
                'SELECT id, name FROM categories ORDER BY sort_order, id'
            )
        cats = cur.fetchall()
        for cat in cats:
            sub_cur = conn.execute(
                '''SELECT name FROM subcategories
                   WHERE category_id = ?
                   ORDER BY sort_order, id''',
                (cat['id'],)
            )
            subs = [r['name'] for r in sub_cur.fetchall()]
            result[cat['name']] = subs
        conn.close()
    return result


def get_category_names(record_type: Optional[str] = None) -> List[str]:
    """获取分类名列表"""
    with _db_lock:
        conn = _get_conn()
        if record_type:
            cur = conn.execute(
                '''SELECT name FROM categories
                   WHERE type = ? OR type = 'both'
                   ORDER BY sort_order, id''',
                (record_type,)
            )
        else:
            cur = conn.execute(
                'SELECT name FROM categories ORDER BY sort_order, id'
            )
        result = [r['name'] for r in cur.fetchall()]
        conn.close()
    return result


def get_subcategories(category: str) -> List[str]:
    """获取指定分类的细分分类列表（数据库 + 历史记录兜底）"""
    result: List[str] = []
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT s.name FROM subcategories s
               JOIN categories c ON s.category_id = c.id
               WHERE c.name = ?
               ORDER BY s.sort_order, s.id''',
            (category,)
        )
        result = [r['name'] for r in cur.fetchall()]
        # 历史记录兜底（用户可能过去用过 DB 中没添加过的细分分类）
        cur2 = conn.execute(
            'SELECT DISTINCT subcategory FROM expenses WHERE category = ? AND subcategory != ""',
            (category,)
        )
        for row in cur2.fetchall():
            sub = row['subcategory']
            if sub and sub not in result:
                result.append(sub)
        conn.close()
    return result


# ==================== 分类 CRUD（供插件调用） ====================
def add_category(name: str, cat_type: str = 'both') -> Tuple[bool, str]:
    """添加分类

    Args:
        name: 分类名
        cat_type: 'expense' / 'income' / 'both'
    Returns: (success, message)
    """
    name = (name or '').strip()
    if not name:
        return False, '分类名不能为空'
    if cat_type not in ('expense', 'income', 'both'):
        return False, f'无效的分类类型: {cat_type}'
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute('SELECT MAX(sort_order) FROM categories')
            max_order = cur.fetchone()[0] or 0
            conn.execute(
                'INSERT INTO categories (name, type, sort_order, created_at) VALUES (?, ?, ?, ?)',
                (name, cat_type, max_order + 1, now_str)
            )
            conn.commit()
            log.info('添加分类: %s (%s)', name, cat_type)
            return True, '添加成功'
        except sqlite3.IntegrityError:
            return False, f'分类 [{name}] 已存在'
        finally:
            conn.close()


def update_category(old_name: str, new_name: str, cat_type: Optional[str] = None) -> Tuple[bool, str]:
    """更新分类"""
    new_name = (new_name or '').strip()
    if not new_name:
        return False, '分类名不能为空'
    with _db_lock:
        conn = _get_conn()
        try:
            # 检查新名称是否与现有其他分类重名
            cur = conn.execute('SELECT id FROM categories WHERE name=? AND name!=?',
                               (new_name, old_name))
            if cur.fetchone():
                return False, f'分类 [{new_name}] 已存在'
            if cat_type:
                if cat_type not in ('expense', 'income', 'both'):
                    return False, f'无效的分类类型: {cat_type}'
                conn.execute(
                    'UPDATE categories SET name=?, type=? WHERE name=?',
                    (new_name, cat_type, old_name)
                )
            else:
                conn.execute(
                    'UPDATE categories SET name=? WHERE name=?',
                    (new_name, old_name)
                )
            # 同步更新 expenses 表中的旧分类名
            conn.execute(
                'UPDATE expenses SET category=? WHERE category=?',
                (new_name, old_name)
            )
            conn.commit()
            log.info('更新分类: %s -> %s', old_name, new_name)
            return True, '更新成功'
        except Exception as e:
            return False, f'更新失败: {e}'
        finally:
            conn.close()


def delete_category(name: str) -> Tuple[bool, str]:
    """删除分类（同时删除其下所有细分分类）

    注意：expenses 表中已使用此分类的记录不会被删除，
    但其 category 字段会保留为旧字符串，便于查看历史。
    """
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute('DELETE FROM categories WHERE name=?', (name,))
            conn.commit()
            if cur.rowcount == 0:
                return False, f'分类 [{name}] 不存在'
            log.info('删除分类: %s', name)
            return True, '删除成功'
        except Exception as e:
            return False, f'删除失败: {e}'
        finally:
            conn.close()


def reorder_category(name: str, direction: str) -> Tuple[bool, str]:
    """上移/下移分类排序

    Args:
        direction: 'up' / 'down'
    """
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute('SELECT id, sort_order FROM categories WHERE name=?', (name,))
            row = cur.fetchone()
            if not row:
                return False, f'分类 [{name}] 不存在'
            cur_order = row['sort_order']
            if direction == 'up':
                cur2 = conn.execute(
                    '''SELECT id, sort_order FROM categories
                       WHERE sort_order < ? ORDER BY sort_order DESC LIMIT 1''',
                    (cur_order,)
                )
            else:
                cur2 = conn.execute(
                    '''SELECT id, sort_order FROM categories
                       WHERE sort_order > ? ORDER BY sort_order ASC LIMIT 1''',
                    (cur_order,)
                )
            other = cur2.fetchone()
            if not other:
                return False, '已到达边界'
            # 交换 sort_order
            conn.execute('UPDATE categories SET sort_order=? WHERE id=?',
                         (other['sort_order'], row['id']))
            conn.execute('UPDATE categories SET sort_order=? WHERE id=?',
                         (cur_order, other['id']))
            conn.commit()
            return True, '排序已更新'
        except Exception as e:
            return False, f'排序失败: {e}'
        finally:
            conn.close()


def add_subcategory(category: str, sub_name: str) -> Tuple[bool, str]:
    """为指定分类添加细分分类"""
    sub_name = (sub_name or '').strip()
    if not sub_name:
        return False, '细分分类名不能为空'
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute('SELECT id FROM categories WHERE name=?', (category,))
            cat_row = cur.fetchone()
            if not cat_row:
                return False, f'分类 [{category}] 不存在'
            cat_id = cat_row['id']
            cur2 = conn.execute(
                'SELECT MAX(sort_order) FROM subcategories WHERE category_id=?',
                (cat_id,)
            )
            max_order = cur2.fetchone()[0] or 0
            conn.execute(
                '''INSERT INTO subcategories (name, category_id, sort_order, created_at)
                   VALUES (?, ?, ?, ?)''',
                (sub_name, cat_id, max_order + 1, now_str)
            )
            conn.commit()
            log.info('添加细分分类: %s / %s', category, sub_name)
            return True, '添加成功'
        except sqlite3.IntegrityError:
            return False, f'细分分类 [{sub_name}] 在 [{category}] 下已存在'
        except Exception as e:
            return False, f'添加失败: {e}'
        finally:
            conn.close()


def update_subcategory(category: str, old_name: str, new_name: str) -> Tuple[bool, str]:
    """更新细分分类名"""
    new_name = (new_name or '').strip()
    if not new_name:
        return False, '细分分类名不能为空'
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                '''SELECT s.id FROM subcategories s
                   JOIN categories c ON s.category_id = c.id
                   WHERE c.name=? AND s.name=?''',
                (category, old_name)
            )
            row = cur.fetchone()
            if not row:
                return False, f'细分分类 [{old_name}] 不存在'
            # 检查新名是否冲突
            cur2 = conn.execute(
                '''SELECT s.id FROM subcategories s
                   JOIN categories c ON s.category_id = c.id
                   WHERE c.name=? AND s.name=? AND s.name != ?''',
                (category, new_name, old_name)
            )
            if cur2.fetchone():
                return False, f'细分分类 [{new_name}] 在 [{category}] 下已存在'
            conn.execute(
                '''UPDATE subcategories SET name=? WHERE id=?''',
                (new_name, row['id'])
            )
            # 同步更新 expenses 表
            conn.execute(
                'UPDATE expenses SET subcategory=? WHERE category=? AND subcategory=?',
                (new_name, category, old_name)
            )
            conn.commit()
            log.info('更新细分分类: %s/%s -> %s', category, old_name, new_name)
            return True, '更新成功'
        except Exception as e:
            return False, f'更新失败: {e}'
        finally:
            conn.close()


def delete_subcategory(category: str, sub_name: str) -> Tuple[bool, str]:
    """删除细分分类"""
    with _db_lock:
        conn = _get_conn()
        try:
            cur = conn.execute(
                '''DELETE FROM subcategories
                   WHERE id IN (
                       SELECT s.id FROM subcategories s
                       JOIN categories c ON s.category_id = c.id
                       WHERE c.name=? AND s.name=?
                   )''',
                (category, sub_name)
            )
            conn.commit()
            if cur.rowcount == 0:
                return False, f'细分分类 [{sub_name}] 不存在'
            log.info('删除细分分类: %s/%s', category, sub_name)
            return True, '删除成功'
        except Exception as e:
            return False, f'删除失败: {e}'
        finally:
            conn.close()


# ==================== 记账 CRUD ====================
def add_expense(item_name: str, store: str, purchase_date: str, amount: float,
                category: str, subcategory: str, delivery_date: str = '',
                note: str = '', record_type: str = '支出') -> int:
    """添加一条记账记录"""
    record_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''INSERT INTO expenses
               (type, item_name, store, purchase_date, amount, category, subcategory,
                delivery_date, record_time, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (record_type, item_name, store or '', purchase_date, amount,
             category or '', subcategory or '', delivery_date or '',
             record_time, note or '')
        )
        record_id = cur.lastrowid
        conn.commit()
        conn.close()
    log.info('添加记账: [%s] %s (%.2f)', record_type, item_name, amount)
    return record_id


def update_expense(record_id: int, **kwargs) -> bool:
    """更新记账记录

    可更新字段: type, item_name, store, purchase_date, amount, category,
              subcategory, delivery_date, note
    """
    allowed = {'type', 'item_name', 'store', 'purchase_date', 'amount',
               'category', 'subcategory', 'delivery_date', 'note'}
    update_fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not update_fields:
        return False
    set_parts = [f'{k}=?' for k in update_fields]
    values = list(update_fields.values()) + [record_id]
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            f'UPDATE expenses SET {", ".join(set_parts)} WHERE id=?', values
        )
        conn.commit()
        success = cur.rowcount > 0
        conn.close()
    if success:
        log.info('更新记账记录: id=%d, 字段=%s', record_id, list(update_fields.keys()))
    return success


def get_expense_by_id(record_id: int) -> Optional[Dict]:
    """根据 ID 获取单条记录"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute('SELECT * FROM expenses WHERE id=?', (record_id,))
        row = cur.fetchone()
        conn.close()
    return dict(row) if row else None


def delete_expense(record_id: int) -> bool:
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute('DELETE FROM expenses WHERE id=?', (record_id,))
        conn.commit()
        success = cur.rowcount > 0
        conn.close()
    return success


def delete_expenses(record_ids: List[int]) -> int:
    """批量删除"""
    if not record_ids:
        return 0
    placeholders = ','.join('?' * len(record_ids))
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(f'DELETE FROM expenses WHERE id IN ({placeholders})', record_ids)
        conn.commit()
        count = cur.rowcount
        conn.close()
    return count


# ==================== 查询 ====================
def get_all_expenses() -> List[Dict]:
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            'SELECT * FROM expenses ORDER BY id DESC'
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


def get_expenses_paginated(page: int = 1, page_size: int = 10,
                            record_type: Optional[str] = None,
                            category: Optional[str] = None,
                            subcategory: Optional[str] = None,
                            keyword: Optional[str] = None,
                            date_from: Optional[str] = None,
                            date_to: Optional[str] = None) -> Tuple[List[Dict], int]:
    """分页查询（支持 类型/分类/细分分类/关键词/日期范围 过滤）

    Returns: (记录列表, 总条数)
    """
    offset = (page - 1) * page_size
    where_parts: List[str] = []
    params: List = []
    if record_type:
        where_parts.append('type=?')
        params.append(record_type)
    if category:
        where_parts.append('category=?')
        params.append(category)
    if subcategory:
        where_parts.append('subcategory=?')
        params.append(subcategory)
    if keyword:
        where_parts.append('(item_name LIKE ? OR note LIKE ? OR category LIKE ? OR subcategory LIKE ?)')
        kw = f'%{keyword}%'
        params.extend([kw, kw, kw, kw])
    if date_from:
        where_parts.append('purchase_date >= ?')
        params.append(date_from)
    if date_to:
        where_parts.append('purchase_date <= ?')
        params.append(date_to)
    where_sql = (' WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            f'''SELECT * FROM expenses{where_sql}
               ORDER BY id DESC
               LIMIT ? OFFSET ?''',
            params + [page_size, offset]
        )
        rows = cur.fetchall()
        cur2 = conn.execute(f'SELECT COUNT(*) FROM expenses{where_sql}', params)
        total = cur2.fetchone()[0]
        conn.close()
    return [dict(row) for row in rows], total


def get_expenses_by_date(target_date: str) -> List[Dict]:
    """查询指定日期的记录"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            'SELECT * FROM expenses WHERE purchase_date=? ORDER BY id DESC',
            (target_date,)
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


def get_expenses_by_month(year: int, month: int) -> List[Dict]:
    start = f'{year:04d}-{month:02d}-01'
    end = f'{year+1:04d}-01-01' if month == 12 else f'{year:04d}-{month+1:02d}-01'
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT * FROM expenses
               WHERE purchase_date >= ? AND purchase_date < ?
               ORDER BY id DESC''',
            (start, end)
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


def get_expenses_by_year(year: int) -> List[Dict]:
    start = f'{year:04d}-01-01'
    end = f'{year+1:04d}-01-01'
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT * FROM expenses
               WHERE purchase_date >= ? AND purchase_date < ?
               ORDER BY id DESC''',
            (start, end)
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


def search_expenses(keyword: str) -> List[Dict]:
    """按关键词搜索（物品名/备注/分类）"""
    kw = f'%{keyword}%'
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT * FROM expenses
               WHERE item_name LIKE ? OR note LIKE ? OR category LIKE ? OR subcategory LIKE ?
               ORDER BY id DESC''',
            (kw, kw, kw, kw)
        )
        rows = cur.fetchall()
        conn.close()
    return [dict(row) for row in rows]


# ==================== 距今多久 ====================
def calculate_days_ago(purchase_date_str: str) -> Dict:
    """计算距今多久

    Returns: {'years': N, 'days': M, 'total_days': 总天数, 'text': '距今X年Y天'}
    """
    try:
        pd = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return {'years': 0, 'days': 0, 'total_days': 0, 'text': '日期格式错误'}
    today = date.today()
    delta = today - pd
    total_days = delta.days
    years = total_days // 365
    remaining_days = total_days % 365
    if years > 0:
        text = f'距今{years}年{remaining_days}天'
    else:
        text = f'距今{total_days}天'
    return {'years': years, 'days': remaining_days, 'total_days': total_days, 'text': text}


def get_days_ago_for_records(record_ids: List[int]) -> Dict[int, Dict]:
    """获取多条记录的距今信息"""
    result = {}
    if not record_ids:
        return result
    placeholders = ','.join('?' * len(record_ids))
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            f'SELECT id, purchase_date FROM expenses WHERE id IN ({placeholders})',
            record_ids
        )
        for row in cur.fetchall():
            result[row['id']] = calculate_days_ago(row['purchase_date'])
        conn.close()
    return result


# ==================== 统计 ====================
def get_monthly_summary(year: int, month: int) -> Dict:
    """月度统计"""
    expenses = get_expenses_by_month(year, month)
    total_expense = sum(e['amount'] for e in expenses if e.get('type') == '支出')
    total_income = sum(e['amount'] for e in expenses if e.get('type') == '收入')
    # 分类明细 = 净值（收入为正、支出为负），避免正负金额被错误累加
    # 例如：同分类收入 500 + 支出 500 -> 净值 0，而不是 1000
    category_stats: Dict[str, float] = {}
    for e in expenses:
        cat = e.get('category') or '未分类'
        amount = e.get('amount') or 0
        if e.get('type') == '收入':
            category_stats[cat] = category_stats.get(cat, 0) + amount
        else:
            category_stats[cat] = category_stats.get(cat, 0) - amount
    return {
        'total_expense': total_expense,
        'total_income': total_income,
        'net': total_income - total_expense,
        'count': len(expenses),
        'category_stats': category_stats,
    }


def get_category_profit_loss(category: str) -> Dict:
    """获取指定分类的盈亏统计

    Returns: {'total_invested': 投入, 'total_earned': 赚取, 'net': 净值, 'records': 记录数}
    """
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT type, SUM(amount) as total, COUNT(*) as cnt
               FROM expenses
               WHERE category = ?
               GROUP BY type''',
            (category,)
        )
        rows = cur.fetchall()
        conn.close()
    invested = 0.0
    earned = 0.0
    count = 0
    for row in rows:
        if row['type'] == '支出':
            invested = row['total'] or 0
        elif row['type'] == '收入':
            earned = row['total'] or 0
        count += row['cnt']
    return {
        'total_invested': invested,
        'total_earned': earned,
        'net': earned - invested,
        'records': count,
    }


def get_all_category_profit_loss() -> List[Dict]:
    """获取所有分类的盈亏统计"""
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT category, type, SUM(amount) as total, COUNT(*) as cnt
               FROM expenses
               WHERE category IS NOT NULL AND category != ''
               GROUP BY category, type
               ORDER BY category'''
        )
        rows = cur.fetchall()
        conn.close()
    cat_map: Dict[str, Dict] = {}
    for row in rows:
        cat = row['category']
        if cat not in cat_map:
            cat_map[cat] = {'category': cat, 'invested': 0, 'earned': 0, 'count': 0}
        if row['type'] == '支出':
            cat_map[cat]['invested'] += row['total'] or 0
        elif row['type'] == '收入':
            cat_map[cat]['earned'] += row['total'] or 0
        cat_map[cat]['count'] += row['cnt']
    result = []
    for v in cat_map.values():
        v['net'] = v['earned'] - v['invested']
        result.append(v)
    result.sort(key=lambda x: abs(x['net']), reverse=True)
    return result


def get_subcategory_profit_loss(category: str) -> List[Dict]:
    """获取指定分类下所有细分分类的盈亏统计

    Args:
        category: 顶级分类名

    Returns: [{'subcategory': 名称, 'invested': 投入, 'earned': 赚取,
              'net': 净值, 'count': 记录数}, ...]
    """
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT subcategory, type, SUM(amount) as total, COUNT(*) as cnt
               FROM expenses
               WHERE category = ? AND subcategory IS NOT NULL AND subcategory != ''
               GROUP BY subcategory, type
               ORDER BY subcategory''',
            (category,)
        )
        rows = cur.fetchall()
        conn.close()
    sub_map: Dict[str, Dict] = {}
    for row in rows:
        sub = row['subcategory']
        if sub not in sub_map:
            sub_map[sub] = {'subcategory': sub, 'invested': 0, 'earned': 0, 'count': 0}
        if row['type'] == '支出':
            sub_map[sub]['invested'] += row['total'] or 0
        elif row['type'] == '收入':
            sub_map[sub]['earned'] += row['total'] or 0
        sub_map[sub]['count'] += row['cnt']
    result = []
    for v in sub_map.values():
        v['net'] = v['earned'] - v['invested']
        result.append(v)
    result.sort(key=lambda x: abs(x['net']), reverse=True)
    return result


def get_category_summary(days: int = 30) -> Dict[str, float]:
    """近 N 天分类支出统计"""
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            '''SELECT category, SUM(amount) as total
               FROM expenses
               WHERE purchase_date >= ? AND type = '支出'
               GROUP BY category
               ORDER BY total DESC''',
            (start_date,)
        )
        result = {row['category'] or '未分类': row['total'] for row in cur.fetchall()}
        conn.close()
    return result
