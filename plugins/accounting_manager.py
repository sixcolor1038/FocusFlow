# -*- coding: utf-8 -*-
"""
FocusFlow 插件：记账本管理
- 收支记录的增删改查
- 分类/子分类筛选
- 日期范围、关键词搜索
- 分页浏览
- 月度汇总

依赖：accounting.py（后端模块，随主程序提供）
"""

import os
import sys
from datetime import date, datetime, timedelta

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import tkinter as tk
from tkinter import ttk, messagebox

PLUGIN_NAME = "记账本"
PLUGIN_DESC = "收支记录管理：增删改查、分类筛选、月度汇总"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "FocusFlow"

try:
    import accounting as _acc
    _acc.init_db()
    _import_error = None
except Exception as _e:
    _acc = None
    _import_error = str(_e)

_PAGE_SIZE = 10


def get_view(parent):
    """构建记账本视图"""
    frame = ttk.Frame(parent)

    if _import_error:
        ttk.Label(frame, text=f"accounting 模块加载失败：\n{_import_error}",
                  foreground='#d13438', justify='left').pack(pady=40)
        return frame

    # 应用上下文（直接保存控件引用，避免遍历查找）
    app = {
        'frame': frame,
        'page': 1,
        'total_pages': 1,
        'total_records': 0,
    }

    # ===== 筛选栏（单行紧凑布局，节省纵向空间） =====
    filter_card = ttk.LabelFrame(frame, text="筛选条件")
    filter_card.pack(fill=tk.X, padx=12, pady=(10, 6))
    filter_grid = ttk.Frame(filter_card)
    filter_grid.pack(fill=tk.X, padx=10, pady=6)

    # 分类筛选
    ttk.Label(filter_grid, text="分类：").grid(row=0, column=0, padx=(0, 2), sticky='w')
    cat_var = tk.StringVar(value="全部")
    cat_combo = ttk.Combobox(filter_grid, textvariable=cat_var,
                             values=['全部'], state='readonly', width=9)
    cat_combo.grid(row=0, column=1, padx=(0, 10), sticky='w')
    app['cat_var'] = cat_var
    app['cat_combo'] = cat_combo

    # 子分类筛选
    ttk.Label(filter_grid, text="子分类：").grid(row=0, column=2, padx=(0, 2), sticky='w')
    sub_var = tk.StringVar(value="全部")
    sub_combo = ttk.Combobox(filter_grid, textvariable=sub_var,
                             values=['全部'], state='readonly', width=9)
    sub_combo.grid(row=0, column=3, padx=(0, 10), sticky='w')
    app['sub_var'] = sub_var
    app['sub_combo'] = sub_combo

    # 日期范围
    ttk.Label(filter_grid, text="从：").grid(row=0, column=4, padx=(0, 2), sticky='w')
    date_from_var = tk.StringVar(value='')
    ttk.Entry(filter_grid, textvariable=date_from_var, width=8).grid(row=0, column=5, padx=(0, 6), sticky='w')
    ttk.Label(filter_grid, text="到：").grid(row=0, column=6, padx=(0, 2), sticky='w')
    date_to_var = tk.StringVar(value='')
    ttk.Entry(filter_grid, textvariable=date_to_var, width=8).grid(row=0, column=7, padx=(0, 10), sticky='w')
    app['date_from_var'] = date_from_var
    app['date_to_var'] = date_to_var

    # 关键词
    ttk.Label(filter_grid, text="关键词：").grid(row=0, column=8, padx=(0, 2), sticky='w')
    kw_var = tk.StringVar(value='')
    ttk.Entry(filter_grid, textvariable=kw_var, width=12).grid(row=0, column=9, padx=(0, 10), sticky='w')
    app['kw_var'] = kw_var

    # 筛选按钮
    ttk.Button(filter_grid, text="查询", command=lambda: _query(app)).grid(row=0, column=10, padx=(0, 4))
    ttk.Button(filter_grid, text="重置", command=lambda: _reset_filters(app)).grid(row=0, column=11)

    # 绑定分类变化
    cat_combo.bind('<<ComboboxSelected>>', lambda e: _refresh_subcategories(app))

    # ===== 操作栏 =====
    op_bar = ttk.Frame(frame)
    op_bar.pack(fill=tk.X, padx=12, pady=(0, 6))
    ttk.Button(op_bar, text="添加记录", width=8,
               command=lambda: _add_expense(app)).pack(side=tk.LEFT)
    ttk.Button(op_bar, text="修改", width=6,
               command=lambda: _edit_expense(app)).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(op_bar, text="删除", width=6,
               command=lambda: _delete_expense(app)).pack(side=tk.LEFT, padx=(4, 0))
    ttk.Button(op_bar, text="距今多久", width=8,
               command=lambda: _show_days_ago(app)).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(op_bar, text="月度汇总", width=8,
               command=lambda: _show_monthly_summary(app)).pack(side=tk.LEFT, padx=(12, 0))
    ttk.Button(op_bar, text="分类盈亏", width=8,
               command=lambda: _show_category_profit(app)).pack(side=tk.LEFT, padx=(6, 0))
    ttk.Button(op_bar, text="细分盈亏", width=8,
               command=lambda: _show_subcategory_profit(app)).pack(side=tk.LEFT, padx=(6, 0))

    # ===== 记录列表（表格高度自适应，避免记录下方大片空白） =====
    list_card = ttk.LabelFrame(frame, text="收支记录")
    list_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

    columns = ('id', 'date', 'type', 'name', 'store', 'amount', 'category', 'subcategory', 'note')
    tree = ttk.Treeview(list_card, columns=columns, show='headings', height=10,
                        selectmode='extended')
    tree.heading('id', text='ID')
    tree.heading('date', text='日期')
    tree.heading('type', text='类型')
    tree.heading('name', text='名称')
    tree.heading('store', text='渠道')
    tree.heading('amount', text='金额')
    tree.heading('category', text='分类')
    tree.heading('subcategory', text='子分类')
    tree.heading('note', text='备注')
    tree.column('id', width=40, anchor='center')
    tree.column('date', width=90, anchor='center')
    tree.column('type', width=50, anchor='center')
    tree.column('name', width=120)
    tree.column('store', width=100, anchor='center')
    tree.column('amount', width=80, anchor='center')
    tree.column('category', width=100, anchor='center')
    tree.column('subcategory', width=100, anchor='center')
    tree.column('note', width=150)

    scrollbar = ttk.Scrollbar(list_card, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
    app['tree'] = tree

    # ===== 分页栏（紧贴表格下方，随窗口高度自适应，无需滚动即可看到） =====
    page_bar = ttk.Frame(frame)
    page_bar.pack(fill=tk.X, padx=12, pady=(0, 12))
    ttk.Button(page_bar, text="上一页", width=8,
               command=lambda: _change_page(app, -1)).pack(side=tk.LEFT)
    page_label = ttk.Label(page_bar, text="第 1/1 页 (共 0 条)")
    page_label.pack(side=tk.LEFT, padx=12)
    ttk.Button(page_bar, text="下一页", width=8,
               command=lambda: _change_page(app, 1)).pack(side=tk.LEFT)
    app['page_label'] = page_label

    # 表格行数随可用高度自适应：消除记录下方空白、保证分页栏始终可见
    list_card.bind('<Configure>', lambda e: _fit_tree(app))
    frame.after(100, lambda: _fit_tree(app))

    # 初始加载
    _refresh_categories(app)
    _refresh_list(app)

    return frame


def _fit_tree(app):
    """让表格行数自适应可用高度，避免收支记录下方出现大片空白或多余的半行

    使用实际的 Treeview 行高（跟随主题），并向下取整保证最后一行完整显示。
    """
    try:
        tree = app['tree']
        h = tree.winfo_height()
        if h < 60:
            return
        row_h = int(ttk.Style().lookup('Treeview', 'rowheight') or 28)
        if row_h < 20:
            row_h = 28
        # 扣除表头高度（约一行）与边框留白，向下取整
        rows = max(4, int((h - row_h - 8) / row_h))
        rows = min(rows, 50)
        if rows != int(tree.cget('height')):
            tree.config(height=rows)
    except Exception:
        pass


def _refresh_categories(app):
    """刷新分类下拉框"""
    try:
        cats = ['全部'] + _acc.get_category_names()
        app['cat_combo']['values'] = cats
    except Exception as e:
        log_err(f"刷新分类失败: {e}")


def _refresh_subcategories(app):
    """根据选中的分类刷新子分类下拉"""
    try:
        cat = app['cat_var'].get()
        if cat == '全部':
            subs = ['全部']
        else:
            subs = ['全部'] + _acc.get_subcategories(cat)
        app['sub_combo']['values'] = subs
        app['sub_var'].set('全部')
    except Exception as e:
        log_err(f"刷新子分类失败: {e}")


def _refresh_list(app):
    """刷新记录列表"""
    try:
        tree = app['tree']
        for item in tree.get_children():
            tree.delete(item)

        cat = app['cat_var'].get()
        sub = app['sub_var'].get()
        kw = app['kw_var'].get().strip()
        date_from = app['date_from_var'].get().strip()
        date_to = app['date_to_var'].get().strip()

        records, total = _acc.get_expenses_paginated(
            page=app['page'],
            page_size=_PAGE_SIZE,
            category=cat if cat != '全部' else None,
            subcategory=sub if sub != '全部' else None,
            keyword=kw if kw else None,
            date_from=date_from or None,
            date_to=date_to or None,
        )

        app['total_records'] = total
        app['total_pages'] = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
        # 防止删除/筛选后当前页超出总页数
        if app['page'] > app['total_pages']:
            app['page'] = app['total_pages']
            records, total = _acc.get_expenses_paginated(
                page=app['page'],
                page_size=_PAGE_SIZE,
                category=cat if cat != '全部' else None,
                subcategory=sub if sub != '全部' else None,
                keyword=kw if kw else None,
                date_from=date_from or None,
                date_to=date_to or None,
            )

        for r in records:
            tree.insert('', tk.END, values=(
                r.get('id', ''),
                r.get('purchase_date', ''),
                r.get('type', ''),
                r.get('item_name', ''),
                r.get('store', ''),
                f"{r.get('amount', 0):.2f}",
                r.get('category', ''),
                r.get('subcategory', ''),
                r.get('note', ''),
            ))

        app['page_label'].config(
            text=f"第 {app['page']}/{app['total_pages']} 页 (共 {total} 条)")
    except Exception as e:
        log_err(f"刷新列表失败: {e}")


def _query(app):
    app['page'] = 1
    _refresh_list(app)


def _reset_filters(app):
    app['cat_var'].set('全部')
    app['sub_var'].set('全部')
    app['date_from_var'].set('')
    app['date_to_var'].set('')
    app['kw_var'].set('')
    _refresh_subcategories(app)
    _query(app)


def _change_page(app, delta):
    new_page = app['page'] + delta
    if new_page < 1 or new_page > app['total_pages']:
        return
    app['page'] = new_page
    _refresh_list(app)


def _add_expense(app):
    """添加记录"""
    dialog = _ExpenseDialog(app['frame'], "添加记录")
    if dialog.result:
        try:
            r = dialog.result
            _acc.add_expense(
                item_name=r['item_name'],
                store=r['store'],
                purchase_date=r['purchase_date'],
                amount=r['amount'],
                category=r['category'],
                subcategory=r['subcategory'],
                note=r['note'],
                record_type=r['record_type'],
            )
            _refresh_list(app)
            messagebox.showinfo("成功", "记录已添加", parent=app['frame'])
        except Exception as e:
            messagebox.showerror("错误", f"添加失败：{e}", parent=app['frame'])


def _edit_expense(app):
    """修改记录"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一条记录", parent=app['frame'])
        return
    values = app['tree'].item(sel[0], 'values')
    record_id = int(values[0])
    record = _acc.get_expense_by_id(record_id)
    if not record:
        messagebox.showerror("错误", "记录不存在", parent=app['frame'])
        return
    dialog = _ExpenseDialog(app['frame'], "修改记录", data=record)
    if dialog.result:
        try:
            r = dialog.result
            _acc.update_expense(record_id,
                type=r['record_type'],
                item_name=r['item_name'],
                store=r['store'],
                purchase_date=r['purchase_date'],
                amount=r['amount'],
                category=r['category'],
                subcategory=r['subcategory'],
                note=r['note'],
            )
            _refresh_list(app)
            messagebox.showinfo("成功", "记录已修改", parent=app['frame'])
        except Exception as e:
            messagebox.showerror("错误", f"修改失败：{e}", parent=app['frame'])


def _delete_expense(app):
    """删除记录"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一条记录", parent=app['frame'])
        return
    if not messagebox.askyesno("确认", "确定删除选中的记录？", parent=app['frame']):
        return
    ids = [int(app['tree'].item(s, 'values')[0]) for s in sel]
    try:
        count = _acc.delete_expenses(ids)
        _refresh_list(app)
        messagebox.showinfo("成功", f"已删除 {count} 条记录", parent=app['frame'])
    except Exception as e:
        messagebox.showerror("错误", f"删除失败：{e}", parent=app['frame'])


