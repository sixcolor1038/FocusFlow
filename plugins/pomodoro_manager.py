# -*- coding: utf-8 -*-
"""
FocusFlow 插件：番茄工作法
- 工作/休息定时器（后台线程计时）
- 每个番茄钟自动记录按键数据（与统计联动）
- 今日完成番茄钟数、总按键数统计
- 历史记录查看

依赖：pomodoro.py（后端模块，随主程序提供）
"""

import os
import sys
from datetime import datetime

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import tkinter as tk
from tkinter import ttk, messagebox

PLUGIN_NAME = "番茄工作法"
PLUGIN_DESC = "番茄钟定时器，自动记录每个番茄钟的按键数据"
PLUGIN_VERSION = "1.1.0"
PLUGIN_AUTHOR = "FocusFlow"

try:
    import pomodoro as _pomo
    _pomo.init_db()
    _import_error = None
except Exception as _e:
    _pomo = None
    _import_error = str(_e)

_STATE_TEXT = {
    _pomo.STATE_WORK if _pomo else 'work': "工作中",
    _pomo.STATE_BREAK if _pomo else 'break': "休息中",
    _pomo.STATE_IDLE if _pomo else 'idle': "空闲",
}


def _fmt(seconds: int) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _center_window(win):
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
    """构建番茄工作法视图"""
    frame = ttk.Frame(parent)

    if _import_error:
        ttk.Label(frame, text=f"pomodoro 模块加载失败：\n{_import_error}",
                  foreground='#d13438', justify='left').pack(pady=40)
        return frame

    app = {'frame': frame}
    timer = _pomo.get_pomodoro()

    # ===== 计时器卡片 =====
    timer_card = ttk.LabelFrame(frame, text="计时器")
    timer_card.pack(fill=tk.X, padx=12, pady=(12, 8))

    inner = ttk.Frame(timer_card)
    inner.pack(fill=tk.X, padx=14, pady=12)

    # 左侧：大号倒计时
    left = ttk.Frame(inner)
    left.pack(side=tk.LEFT)
    state_lbl = ttk.Label(left, text="空闲", font=("Segoe UI", 12, "bold"),
                          foreground='#4D8CF7')
    state_lbl.pack(anchor='w')
    time_lbl = ttk.Label(left, text="25:00", font=("Segoe UI", 36, "bold"))
    time_lbl.pack(anchor='w')
    hint_lbl = ttk.Label(left, text="点击「开始工作」开始一个番茄钟", style='Subtitle.TLabel')
    hint_lbl.pack(anchor='w')

    # 右侧：按键统计联动
    right = ttk.Frame(inner)
    right.pack(side=tk.RIGHT)
    keys_lbl = ttk.Label(right, text="本番茄钟键鼠：0", font=("Segoe UI", 11, "bold"))
    keys_lbl.pack(anchor='e')
    today_lbl = ttk.Label(right, text="今日番茄钟：0 个 · 0 次", style='Subtitle.TLabel')
    today_lbl.pack(anchor='e')
    speed_lbl = ttk.Label(right, text="平均速度：-- 次/分", style='Subtitle.TLabel')
    speed_lbl.pack(anchor='e')

    # 按钮栏
    btn_bar = ttk.Frame(frame)
    btn_bar.pack(fill=tk.X, padx=12, pady=(0, 8))
    ttk.Button(btn_bar, text="开始工作", width=10,
               command=lambda: _start_work(app)).pack(side=tk.LEFT)
    ttk.Button(btn_bar, text="开始休息", width=10,
               command=lambda: _start_break(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="暂停/继续", width=10,
               command=lambda: _toggle_pause(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="跳过", width=6,
               command=lambda: _skip(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="停止", width=6,
               command=lambda: _stop(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="刷新", width=6,
               command=lambda: _refresh(app)).pack(side=tk.RIGHT)

    # 时长设置
    set_bar = ttk.Frame(frame)
    set_bar.pack(fill=tk.X, padx=12, pady=(0, 8))
    ttk.Label(set_bar, text="工作时长(分)").pack(side=tk.LEFT)
    work_min = tk.IntVar(value=timer.get_state_info()['work_minutes'])
    ttk.Spinbox(set_bar, from_=1, to=120, textvariable=work_min,
                width=5).pack(side=tk.LEFT, padx=(4, 0))
    ttk.Label(set_bar, text="休息时长(分)").pack(side=tk.LEFT, padx=(12, 0))
    break_min = tk.IntVar(value=timer.get_state_info()['break_minutes'])
    ttk.Spinbox(set_bar, from_=1, to=60, textvariable=break_min,
                width=5).pack(side=tk.LEFT, padx=(4, 0))
    ttk.Button(set_bar, text="应用时长", width=8,
               command=lambda: _apply_durations(app, work_min, break_min)
               ).pack(side=tk.LEFT, padx=(12, 0))
    ttk.Checkbutton(set_bar, text="工作后自动休息", variable=tk.BooleanVar(
        value=timer.get_state_info().get('auto_break', True)),
        command=lambda: _toggle_auto_break(app, timer)).pack(side=tk.LEFT, padx=(12, 0))

    # ===== 历史记录 =====
    list_card = ttk.LabelFrame(frame, text="历史记录（最近 50 条）")
    list_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

    columns = ("id", "type", "start", "duration", "keys")
    tree = ttk.Treeview(list_card, columns=columns, show='headings', height=12)
    tree.heading('id', text='ID')
    tree.heading('type', text='类型')
    tree.heading('start', text='开始时间')
    tree.heading('duration', text='实际时长')
    tree.heading('keys', text='键鼠数')
    tree.column('id', width=50, anchor='center')
    tree.column('type', width=60, anchor='center')
    tree.column('start', width=160, anchor='center')
    tree.column('duration', width=90, anchor='center')
    tree.column('keys', width=90, anchor='center')

    tree.tag_configure('even', background='#f8fafc')
    tree.tag_configure('odd', background='#ffffff')
    tree.tag_configure('work', foreground='#16a34a')
    tree.tag_configure('break', foreground='#4D8CF7')

    vsb = ttk.Scrollbar(list_card, orient='vertical', command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
    vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
    app['tree'] = tree

    # ===== 实时刷新（主线程 after 轮询，避免跨线程更新 Tk） =====
    def _tick(state, remaining, key_count):
        try:
            if not frame.winfo_exists():
                return
            state_cn = _STATE_TEXT.get(state, state)
            state_lbl.config(text=state_cn)
            time_lbl.config(text=_fmt(remaining))
            keys_lbl.config(text=f"本番茄钟键鼠：{key_count:,}")
            info = timer.get_state_info()
            if state == _pomo.STATE_IDLE:
                hint_lbl.config(text="点击「开始工作」开始一个番茄钟")
            elif state == _pomo.STATE_WORK:
                if info.get('paused'):
                    hint_lbl.config(text="已暂停")
                else:
                    hint_lbl.config(text=f"努力工作！目标 {info['work_minutes']} 分钟")
            elif state == _pomo.STATE_BREAK:
                if info.get('paused'):
                    hint_lbl.config(text="已暂停")
                else:
                    hint_lbl.config(text=f"休息一下，放松眼睛 ~ 目标 {info['break_minutes']} 分钟")
        except Exception:
            pass

    def _refresh_summary():
        try:
            if not frame.winfo_exists():
                return
            info = timer.get_state_info()
            summary = _pomo.get_today_summary()
            if summary.get('count', 0) > 0:
                avg_speed = summary['total_keys'] / max(1, summary['total_seconds'] / 60.0)
                speed_lbl.config(text=f"今日键鼠 {summary['total_keys']:,} · 均速 {avg_speed:.0f} 次/分")
                today_lbl.config(text=f"今日番茄钟：{summary['count']} 个 · {summary['total_keys']:,} 次")
            else:
                speed_lbl.config(text="平均速度：-- 次/分")
                today_lbl.config(text=f"今日番茄钟：{info['work_finished']} 个")
        except Exception:
            pass

    def _tick_refresh():
        try:
            if not frame.winfo_exists():
                return
            info = timer.get_state_info()
            _tick(info['state'], info['remaining'], info['key_count'])
            _refresh_summary()
        except Exception:
            pass
        frame.after(1000, _tick_refresh)

    frame.after(1000, _tick_refresh)
    _refresh(app)

    return frame


def _refresh(app):
    """刷新历史记录表"""
    tree = app['tree']
    for item in tree.get_children():
        tree.delete(item)
    try:
        sessions = _pomo.get_recent_sessions(50)
    except Exception:
        sessions = []
    for i, s in enumerate(sessions):
        stype = s.get('type', 'work')
        dur = s.get('actual_seconds', 0)
        dur_str = f"{dur // 60}分{dur % 60}秒"
        tag = ('even' if i % 2 == 0 else 'odd',
               'work' if stype == 'work' else 'break')
        tree.insert('', tk.END, values=(
            s.get('id', ''),
            '工作' if stype == 'work' else '休息',
            s.get('start_time', ''),
            dur_str,
            f"{s.get('key_count', 0):,}",
        ), tags=tag)


def _start_work(app):
    timer = _pomo.get_pomodoro()
    timer.start_work()
    messagebox.showinfo("番茄钟", "工作番茄钟已开始，加油！")


def _start_break(app):
    timer = _pomo.get_pomodoro()
    timer.start_break()
    messagebox.showinfo("番茄钟", "休息时间已开始，放松一下眼睛吧 ~")


def _toggle_pause(app):
    timer = _pomo.get_pomodoro()
    paused = timer.toggle_pause()
    messagebox.showinfo("番茄钟", "已暂停" if paused else "已继续")


def _skip(app):
    timer = _pomo.get_pomodoro()
    timer.skip()
    _refresh(app)


def _stop(app):
    timer = _pomo.get_pomodoro()
    timer.stop()
    _refresh(app)
    messagebox.showinfo("番茄钟", "番茄钟已停止")


def _apply_durations(app, work_min, break_min):
    timer = _pomo.get_pomodoro()
    timer.set_durations(work_min.get(), break_min.get())
    messagebox.showinfo("番茄钟", "时长设置已应用")


def _toggle_auto_break(app, timer):
    timer.set_auto_break(not timer.get_state_info().get('auto_break', True))
