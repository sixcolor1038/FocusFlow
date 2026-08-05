# -*- coding: utf-8 -*-
"""
FocusFlow 插件：定时任务管理
- 任务的增删改查
- 启用/禁用任务
- 调度类型：每天/每周/每月/间隔/开机
- 任务执行状态查看

依赖：scheduler.py（后端模块，随主程序提供）
"""

import os
import sys
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

PLUGIN_NAME = "定时任务"
PLUGIN_DESC = "管理定时执行的任务（每天/一次性/间隔执行）"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "FocusFlow"

try:
    import scheduler as _sch
    _sch.init_db()
    _import_error = None
except Exception as _e:
    _sch = None
    _import_error = str(_e)

_SCHEDULE_TYPES = ['每天', '一次性', '间隔执行']
_TYPE_MAP = {'每天': 'daily', '一次性': 'once', '间隔执行': 'interval'}
_TYPE_MAP_REV = {v: k for k, v in _TYPE_MAP.items()}


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


def get_view(parent):
    """构建定时任务视图"""
    frame = ttk.Frame(parent)

    if _import_error:
        ttk.Label(frame, text=f"scheduler 模块加载失败：\n{_import_error}",
                  foreground='#d13438', justify='left').pack(pady=40)
        return frame

    app = {'frame': frame}

    # 说明
    ttk.Label(frame,
              text="定时任务会在后台自动检查并执行。支持每天/每周/每月/固定间隔/开机时执行。\n"
                   "可执行程序(.exe/.bat/.py)或打开文件。",
              style='Subtitle.TLabel', justify='left').pack(fill=tk.X, padx=12, pady=(12, 8))

    # 按钮栏
    btn_bar = ttk.Frame(frame)
    btn_bar.pack(fill=tk.X, padx=12, pady=(0, 8))
    ttk.Button(btn_bar, text="添加任务", width=10,
               command=lambda: _add_task(app)).pack(side=tk.LEFT)
    ttk.Button(btn_bar, text="修改", width=6,
               command=lambda: _edit_task(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="删除", width=6,
               command=lambda: _delete_task(app)).pack(side=tk.LEFT, padx=(4, 0))
    ttk.Button(btn_bar, text="启用/禁用", width=10,
               command=lambda: _toggle_task(app)).pack(side=tk.LEFT, padx=(4, 0))
    ttk.Button(btn_bar, text="刷新", width=6,
               command=lambda: _refresh(app)).pack(side=tk.RIGHT)

    # 任务列表
    list_card = ttk.LabelFrame(frame, text="任务列表")
    list_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

    columns = ("id", "name", "type", "time", "target", "enabled", "last_run")
    tree = ttk.Treeview(list_card, columns=columns, show='headings', height=10)
    tree.heading('id', text='ID')
    tree.heading('name', text='任务名称')
    tree.heading('type', text='调度类型')
    tree.heading('time', text='调度时间')
    tree.heading('target', text='目标程序')
    tree.heading('enabled', text='状态')
    tree.heading('last_run', text='上次执行')
    tree.column('id', width=40, anchor='center')
    tree.column('name', width=120)
    tree.column('type', width=70, anchor='center')
    tree.column('time', width=100, anchor='center')
    tree.column('target', width=200)
    tree.column('enabled', width=60, anchor='center')
    tree.column('last_run', width=140, anchor='center')

    tree.tag_configure('even', background='#f8fafc')
    tree.tag_configure('odd', background='#ffffff')
    tree.tag_configure('disabled', foreground='#94a3b8')
    tree.tag_configure('enabled', foreground='#16a34a')

    vsb = ttk.Scrollbar(list_card, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
    vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
    app['tree'] = tree

    # 双击修改
    tree.bind('<Double-1>', lambda e: _edit_task(app))

    # 首次加载
    _refresh(app)

    return frame


def _refresh(app):
    """刷新任务列表"""
    tree = app['tree']
    for item in tree.get_children():
        tree.delete(item)

    tasks = _sch.get_all_tasks()
    for i, t in enumerate(tasks):
        stype = _TYPE_MAP_REV.get(t.get('schedule_type', ''), t.get('schedule_type', ''))
        enabled = t.get('enabled', 0)
        status = "启用" if enabled else "禁用"
        tag = ('even' if i % 2 == 0 else 'odd',
               'enabled' if enabled else 'disabled')
        tree.insert('', tk.END, values=(
            t.get('id', ''),
            t.get('name', ''),
            stype,
            t.get('schedule_time', ''),
            t.get('target_path', ''),
            status,
            t.get('last_run', '—') or '—',
        ), tags=tag)


def _add_task(app):
    """添加任务"""
    dialog = _TaskDialog(app['frame'], title="添加定时任务")
    if dialog.result:
        try:
            r = dialog.result
            task_id = _sch.add_task(
                name=r['name'],
                target_path=r['target_path'],
                args=r['args'],
                schedule_type=r['schedule_type'],
                schedule_time=r['schedule_time'],
                enabled=bool(r['enabled']),
            )
            if task_id:
                messagebox.showinfo("成功", f"任务添加成功（ID={task_id}）")
                _refresh(app)
            else:
                messagebox.showerror("失败", "添加失败")
        except Exception as e:
            messagebox.showerror("错误", f"添加失败：{e}")


def _edit_task(app):
    """修改任务"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个任务")
        return
    values = app['tree'].item(sel[0], 'values')
    task_id = int(values[0])
    task = _sch.get_task(task_id)
    if not task:
        messagebox.showerror("错误", "找不到该任务")
        return

    dialog = _TaskDialog(app['frame'], title="修改定时任务", data=task)
    if dialog.result:
        try:
            r = dialog.result
            ok = _sch.update_task(
                task_id,
                name=r['name'],
                target_path=r['target_path'],
                args=r['args'],
                schedule_type=r['schedule_type'],
                schedule_time=r['schedule_time'],
                enabled=bool(r['enabled']),
            )
            if ok:
                messagebox.showinfo("成功", "任务修改成功")
                _refresh(app)
            else:
                messagebox.showerror("失败", "修改失败")
        except Exception as e:
            messagebox.showerror("错误", f"修改失败：{e}")


def _delete_task(app):
    """删除任务"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个任务")
        return
    if not messagebox.askyesno("确认", "确定删除选中的任务？"):
        return
    task_id = int(app['tree'].item(sel[0], 'values')[0])
    try:
        ok = _sch.delete_task(task_id)
        if ok:
            messagebox.showinfo("成功", "任务已删除")
            _refresh(app)
        else:
            messagebox.showerror("失败", "删除失败")
    except Exception as e:
        messagebox.showerror("错误", f"删除失败：{e}")


def _toggle_task(app):
    """启用/禁用任务"""
    sel = app['tree'].selection()
    if not sel:
        messagebox.showwarning("提示", "请先选择一个任务")
        return
    values = app['tree'].item(sel[0], 'values')
    task_id = int(values[0])
    current_status = values[5]  # "启用" or "禁用"
    new_enabled = 0 if current_status == "启用" else 1
    try:
        _sch.toggle_task(task_id, bool(new_enabled))
        _refresh(app)
    except Exception as e:
        messagebox.showerror("错误", f"切换状态失败：{e}")


class _TaskDialog(tk.Toplevel):
    """任务添加/修改对话框"""

    def __init__(self, parent, title="定时任务", data=None):
        tk.Toplevel.__init__(self, parent)
        self.title(title)
        self.geometry("500x380")
        self.transient(parent)
        self.grab_set()
        self.result = None
        _center_window(self)

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        # 任务名称
        ttk.Label(container, text="任务名称：").grid(row=0, column=0, sticky='w', pady=4)
        name_var = tk.StringVar(value=data.get('name', '') if data else '')
        ttk.Entry(container, textvariable=name_var, width=30).grid(row=0, column=1, pady=4, sticky='w')

        # 调度类型
        ttk.Label(container, text="调度类型：").grid(row=1, column=0, sticky='w', pady=4)
        type_var = tk.StringVar(value=_TYPE_MAP_REV.get(data.get('schedule_type', 'daily'), '每天') if data else '每天')
        type_combo = ttk.Combobox(container, textvariable=type_var, values=_SCHEDULE_TYPES,
                                  state='readonly', width=12)
        type_combo.grid(row=1, column=1, pady=4, sticky='w')

        # 调度时间
        ttk.Label(container, text="调度时间：").grid(row=2, column=0, sticky='w', pady=4)
        time_var = tk.StringVar(value=data.get('schedule_time', '09:00') if data else '09:00')
        time_entry = ttk.Entry(container, textvariable=time_var, width=30)
        time_entry.grid(row=2, column=1, pady=4, sticky='w')

        # 时间说明
        time_hint = ttk.Label(container, text="", style='Subtitle.TLabel', foreground='#64748b')
        time_hint.grid(row=3, column=1, sticky='w', pady=(0, 4))

        def _update_hint(e=None):
            t = type_var.get()
            if t == '每天':
                time_hint.config(text="格式：HH:MM（如 09:30）")
            elif t == '一次性':
                time_hint.config(text="格式：YYYY-MM-DD HH:MM（如 2026-08-01 09:30）")
            elif t == '间隔执行':
                time_hint.config(text="格式：HH:MM-HH:MM|分钟数（如 07:00-23:00|60）")

        type_combo.bind('<<ComboboxSelected>>', _update_hint)
        _update_hint()

        # 目标程序
        ttk.Label(container, text="目标程序：").grid(row=4, column=0, sticky='w', pady=4)
        target_var = tk.StringVar(value=data.get('target_path', '') if data else '')
        target_entry = ttk.Entry(container, textvariable=target_var, width=30)
        target_entry.grid(row=4, column=1, pady=4, sticky='w')
        ttk.Button(container, text="浏览...",
                   command=lambda: _browse(target_var)).grid(row=4, column=2, padx=(4, 0), pady=4)

        # 参数
        ttk.Label(container, text="参数：").grid(row=5, column=0, sticky='w', pady=4)
        args_var = tk.StringVar(value=data.get('args', '') if data else '')
        ttk.Entry(container, textvariable=args_var, width=30).grid(row=5, column=1, pady=4, sticky='w')

        # 启用状态
        ttk.Label(container, text="启用：").grid(row=6, column=0, sticky='w', pady=4)
        enabled_var = tk.StringVar(value='1' if (not data or data.get('enabled', 1)) else '0')
        ttk.Radiobutton(container, text="启用", variable=enabled_var, value='1').grid(row=6, column=1, sticky='w', pady=4)
        ttk.Radiobutton(container, text="禁用", variable=enabled_var, value='0').grid(row=6, column=1, sticky='w', padx=80, pady=4)

        # 按钮
        btn_frame = ttk.Frame(container)
        btn_frame.grid(row=7, column=0, columnspan=3, pady=(16, 0))

        def _ok():
            if not name_var.get().strip():
                messagebox.showerror("错误", "任务名称不能为空", parent=self)
                return
            if not target_var.get().strip():
                messagebox.showerror("错误", "目标程序不能为空", parent=self)
                return
            stype = _TYPE_MAP.get(type_var.get(), 'daily')
            stime = time_var.get().strip()
            # 验证调度时间
            valid, vmsg = _sch.validate_schedule(stype, stime)
            if not valid:
                messagebox.showerror("错误", f"调度时间格式错误：\n{vmsg}", parent=self)
                return
            self.result = {
                'name': name_var.get().strip(),
                'target_path': target_var.get().strip(),
                'args': args_var.get().strip(),
                'schedule_type': stype,
                'schedule_time': stime,
                'enabled': int(enabled_var.get()),
            }
            self.destroy()

        ttk.Button(btn_frame, text="确定", command=_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=8)

        self.wait_window()


def _browse(target_var):
    """浏览选择文件"""
    filepath = filedialog.askopenfilename(
        title="选择目标程序",
        filetypes=[("可执行文件", "*.exe *.bat *.cmd *.py"),
                   ("所有文件", "*.*")])
    if filepath:
        target_var.set(filepath)
