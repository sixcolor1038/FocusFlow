# -*- coding: utf-8 -*-
"""
FocusFlow 插件：Edge 浏览器历史记录查看器
- 查询今日/总记录数
- 30 天趋势图
- 一键更新今日数据

依赖：edge_history.py（后端模块，随主程序提供）
"""

import os
import sys
from datetime import date

_here = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_here)
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import tkinter as tk
from tkinter import ttk, messagebox

PLUGIN_NAME = "Edge历史记录"
PLUGIN_DESC = "查看 Edge 浏览器历史记录数量及 30 天趋势"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "FocusFlow"

try:
    import edge_history as _eh
    _import_error = None
except Exception as _e:
    _eh = None
    _import_error = str(_e)


def get_view(parent):
    """构建 Edge 历史记录视图"""
    frame = ttk.Frame(parent)

    if _import_error:
        ttk.Label(frame, text=f"edge_history 模块加载失败：\n{_import_error}",
                  foreground='#d13438', justify='left').pack(pady=40)
        return frame

    # 顶部统计卡片
    stats_card = ttk.LabelFrame(frame, text="Edge 历史记录概览")
    stats_card.pack(fill=tk.X, padx=12, pady=(12, 8))

    stats_grid = ttk.Frame(stats_card)
    stats_grid.pack(fill=tk.X, padx=12, pady=8)

    ttk.Label(stats_grid, text="今日记录数", style='Subtitle.TLabel').grid(row=0, column=0, padx=20, sticky='w')
    today_label = ttk.Label(stats_grid, text="—", font=("Segoe UI", 18, "bold"), foreground='#3b82f6')
    today_label.grid(row=1, column=0, padx=20, sticky='w')

    ttk.Label(stats_grid, text="总记录数", style='Subtitle.TLabel').grid(row=0, column=1, padx=20, sticky='w')
    total_label = ttk.Label(stats_grid, text="—", font=("Segoe UI", 18, "bold"), foreground='#10b981')
    total_label.grid(row=1, column=1, padx=20, sticky='w')

    ttk.Label(stats_grid, text="近30天峰值", style='Subtitle.TLabel').grid(row=0, column=2, padx=20, sticky='w')
    peak_label = ttk.Label(stats_grid, text="—", font=("Segoe UI", 18, "bold"), foreground='#f59e0b')
    peak_label.grid(row=1, column=2, padx=20, sticky='w')

    # 按钮栏
    btn_bar = ttk.Frame(frame)
    btn_bar.pack(fill=tk.X, padx=12, pady=(0, 8))
    ttk.Button(btn_bar, text="查询今日", width=10,
               command=lambda: _refresh(today_label, total_label, peak_label, chart_frame)).pack(side=tk.LEFT)
    ttk.Button(btn_bar, text="刷新趋势图", width=12,
               command=lambda: _draw_chart(chart_frame)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(btn_bar, text="说明", width=6,
               command=_show_help).pack(side=tk.RIGHT)

    # 趋势图区域
    chart_card = ttk.LabelFrame(frame, text="近 30 天趋势")
    chart_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))
    chart_frame = ttk.Frame(chart_card)
    chart_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # 首次加载
    _refresh(today_label, total_label, peak_label, chart_frame)

    return frame


def _refresh(today_label, total_label, peak_label, chart_frame):
    """刷新统计数据"""
    try:
        today_count, total_count = _eh.update_today_edge_history()
        today_label.config(text=f"{today_count:,}")
        total_label.config(text=f"{total_count:,}")

        counts = _eh.get_edge_history_counts(30)
        if counts:
            peak = max(c for _, c in counts)
            peak_label.config(text=f"{peak:,}")
        else:
            peak_label.config(text="0")

        _draw_chart(chart_frame)
    except Exception as e:
        today_label.config(text="失败")
        total_label.config(text="失败")
        peak_label.config(text="失败")
        messagebox.showerror("错误", f"查询 Edge 历史失败：\n{e}")


def _draw_chart(chart_frame):
    """绘制趋势图（使用 Tkinter Canvas，不依赖 matplotlib，避免打包后报错）"""
    for child in chart_frame.winfo_children():
        child.destroy()

    counts = _eh.get_edge_history_counts(30)
    if not counts:
        ttk.Label(chart_frame, text="暂无数据，请点击「查询今日」开始记录",
                  style='Subtitle.TLabel').pack(pady=40)
        return

    canvas = tk.Canvas(chart_frame, bg='#fafafa', highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    def _render():
        try:
            canvas.delete('all')
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            if w <= 20 or h <= 20:
                return
            ml, mr, mt, mb = 50, 16, 30, 45
            chart_w = w - ml - mr
            chart_h = h - mt - mb
            vals = [c for _, c in counts]
            dates = [d[5:] for d, _ in counts]  # MM-DD
            today_str = date.today().isoformat()
            max_val = max(vals) or 1
            n = len(vals)
            bar_w = chart_w / n * 0.7
            gap = chart_w / n * 0.3

            # 标题
            canvas.create_text(w // 2, 12, text="Edge 历史记录趋势（近30天）",
                               fill='#333333', font=('Segoe UI', 11, 'bold'))

            # Y 轴刻度
            for i in range(5):
                y = mt + chart_h * (1 - i / 4)
                val = int(max_val * i / 4)
                canvas.create_text(ml - 8, y, text=str(val), anchor='e',
                                   fill='#666666', font=('Segoe UI', 8))
                canvas.create_line(ml, y, ml + chart_w, y, fill='#e0e0e0')

            # 柱子
            for i, (d, c) in enumerate(counts):
                x = ml + i * (bar_w + gap) + gap / 2
                bar_h = (c / max_val) * chart_h
                y = mt + chart_h - bar_h
                color = '#3b82f6' if d == today_str else '#93c5fd'
                canvas.create_rectangle(x, y, x + bar_w, mt + chart_h,
                                        fill=color, outline='')
                if c > 0:
                    canvas.create_text(x + bar_w / 2, y - 8, text=f'{c:,}',
                                       fill='#666666', font=('Segoe UI', 7))

            # X 轴标签（间隔显示，避免重叠）
            step = max(1, n // 6)
            for i in range(0, n, step):
                x = ml + i * (bar_w + gap) + (bar_w + gap) / 2
                canvas.create_text(x, mt + chart_h + 15, text=dates[i],
                                   fill='#666666', font=('Segoe UI', 8))
        except Exception as e:
            log_err(f"绘制趋势图失败: {e}")

    canvas.bind('<Configure>', lambda e: _render())
    chart_frame.after(50, _render)


def _show_help():
    messagebox.showinfo("Edge 历史记录说明",
        "本插件读取 Edge 浏览器的历史记录数据库，统计每日访问的网页数量。\n\n"
        "• 「查询今日」：读取今天的 Edge 历史记录数并保存\n"
        "• 趋势图展示最近 30 天的每日记录数\n"
        "• 蓝色柱表示今天的数据\n\n"
        "注意：需要 Edge 浏览器已安装且有历史记录。")


def log_err(msg):
    """简易日志"""
    try:
        from logger import get_logger
        get_logger('edge_history_viewer').error(msg)
    except Exception:
        print(msg)
