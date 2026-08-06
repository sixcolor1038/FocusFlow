# -*- coding: utf-8 -*-
"""
FocusFlow 主界面 GUI 模块（性能优化版）

优化点：
- 移除 matplotlib 依赖，用 Tkinter Canvas 手绘趋势图（启动快、体积小）
- 主题切换瞬时完成（只更新颜色，不重绘表格）
- Treeview 交替行颜色正确生效
- 增量刷新只更新数字标签，不查数据库
- 启动优化：延迟加载非必要模块
"""

import os
import threading
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from PIL import Image, ImageDraw, ImageTk

from config import (config, APP_NAME, APP_DISPLAY_NAME,
                    APP_VERSION, APP_UPDATE_DATE,
                    get_window_geometry, set_window_geometry,
                    get_ui_state, set_ui_state)
from logger import get_logger

log = get_logger('gui')


# ==================== 主题配置（DeepSeek 风格 · 液态玻璃） ====================
# 设计语言：极简主义 + 液态玻璃。柔和渐变背景、半透明玻璃卡片、
# 科技蓝 #4D8CF7、深灰 #1A1A2E 主文字、中灰 #4A4A6A 辅助文字。
THEMES = {
    'light': {
        'bg': '#F5F7FA',
        'page_bg': '#EFF3FB',       # 页面底色（玻璃卡片圆角透出的颜色）
        'fg': '#1A1A2E',
        'card_bg': '#FFFFFF',
        'accent': '#4D8CF7',
        'accent_hover': '#3B7DE0',
        'accent_soft': '#E8F0FE',   # 主色浅底
        'success': '#10B981',
        'warning': '#F59E0B',
        'danger': '#E5484D',
        'muted': '#4A4A6A',
        'border': '#E5ECF8',
        'tree_bg': '#FFFFFF',
        'tree_alt': '#F3F7FD',
        'tree_selected': '#D6E6FC',
        'tree_header_bg': '#F1F5FB',
        'tree_header_fg': '#1A1A2E',
        # 背景渐变（RGB 元组，供 PIL 绘制）
        'grad_top': (232, 239, 252),
        'grad_bottom': (247, 250, 254),
        # 玻璃卡片
        'glass_fill': (255, 255, 255),
        'glass_alpha': 0.86,
        'glass_border': (224, 233, 248),
        # 顶部导航 / 头像
        'nav_alpha': 0.92,
        'avatar_bg': '#D9E7FB',
        'avatar_fg': '#2E5FB8',
    },
    'dark': {
        'bg': '#14161F',
        'page_bg': '#171B27',
        'fg': '#E8EAEE',
        'card_bg': '#1E2331',
        'accent': '#5B9CF7',
        'accent_hover': '#7AB1FA',
        'accent_soft': '#22304A',
        'success': '#34D399',
        'warning': '#FBBF24',
        'danger': '#F87171',
        'muted': '#8A92A6',
        'border': '#2A3042',
        'tree_bg': '#1E2331',
        'tree_alt': '#242B3C',
        'tree_selected': '#2C4A78',
        'tree_header_bg': '#232A3C',
        'tree_header_fg': '#E8EAEE',
        # 背景渐变（RGB 元组，供 PIL 绘制）
        'grad_top': (23, 27, 39),
        'grad_bottom': (16, 19, 28),
        # 玻璃卡片
        'glass_fill': (30, 35, 49),
        'glass_alpha': 0.9,
        'glass_border': (43, 50, 70),
        # 顶部导航 / 头像
        'nav_alpha': 0.94,
        'avatar_bg': '#2A3550',
        'avatar_fg': '#9DB8F5',
    },
}

# 当前生效主题（供玻璃卡片/背景绘制时读取）
_GLASS_THEME = THEMES['light']


def _rgb(hex_str: str) -> tuple:
    """十六进制颜色转 RGB 元组"""
    hex_str = hex_str.lstrip('#')
    return tuple(int(hex_str[i:i + 2], 16) for i in (0, 2, 4))


def _blend(c1: tuple, c2: tuple, t: float) -> tuple:
    """线性插值颜色"""
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


# 玻璃卡片实例注册表（主题切换时统一重绘）
_GLASS_FRAMES: list = []


class GlassFrame(tk.Frame):
    """液态玻璃风格容器：圆角 + 半透明填充 + 高光描边

    - 背景由 PIL 绘制（RGBA 圆角矩形），透明圆角处透出页面底色
    - 自带柔和阴影与顶部高光，营造"液态玻璃"质感
    - 主题切换时自动重绘（读取 _GLASS_THEME）
    - kind: 'card'（常规玻璃卡片）/ 'nav'（导航栏，更高透明度）
    """

    def __init__(self, parent, kind: str = 'card', radius: int = 16, **kw):
        super().__init__(parent, **kw)
        self.kind = kind
        self.radius = radius
        self._photo = None
        self._bg_label = None
        self._redraw_after = None
        self.configure(highlightthickness=0, bd=0)
        _GLASS_FRAMES.append(self)
        self.bind('<Configure>', self._on_configure)

    # ---------- 生命周期 ----------
    def destroy(self):
        try:
            _GLASS_FRAMES.remove(self)
        except ValueError:
            pass
        super().destroy()

    def refresh(self):
        """重新绘制玻璃背景（主题切换时调用）"""
        if self._redraw_after:
            try:
                self.after_cancel(self._redraw_after)
            except Exception:
                pass
            self._redraw_after = None
        self._draw()

    def _on_configure(self, e):
        if self._redraw_after:
            try:
                self.after_cancel(self._redraw_after)
            except Exception:
                pass
        self._redraw_after = self.after(25, self._draw)

    # ---------- 绘制 ----------
    def _draw(self):
        try:
            self._redraw_after = None
            w = self.winfo_width()
            h = self.winfo_height()
            if w < 20 or h < 20:
                return
            theme = _GLASS_THEME
            page_bg = _rgb(theme.get('page_bg', '#EFF3FB'))
            r = self.radius
            if self.kind == 'nav':
                fill = _rgb(theme.get('card_bg', '#FFFFFF'))
                alpha = theme.get('nav_alpha', 0.92)
            else:
                fill = tuple(theme.get('glass_fill', (255, 255, 255)))
                alpha = theme.get('glass_alpha', 0.86)
            border = tuple(theme.get('glass_border', (224, 233, 248)))

            # 阴影（右下偏移，露出柔和投影）
            shadow_color = _blend(page_bg, (24, 40, 80), 0.25)
            img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((3, 5, w - 1, h - 1), radius=r,
                                fill=shadow_color + (44,))
            # 主体（半透明玻璃）
            d.rounded_rectangle((1, 1, w - 2, h - 2), radius=r,
                                fill=fill + (int(alpha * 255),))
            # 描边
            d.rounded_rectangle((1, 1, w - 2, h - 2), radius=r,
                                outline=border + (255,), width=1)
            # 顶部高光（液态玻璃折射感）
            d.line((1 + r, 2, w - 2 - r, 2), fill=(255, 255, 255, 140), width=1)

            self._photo = ImageTk.PhotoImage(img)
            if self._bg_label is None:
                self._bg_label = tk.Label(self, image=self._photo,
                                          bg=theme.get('page_bg', '#EFF3FB'))
                self._bg_label.place(x=0, y=0, relwidth=1, relheight=1)
                self._bg_label.lower()
            else:
                self._bg_label.configure(image=self._photo,
                                         bg=theme.get('page_bg', '#EFF3FB'))
                self._bg_label.lower()
            self.configure(bg=theme.get('page_bg', '#EFF3FB'))
        except Exception as e:
            log.debug('玻璃卡片绘制失败: %s', e)


# ==================== 按键分组 ====================
def classify_key(key_name: str) -> str:
    """将按键/鼠标操作分类"""
    if key_name in ('Shift', '左Shift', '右Shift', 'Ctrl', '左Ctrl', '右Ctrl',
                    'Alt', '左Alt', '右Alt', 'Win', '左Win', '右Win'):
        return '修饰键'
    if key_name.startswith('F') and key_name[1:].isdigit():
        return '功能键'
    if key_name.isdigit():
        return '数字键'
    if len(key_name) == 1 and key_name.isalpha():
        return '字母键'
    if key_name in ('空格', '回车', '退格', 'Tab', 'Esc', 'Delete', 'Insert',
                    'Home', 'End', 'PageUp', 'PageDown', '↑', '↓', '←', '→'):
        return '编辑键'
    return '其他'


KEY_GROUP_ORDER = ['字母键', '数字键', '功能键', '修饰键', '编辑键', '其他']


