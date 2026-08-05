# -*- coding: utf-8 -*-
"""
FocusFlow 系统托盘模块
- 暂停/恢复菜单
- 暂停时图标变灰
- tooltip 显示"今日活跃 X,XXX 次 | 速度 XX 键/分 | [已暂停]"
- 显示/隐藏主窗口
- 显示/隐藏悬浮窗
"""

import threading
import time
from typing import Optional, Callable

import pystray
from PIL import Image, ImageDraw

from config import config
from logger import get_logger

# 延迟导入 database、stats、listener，避免启动时强耦合
# 这些模块在回调函数中按需导入
log = get_logger('tray')


def _create_image(paused: bool = False) -> Image.Image:
    """生成 FocusFlow 托盘图标：蓝色 F 字母 + 流动线条，暂停时变灰"""
    base_color = (120, 120, 120) if paused else (0, 120, 212)  # #0078d4
    accent = (220, 38, 38) if paused else (0, 150, 255)
    image = Image.new('RGBA', (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 圆角背景
    draw.rounded_rectangle((8, 8, 56, 56), radius=12, fill=base_color)
    # F 字母（简化为线条）
    # 竖线
    draw.rectangle((22, 18, 28, 46), fill=(255, 255, 255))
    # 上横线
    draw.rectangle((22, 18, 42, 24), fill=(255, 255, 255))
    # 中横线
    draw.rectangle((22, 30, 38, 36), fill=(255, 255, 255))
    # 流动线条装饰（右下角）
    draw.arc((36, 38, 52, 54), start=0, end=180, fill=accent, width=2)
    # 暂停标识：右上角红点
    if paused:
        draw.ellipse((46, 10, 56, 20), fill=(220, 38, 38))
    return image


class TrayController:
    """托盘控制器"""

    def __init__(self):
        self._icon: Optional[pystray.Icon] = None
        self._app = None
        self._floating = None
        self._tooltip_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_app(self, app):
        self._app = app

    def set_floating(self, floating):
        self._floating = floating

    # ---------- 菜单回调 ----------
    def _on_show(self, icon, item):
        if self._app:
            self._app.show_window()

    def _on_quit(self, icon, item):
        if self._app:
            self._app.request_quit()

    def _on_pause_toggle(self, icon, item):
        from listener import get_listener
        listener = get_listener()
        new_state = listener.toggle_pause()
        if self._app:
            self._app.on_pause_changed(new_state)
        self._update_icon(new_state)

    def _on_toggle_floating(self, icon, item):
        if self._app:
            self._app.toggle_floating()

    def _is_paused(self) -> bool:
        from listener import get_listener
        return get_listener().is_paused()

    def _is_floating_visible(self) -> bool:
        from floating_window import get_floating
        return get_floating().is_visible()

    def _update_icon(self, paused: bool):
        if self._icon:
            try:
                self._icon.icon = _create_image(paused)
            except Exception as e:
                log.warning('更新托盘图标失败: %s', e)

    # ---------- tooltip 循环 ----------
    def _tooltip_loop(self):
        """每 5 秒更新托盘 tooltip"""
        while not self._stop_event.wait(5):
            try:
                import database
                import stats
                from listener import get_listener
                today = database.get_today_count()
                cpm = stats.get_current_cpm()
                paused = get_listener().is_paused()
                tip = f"今日活跃 {today:,} 次 | 速度 {cpm} 键/分"
                if paused:
                    tip += " | [已暂停]"
                if self._icon:
                    self._icon.title = tip
            except Exception as e:
                log.debug('更新 tooltip 失败: %s', e)

    # ---------- 启动 ----------
    def start(self):
        menu = pystray.Menu(
            pystray.MenuItem("显示统计面板", self._on_show, default=True),
            pystray.MenuItem(
                "暂停记录",
                self._on_pause_toggle,
                checked=lambda item: self._is_paused()
            ),
            pystray.MenuItem(
                "显示悬浮窗",
                self._on_toggle_floating,
                checked=lambda item: self._is_floating_visible()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", self._on_quit),
        )
        self._icon = pystray.Icon(
            "focusflow",
            _create_image(False),
            "FocusFlow - 效率追踪器",
            menu
        )
        self._icon.daemon = True
        self._icon.run_detached()

        self._tooltip_thread = threading.Thread(
            target=self._tooltip_loop, name='tray-tooltip', daemon=True
        )
        self._tooltip_thread.start()
        log.info('托盘已启动')

    def stop(self):
        self._stop_event.set()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass


# 全局实例
_tray: Optional[TrayController] = None


def get_tray() -> TrayController:
    global _tray
    if _tray is None:
        _tray = TrayController()
    return _tray


def init_tray(app, floating=None):
    tray = get_tray()
    tray.set_app(app)
    tray.set_floating(floating)
    tray.start()


def update_icon(paused: bool):
    """供外部调用更新图标"""
    if _tray:
        _tray._update_icon(paused)
