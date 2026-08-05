# -*- coding: utf-8 -*-
"""
FocusFlow 插件：记账本数据维护

功能：
- 维护记账本的分类（顶级分类）
- 维护记账本的细分分类（每个分类下的子项）
- 支持添加 / 修改 / 删除 / 上下排序
- 分类按类型筛选（支出/收入/双向）
- 直接调用 accounting 模块的 CRUD 函数，数据持久化到 DB

依赖：accounting 模块
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# 确保父目录在 sys.path（便于 import accounting）
_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

try:
    import accounting
except Exception as _e:
    accounting = None
    _import_error = str(_e)
else:
    _import_error = ''

PLUGIN_NAME = "记账本数据维护"
PLUGIN_DESC = "维护记账本的分类与细分分类：添加/修改/删除/排序"
PLUGIN_VERSION = "1.0"
PLUGIN_AUTHOR = "FocusFlow"


# 分类类型显示
_TYPE_DISPLAY = {'expense': '支出', 'income': '收入', 'both': '双向'}


def _center_window(win):
    """将弹窗居中显示（水平居中，垂直略偏上）"""
    try:
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        if w <= 1:
            w = win.winfo_reqwidth()
        if h <= 1:
            h = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        win.geometry(f"+{x}+{y}")
    except Exception:
        pass


def init():
    """插件初始化"""
    if accounting is None:
        # 不在 init 阶段抛异常，让 get_view 显示错误
        pass


def get_view(parent):
    """返回插件的 GUI 视图"""
    frame = ttk.Frame(parent)

    if accounting is None:
        ttk.Label(frame, text=f"accounting 模块加载失败：\n{_import_error}",
                  foreground='#d13438').pack(pady=40)
        return frame

    # 确保数据库已初始化
    try:
        accounting.init_db()
    except Exception as e:
        ttk.Label(frame, text=f"数据库初始化失败：\n{e}",
                  foreground='#d13438').pack(pady=40)
        return frame

    # ===== 顶部标题与刷新 =====
    header = ttk.Frame(frame)
    header.pack(fill=tk.X, padx=12, pady=(8, 4))
    ttk.Label(header, text="记账本分类与细分分类维护",
              font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
    ttk.Button(header, text="刷新", command=lambda: _refresh_all(app)).pack(side=tk.RIGHT)

    # 说明
    ttk.Label(frame,
              text="说明：删除分类不会影响已存在的记账记录（保留 category 字符串）；"
                   "修改分类名会自动同步到所有相关记账记录。",
              foreground='#666666', wraplength=800, justify='left').pack(
              fill=tk.X, padx=12, pady=(0, 4))

    # ===== 主体：左右两栏 =====
    body = ttk.Frame(frame)
    body.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

    # 左栏：分类
    cat_frame = ttk.LabelFrame(body, text="分类")
    cat_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

    cat_ctrl = ttk.Frame(cat_frame)
    cat_ctrl.pack(fill=tk.X, padx=8, pady=6)
    ttk.Label(cat_ctrl, text="类型筛选：").pack(side=tk.LEFT)
    cat_filter_var = tk.StringVar(value='全部')
    cat_filter_combo = ttk.Combobox(cat_ctrl, textvariable=cat_filter_var, width=8,
                                     state='readonly',
                                     values=['全部', '支出', '收入', '双向'])
    cat_filter_combo.current(0)
    cat_filter_combo.pack(side=tk.LEFT, padx=(4, 0))

    cat_tree_frame = ttk.Frame(cat_frame)
    cat_tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
    cat_tree = ttk.Treeview(cat_tree_frame,
                             columns=('name', 'type', 'sub_count'),
                             show='headings', selectmode='browse')
    cat_tree.heading('name', text='分类名')
    cat_tree.heading('type', text='类型')
    cat_tree.heading('sub_count', text='细分数量')
    cat_tree.column('name', width=140)
    cat_tree.column('type', width=60, anchor='center')
    cat_tree.column('sub_count', width=70, anchor='center')
    cat_tree.tag_configure('even', background='#f4f8fc')
    cat_tree.tag_configure('odd', background='#ffffff')
    vsb1 = ttk.Scrollbar(cat_tree_frame, orient='vertical', command=cat_tree.yview)
    cat_tree.configure(yscrollcommand=vsb1.set)
    cat_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb1.pack(side=tk.RIGHT, fill=tk.Y)

    cat_btns = ttk.Frame(cat_frame)
    cat_btns.pack(fill=tk.X, padx=8, pady=(0, 8))
    ttk.Button(cat_btns, text="添加", width=6, command=lambda: _add_category(app)).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(cat_btns, text="修改", width=6, command=lambda: _edit_category(app)).pack(side=tk.LEFT, padx=4)
    ttk.Button(cat_btns, text="删除", width=6, command=lambda: _delete_category(app)).pack(side=tk.LEFT, padx=4)
    ttk.Button(cat_btns, text="↑ 上移", width=6, command=lambda: _move_category(app, 'up')).pack(side=tk.LEFT, padx=4)
    ttk.Button(cat_btns, text="↓ 下移", width=6, command=lambda: _move_category(app, 'down')).pack(side=tk.LEFT, padx=4)

    # 右栏：细分分类
    sub_frame = ttk.LabelFrame(body, text="细分分类（在上方选择分类后显示）")
    sub_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

    sub_tree_frame = ttk.Frame(sub_frame)
    sub_tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
    sub_tree = ttk.Treeview(sub_tree_frame,
                             columns=('name',),
                             show='headings', selectmode='browse')
    sub_tree.heading('name', text='细分分类名')
    sub_tree.column('name', width=200)
    sub_tree.tag_configure('even', background='#f4f8fc')
    sub_tree.tag_configure('odd', background='#ffffff')
    vsb2 = ttk.Scrollbar(sub_tree_frame, orient='vertical', command=sub_tree.yview)
    sub_tree.configure(yscrollcommand=vsb2.set)
    sub_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vsb2.pack(side=tk.RIGHT, fill=tk.Y)

    sub_btns = ttk.Frame(sub_frame)
    sub_btns.pack(fill=tk.X, padx=8, pady=(0, 8))
    ttk.Button(sub_btns, text="添加", width=6, command=lambda: _add_subcategory(app)).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(sub_btns, text="修改", width=6, command=lambda: _edit_subcategory(app)).pack(side=tk.LEFT, padx=4)
    ttk.Button(sub_btns, text="删除", width=6, command=lambda: _delete_subcategory(app)).pack(side=tk.LEFT, padx=4)

    # 选中分类时，刷新右侧细分分类列表
    def on_cat_select(event=None):
        _refresh_subcategories(app)
    cat_tree.bind('<<TreeviewSelect>>', on_cat_select)

    # 类型筛选变化
    def on_filter_change(*args):
        _refresh_categories(app)
    cat_filter_var.trace_add('write', on_filter_change)

    # 构建应用上下文对象
    app = {
        'frame': frame,
        'cat_tree': cat_tree,
        'sub_tree': sub_tree,
        'cat_filter_var': cat_filter_var,
        'current_category': None,
    }

    # 首次刷新
    _refresh_all(app)

    return frame


# ==================== 刷新逻辑 ====================
def _refresh_all(app):
    _refresh_categories(app)
    _refresh_subcategories(app)


def _refresh_categories(app):
    cat_tree = app['cat_tree']
    for item in cat_tree.get_children():
        cat_tree.delete(item)
    # 获取分类（保留排序信息）
    from accounting import _get_conn, _db_lock
    filter_val = app['cat_filter_var'].get()
    cats = []
    sub_counts = {}
    name_to_id = {}
    with _db_lock:
        conn = None
        try:
            conn = _get_conn()
            if filter_val == '全部':
                cur = conn.execute(
                    'SELECT name, type FROM categories ORDER BY sort_order, id'
                )
            else:
                # 反向映射显示->内部值
                type_map = {'支出': 'expense', '收入': 'income', '双向': 'both'}
                t = type_map.get(filter_val, 'both')
                cur = conn.execute(
                    '''SELECT name, type FROM categories
                       WHERE type = ? OR type = 'both'
                       ORDER BY sort_order, id''',
                    (t,)
                )
            cats = cur.fetchall()
            # 一次性查询所有分类的细分数量
            cur2 = conn.execute(
                'SELECT category_id, COUNT(*) as cnt FROM subcategories GROUP BY category_id'
            )
            for row in cur2.fetchall():
                sub_counts[row['category_id']] = row['cnt']
            # 还需根据分类名查 id
            cur3 = conn.execute('SELECT id, name FROM categories')
            name_to_id = {row['name']: row['id'] for row in cur3.fetchall()}
        finally:
            if conn is not None:
                conn.close()
    for i, cat in enumerate(cats):
        name = cat['name']
        t = cat['type']
        cnt = sub_counts.get(name_to_id.get(name, -1), 0)
        tag = 'even' if i % 2 == 0 else 'odd'
        cat_tree.insert('', tk.END, values=(name, _TYPE_DISPLAY.get(t, t), cnt),
                        tags=(tag,))


def _refresh_subcategories(app):
    sub_tree = app['sub_tree']
    for item in sub_tree.get_children():
        sub_tree.delete(item)
    sel = app['cat_tree'].selection()
    if not sel:
        app['current_category'] = None
        return
    values = app['cat_tree'].item(sel[0], 'values')
    cat_name = values[0]
    app['current_category'] = cat_name
    subs = accounting.get_subcategories(cat_name)
    for i, s in enumerate(subs):
        tag = 'even' if i % 2 == 0 else 'odd'
        sub_tree.insert('', tk.END, values=(s,), tags=(tag,))


# ==================== 分类 CRUD ====================
def _add_category(app):
    """添加分类"""
    dialog = tk.Toplevel()
    dialog.title("添加分类")
    dialog.geometry("360x180")
    dialog.resizable(False, False)
    dialog.transient(app['frame'])
    dialog.grab_set()
    _center_window(dialog)

    ttk.Label(dialog, text="分类名：").pack(pady=(16, 4), padx=20, anchor='w')
    name_entry = ttk.Entry(dialog, width=32)
    name_entry.pack(padx=20, fill=tk.X)
    name_entry.focus_set()

    ttk.Label(dialog, text="类型：").pack(pady=(8, 4), padx=20, anchor='w')
    type_var = tk.StringVar(value='both')
    type_frame = ttk.Frame(dialog)
    type_frame.pack(padx=20, fill=tk.X)
    for text, val in [('支出', 'expense'), ('收入', 'income'), ('双向', 'both')]:
        ttk.Radiobutton(type_frame, text=text, value=val, variable=type_var).pack(side=tk.LEFT, padx=(0, 8))

    def save():
        name = name_entry.get().strip()
        if not name:
            messagebox.showerror("错误", "分类名不能为空", parent=dialog)
            return
        ok, msg = accounting.add_category(name, type_var.get())
        if ok:
            dialog.destroy()
            _refresh_categories(app)
        else:
            messagebox.showerror("错误", msg, parent=dialog)

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=12)
    ttk.Button(btn_frame, text="保存", command=save).pack(side=tk.RIGHT, padx=4)
    ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=4)


def _edit_category(app):
    """修改分类"""
    sel = app['cat_tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个分类")
        return
    values = app['cat_tree'].item(sel[0], 'values')
    old_name = values[0]

    # 查询当前类型
    from accounting import _get_conn, _db_lock
    cur_type = 'both'
    with _db_lock:
        conn = None
        try:
            conn = _get_conn()
            cur = conn.execute('SELECT type FROM categories WHERE name=?', (old_name,))
            row = cur.fetchone()
        finally:
            if conn is not None:
                conn.close()
    cur_type = row['type'] if row else 'both'

    dialog = tk.Toplevel()
    dialog.title("修改分类")
    dialog.geometry("360x200")
    dialog.resizable(False, False)
    dialog.transient(app['frame'])
    dialog.grab_set()
    _center_window(dialog)

    ttk.Label(dialog, text="分类名：").pack(pady=(16, 4), padx=20, anchor='w')
    name_entry = ttk.Entry(dialog, width=32)
    name_entry.pack(padx=20, fill=tk.X)
    name_entry.insert(0, old_name)
    name_entry.focus_set()

    ttk.Label(dialog, text="类型：").pack(pady=(8, 4), padx=20, anchor='w')
    type_var = tk.StringVar(value=cur_type)
    type_frame = ttk.Frame(dialog)
    type_frame.pack(padx=20, fill=tk.X)
    for text, val in [('支出', 'expense'), ('收入', 'income'), ('双向', 'both')]:
        ttk.Radiobutton(type_frame, text=text, value=val, variable=type_var).pack(side=tk.LEFT, padx=(0, 8))

    def save():
        new_name = name_entry.get().strip()
        if not new_name:
            messagebox.showerror("错误", "分类名不能为空", parent=dialog)
            return
        ok, msg = accounting.update_category(old_name, new_name, type_var.get())
        if ok:
            dialog.destroy()
            _refresh_categories(app)
            _refresh_subcategories(app)
        else:
            messagebox.showerror("错误", msg, parent=dialog)

    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(pady=12)
    ttk.Button(btn_frame, text="保存", command=save).pack(side=tk.RIGHT, padx=4)
    ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, padx=4)


def _delete_category(app):
    """删除分类"""
    sel = app['cat_tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个分类")
        return
    values = app['cat_tree'].item(sel[0], 'values')
    name = values[0]
    if not messagebox.askyesno("确认删除",
        f"确定删除分类 [{name}] 吗？\n"
        f"该分类下的所有细分分类也会被删除。\n"
        f"已存在的记账记录不会被删除（保留 category 字符串）。"):
        return
    ok, msg = accounting.delete_category(name)
    if ok:
        _refresh_categories(app)
        _refresh_subcategories(app)
        messagebox.showinfo("成功", msg)
    else:
        messagebox.showerror("错误", msg)


def _move_category(app, direction):
    """上移/下移分类"""
    sel = app['cat_tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个分类")
        return
    values = app['cat_tree'].item(sel[0], 'values')
    name = values[0]
    ok, msg = accounting.reorder_category(name, direction)
    if ok:
        _refresh_categories(app)
    else:
        # 边界情况不弹窗，安静处理
        pass


# ==================== 细分分类 CRUD ====================
def _get_selected_category(app) -> str:
    sel = app['cat_tree'].selection()
    if not sel:
        return ''
    return app['cat_tree'].item(sel[0], 'values')[0]


def _add_subcategory(app):
    """添加细分分类"""
    cat_name = _get_selected_category(app)
    if not cat_name:
        messagebox.showwarning("提示", "请先在左侧选择一个分类")
        return
    name = simpledialog.askstring("添加细分分类",
                                   f"在分类 [{cat_name}] 下添加细分分类：\n请输入名称：",
                                   parent=app['frame'])
    if not name:
        return
    name = name.strip()
    if not name:
        return
    ok, msg = accounting.add_subcategory(cat_name, name)
    if ok:
        _refresh_subcategories(app)
        _refresh_categories(app)  # 更新细分数量
        messagebox.showinfo("成功", msg)
    else:
        messagebox.showerror("错误", msg)


def _edit_subcategory(app):
    """修改细分分类"""
    cat_name = _get_selected_category(app)
    if not cat_name:
        messagebox.showwarning("提示", "请先在左侧选择一个分类")
        return
    sel = app['sub_tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先在右侧选择一个细分分类")
        return
    old_name = app['sub_tree'].item(sel[0], 'values')[0]
    new_name = simpledialog.askstring("修改细分分类",
                                       f"将 [{old_name}] 修改为：",
                                       parent=app['frame'],
                                       initialvalue=old_name)
    if not new_name:
        return
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return
    ok, msg = accounting.update_subcategory(cat_name, old_name, new_name)
    if ok:
        _refresh_subcategories(app)
        messagebox.showinfo("成功", msg)
    else:
        messagebox.showerror("错误", msg)


def _delete_subcategory(app):
    """删除细分分类"""
    cat_name = _get_selected_category(app)
    if not cat_name:
        messagebox.showwarning("提示", "请先在左侧选择一个分类")
        return
    sel = app['sub_tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先在右侧选择一个细分分类")
        return
    old_name = app['sub_tree'].item(sel[0], 'values')[0]
    if not messagebox.askyesno("确认删除",
        f"确定删除细分分类 [{cat_name} / {old_name}] 吗？\n"
        f"已存在的记账记录不会被删除。"):
        return
    ok, msg = accounting.delete_subcategory(cat_name, old_name)
    if ok:
        _refresh_subcategories(app)
        _refresh_categories(app)
        messagebox.showinfo("成功", msg)
    else:
        messagebox.showerror("错误", msg)


def cleanup():
    """插件清理"""
    pass