def _show_days_ago(app):
    """查看选中记录的日期距今多久（年+天，支持多选，按记录名称显示）"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一条或多条记录", parent=app['frame'])
        return
    ids = []
    names = []
    for s in sel:
        vals = app['tree'].item(s, 'values')
        try:
            ids.append(int(vals[0]))
        except (ValueError, TypeError, IndexError):
            continue
        names.append(str(vals[3]) if len(vals) > 3 and vals[3] else '(无名称)')
    if not ids:
        return
    try:
        info = _acc.get_days_ago_for_records(ids)
        if not info:
            messagebox.showinfo("距今多久", "未获取到记录信息", parent=app['frame'])
            return
        lines = []
        for rid, name in zip(ids, names):
            d = info.get(rid)
            if not d:
                continue
            text = d.get('text', '')
            if d.get('years', 0) > 0:
                # 明确显示 年+天 格式，例如 5年150天
                text = f"距今{d['years']}年{d['days']}天"
            lines.append(f"《{name}》: {text}")
        msg = "选中记录距今多久：\n\n" + "\n".join(lines)
        messagebox.showinfo("距今多久", msg, parent=app['frame'])
    except Exception as e:
        messagebox.showerror("错误", f"查询失败：{e}", parent=app['frame'])


def _show_monthly_summary(app):
    """显示月度汇总"""
    try:
        now = datetime.now()
        summary = _acc.get_monthly_summary(now.year, now.month)
        msg = (f"【{now.year}年{now.month}月汇总】\n\n"
               f"  总支出：{summary.get('total_expense', 0):.2f}\n"
               f"  总收入：{summary.get('total_income', 0):.2f}\n"
               f"  净额：{summary.get('net', 0):.2f}\n"
               f"  记录数：{summary.get('count', 0)}\n")
        cat_stats = summary.get('category_stats', {})
        if cat_stats:
            msg += "\n分类明细（净值，收入为正/支出为负）：\n"
            # category_stats 是 {分类名: 净值} 的字典（收入 - 支出）
            for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1])[:10]:
                msg += f"  {cat}: {amt:+.2f}\n"
        messagebox.showinfo("月度汇总", msg, parent=app['frame'])
    except Exception as e:
        messagebox.showerror("错误", f"获取汇总失败：{e}", parent=app['frame'])


def _show_category_profit(app):
    """显示所有分类的盈亏统计"""
    try:
        data = _acc.get_all_category_profit_loss()
        if not data:
            messagebox.showinfo("分类盈亏", "暂无数据", parent=app['frame'])
            return
        total_invested = sum(d['invested'] for d in data)
        total_earned = sum(d['earned'] for d in data)
        msg = (f"【分类盈亏统计】\n\n"
               f"  总投入：{total_invested:.2f}\n"
               f"  总赚取：{total_earned:.2f}\n"
               f"  净额：{total_earned - total_invested:.2f}\n\n"
               f"{'─' * 40}\n"
               f"{'分类':<12} {'投入':>10} {'赚取':>10} {'净值':>10} {'记录':>6}\n"
               f"{'─' * 40}\n")
        for d in data:
            net = d['net']
            net_str = f"{net:+.2f}" if net != 0 else "0.00"
            msg += f"{d['category']:<12} {d['invested']:>10.2f} {d['earned']:>10.2f} {net_str:>10} {d['count']:>6}\n"
        messagebox.showinfo("分类盈亏", msg, parent=app['frame'])
    except Exception as e:
        messagebox.showerror("错误", f"获取分类盈亏失败：{e}", parent=app['frame'])


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


def _show_subcategory_profit(app):
    """显示指定分类下细分分类的盈亏统计"""
    try:
        # 先选择一个分类
        cats = _acc.get_category_names()
        if not cats:
            messagebox.showinfo("细分盈亏", "暂无分类数据", parent=app['frame'])
            return

        # 弹出选择框
        dialog = tk.Toplevel(app['frame'])
        dialog.title("选择分类")
        dialog.geometry("300x400")
        dialog.transient(app['frame'])
        dialog.grab_set()
        _center_window(dialog)

        ttk.Label(dialog, text="请选择要查看细分盈亏的分类：",
                  font=("Segoe UI", 10)).pack(pady=(12, 8))

        listbox = tk.Listbox(dialog, height=15, font=("Segoe UI", 10))
        listbox.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        for c in cats:
            listbox.insert(tk.END, c)
        if cats:
            listbox.selection_set(0)

        result = {'selected': None}

        def _ok():
            sel = listbox.curselection()
            if sel:
                result['selected'] = cats[sel[0]]
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 12))
        ttk.Button(btn_frame, text="确定", command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=8)

        dialog.wait_window()

        if not result['selected']:
            return

        category = result['selected']
        data = _acc.get_subcategory_profit_loss(category)
        if not data:
            messagebox.showinfo("细分盈亏",
                f"分类 [{category}] 下暂无细分分类的记录",
                parent=app['frame'])
            return

        total_invested = sum(d['invested'] for d in data)
        total_earned = sum(d['earned'] for d in data)
        msg = (f"【{category} - 细分分类盈亏】\n\n"
               f"  总投入：{total_invested:.2f}\n"
               f"  总赚取：{total_earned:.2f}\n"
               f"  净额：{total_earned - total_invested:.2f}\n\n"
               f"{'─' * 44}\n"
               f"{'细分分类':<14} {'投入':>10} {'赚取':>10} {'净值':>10} {'记录':>6}\n"
               f"{'─' * 44}\n")
        for d in data:
            net = d['net']
            net_str = f"{net:+.2f}" if net != 0 else "0.00"
            msg += f"{d['subcategory']:<14} {d['invested']:>10.2f} {d['earned']:>10.2f} {net_str:>10} {d['count']:>6}\n"
        messagebox.showinfo("细分盈亏", msg, parent=app['frame'])
    except Exception as e:
        messagebox.showerror("错误", f"获取细分盈亏失败：{e}", parent=app['frame'])


class _ExpenseDialog(tk.Toplevel):
    """添加/修改记录对话框"""

    def __init__(self, parent, title, data=None):
        super().__init__(parent)
        self.title(title)
        self.geometry("420x520")
        self.transient(parent)
        self.result = None
        _center_window(self)

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # 名称
        ttk.Label(container, text="名称：").grid(row=0, column=0, sticky='w', pady=4)
        name_var = tk.StringVar(value=data['item_name'] if data else '')
        ttk.Entry(container, textvariable=name_var, width=28).grid(row=0, column=1, pady=4, sticky='w')

        # 渠道
        ttk.Label(container, text="渠道：").grid(row=1, column=0, sticky='w', pady=4)
        store_var = tk.StringVar(value=data.get('store', '') if data else '')
        ttk.Entry(container, textvariable=store_var, width=28).grid(row=1, column=1, pady=4, sticky='w')

        # 日期
        ttk.Label(container, text="日期：").grid(row=2, column=0, sticky='w', pady=4)
        date_var = tk.StringVar(value=data.get('purchase_date', date.today().isoformat()) if data else date.today().isoformat())
        ttk.Entry(container, textvariable=date_var, width=28).grid(row=2, column=1, pady=4, sticky='w')

        # 金额
        ttk.Label(container, text="金额：").grid(row=3, column=0, sticky='w', pady=4)
        amount_var = tk.StringVar(value=str(data.get('amount', '')) if data else '')
        ttk.Entry(container, textvariable=amount_var, width=28).grid(row=3, column=1, pady=4, sticky='w')

        # 类型
        ttk.Label(container, text="类型：").grid(row=4, column=0, sticky='w', pady=4)
        type_var = tk.StringVar(value=data.get('type', '支出') if data else '支出')
        ttk.Combobox(container, textvariable=type_var, values=['支出', '收入'],
                     state='readonly', width=10).grid(row=4, column=1, pady=4, sticky='w')

        # 分类
        ttk.Label(container, text="分类：").grid(row=5, column=0, sticky='w', pady=4)
        cat_var = tk.StringVar(value=data.get('category', '') if data else '')
        cat_combo = ttk.Combobox(container, textvariable=cat_var,
                                 values=_acc.get_category_names(), width=15)
        cat_combo.grid(row=5, column=1, pady=4, sticky='w')

        # 子分类
        ttk.Label(container, text="子分类：").grid(row=6, column=0, sticky='w', pady=4)
        sub_var = tk.StringVar(value=data.get('subcategory', '') if data else '')
        sub_combo = ttk.Combobox(container, textvariable=sub_var, width=15)
        sub_combo.grid(row=6, column=1, pady=4, sticky='w')

        # 分类变化时更新子分类
        def _update_subs(*_):
            cat = cat_var.get()
            if cat:
                subs = _acc.get_subcategories(cat)
                sub_combo['values'] = subs
        cat_combo.bind('<<ComboboxSelected>>', _update_subs)
        if cat_var.get():
            _update_subs()

        # 备注
        ttk.Label(container, text="备注：").grid(row=7, column=0, sticky='nw', pady=4)
        note_text = tk.Text(container, width=28, height=3)
        note_text.grid(row=7, column=1, pady=4, sticky='w')
        if data and data.get('note'):
            note_text.insert('1.0', data['note'])

        # 按钮
        btn_frame = ttk.Frame(container)
        btn_frame.grid(row=8, column=0, columnspan=2, pady=(16, 0))

        def _ok():
            try:
                amount = float(amount_var.get())
            except ValueError:
                messagebox.showerror("错误", "金额必须是数字", parent=self)
                return
            if not name_var.get().strip():
                messagebox.showerror("错误", "名称不能为空", parent=self)
                return
            self.result = {
                'item_name': name_var.get().strip(),
                'store': store_var.get().strip(),
                'purchase_date': date_var.get().strip(),
                'amount': amount,
                'record_type': type_var.get(),
                'category': cat_var.get(),
                'subcategory': sub_var.get(),
                'note': note_text.get('1.0', 'end').strip(),
            }
            self.destroy()

        ttk.Button(btn_frame, text="确定", command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=8)

        self.wait_window()


def log_err(msg):
    """简易日志"""
    try:
        from logger import get_logger
        get_logger('accounting_plugin').error(msg)
    except Exception:
        print(msg)