# ==================== 主应用 ====================
class FocusFlowApp:
    def __init__(self, hidden: bool = False):
        self.root = tk.Tk()
        self.root.title(APP_DISPLAY_NAME)
        self.root.minsize(640, 460)
        # 恢复上次保存的窗口尺寸与位置；首次运行才默认居中
        self._load_window_geometry()
        self.current_days: Optional[int] = None
        self.current_date: Optional[date] = None
        self.current_year: Optional[int] = None
        self._quitting = False
        self._theme = config.get('gui', 'theme', 'light')
        self._trend_canvas = None
        self._trend_data = []  # 缓存趋势数据，避免重复查询

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        self._apply_theme()
        self._set_window_icon()
        self.setup_ui()
        # 首次刷新：延迟到 UI 显示后，避免阻塞启动
        self.root.after(100, lambda: self.refresh_stats(full=True))
        self._start_auto_refresh()

        if hidden:
            self.root.withdraw()

        if config.getbool('floating', 'enabled', False):
            def _show_floating():
                # v1.0：延迟显示悬浮窗，带重试机制
                # 确保 root 窗口已完全初始化后再创建 Toplevel
                try:
                    from floating_window import get_floating
                    floating = get_floating()
                    if not floating.is_visible():
                        floating.show(self.root)
                except Exception as e:
                    log.warning('启动时显示悬浮窗失败: %s', e)
                    # 重试一次（再等 1 秒）
                    self.root.after(1000, lambda: _show_floating_retry(1))

            def _show_floating_retry(attempt):
                try:
                    from floating_window import get_floating
                    floating = get_floating()
                    if not floating.is_visible():
                        floating.show(self.root)
                except Exception as e:
                    if attempt < 3:
                        log.debug('悬浮窗重试 %d 失败: %s', attempt, e)
                        self.root.after(1000, lambda: _show_floating_retry(attempt + 1))
                    else:
                        log.warning('悬浮窗重试 %d 次后放弃: %s', attempt, e)

            self.root.after(800, _show_floating)

    # ---------- 主题 ----------
    def _apply_theme(self):
        global _GLASS_THEME
        theme = THEMES.get(self._theme, THEMES['light'])
        self._theme_colors = theme
        _GLASS_THEME = theme
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass

        # 全局样式
        style.configure('.', background=theme['bg'], foreground=theme['fg'],
                        font=('Segoe UI', 10))
        style.configure('TFrame', background=theme['page_bg'])
        style.configure('Card.TFrame', background=theme['card_bg'],
                        relief='flat', borderwidth=0)

        # 标签
        style.configure('Title.TLabel', background=theme['card_bg'],
                        foreground=theme['accent'], font=('Segoe UI', 14, 'bold'))
        style.configure('Subtitle.TLabel', background=theme['card_bg'],
                        foreground=theme['muted'], font=('Segoe UI', 9))
        style.configure('Stat.TLabel', background=theme['card_bg'],
                        foreground=theme['fg'], font=('Segoe UI', 11))
        style.configure('BigStat.TLabel', background=theme['card_bg'],
                        foreground=theme['accent'],
                        font=('Segoe UI', 22, 'bold'))
        style.configure('Muted.TLabel', background=theme['card_bg'],
                        foreground=theme['muted'], font=('Segoe UI', 10))
        style.configure('Status.TLabel', background=theme['page_bg'],
                        foreground=theme['muted'], font=('Segoe UI', 9))
        # 导航 / 头像文字
        style.configure('NavTitle.TLabel', background=theme['card_bg'],
                        foreground=theme['fg'], font=('Segoe UI', 13, 'bold'))
        style.configure('NavSub.TLabel', background=theme['card_bg'],
                        foreground=theme['muted'], font=('Segoe UI', 9))

        # 按钮（液态玻璃风格：浅底 + 主色）
        style.configure('TButton', font=('Segoe UI', 10), padding=(14, 7),
                        background=theme['card_bg'], foreground=theme['fg'],
                        bordercolor=theme['border'],
                        lightcolor=theme['border'], darkcolor=theme['border'])
        style.map('TButton',
                  background=[('active', theme['accent_soft']),
                              ('pressed', theme['accent_soft'])],
                  foreground=[('active', theme['accent']),
                              ('pressed', theme['accent'])])
        style.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'),
                        background=theme['accent'], foreground='#FFFFFF',
                        bordercolor=theme['accent'])
        style.map('Accent.TButton',
                  background=[('active', theme['accent_hover']),
                              ('pressed', theme['accent_hover'])],
                  foreground=[('active', '#FFFFFF'), ('pressed', '#FFFFFF')])
        style.configure('Danger.TButton', foreground=theme['danger'])
        style.configure('Success.TButton', foreground=theme['success'])

        # 单选/复选
        style.configure('TRadiobutton', background=theme['page_bg'],
                        foreground=theme['fg'], font=('Segoe UI', 10))
        style.map('TRadiobutton',
                  background=[('active', theme['page_bg'])],
                  foreground=[('active', theme['accent'])])

        # 输入框
        style.configure('TEntry', fieldbackground=theme['card_bg'],
                        foreground=theme['fg'], insertcolor=theme['fg'],
                        bordercolor=theme['border'])
        style.configure('TCombobox', fieldbackground=theme['card_bg'],
                        foreground=theme['fg'],
                        bordercolor=theme['border'],
                        arrowcolor=theme['accent'])

        # Treeview
        style.configure('Treeview',
                        background=theme['tree_bg'],
                        foreground=theme['fg'],
                        fieldbackground=theme['tree_bg'],
                        borderwidth=0,
                        font=('Segoe UI', 10),
                        rowheight=30)
        style.configure('Treeview.Heading',
                        background=theme['tree_header_bg'],
                        foreground=theme['tree_header_fg'],
                        font=('Segoe UI', 10, 'bold'),
                        relief='flat')
        style.map('Treeview',
                  background=[('selected', theme['tree_selected'])],
                  foreground=[('selected', theme['fg'])])
        style.map('Treeview.Heading',
                  background=[('active', theme['accent_soft'])])

        # Notebook
        style.configure('TNotebook', background=theme['page_bg'], borderwidth=0)
        style.configure('TNotebook.Tab',
                        background=theme['page_bg'],
                        foreground=theme['muted'],
                        padding=(16, 8),
                        font=('Segoe UI', 10))
        style.map('TNotebook.Tab',
                  background=[('selected', theme['card_bg'])],
                  foreground=[('selected', theme['accent'])])

        self.root.configure(bg=theme['page_bg'])

        # 更新已存在的 Treeview tag 颜色
        if hasattr(self, 'tree'):
            self.tree.tag_configure('even', background=theme['tree_alt'])
            self.tree.tag_configure('odd', background=theme['tree_bg'])
        if hasattr(self, 'group_tree'):
            self.group_tree.tag_configure('even', background=theme['tree_alt'])
            self.group_tree.tag_configure('odd', background=theme['tree_bg'])

        # 主题切换后重绘背景与玻璃卡片
        self.root.after(20, self._refresh_glass)

    @staticmethod
    def _center_window(win):
        """将窗口居中显示（水平居中，垂直略偏上）"""
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
        except Exception as e:
            log.debug('窗口居中失败: %s', e)

    def _load_window_geometry(self):
        """恢复上次保存的窗口尺寸与位置（独立文件，首次运行默认 880x720 并居中）"""
        try:
            geo = get_window_geometry()
            if geo and 'x' in geo:
                self.root.geometry(geo)
                self.root.update_idletasks()
                # 保证窗口至少部分在屏幕内
                w = self.root.winfo_width()
                h = self.root.winfo_height()
                if w > self.root.winfo_screenwidth() or h > self.root.winfo_screenheight():
                    self.root.geometry("880x720")
                    self._center_window(self.root)
                return
        except Exception:
            pass
        self.root.geometry("880x720")
        self._center_window(self.root)

    def _save_window_geometry(self):
        """保存当前窗口尺寸与位置到独立文件（供下次启动恢复）"""
        try:
            if self.root.state() != 'normal':
                return
            geo = self.root.geometry()
            if not geo or 'x' not in geo:
                return
            set_window_geometry(geo)
        except Exception as e:
            log.debug('保存窗口几何失败: %s', e)
        # 一并保存已打开的插件窗口尺寸
        for name, win in list(getattr(self, '_plugin_windows', {}).items()):
            try:
                if win is not None and win.winfo_exists() and win.state() == 'normal':
                    set_ui_state(f'plugin:{name}', 'geometry', win.geometry())
            except Exception:
                pass

    def _schedule_plugin_geo_save(self, section: str, top):
        """插件窗口尺寸防抖保存"""
        try:
            if not hasattr(self, '_plugin_geo_after'):
                self._plugin_geo_after = {}
            if section in self._plugin_geo_after:
                try:
                    self.root.after_cancel(self._plugin_geo_after[section])
                except Exception:
                    pass
            self._plugin_geo_after[section] = self.root.after(
                600, lambda: self._save_plugin_geo_now(section, top))
        except Exception:
            pass

    def _save_plugin_geo_now(self, section: str, top):
        try:
            if top is not None and top.winfo_exists() and top.state() == 'normal':
                set_ui_state(section, 'geometry', top.geometry())
        except Exception:
            pass

    def _set_window_icon(self):
        """设置窗口图标"""
        try:
            from tray import _create_image
            img = _create_image(False)
            self._icon_photo = ImageTk.PhotoImage(img)
            self.root.iconphoto(True, self._icon_photo)
        except Exception as e:
            log.debug('设置窗口图标失败: %s', e)

    @staticmethod
    def _make_avatar(size: int = 34):
        """生成圆形用户头像（含首字母）"""
        from PIL import Image, ImageDraw, ImageTk, ImageFont
        theme = _GLASS_THEME
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((0, 0, size, size), fill=_rgb(theme['avatar_bg']))
        try:
            font = ImageFont.truetype('segoeui.ttf', int(size * 0.46))
        except Exception:
            font = ImageFont.load_default()
        text = 'F'
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
               text, font=font, fill=_rgb(theme['avatar_fg']))
        return ImageTk.PhotoImage(img)

    def toggle_theme(self):
        """切换主题 - 优化版：只更新颜色与玻璃卡片，不重绘表格"""
        self._theme = 'dark' if self._theme == 'light' else 'light'
        config.set('gui', 'theme', self._theme)
        self._apply_theme()
        # 不调用 refresh_stats，只重绘趋势图（因为趋势图颜色需要更新）
        self.root.after(10, self._redraw_trend)

    def _refresh_glass(self):
        """主题切换后重绘背景渐变与所有玻璃卡片"""
        try:
            self._draw_background()
            for g in list(_GLASS_FRAMES):
                try:
                    g.refresh()
                except Exception:
                    pass
            # 悬浮窗同步风格
            try:
                from floating_window import get_floating
                get_floating().apply_theme()
            except Exception:
                pass
        except Exception as e:
            log.debug('刷新玻璃界面失败: %s', e)

    # ---------- 渐变背景 ----------
    def _setup_background(self):
        """创建窗口渐变背景（置于所有内容之下）"""
        self._bg_photo = None
        self._bg_label = tk.Label(self.root, bg=self._theme_colors['page_bg'])
        self._bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self._bg_label.lower()
        self.root.bind('<Configure>', self._on_bg_resize)
        self.root.after(30, self._draw_background)

    def _on_bg_resize(self, e):
        try:
            if e.widget is not self.root:
                return
            if hasattr(self, '_bg_after'):
                try:
                    self.root.after_cancel(self._bg_after)
                except Exception:
                    pass
            self._bg_after = self.root.after(80, self._draw_background)
            # 防抖保存窗口尺寸与位置（手动拉长/减短后重启可保持）
            if hasattr(self, '_geo_after'):
                try:
                    self.root.after_cancel(self._geo_after)
                except Exception:
                    pass
            self._geo_after = self.root.after(500, self._save_window_geometry)
        except Exception:
            pass

    def _draw_background(self):
        """绘制柔和渐变 + 装饰光斑（液态玻璃流动感）"""
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w < 40 or h < 40:
                return
            theme = _GLASS_THEME
            top = tuple(theme.get('grad_top', (232, 239, 252)))
            bottom = tuple(theme.get('grad_bottom', (247, 250, 254)))
            # 垂直渐变
            strip = Image.new('RGB', (1, h))
            for y in range(h):
                strip.putpixel((0, y), _blend(top, bottom, y / max(h - 1, 1)))
            img = strip.resize((w, h), Image.Resampling.LANCZOS)
            # 装饰光斑（右上、左下淡蓝晕染）
            overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(overlay, 'RGBA')
            accent = _rgb(theme['accent'])
            d.ellipse((int(w * 0.68), -int(h * 0.28), int(w * 1.12), int(h * 0.32)),
                      fill=accent + (34,))
            d.ellipse((-int(w * 0.22), int(h * 0.58), int(w * 0.28), int(h * 1.16)),
                      fill=accent + (26,))
            img = Image.alpha_composite(img.convert('RGBA'), overlay)
            self._bg_photo = ImageTk.PhotoImage(img)
            if self._bg_label is not None:
                self._bg_label.configure(image=self._bg_photo)
        except Exception as e:
            log.debug('绘制背景失败: %s', e)

    def _redraw_trend(self):
        """重绘趋势图（仅颜色变化时）"""
        if self._trend_data:
            self._draw_trend_chart(self._trend_data)

    # ---------- UI 构建 ----------
    def setup_ui(self):
        theme = self._theme_colors
        # 渐变背景（置于最底层）
        self._setup_background()

        # ===== 顶部导航栏（液态玻璃） =====
        nav = GlassFrame(self.root, kind='nav', radius=20)
        nav.pack(fill=tk.X, padx=20, pady=(16, 10))
        nav_inner = ttk.Frame(nav, style='Card.TFrame')
        nav_inner.pack(fill=tk.X, padx=18, pady=12)

        # Logo + 品牌名
        try:
            from tray import _create_image
            logo_img = _create_image(False).resize(
                (26, 26), Image.Resampling.LANCZOS)
            self._nav_logo = ImageTk.PhotoImage(logo_img)
            tk.Label(nav_inner, image=self._nav_logo,
                     bg=theme['card_bg']).pack(side=tk.LEFT)
        except Exception as e:
            log.debug('导航 Logo 加载失败: %s', e)
        ttk.Label(nav_inner, text="FocusFlow",
                  style='NavTitle.TLabel').pack(side=tk.LEFT, padx=(10, 4))
        ttk.Label(nav_inner, text="效率追踪器",
                  style='NavSub.TLabel').pack(side=tk.LEFT, pady=(6, 0))

        # 右侧：头像 + 设置按钮
        try:
            self._avatar_photo = self._make_avatar(34)
            tk.Label(nav_inner, image=self._avatar_photo,
                     bg=theme['card_bg']).pack(side=tk.RIGHT)
        except Exception as e:
            log.debug('头像生成失败: %s', e)
        self.settings_btn = ttk.Button(nav_inner, text="设置", width=6,
                                       command=self._open_settings)
        self.settings_btn.pack(side=tk.RIGHT, padx=(10, 0))

        # ===== 主卡片：核心数据（液态玻璃 · 突出展示） =====
        hero = GlassFrame(self.root, kind='card', radius=20)
        hero.pack(fill=tk.X, padx=20, pady=(0, 10))
        hero_inner = ttk.Frame(hero, style='Card.TFrame')
        hero_inner.pack(fill=tk.X, padx=22, pady=16)

        # 今日活跃（大数字 · 主色）
        col1 = ttk.Frame(hero_inner, style='Card.TFrame')
        col1.pack(side=tk.LEFT, padx=(0, 44))
        ttk.Label(col1, text="今日活跃", style='Subtitle.TLabel').pack(anchor='w')
        self.today_label = ttk.Label(col1, text="0", style='BigStat.TLabel')
        self.today_label.pack(anchor='w')

        # 当前速度
        col2 = ttk.Frame(hero_inner, style='Card.TFrame')
        col2.pack(side=tk.LEFT, padx=(0, 44))
        ttk.Label(col2, text="当前速度", style='Subtitle.TLabel').pack(anchor='w')
        self.cpm_label = ttk.Label(col2, text="0 键/分", style='BigStat.TLabel')
        self.cpm_label.pack(anchor='w')

        # 周期总数
        col3 = ttk.Frame(hero_inner, style='Card.TFrame')
        col3.pack(side=tk.LEFT, padx=(0, 44))
        ttk.Label(col3, text="周期总数", style='Subtitle.TLabel').pack(anchor='w')
        self.total_label = ttk.Label(col3, text="0", style='BigStat.TLabel')
        self.total_label.pack(anchor='w')

        # 日均（近7天）
        col4 = ttk.Frame(hero_inner, style='Card.TFrame')
        col4.pack(side=tk.LEFT, padx=(0, 44))
        ttk.Label(col4, text="日均(7天)", style='Subtitle.TLabel').pack(anchor='w')
        self.avg_label = ttk.Label(col4, text="--", style='BigStat.TLabel')
        self.avg_label.pack(anchor='w')

        # 最高单日（近7天）
        col5 = ttk.Frame(hero_inner, style='Card.TFrame')
        col5.pack(side=tk.LEFT)
        ttk.Label(col5, text="最高单日", style='Subtitle.TLabel').pack(anchor='w')
        self.max_label = ttk.Label(col5, text="--", style='BigStat.TLabel')
        self.max_label.pack(anchor='w')

        # 暂停状态（右侧）
        self.pause_status_label = ttk.Label(hero_inner, text="", style='Stat.TLabel')
        self.pause_status_label.pack(side=tk.RIGHT)

        # ===== 控制卡：周期 / 年度 / 视图（液态玻璃） =====
        ctrl = GlassFrame(self.root, kind='card', radius=16)
        ctrl.pack(fill=tk.X, padx=20, pady=(0, 10))
        ctrl_inner = ttk.Frame(ctrl, style='Card.TFrame')
        ctrl_inner.pack(fill=tk.X, padx=16, pady=10)

        # 行 1：统计周期
        row1 = ttk.Frame(ctrl_inner, style='Card.TFrame')
        row1.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row1, text="统计周期", style='Stat.TLabel').pack(side=tk.LEFT)
        self.period_var = tk.IntVar(value=0)
        for text, val in [("今日", -1), ("7天", 7), ("15天", 15),
                          ("30天", 30), ("1年", 365), ("总计", 0)]:
            ttk.Radiobutton(row1, text=text, value=val,
                            variable=self.period_var,
                            command=lambda v=val: self.on_period_change(v)
                            ).pack(side=tk.LEFT, padx=(10, 0))

        # 行 2：年度 / 日期 / 视图
        row2 = ttk.Frame(ctrl_inner, style='Card.TFrame')
        row2.pack(fill=tk.X)
        ttk.Label(row2, text="年度", style='Stat.TLabel').pack(side=tk.LEFT)
        self.year_var = tk.StringVar(value="当前")
        self.year_combo = ttk.Combobox(row2, textvariable=self.year_var,
                                       width=8, state='readonly')
        self.year_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.year_combo.bind('<<ComboboxSelected>>', self.on_year_change)

        ttk.Label(row2, text="日期", style='Stat.TLabel').pack(side=tk.LEFT, padx=(18, 0))
        self.date_entry = ttk.Entry(row2, width=12)
        self.date_entry.pack(side=tk.LEFT, padx=(6, 0))
        self.date_entry.insert(0, "YYYY-MM-DD")
        self.date_entry.bind('<FocusIn>', self._on_date_entry_focus)
        ttk.Button(row2, text="查询", width=6,
                   command=self.on_date_query).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(row2, text="今天", width=6,
                   command=self.on_today_click).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Label(row2, text="视图", style='Stat.TLabel').pack(side=tk.LEFT, padx=(22, 0))
        self.view_var = tk.StringVar(value="按键排行")
        self.view_combo = ttk.Combobox(row2, textvariable=self.view_var,
                                       state='readonly', width=14,
                                       values=["按键排行", "分组统计", "趋势图",
                                               "小时分布", "星期分布",
                                               "插件管理"])
        self.view_combo.pack(side=tk.LEFT, padx=(6, 0))
        self.view_combo.bind('<<ComboboxSelected>>', self._on_view_changed)
        ttk.Button(row2, text="刷新当前视图",
                   command=self._refresh_current_view).pack(side=tk.LEFT, padx=(10, 0))

        # ===== 内容区（液态玻璃卡片） =====
        self.display_area = GlassFrame(self.root, kind='card', radius=16)
        self.display_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        # 构建所有视图（但不显示，按需 pack）
        self._build_rank_view(self.display_area)
        self._build_group_view(self.display_area)
        self._build_trend_view(self.display_area)
        self._build_hourly_view(self.display_area)
        self._build_weekday_view(self.display_area)
        self._build_plugins_view(self.display_area)

        # 默认显示按键排行
        self._current_view = None
        self._show_view("按键排行")

        # ===== 内容区（液态玻璃卡片） =====
        self.display_area = GlassFrame(self.root, kind='card', radius=16)
        self.display_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))

    def _build_rank_view(self, parent):
        """按键排行表格（纯键盘按键排行）"""
        self.rank_view = ttk.Frame(parent, style='Card.TFrame')

        container = self.rank_view
        columns = ('rank', 'key', 'count', 'percent')
        self.tree = ttk.Treeview(container, columns=columns, show='headings',
                                 selectmode='browse')
        self.tree.heading('rank', text='排名')
        self.tree.heading('key', text='按键')
        self.tree.heading('count', text='次数')
        self.tree.heading('percent', text='占比')
        self.tree.column('rank', width=80, anchor='center')
        self.tree.column('key', width=150, anchor='center')
        self.tree.column('count', width=120, anchor='w')
        self.tree.column('percent', width=100, anchor='center')

        theme = self._theme_colors
        self.tree.tag_configure('even', background=theme['tree_alt'])
        self.tree.tag_configure('odd', background=theme['tree_bg'])

        # 右键菜单：快速清除选中按键的今日次数
        self.tree.bind('<Button-3>', self._on_rank_right_click)

        vsb = ttk.Scrollbar(container, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16,0), pady=12)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=12)

    def _on_rank_right_click(self, event):
        """按键排行右键菜单"""
        try:
            row = self.tree.identify_row(event.y)
            if not row:
                return
            self.tree.selection_set(row)
            self.tree.focus(row)
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="清除该按键今日次数", command=self.clear_today_key)
            menu.add_command(label="刷新统计", command=lambda: self.refresh_stats(full=True))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        except Exception as e:
            log.debug('右键菜单异常: %s', e)

    def clear_today_key(self, key_name: Optional[str] = None):
        """清除今日指定按键的次数（修复长按自动重复导致的今日计数虚高）

        在后台线程执行删除，避免阻塞 GUI；完成后回到主线程刷新统计。
        """
        import database
        if not key_name:
            sel = self.tree.selection()
            if sel:
                values = self.tree.item(sel[0], 'values')
                if len(values) > 1:
                    key_name = str(values[1])
        if not key_name:
            key_name = simpledialog.askstring(
                "清除今日按键",
                "请输入要清除今日次数的按键名：\n例如 A、空格、回车、Esc",
                parent=self.root)
        if not key_name:
            return
        key_name = key_name.strip()
        if not key_name:
            return
        if not messagebox.askyesno(
                "确认清除",
                f"确定清除今日按键 [{key_name}] 的所有次数吗？\n"
                f"此操作仅影响今天的数据，不可撤销。"):
            return

        try:
            self.root.config(cursor="watch")
        except Exception:
            pass

        def _do_clear():
            try:
                deleted = database.delete_key_today(key_name)
                import stats
                stats.reset_cpm()
                self.root.after(0, lambda: self._on_key_cleared(key_name, deleted))
            except Exception as e:
                log.error('清除今日按键失败: %s', e, exc_info=True)
                self.root.after(0, lambda: self._on_key_clear_error(str(e)))

        threading.Thread(target=_do_clear, name='clear-key', daemon=True).start()

    def _on_key_cleared(self, key_name: str, deleted: int):
        try:
            self.root.config(cursor="")
        except Exception:
            pass
        messagebox.showinfo("清除完成",
            f"已清除今日按键 [{key_name}] 的 {deleted:,} 条记录",
            parent=self.root)
        self.refresh_stats(full=True)

    def _on_key_clear_error(self, err: str):
        try:
            self.root.config(cursor="")
        except Exception:
            pass
        messagebox.showerror("清除失败", err, parent=self.root)

    def _build_group_view(self, parent):
        """按键分组统计"""
        self.group_view = ttk.Frame(parent, style='Card.TFrame')

        container = self.group_view
        columns = ('group', 'count', 'percent')
        self.group_tree = ttk.Treeview(container, columns=columns,
                                       show='headings', selectmode='browse',
                                       height=8)
        self.group_tree.heading('group', text='分组')
        self.group_tree.heading('count', text='次数')
        self.group_tree.heading('percent', text='占比')
        self.group_tree.column('group', width=200, anchor='center')
        self.group_tree.column('count', width=150, anchor='e')
        self.group_tree.column('percent', width=120, anchor='center')

        theme = self._theme_colors
        self.group_tree.tag_configure('even', background=theme['tree_alt'])
        self.group_tree.tag_configure('odd', background=theme['tree_bg'])
        self.group_tree.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

    def _build_trend_view(self, parent):
        """趋势图 - 用 Tkinter Canvas 手绘"""
        self.trend_view = ttk.Frame(parent, style='Card.TFrame')
        container = self.trend_view

        # 控制栏
        ctrl = ttk.Frame(container, style='Card.TFrame')
        ctrl.pack(fill=tk.X, pady=(12, 8), padx=16)
        ttk.Label(ctrl, text="趋势范围：", style='Stat.TLabel').pack(side=tk.LEFT)
        self.trend_var = tk.StringVar(value="7")
        for text, val in [("近7天", "7"), ("近30天", "30")]:
            ttk.Radiobutton(ctrl, text=text, value=val,
                            variable=self.trend_var,
                            command=self.refresh_trend).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(ctrl, text="刷新趋势",
                   command=self.refresh_trend).pack(side=tk.LEFT, padx=(16, 0))

        # Canvas 容器
        self.trend_container = ttk.Frame(container, style='Card.TFrame')
        self.trend_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        self._trend_canvas_widget = None

    def _build_hourly_view(self, parent):
        """小时分布图 - 显示今日每小时的按键数"""
        self.hourly_view = ttk.Frame(parent, style='Card.TFrame')
        container = self.hourly_view

        ctrl = ttk.Frame(container, style='Card.TFrame')
        ctrl.pack(fill=tk.X, pady=(12, 8), padx=16)
        ttk.Label(ctrl, text="每小时活跃分布（今日）", style='Stat.TLabel').pack(side=tk.LEFT)
        ttk.Button(ctrl, text="刷新",
                   command=self._refresh_hourly_chart).pack(side=tk.LEFT, padx=(16, 0))

        self.hourly_container = ttk.Frame(container, style='Card.TFrame')
        self.hourly_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        self._hourly_canvas_widget = None

    def _build_weekday_view(self, parent):
        """星期分布图 - 显示近30天按星期几的按键数"""
        self.weekday_view = ttk.Frame(parent, style='Card.TFrame')
        container = self.weekday_view

        ctrl = ttk.Frame(container, style='Card.TFrame')
        ctrl.pack(fill=tk.X, pady=(12, 8), padx=16)
        ttk.Label(ctrl, text="星期活跃分布（近30天）", style='Stat.TLabel').pack(side=tk.LEFT)
        ttk.Button(ctrl, text="刷新",
                   command=self._refresh_weekday_chart).pack(side=tk.LEFT, padx=(16, 0))

        self.weekday_container = ttk.Frame(container, style='Card.TFrame')
        self.weekday_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        self._weekday_canvas_widget = None



    # ---------- 插件管理视图 ----------
    def _build_plugins_view(self, parent):
        """插件管理视图（v1.0：可加载/卸载/重载/删除/编辑/查看视图，支持热加载）"""
        self.plugins_view = ttk.Frame(parent, style='Card.TFrame')
        container = self.plugins_view

        # 控制栏
        ctrl = ttk.Frame(container, style='Card.TFrame')
        ctrl.pack(fill=tk.X, pady=(12, 8), padx=16)
        ttk.Label(ctrl, text="插件管理", style='Stat.TLabel').pack(side=tk.LEFT)
        # 醒目的「打开窗口」按钮：在独立弹窗中打开选中插件的界面
        ttk.Button(ctrl, text="打开窗口", width=10,
                   command=self._open_plugin_window).pack(side=tk.LEFT, padx=(16, 0))
        ttk.Button(ctrl, text="刷新", width=6,
                   command=self._refresh_plugins).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(ctrl, text="新建插件", width=8,
                   command=self._create_plugin).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(ctrl, text="加载", width=5,
                   command=lambda: self._plugin_action('load')).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(ctrl, text="卸载", width=5,
                   command=lambda: self._plugin_action('unload')).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(ctrl, text="热重载", width=6,
                   command=lambda: self._plugin_action('reload')).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(ctrl, text="编辑", width=5,
                   command=lambda: self._plugin_action('edit')).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Button(ctrl, text="删除", width=5,
                   style='Danger.TButton',
                   command=lambda: self._plugin_action('delete')).pack(side=tk.LEFT, padx=(4, 0))

        # 热加载开关
        hot_frame = ttk.Frame(container, style='Card.TFrame')
        hot_frame.pack(fill=tk.X, padx=16, pady=(0, 8))
        self._hot_reload_var = tk.BooleanVar(value=False)
        def toggle_hot_reload():
            import plugins
            manager = plugins.get_plugin_manager()
            if self._hot_reload_var.get():
                manager.enable_hot_reload(on_change=self._on_plugin_hot_reloaded)
                messagebox.showinfo("热加载", "已开启：修改/新增插件文件后会自动重载")
            else:
                manager.disable_hot_reload()
        ttk.Checkbutton(hot_frame, text="启用插件热加载（自动检测文件变更并重载）",
                        variable=self._hot_reload_var,
                        command=toggle_hot_reload).pack(side=tk.LEFT)
        ttk.Label(hot_frame, text="    选中插件后点击下方按钮操作",
                  style='Subtitle.TLabel').pack(side=tk.LEFT, padx=(16, 0))

        # 说明
        ttk.Label(container,
                  text="插件独立运行，不影响主功能。插件出错不会影响其他插件和主程序。\n"
                       "► 选中插件后点击 [打开窗口] 按钮，或直接双击插件名，即可在独立弹窗中打开其界面。",
                  style='Subtitle.TLabel', justify='left').pack(fill=tk.X, padx=16, pady=(0, 8))

        # 上半部分：插件列表
        tree_frame = ttk.Frame(container, style='Card.TFrame')
        tree_frame.pack(fill=tk.BOTH, expand=False, padx=16, pady=(0, 8))

        columns = ('name', 'desc', 'version', 'status', 'error')
        self.plugin_tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                         selectmode='browse', height=8)
        self.plugin_tree.heading('name', text='插件名称')
        self.plugin_tree.heading('desc', text='描述')
        self.plugin_tree.heading('version', text='版本')
        self.plugin_tree.heading('status', text='状态')
        self.plugin_tree.heading('error', text='错误信息')
        self.plugin_tree.column('name', width=140)
        self.plugin_tree.column('desc', width=240)
        self.plugin_tree.column('version', width=60, anchor='center')
        self.plugin_tree.column('status', width=80, anchor='center')
        self.plugin_tree.column('error', width=220)
        # 双击插件：在独立弹窗中打开其界面
        self.plugin_tree.bind('<Double-1>', lambda e: self._open_plugin_window())

        theme = self._theme_colors
        self.plugin_tree.tag_configure('even', background=theme['tree_alt'])
        self.plugin_tree.tag_configure('odd', background=theme['tree_bg'])
        self.plugin_tree.tag_configure('loaded', foreground=theme['success'])
        self.plugin_tree.tag_configure('error', foreground=theme['danger'])
        self.plugin_tree.tag_configure('unloaded', foreground=theme['muted'])

        vsb = ttk.Scrollbar(tree_frame, orient='vertical', command=self.plugin_tree.yview)
        self.plugin_tree.configure(yscrollcommand=vsb.set)
        self.plugin_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

    def _refresh_plugins(self):
        """刷新插件列表"""
        try:
            import plugins
            manager = plugins.get_plugin_manager()
            manager.load_all()
            plugin_list = manager.get_all_plugins()

            for item in self.plugin_tree.get_children():
                self.plugin_tree.delete(item)

            for i, p in enumerate(plugin_list):
                if p.error:
                    tag = 'error'
                    status = '错误'
                elif p.loaded:
                    tag = 'loaded'
                    status = '已加载'
                else:
                    tag = 'unloaded'
                    status = '已卸载'
                self.plugin_tree.insert('', tk.END, values=(
                    p.name, p.desc or '(无描述)', p.version or '-', status, p.error
                ), tags=(tag,))
        except Exception as e:
            log.error('刷新插件列表失败: %s', e, exc_info=True)

    def _on_plugin_hot_reloaded(self, plugin_name: str):
        """热加载回调：UI 线程刷新"""
        try:
            if hasattr(self, 'plugin_tree'):
                self.root.after(0, self._refresh_plugins)
        except Exception as e:
            log.debug('热加载 UI 刷新异常: %s', e)

    def _get_selected_plugin_name(self) -> Optional[str]:
        """获取当前在插件列表中选中的插件名称"""
        sel = self.plugin_tree.selection()
        if not sel:
            return None
        item = sel[0]
        values = self.plugin_tree.item(item, 'values')
        if not values:
            return None
        return values[0]

    def _open_plugin_window(self):
        """在独立弹窗（Toplevel）中打开选中插件的界面。

        解决用户反馈：点击插件管理中的「记账本维护」没有任何弹窗/界面出现。
        原因：旧版只在下方内嵌区域显示视图，用户不易察觉。
        本方法改为弹出一个独立窗口，更符合用户对「弹窗」的预期。
        """
        plugin_name = self._get_selected_plugin_name()
        if not plugin_name:
            messagebox.showwarning("提示", "请先在上方列表中选择一个插件")
            return

        import plugins
        manager = plugins.get_plugin_manager()
        info = manager.get_plugin(plugin_name)

        if not info:
            messagebox.showerror("错误", f"找不到插件 {plugin_name}")
            return

        # 若插件未加载，自动尝试加载一次
        if not info.loaded and info.file_path:
            manager.load_plugin(info.file_path)
            self._refresh_plugins()
            info = manager.get_plugin(plugin_name)
            if not info or not info.loaded:
                messagebox.showerror("加载失败",
                    f"插件 {plugin_name} 加载失败：\n{info.error if info else '未知错误'}")
                return

        if info.error:
            messagebox.showerror("错误",
                f"插件 {plugin_name} 加载错误：\n{info.error}")
            return

        if not info.has_view:
            messagebox.showinfo("提示",
                f"插件 {plugin_name} 未提供界面（get_view），无可打开的窗口")
            return

        # 若该插件已有打开的窗口，直接前置并返回，避免重复弹出
        existing = getattr(self, '_plugin_windows', {}).get(plugin_name)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass

        # 创建独立弹窗
        top = tk.Toplevel(self.root)
        top.title(f"{plugin_name} - {info.desc or 'FocusFlow 插件'}")
        top.minsize(640, 440)
        top.transient(self.root)

        # 恢复该插件窗口上次的尺寸与位置（独立记忆）
        _state_section = f'plugin:{plugin_name}'
        saved = get_ui_state(_state_section, 'geometry')
        if saved and 'x' in saved:
            top.geometry(saved)
        else:
            top.geometry("900x600")
            self._center_window(top)

        # 顶部信息栏
        info_bar = ttk.Frame(top, style='Card.TFrame')
        info_bar.pack(fill=tk.X, padx=10, pady=(10, 6))
        ttk.Label(info_bar, text=f"插件：{plugin_name}",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Label(info_bar,
                  text=f"  v{info.version}  作者：{info.author or '未知'}",
                  style='Subtitle.TLabel').pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(info_bar, text="关闭窗口",
                   command=top.destroy).pack(side=tk.RIGHT)

        # 内容区
        content = ttk.Frame(top)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        try:
            view = info.module.get_view(content)
            if view:
                view.pack(fill=tk.BOTH, expand=True)
            else:
                ttk.Label(content, text="插件未返回有效视图",
                          foreground='#d13438').pack(pady=40)
        except Exception as e:
            for child in content.winfo_children():
                child.destroy()
            ttk.Label(content,
                      text=f"插件 {plugin_name} 视图加载失败：\n{e}",
                      foreground='#d13438', justify='left').pack(pady=40)
            log.error('插件窗口视图加载失败 %s: %s', plugin_name, e, exc_info=True)

        # 注册窗口引用，便于复用与关闭时清理
        if not hasattr(self, '_plugin_windows'):
            self._plugin_windows = {}
        self._plugin_windows[plugin_name] = top

        # 窗口关闭时清理引用并保存尺寸
        def _on_close():
            try:
                if top.state() == 'normal':
                    set_ui_state(_state_section, 'geometry', top.geometry())
            except Exception:
                pass
            try:
                self._plugin_windows.pop(plugin_name, None)
            except Exception:
                pass
            top.destroy()
        top.protocol("WM_DELETE_WINDOW", _on_close)
        # 拖动/缩放后（防抖）保存尺寸
        try:
            top.bind('<Configure>',
                     lambda e: self._schedule_plugin_geo_save(_state_section, top))
        except Exception:
            pass

    def _plugin_action(self, action: str):
        """执行插件操作：load/unload/reload/edit/delete"""
        sel = self.plugin_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个插件")
            return
        item = sel[0]
        values = self.plugin_tree.item(item, 'values')
        plugin_name = values[0]
        import plugins
        manager = plugins.get_plugin_manager()
        info = manager.get_plugin(plugin_name)
        if not info:
            messagebox.showerror("错误", f"找不到插件 {plugin_name}")
            return

        if action == 'load':
            if info.loaded:
                messagebox.showinfo("提示", f"插件 {plugin_name} 已加载")
                return
            manager.load_plugin(info.file_path)
            self._refresh_plugins()
            if info.error:
                messagebox.showerror("加载失败", info.error)
            else:
                messagebox.showinfo("成功", f"插件 {plugin_name} 已加载")
        elif action == 'unload':
            if not info.loaded:
                messagebox.showinfo("提示", f"插件 {plugin_name} 已卸载")
                return
            manager.unload_plugin(plugin_name)
            self._refresh_plugins()
            messagebox.showinfo("成功", f"插件 {plugin_name} 已卸载")
        elif action == 'reload':
            ok = manager.reload_plugin(plugin_name)
            self._refresh_plugins()
            if ok:
                messagebox.showinfo("成功", f"插件 {plugin_name} 已重载")
            else:
                messagebox.showerror("失败", f"插件 {plugin_name} 重载失败")
        elif action == 'edit':
            ok, msg = manager.edit_plugin(plugin_name)
            if ok:
                messagebox.showinfo("提示", f"已用系统默认编辑器打开插件文件\n\n{msg}\n\n"
                                            "修改保存后，如已开启热加载会自动重载；"
                                            "如未开启，请点击 [热重载] 按钮")
            else:
                messagebox.showerror("失败", msg)
        elif action == 'delete':
            if not messagebox.askyesno("确认删除",
                f"确定删除插件 {plugin_name} 吗？\n这将同时卸载插件并删除 .py 文件，不可恢复！"):
                return
            ok, msg = manager.delete_plugin(plugin_name)
            self._refresh_plugins()
            if ok:
                messagebox.showinfo("成功", msg)
            else:
                messagebox.showerror("失败", msg)

    def _create_plugin(self):
        """创建新插件"""
        name = simpledialog.askstring("新建插件", "请输入插件名称（英文）：",
                                        parent=self.root)
        if not name:
            return
        desc = simpledialog.askstring("新建插件", "请输入插件描述：",
                                       parent=self.root) or ''
        import plugins
        file_path = plugins.get_plugin_manager().create_plugin_template(name, desc)
        messagebox.showinfo("成功",
            f"插件模板已创建：\n{file_path}\n\n"
            "请编辑该文件实现功能，保存后会自动加载（如已开启热加载）；\n"
            "未开启热加载时，请点击 [刷新] 或 [加载] 按钮。")
        self._refresh_plugins()

    def _show_view(self, view_name: str):
        """切换显示的视图"""
        # 视图名 -> Frame 映射
        view_map = {
            "按键排行": self.rank_view,
            "分组统计": self.group_view,
            "趋势图": self.trend_view,
            "小时分布": self.hourly_view,
            "星期分布": self.weekday_view,
            "插件管理": self.plugins_view,
        }
        # 隐藏所有视图
        for v in view_map.values():
            v.pack_forget()
        # 显示选中的视图
        frame = view_map.get(view_name)
        if frame:
            frame.pack(fill=tk.BOTH, expand=True)
            self._current_view = view_name
            # 刷新当前视图
            self._refresh_current_view()

    def _on_view_changed(self, event=None):
        """下拉框选择变化"""
        view_name = self.view_var.get()
        self._show_view(view_name)

    def _refresh_current_view(self):
        """刷新当前视图"""
        if not self._current_view:
            return
        try:
            if self._current_view == "按键排行":
                self.refresh_stats(full=True)
            elif self._current_view == "分组统计":
                self.refresh_stats(full=True)
            elif self._current_view == "趋势图":
                self.refresh_trend()
            elif self._current_view == "小时分布":
                self._refresh_hourly_chart()
            elif self._current_view == "星期分布":
                self._refresh_weekday_chart()
            elif self._current_view == "插件管理":
                self._refresh_plugins()
        except Exception as e:
            log.debug('刷新视图异常: %s', e)

    # ---------- 事件处理 ----------
    def _on_date_entry_focus(self, event):
        if self.date_entry.get() == "YYYY-MM-DD":
            self.date_entry.delete(0, tk.END)

    def on_period_change(self, days: int):
        # -1 表示今日
        if days == -1:
            self.current_days = 1
            self.current_date = date.today()
        else:
            self.current_days = days if days > 0 else None
            self.current_date = None
        self.current_year = None
        self.year_var.set("当前")
        self.refresh_stats(full=True)

    def on_year_change(self, event=None):
        val = self.year_var.get()
        if val == "当前":
            self.current_year = None
        else:
            try:
                self.current_year = int(val)
            except ValueError:
                self.current_year = None
        self.current_date = None
        self.refresh_stats(full=True)

    def on_date_query(self):
        text = self.date_entry.get().strip()
        if not text or text == "YYYY-MM-DD":
            return
        try:
            qdate = datetime.strptime(text, "%Y-%m-%d").date()
            self.current_date = qdate
            self.current_days = None
            self.current_year = None
            self.year_var.set("当前")
            self.period_var.set(0)
            self.refresh_stats(full=True)
        except ValueError:
            messagebox.showerror("格式错误", "日期格式应为 YYYY-MM-DD")

    def on_today_click(self):
        self.current_days = 1
        self.current_date = date.today()
        self.current_year = None
        self.year_var.set("当前")
        self.refresh_stats(full=True)

    # ---------- 开机自启 ----------
    def toggle_autostart(self):
        from autostart import is_autostart_enabled, enable_autostart, disable_autostart
        if is_autostart_enabled():
            ok, msg = disable_autostart()
        else:
            ok, msg = enable_autostart()
        messagebox.showinfo("开机自启", msg)
        self.update_autostart_ui()

    # ---------- 设置 ----------
    def _open_settings(self):
        """打开设置对话框：主题 / 开机自启 / 暂停记录 / 悬浮窗 / 启动入托盘 / 数据操作"""
        top = tk.Toplevel(self.root)
        top.title("设置")
        top.resizable(False, False)
        top.transient(self.root)
        # 先隐藏窗口，构建完成并居中后再显示，避免从左上角"闪现"到中间的视觉跳动
        top.withdraw()

        container = ttk.Frame(top, padding=18)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="设置", style='Title.TLabel').pack(anchor='w', pady=(0, 10))

        # ===== 常规选项 =====
        ttk.Label(container, text="常规", style='Muted.TLabel').pack(anchor='w', pady=(0, 2))

        # 暗色模式
        dark_var = tk.BooleanVar(value=self._theme == 'dark')

        def _toggle_dark():
            self.toggle_theme()
            dark_var.set(self._theme == 'dark')

        ttk.Checkbutton(container, text="暗色模式", variable=dark_var,
                        command=_toggle_dark).pack(anchor='w', pady=3)

        # 开机自启（勾选=启用，取消勾选=关闭）
        from autostart import is_autostart_enabled, enable_autostart, disable_autostart
        auto_var = tk.BooleanVar(value=is_autostart_enabled())

        def _toggle_auto():
            # tkinter 会先切换 auto_var 再调用 command，因此这里直接按新勾选状态执行
            if auto_var.get():
                ok, msg = enable_autostart()
            else:
                ok, msg = disable_autostart()
            if not ok:
                messagebox.showerror("开机自启", msg, parent=top)
                auto_var.set(is_autostart_enabled())
            self.update_autostart_ui()

        ttk.Checkbutton(container, text="开机自启", variable=auto_var,
                        command=_toggle_auto).pack(anchor='w', pady=3)

        # 暂停记录
        from listener import get_listener
        pause_var = tk.BooleanVar(value=get_listener().is_paused())

        def _toggle_pause():
            ns = get_listener().toggle_pause()
            self.on_pause_changed(ns)
            pause_var.set(ns)

        ttk.Checkbutton(container, text="暂停记录", variable=pause_var,
                        command=_toggle_pause).pack(anchor='w', pady=3)

        # 显示悬浮窗
        from floating_window import get_floating
        float_var = tk.BooleanVar(value=get_floating().is_visible())

        def _toggle_float():
            self.toggle_floating()
            float_var.set(get_floating().is_visible())

        ttk.Checkbutton(container, text="显示悬浮窗", variable=float_var,
                        command=_toggle_float).pack(anchor='w', pady=3)

        # 启动时最小化到托盘（存入独立状态文件，更新软件后保留）
        from config import get_start_to_tray, set_start_to_tray
        tray_var = tk.BooleanVar(value=get_start_to_tray())

        def _toggle_tray():
            set_start_to_tray(tray_var.get())

        ttk.Checkbutton(container, text="启动时直接进入托盘（不显示主窗口）",
                        variable=tray_var,
                        command=_toggle_tray).pack(anchor='w', pady=3)

        # ===== 全局热键 =====
        ttk.Label(container, text="全局热键", style='Muted.TLabel').pack(anchor='w', pady=(8, 2))

        hotkey_frame = ttk.Frame(container)
        hotkey_frame.pack(anchor='w', pady=3)

        # 启用/关闭 热键（Ctrl+Shift+F 显示/隐藏主窗口）
        hotkey_enabled = tk.BooleanVar(value=config.getbool('hotkey', 'enabled', False))

        def _sync_hotkey_state():
            """根据启用状态应用/取消热键"""
            try:
                from hotkey import get_hotkey_manager, stop_hotkey
                if hotkey_enabled.get():
                    hotkey_combo.state(['!disabled'])
                    # 先停止旧的，再注册新的
                    stop_hotkey()
                    from hotkey import get_hotkey_manager as _ghm
                    _ghm().register(hotkey_combo.get(), self.toggle_window_visibility)
                else:
                    stop_hotkey()
                    hotkey_combo.state(['disabled'])
            except Exception as e:
                log.debug('热键切换异常: %s', e)

        def _toggle_hotkey():
            config.set('hotkey', 'enabled', 'true' if hotkey_enabled.get() else 'false')
            _sync_hotkey_state()

        ttk.Checkbutton(hotkey_frame, text="启用全局热键（显示/隐藏主窗口）",
                        variable=hotkey_enabled,
                        command=_toggle_hotkey).pack(side=tk.LEFT)

        # 热键组合选择
        hotkey_var = tk.StringVar(value=config.get('hotkey', 'toggle_window', 'ctrl+shift+f'))
        hotkey_combo = ttk.Combobox(hotkey_frame, textvariable=hotkey_var,
                                    width=16, state='readonly',
                                    values=['ctrl+shift+f', 'ctrl+alt+f', 'ctrl+alt+z',
                                            'ctrl+shift+h', 'alt+shift+f'])
        hotkey_combo.pack(side=tk.LEFT, padx=(12, 0))

        def _on_hotkey_changed(event=None):
            try:
                config.set('hotkey', 'toggle_window', hotkey_combo.get())
                if hotkey_enabled.get():
                    _sync_hotkey_state()
            except Exception as e:
                log.debug('热键设置变更异常: %s', e)

        hotkey_combo.bind('<<ComboboxSelected>>', _on_hotkey_changed)

        # 初始化组合框状态
        hotkey_combo.state(['disabled'] if not hotkey_enabled.get() else ['!disabled'])

        # ===== 数据操作 =====
        ttk.Separator(container, orient='horizontal').pack(fill=tk.X, pady=10)
        ttk.Label(container, text="数据操作", style='Muted.TLabel').pack(anchor='w', pady=(0, 6))

        op_grid = ttk.Frame(container)
        op_grid.pack(fill=tk.X)
        ttk.Button(op_grid, text="清理数据", width=14,
                   command=self.on_cleanup).grid(row=0, column=0, padx=(0, 8), pady=3, sticky='w')
        ttk.Button(op_grid, text="压缩数据库", width=14,
                   command=self.on_vacuum).grid(row=0, column=1, pady=3, sticky='w')
        ttk.Button(op_grid, text="清除今日按键", width=14,
                   command=self.clear_today_key).grid(row=1, column=0, padx=(0, 8), pady=3, sticky='w')
        ttk.Button(op_grid, text="导出数据", width=14,
                   command=self.on_export).grid(row=1, column=1, pady=3, sticky='w')
        ttk.Button(op_grid, text="隐藏到托盘", width=14,
                   command=lambda: (self.hide_window(), top.destroy())
                   ).grid(row=2, column=0, pady=(3, 0), sticky='w')

        ttk.Separator(container, orient='horizontal').pack(fill=tk.X, pady=10)

        ttk.Label(container,
                  text="提示：退出程序请右键托盘图标选择「退出程序」。",
                  style='Subtitle.TLabel', justify='left').pack(anchor='w', pady=(0, 10))

        # ===== 版本信息 =====
        ttk.Label(container,
                  text=f"FocusFlow v{APP_VERSION} · 更新日期 {APP_UPDATE_DATE}",
                  style='Subtitle.TLabel', foreground=self._theme_colors['muted']).pack(anchor='w', pady=(0, 8))

        ttk.Button(container, text="关闭", width=8,
                   command=top.destroy).pack(anchor='e')

        # 按内容自适应尺寸并居中（隐藏状态下调整，避免可见闪烁）
        try:
            top.update_idletasks()
            w = max(360, top.winfo_reqwidth())
            h = top.winfo_reqheight()
            top.geometry(f'{w}x{h}')
        except Exception:
            top.geometry("400x560")
        self._center_window(top)
        # 居中完成后一次性显示（避免可见闪烁）
        top.deiconify()
        # 显示后再设置模态，确保 grab 生效
        try:
            top.grab_set()
        except Exception:
            pass

    def update_autostart_ui(self):
        # 开机自启已移入"设置"对话框，此处保留空实现以兼容旧调用
        if not (hasattr(self, 'autostart_btn') and self.autostart_btn.winfo_exists()):
            return
        from autostart import is_autostart_enabled
        theme = self._theme_colors
        if is_autostart_enabled():
            self.autostart_btn.config(text="取消开机自启")
            self.autostart_status.config(text="已开启",
                                         foreground=theme['success'])
        else:
            self.autostart_btn.config(text="启用开机自启")
            self.autostart_status.config(text="未开启",
                                         foreground=theme['muted'])

    # ---------- 暂停 ----------
    def toggle_pause(self):
        from listener import get_listener
        listener = get_listener()
        new_state = listener.toggle_pause()
        self.on_pause_changed(new_state)

    def on_pause_changed(self, paused: bool):
        if paused:
            self.pause_status_label.config(text="[已暂停]",
                                           foreground=self._theme_colors['danger'])
        else:
            self.pause_status_label.config(text="")

    # ---------- 数据清理 ----------
    def on_cleanup(self):
        days = simpledialog.askinteger("清理数据",
            "请输入保留天数（删除该天数之前的数据）：\n例如输入 30 表示保留最近 30 天",
            parent=self.root, minvalue=1, maxvalue=3650)
        if days is None:
            return
        if not messagebox.askyesno("确认清理",
            f"确定要删除 {days} 天前的所有记录吗？\n此操作不可撤销。"):
            return
        try:
            import database
            deleted = database.cleanup_old_data(days)
            database.vacuum()
            messagebox.showinfo("清理完成", f"已删除 {deleted:,} 条记录\n数据库已压缩")
            self.refresh_stats(full=True)
        except Exception as e:
            messagebox.showerror("清理失败", str(e))

    def on_vacuum(self):
        if not messagebox.askyesno("压缩数据库", "压缩数据库可能需要几秒钟。\n继续吗？"):
            return
        try:
            import database
            database.vacuum()
            messagebox.showinfo("压缩完成", "数据库压缩完成")
        except Exception as e:
            messagebox.showerror("压缩失败", str(e))

    # ---------- 导出 ----------
    def on_export(self):
        filepath = filedialog.asksaveasfilename(
            parent=self.root,
            defaultextension=".csv",
            initialfile=f"focusflow_{datetime.now().strftime('%Y%m%d')}",
            filetypes=[("CSV 文件", "*.csv"), ("HTML 文件", "*.html"), ("所有文件", "*.*")]
        )
        if not filepath:
            return
        ext = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else 'csv'
        from exporter import export_csv, export_html
        try:
            if ext == 'html':
                ok = export_html(self.current_days, filepath, year=self.current_year)
            else:
                ok = export_csv(self.current_days, filepath, year=self.current_year)
            if ok:
                messagebox.showinfo("导出成功", f"文件已保存到：\n{filepath}")
            else:
                messagebox.showerror("导出失败", "请查看日志获取详细信息")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    # ---------- 小憩与护眼 ----------
    def show_rest_reminder(self, count: int = 0):
        """弹出护眼提醒对话框（20 秒倒计时，可选"休息一下 / 继续工作"）

        由护眼提醒模块在检测到高强度输入后调用（已通过 root.after 调度到主线程）。
        """
        try:
            # 避免重复弹窗：已有提醒窗口则前置并返回
            existing = getattr(self, '_rest_reminder_window', None)
            if existing is not None:
                try:
                    if existing.winfo_exists():
                        existing.lift()
                        existing.focus_force()
                        return
                except Exception:
                    pass

            rest_seconds = max(5, config.getint('rest', 'rest_seconds', 20))
            window_minutes = config.getint('rest', 'window_minutes', 30)

            top = tk.Toplevel(self.root)
            top.title("小憩与护眼")
            top.resizable(False, False)
            top.attributes('-topmost', True)
            top.transient(self.root)
            self._rest_reminder_window = top

            container = ttk.Frame(top, padding=24)
            container.pack(fill=tk.BOTH, expand=True)

            ttk.Label(container, text="眼睛需要休息啦",
                      font=("Segoe UI", 16, "bold"),
                      foreground=self._theme_colors['accent']).pack(anchor='w')

            msg = (f"检测到你在 {window_minutes} 分钟内按键 {count:,} 次，\n"
                   "高强度输入已持续一段时间，建议休息片刻保护眼睛。")
            ttk.Label(container, text=msg, justify='left',
                      font=("Segoe UI", 11)).pack(anchor='w', pady=(10, 6))

            countdown_var = tk.StringVar(value=f"{rest_seconds} 秒后自动继续")
            ttk.Label(container, textvariable=countdown_var,
                      style='Subtitle.TLabel').pack(anchor='w', pady=(0, 12))

            btn_frame = ttk.Frame(container)
            btn_frame.pack(anchor='w')

            state = {'countdown': rest_seconds, 'closed': False}

            def _on_break():
                state['closed'] = True
                _close()
                # 休息一下：若番茄钟在工作，自动切到休息
                try:
                    from pomodoro import get_pomodoro
                    timer = get_pomodoro()
                    if timer.get_state() == 'work':
                        timer.start_break()
                except Exception as e:
                    log.debug('切换番茄钟休息失败: %s', e)
                self._reset_rest_reminder()

            def _on_continue():
                state['closed'] = True
                _close()
                self._reset_rest_reminder()

            def _close():
                try:
                    top.destroy()
                except Exception:
                    pass
                try:
                    self._rest_reminder_window = None
                except Exception:
                    pass

            def _tick_countdown():
                if state['closed']:
                    return
                state['countdown'] -= 1
                if state['countdown'] <= 0:
                    state['closed'] = True
                    _close()
                    self._reset_rest_reminder()
                    return
                countdown_var.set(f"{state['countdown']} 秒后自动继续")
                top.after(1000, _tick_countdown)

            ttk.Button(btn_frame, text="休息一下", style='Accent.TButton',
                       command=_on_break).pack(side=tk.LEFT)
            ttk.Button(btn_frame, text="继续工作", width=10,
                       command=_on_continue).pack(side=tk.LEFT, padx=(10, 0))

            top.protocol("WM_DELETE_WINDOW", _on_continue)
            try:
                top.update_idletasks()
                w = top.winfo_reqwidth()
                h = top.winfo_reqheight()
                sw = top.winfo_screenwidth()
                sh = top.winfo_screenheight()
                top.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
            except Exception:
                pass

            top.after(1000, _tick_countdown)
        except Exception as e:
            log.error('显示护眼提醒失败: %s', e, exc_info=True)

    def _reset_rest_reminder(self):
        """提醒关闭后重置护眼计数，避免立刻再次触发"""
        try:
            from rest_reminder import get_rest_reminder
            get_rest_reminder().reset()
        except Exception:
            pass

    # ---------- 悬浮窗 ----------
    def toggle_floating(self):
        """切换悬浮窗显示/隐藏。

        v1.0：增加异常保护和日志，确保切换可靠。
        """
        try:
            from floating_window import get_floating
            floating = get_floating()
            if floating.is_visible():
                floating.hide()
                config.set('floating', 'enabled', 'false')
            else:
                ok = floating.show(self.root)
                if ok:
                    config.set('floating', 'enabled', 'true')
                else:
                    log.warning('悬浮窗显示失败')
        except Exception as e:
            log.error('切换悬浮窗异常: %s', e, exc_info=True)

    # ---------- 数据刷新 ----------
    def refresh_stats(self, full: bool = False):
        """刷新统计（纯键盘）

        优化：只更新当前可见的标签页，不重绘所有表格
        趋势图只在切换到趋势标签页或全量刷新时更新
        """
        try:
            import database
            # 更新年度下拉（轻量操作）
            self._update_year_combo()

            # 今日活跃 = 键盘按键数（v1.0：纯键盘）
            today = database.get_today_count()
            self.today_label.config(text=f"{today:,}")

            # 周期总数 + 按键排行（纯键盘统计）
            if self.current_date:
                total, key_stats = database.get_stats_by_date(
                    self.current_date)
            elif self.current_year:
                total, key_stats = database.get_stats(
                    None, year=self.current_year)
            else:
                total, key_stats = database.get_stats(self.current_days)
            self.total_label.config(text=f"{total:,}")

            if full:
                # 只更新当前视图
                cv = getattr(self, '_current_view', '按键排行')
                if cv == "按键排行":
                    self._refresh_rank_table(key_stats, total)
                elif cv == "分组统计":
                    self._refresh_group_table(key_stats, total)
                # 其他视图由各自的刷新按钮或 _refresh_current_view 处理
        except Exception as e:
            log.error('刷新统计失败: %s', e, exc_info=True)

    def _update_year_combo(self):
        import database
        years = database.get_available_years()
        values = ["当前"] + [str(y) for y in years]
        current = self.year_var.get()
        self.year_combo['values'] = values
        if current not in values:
            self.year_var.set("当前")

    def _refresh_rank_table(self, key_stats: Dict[str, int], total: int):
        for item in self.tree.get_children():
            self.tree.delete(item)
        rank = 0
        for key_name, count in sorted(key_stats.items(), key=lambda x: -x[1]):
            rank += 1
            percent = f"{count / total * 100:.2f}%" if total > 0 else "0.00%"
            tag = 'even' if rank % 2 == 0 else 'odd'
            self.tree.insert('', tk.END, values=(rank, key_name, f"{count:,}", percent),
                             tags=(tag,))

    def _refresh_group_table(self, key_stats: Dict[str, int], total: int):
        for item in self.group_tree.get_children():
            self.group_tree.delete(item)
        group_counts: Dict[str, int] = {g: 0 for g in KEY_GROUP_ORDER}
        for key_name, count in key_stats.items():
            g = classify_key(key_name)
            group_counts[g] = group_counts.get(g, 0) + count
        rank = 0
        for g in KEY_GROUP_ORDER:
            count = group_counts.get(g, 0)
            if count == 0:
                continue
            rank += 1
            percent = f"{count / total * 100:.2f}%" if total > 0 else "0.00%"
            tag = 'even' if rank % 2 == 0 else 'odd'
            self.group_tree.insert('', tk.END, values=(g, f"{count:,}", percent),
                                   tags=(tag,))

    def refresh_cpm(self):
        try:
            import stats
            cpm = stats.get_current_cpm()
            self.cpm_label.config(text=f"{cpm} 键/分")
        except Exception as e:
            log.debug('刷新 CPM 失败: %s', e)

    def refresh_trend(self):
        """刷新趋势图 - 用 Tkinter Canvas 手绘"""
        try:
            import database
            days = int(self.trend_var.get())
            daily_counts = database.get_daily_counts(days)
            self._trend_data = daily_counts
            self._draw_trend_chart(daily_counts)
        except Exception as e:
            log.error('刷新趋势图失败: %s', e, exc_info=True)

    def _draw_trend_chart(self, daily_counts):
        """用 Tkinter Canvas 绘制趋势图"""
        # 清除旧 canvas
        if self._trend_canvas_widget:
            self._trend_canvas_widget.destroy()
            self._trend_canvas_widget = None

        theme = self._theme_colors
        # 创建 Canvas
        canvas = tk.Canvas(self.trend_container,
                           bg=theme['card_bg'],
                           highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._trend_canvas_widget = canvas

        # 等待容器尺寸确定后绘制
        self.trend_container.update_idletasks()
        w = self.trend_container.winfo_width()
        h = self.trend_container.winfo_height()
        if w <= 1 or h <= 1:
            # 容器还没渲染，延迟重试
            self.root.after(100, lambda: self._draw_trend_chart(daily_counts))
            return

        # 边距
        margin_left = 50
        margin_right = 20
        margin_top = 30
        margin_bottom = 50
        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        # 标题
        days = int(self.trend_var.get())
        canvas.create_text(w // 2, 12, text=f"近 {days} 天活跃趋势",
                           fill=theme['fg'], font=('Segoe UI', 11, 'bold'))

        if not daily_counts:
            canvas.create_text(w // 2, h // 2, text="暂无数据",
                               fill=theme['muted'], font=('Segoe UI', 14))
            return

        # 数据范围
        max_count = max(c for _, c in daily_counts) if daily_counts else 1
        if max_count == 0:
            max_count = 1

        # 绘制 Y 轴刻度
        for i in range(5):
            y = margin_top + plot_h * (1 - i / 4)
            value = int(max_count * i / 4)
            canvas.create_line(margin_left, y, w - margin_right, y,
                               fill=theme['border'], dash=(2, 2))
            canvas.create_text(margin_left - 8, y, text=str(value),
                               fill=theme['muted'], font=('Segoe UI', 8), anchor='e')

        # 绘制折线和填充
        points = []
        n = len(daily_counts)
        for i, (d, c) in enumerate(daily_counts):
            x = margin_left + (plot_w * i / max(n - 1, 1)) if n > 1 else margin_left + plot_w / 2
            y = margin_top + plot_h * (1 - c / max_count)
            points.append((x, y))

        # 填充区域
        if len(points) >= 2:
            fill_points = [(margin_left, margin_top + plot_h)] + points + [(w - margin_right, margin_top + plot_h)]
            canvas.create_polygon(fill_points, fill=theme['accent'], outline='')

        # 折线
        if len(points) >= 2:
            for i in range(len(points) - 1):
                canvas.create_line(points[i][0], points[i][1],
                                   points[i+1][0], points[i+1][1],
                                   fill=theme['accent'], width=2)

        # 数据点
        for i, (x, y) in enumerate(points):
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                               fill=theme['accent'], outline='')

        # X 轴标签（每隔几个显示一个，避免重叠）
        label_step = max(1, n // 7)
        for i, (d, c) in enumerate(daily_counts):
            if i % label_step == 0 or i == n - 1:
                x = margin_left + (plot_w * i / max(n - 1, 1)) if n > 1 else margin_left + plot_w / 2
                # 只显示月-日
                short_date = d[5:] if len(d) >= 10 else d
                canvas.create_text(x, h - margin_bottom + 15, text=short_date,
                                   fill=theme['muted'], font=('Segoe UI', 8),
                                   angle=30)

        # 绑定 resize 事件，重绘
        def _on_resize(event):
            # 防抖：只处理最后一次
            if hasattr(self, '_resize_after_id'):
                self.root.after_cancel(self._resize_after_id)
            self._resize_after_id = self.root.after(200, lambda: self._draw_trend_chart(self._trend_data))

        canvas.bind('<Configure>', _on_resize)

    # ---------- 小时分布图 ----------
    def _refresh_hourly_chart(self):
        """刷新每小时分布图"""
        try:
            import database
            hourly = database.get_hourly_stats()
            self._draw_hourly_chart(hourly)
        except Exception as e:
            log.error('刷新小时分布失败: %s', e)

    def _draw_hourly_chart(self, hourly: list):
        """用 Canvas 绘制每小时柱状图"""
        if self._hourly_canvas_widget:
            self._hourly_canvas_widget.destroy()
            self._hourly_canvas_widget = None

        theme = self._theme_colors
        w = max(600, self.hourly_container.winfo_width() - 20)
        h = max(300, self.hourly_container.winfo_height() - 20)
        canvas = tk.Canvas(self.hourly_container, width=w, height=h,
                           bg=theme['card_bg'], highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._hourly_canvas_widget = canvas

        max_val = max(hourly) if hourly else 0
        if max_val == 0:
            canvas.create_text(w//2, h//2, text='暂无数据',
                             fill=theme['muted'], font=('Segoe UI', 14))
            return

        # 边距
        ml, mr, mt, mb = 50, 20, 30, 40
        chart_w = w - ml - mr
        chart_h = h - mt - mb
        bar_w = chart_w / 24 * 0.7
        gap = chart_w / 24 * 0.3

        # Y 轴刻度
        for i in range(5):
            y = mt + chart_h * (1 - i/4)
            val = int(max_val * i / 4)
            canvas.create_text(ml - 8, y, text=str(val), anchor='e',
                             fill=theme['muted'], font=('Segoe UI', 8))
            canvas.create_line(ml, y, ml + chart_w, y, fill=theme['border'])

        # 柱子
        for hour in range(24):
            val = hourly[hour]
            x = ml + hour * (bar_w + gap) + gap/2
            bar_h = (val / max_val) * chart_h if max_val > 0 else 0
            y = mt + chart_h - bar_h
            # 当前小时高亮
            now_hour = datetime.now().hour
            color = theme['accent'] if hour == now_hour else theme['accent_hover']
            canvas.create_rectangle(x, y, x + bar_w, mt + chart_h,
                                   fill=color, outline='')
            # 数值
            if val > 0:
                canvas.create_text(x + bar_w/2, y - 8, text=str(val),
                                 fill=theme['fg'], font=('Segoe UI', 7))
            # X 轴标签（每3小时）
            if hour % 3 == 0:
                canvas.create_text(x + bar_w/2, mt + chart_h + 15,
                                 text=f'{hour}时', fill=theme['muted'],
                                 font=('Segoe UI', 8))

        # 标题
        canvas.create_text(w//2, 12, text='今日每小时活跃次数',
                         fill=theme['fg'], font=('Segoe UI', 11, 'bold'))

    # ---------- 星期分布图 ----------
    def _refresh_weekday_chart(self):
        """刷新星期分布图"""
        try:
            import database
            weekday_counts = database.get_weekday_stats(30)
            self._draw_weekday_chart(weekday_counts)
        except Exception as e:
            log.error('刷新星期分布失败: %s', e)

    def _draw_weekday_chart(self, weekday_counts: dict):
        """用 Canvas 绘制星期分布柱状图"""
        if self._weekday_canvas_widget:
            self._weekday_canvas_widget.destroy()
            self._weekday_canvas_widget = None

        theme = self._theme_colors
        w = max(600, self.weekday_container.winfo_width() - 20)
        h = max(300, self.weekday_container.winfo_height() - 20)
        canvas = tk.Canvas(self.weekday_container, width=w, height=h,
                           bg=theme['card_bg'], highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self._weekday_canvas_widget = canvas

        labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        values = [weekday_counts.get(i, 0) for i in range(7)]
        max_val = max(values) if values else 0
        if max_val == 0:
            canvas.create_text(w//2, h//2, text='暂无数据',
                             fill=theme['muted'], font=('Segoe UI', 14))
            return

        ml, mr, mt, mb = 50, 20, 30, 40
        chart_w = w - ml - mr
        chart_h = h - mt - mb
        bar_w = chart_w / 7 * 0.6
        gap = chart_w / 7 * 0.4

        # Y 轴
        for i in range(5):
            y = mt + chart_h * (1 - i/4)
            val = int(max_val * i / 4)
            canvas.create_text(ml - 8, y, text=str(val), anchor='e',
                             fill=theme['muted'], font=('Segoe UI', 8))
            canvas.create_line(ml, y, ml + chart_w, y, fill=theme['border'])

        # 柱子
        today_wd = datetime.now().weekday()
        for i in range(7):
            val = values[i]
            x = ml + i * (bar_w + gap) + gap/2
            bar_h = (val / max_val) * chart_h if max_val > 0 else 0
            y = mt + chart_h - bar_h
            color = theme['accent'] if i == today_wd else theme['accent_hover']
            canvas.create_rectangle(x, y, x + bar_w, mt + chart_h,
                                   fill=color, outline='')
            if val > 0:
                canvas.create_text(x + bar_w/2, y - 8, text=f'{val:,}',
                                 fill=theme['fg'], font=('Segoe UI', 9))
            canvas.create_text(x + bar_w/2, mt + chart_h + 15,
                             text=labels[i], fill=theme['muted'],
                             font=('Segoe UI', 9))

        canvas.create_text(w//2, 12, text='近30天星期活跃分布',
                         fill=theme['fg'], font=('Segoe UI', 11, 'bold'))

    # ---------- 自动刷新 ----------
    def _start_auto_refresh(self):
        self._incremental_tick()
        self._full_tick()

    def _incremental_tick(self):
        """增量刷新：只更新今日次数和 CPM（轻量，不查统计表）

        使用内存缓存值（键盘），确保实时性。
        """
        try:
            import database
            import stats
            from listener import get_listener
            # 今日活跃 = 键盘按键数（内存值，极快）
            today = database.get_today_count()
            self.today_label.config(text=f"{today:,}")
            cpm = stats.get_current_cpm()
            self.cpm_label.config(text=f"{cpm} 键/分")
            if get_listener().is_paused():
                self.pause_status_label.config(text="[已暂停]",
                                               foreground=self._theme_colors['danger'])
            else:
                self.pause_status_label.config(text="")
        except Exception as e:
            log.debug('增量刷新异常: %s', e)
        interval = config.getint('gui', 'refresh_interval', 2) * 1000
        self.root.after(interval, self._incremental_tick)

    def _full_tick(self):
        """全量刷新：重绘表格 + 更新摘要（10秒一次，降低延迟）"""
        try:
            # 非阻塞 flush：只发信号让后台写入线程尽快落库，不等待，避免界面卡顿
            import database
            database.flush_now(wait=False)
            self.refresh_stats(full=True)
            self._refresh_summary()
        except Exception as e:
            log.debug('全量刷新异常: %s', e)
        interval = config.getint('gui', 'full_refresh_interval', 10) * 1000
        self.root.after(interval, self._full_tick)

    def _refresh_summary(self):
        """刷新日均和最高单日统计"""
        try:
            import database
            daily = database.get_daily_counts(7)
            if daily:
                counts = [c for _, c in daily]
                avg = sum(counts) // len(counts) if counts else 0
                max_val = max(counts) if counts else 0
                self.avg_label.config(text=f"{avg:,}")
                self.max_label.config(text=f"{max_val:,}")
            else:
                self.avg_label.config(text="--")
                self.max_label.config(text="--")
        except Exception as e:
            log.debug('刷新摘要异常: %s', e)

    # ---------- 窗口控制 ----------
    def hide_window(self):
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def toggle_window_visibility(self):
        """全局热键回调：切换窗口显示/隐藏"""
        if self.root.state() == 'withdrawn' or not self.root.winfo_viewable():
            self.show_window()
        else:
            self.hide_window()

    def request_quit(self):
        if self._quitting:
            return
        self._quitting = True
        if not messagebox.askyesno("退出确认",
            "确定要完全退出 FocusFlow 吗？\n退出后将停止记录。"):
            self._quitting = False
            return
        self._save_window_geometry()
        from shutdown import graceful_shutdown
        graceful_shutdown()

    def run(self):
        self.root.mainloop()
