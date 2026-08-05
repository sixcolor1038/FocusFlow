# -*- coding: utf-8 -*-
"""
FocusFlow 悬浮窗模块（画中画 · 健壮版）
- 置顶小型窗口，实时显示今日活跃次数和当前速度
- 可拖动
- 透明度可调
- 双击打开主界面

v3.9.2 健壮性增强：
- show() 幂等化：重复调用不会创建多个刷新循环
- winfo_exists() 检查：窗口被销毁后自动重建，不再报错
- _build() 异常保护：构建失败时回滚，不留半成品
- 线程安全：show/hide/toggle 可从任意线程调用，自动调度到主线程
- after_id 管理：hide() 时取消待执行的刷新回调，避免残留
- parent 校验：parent 无效时给出明确日志而非崩溃
"""

import sys
import ctypes
import tkinter as tk
from typing import Optional, Callable

from config import config, get_ui_state, set_ui_state
from logger import get_logger

log = get_logger('floating')


# ==================== Win32 置顶相关常量 ====================
_HWND_TOPMOST = -1
_GWL_EXSTYLE = -20
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


class FloatingWindow:
    """置顶悬浮窗（健壮版）"""

    def __init__(self, on_double_click: Optional[Callable] = None):
        self._on_double_click = on_double_click
        self._window: Optional[tk.Toplevel] = None
        self._visible = False
        self._drag_data = {'x': 0, 'y': 0}
        self._opacity = config.getfloat('floating', 'opacity', 0.85)
        self._refresh_after_id = None        # 待执行的 after 回调 ID
        self._refresh_active = False         # 刷新循环是否已在运行
        self._topmost_after_id = None        # 置顶重申循环 ID
        self._topmost_active = False         # 置顶重申是否运行中
        self._win32_hwnd = None              # 窗口原生 HWND（缓存）
        self._pos_after = None               # 位置防抖保存回调 ID

    # ==================== 窗口构建 ====================
    def _theme_colors(self) -> dict:
        """获取当前主题的液态玻璃配色（与主界面一致）"""
        dark = config.get('gui', 'theme', 'light') == 'dark'
        if dark:
            return {
                'page_bg': '#171B27',
                'glass': (30, 35, 49),
                'glass_hex': '#1E2331',
                'border': (43, 50, 70),
                'fg': '#E8EAEE',
                'accent': '#5B9CF7',
                'muted': '#9DB8F5',
            }
        return {
            'page_bg': '#EFF3FB',
            'glass': (255, 255, 255),
            'glass_hex': '#FFFFFF',
            'border': (200, 214, 240),
            'fg': '#1A1A2E',
            'accent': '#4D8CF7',
            'muted': '#2E5FB8',
        }

    def _build(self, parent: tk.Tk) -> bool:
        """构建悬浮窗窗口及内部控件。

        Returns: True 表示构建成功，False 表示失败。
        """
        if self._window is not None and self._window_exists():
            return True

        # 校验 parent
        if parent is None:
            log.error('悬浮窗 _build: parent 为 None')
            return False
        try:
            if not parent.winfo_exists():
                log.error('悬浮窗 _build: parent 窗口已销毁')
                return False
        except Exception as e:
            log.error('悬浮窗 _build: parent 校验异常: %s', e)
            return False

        try:
            self._window = tk.Toplevel(parent)
            self._window.overrideredirect(True)
            self._window.attributes('-topmost', True)
            self._window.attributes('-alpha', self._opacity)
            sw = self._window.winfo_screenwidth()
            sh = self._window.winfo_screenheight()

            colors = self._theme_colors()

            # 稍大的加粗字体，保证清晰可读
            self._today_label = tk.Label(
                self._window, text="活跃 0",
                font=("Segoe UI", 9, "bold"),
                fg=colors['fg'], bg=colors['glass_hex']
            )
            self._today_label.pack(anchor='w', padx=6, pady=(0, 0))

            self._cpm_label = tk.Label(
                self._window, text="0/分",
                font=("Segoe UI", 8, "bold"),
                fg=colors['accent'], bg=colors['glass_hex']
            )
            self._cpm_label.pack(anchor='w', padx=6, pady=(0, 1))

            # 窗口背景与文字背景完全一致（纯色矩形，无四角色差）
            self._window.configure(bg=colors['glass_hex'])
            # Win32 置顶/工具窗口/不抢焦点样式
            self._apply_win32_style()

            # 尺寸：长度(宽)减 6mm、宽度(高)减 3mm（按屏幕 DPI 精确换算），
            # 且不小于文字所需尺寸，保证不裁切
            try:
                mm_px = self._window.winfo_fpixels('1m')
            except Exception:
                mm_px = 0.0
            if not mm_px or mm_px <= 0:
                mm_px = 3.78  # 96 DPI 兜底
            w = int(112 - 6 * mm_px)
            h = int(54 - 3 * mm_px)
            # 内容所需最小尺寸
            req_w = self._today_label.winfo_reqwidth() + 10
            req_h = (self._today_label.winfo_reqheight()
                     + self._cpm_label.winfo_reqheight() + 4)
            w = max(w, min(req_w, 112))
            h = max(h, min(req_h, 54))

            # 位置：优先恢复上次拖动位置，否则默认顶部靠右
            saved = get_ui_state('floating', 'geometry')
            pos_str = ''
            if saved and '+' in saved:
                pos_str = saved[saved.index('+'):]
            self._window.geometry(f'{w}x{h}{pos_str or f"+{max(0, sw - w - 22)}+60"}')
            # 若恢复的位置不在当前屏幕内，回退到默认位置
            try:
                if not (0 <= self._window.winfo_x() < sw - 10
                        and 0 <= self._window.winfo_y() < sh - 10):
                    self._window.geometry(f'{w}x{h}+{max(0, sw - w - 22)}+60')
            except Exception:
                pass

            # 拖动绑定
            for wgt in (self._today_label, self._cpm_label):
                wgt.bind('<Button-1>', self._start_drag)
                wgt.bind('<B1-Motion>', self._on_drag)
                wgt.bind('<Double-Button-1>', self._on_double_click_internal)

            log.debug('悬浮窗构建完成 (%dx%d)', w, h)
            return True
        except Exception as e:
            log.error('悬浮窗构建失败: %s', e, exc_info=True)
            # 构建失败：清理半成品
            self._window = None
            self._visible = False
            return False

    def _apply_style(self):
        """应用当前主题配色（窗口背景与文字背景一致，无四角色差）"""
        if not self._window_exists():
            return
        try:
            colors = self._theme_colors()
            self._window.configure(bg=colors['glass_hex'])
            self._today_label.configure(fg=colors['fg'],
                                        bg=colors['glass_hex'])
            self._cpm_label.configure(fg=colors['accent'],
                                      bg=colors['glass_hex'])
        except Exception as e:
            log.debug('悬浮窗样式应用失败: %s', e)

    def apply_theme(self):
        """主题切换后刷新悬浮窗配色（由主界面调用）"""
        if self._window_exists():
            self._apply_style()

    def _window_exists(self) -> bool:
        """检查窗口是否仍然存在（未被销毁）。"""
        if self._window is None:
            return False
        try:
            return self._window.winfo_exists()
        except Exception:
            # 窗口对象已失效
            self._window = None
            return False

    # ==================== 拖动 ====================
    def _start_drag(self, event):
        self._drag_data['x'] = event.x
        self._drag_data['y'] = event.y

    def _on_drag(self, event):
        if self._window_exists():
            try:
                x = self._window.winfo_x() + event.x - self._drag_data['x']
                y = self._window.winfo_y() + event.y - self._drag_data['y']
                self._window.geometry(f'+{x}+{y}')
                self._schedule_save_position()
            except Exception as e:
                log.debug('拖动异常: %s', e)

    # ==================== 位置记忆 ====================
    def _schedule_save_position(self):
        """拖动结束后（防抖）保存窗口位置"""
        try:
            if self._pos_after is not None and self._window_exists():
                self._window.after_cancel(self._pos_after)
            self._pos_after = self._window.after(600, self._save_position)
        except Exception:
            pass

    def _save_position(self):
        """保存悬浮窗位置到独立状态文件（下次启动恢复）"""
        try:
            self._pos_after = None
            if not self._window_exists():
                return
            geo = self._window.geometry()
            if not geo or '+' not in geo:
                return
            set_ui_state('floating', 'geometry', geo)
        except Exception:
            pass

    def _on_double_click_internal(self, event):
        if self._on_double_click:
            try:
                self._on_double_click()
            except Exception as e:
                log.error('双击回调异常: %s', e)

    # ==================== 刷新循环 + 置顶重申 ====================
    def _refresh_loop(self):
        """定时刷新今日活跃数和 CPM。

        v3.9.2：使用 _refresh_active 标志防止重复启动。
        """
        if not self._refresh_active:
            return
        if not self._window_exists() or not self._visible:
            self._refresh_active = False
            return
        try:
            import database
            import stats
            today = database.get_today_count()
            cpm = stats.get_current_cpm()
            if self._window_exists():
                self._today_label.config(text=f"活跃 {today:,}")
                self._cpm_label.config(text=f"{cpm}/分")
        except Exception as e:
            log.debug('悬浮窗刷新异常: %s', e)
        # 安排下一次刷新
        if self._window_exists() and self._visible:
            try:
                self._refresh_after_id = self._window.after(1000, self._refresh_loop)
            except Exception:
                self._refresh_active = False
                self._visible = False
        else:
            self._refresh_active = False

    def _topmost_loop(self):
        """周期性重申置顶，防止被任务栏/其他置顶窗口/前台应用遮挡。"""
        if not self._topmost_active or not self._window_exists() or not self._visible:
            self._topmost_active = False
            return
        self._assert_topmost()
        try:
            self._topmost_after_id = self._window.after(350, self._topmost_loop)
        except Exception:
            self._topmost_active = False

    def _start_refresh_loop(self):
        """启动刷新循环 + 置顶重申（幂等：已在运行则不重复启动）。"""
        if self._refresh_active:
            return
        self._refresh_active = True
        self._refresh_loop()
        self._topmost_active = True
        self._assert_topmost()
        self._topmost_loop()

    def _stop_refresh_loop(self):
        """停止刷新循环与置顶重申，并取消待执行的回调。"""
        self._refresh_active = False
        self._topmost_active = False
        if self._refresh_after_id is not None:
            try:
                if self._window_exists():
                    self._window.after_cancel(self._refresh_after_id)
            except Exception:
                pass
            self._refresh_after_id = None
        if self._topmost_after_id is not None:
            try:
                if self._window_exists():
                    self._window.after_cancel(self._topmost_after_id)
            except Exception:
                pass
            self._topmost_after_id = None

    # ==================== Win32 置顶 ====================
    def _get_win32_hwnd(self):
        """获取悬浮窗顶层窗口的原生 HWND（overrideredirect 需取父窗口）"""
        try:
            if not self._window_exists():
                return None
            child = self._window.winfo_id()
            if sys.platform == 'win32':
                hwnd = ctypes.windll.user32.GetParent(child)
                return hwnd or child
            return child
        except Exception:
            return None

    def _apply_win32_style(self):
        """设置 Win32 扩展样式：置顶 + 工具窗口 + 不抢焦点"""
        if sys.platform != 'win32':
            return
        try:
            hwnd = self._get_win32_hwnd()
            if not hwnd:
                return
            style = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            style |= (_WS_EX_TOPMOST | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE)
            ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style)
            # 应用样式变更
            ctypes.windll.user32.SetWindowPos(
                hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
            self._win32_hwnd = hwnd
        except Exception as e:
            log.debug('应用置顶样式失败: %s', e)

    def _assert_topmost(self):
        """重申窗口置顶（Tk 属性 + Win32 SetWindowPos 双保险）。"""
        try:
            if not self._window_exists():
                return
            self._window.attributes('-topmost', True)
            if sys.platform == 'win32':
                hwnd = self._get_win32_hwnd() or self._win32_hwnd
                if hwnd:
                    ctypes.windll.user32.SetWindowPos(
                        hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
        except Exception as e:
            log.debug('重申置顶失败: %s', e)

    # ==================== 显示/隐藏 ====================
    def _schedule_on_main(self, parent: tk.Tk, func: Callable, *args):
        """线程安全调度：如果不在主线程，通过 parent.after 调度到主线程。

        Returns: True 表示已调度（调用方应直接返回），False 表示已在主线程。
        """
        if parent is None:
            return False
        try:
            if not self._is_main_thread():
                parent.after(0, lambda: func(*args))
                return True
        except Exception as e:
            log.debug('线程调度异常: %s', e)
        return False

    def show(self, parent: tk.Tk = None) -> bool:
        """显示悬浮窗。

        幂等：重复调用安全，不会创建多个刷新循环。
        线程安全：可从任意线程调用，自动调度到主线程。
        Returns: True 表示显示成功，False 表示失败。
        """
        # 线程安全：如果不在主线程，调度到主线程
        if self._schedule_on_main(parent, self.show, parent):
            return False

        # 窗口已存在但被销毁 → 清理重建
        if self._window is not None and not self._window_exists():
            log.info('悬浮窗窗口已失效，准备重建')
            self._window = None
            self._refresh_active = False

        # 构建窗口（如果需要）
        if self._window is None:
            if parent is None:
                log.warning('悬浮窗 show: parent 为 None，无法构建')
                return False
            if not self._build(parent):
                log.error('悬浮窗构建失败，show 中止')
                return False

        # 显示窗口
        try:
            if self._window_exists():
                self._window.deiconify()
                self._window.attributes('-topmost', True)
                # 窗口映射后重新应用置顶/工具窗口样式（部分样式需映射后才生效）
                self._apply_win32_style()
                self._visible = True
                self._start_refresh_loop()
                log.info('悬浮窗已显示')
                return True
            else:
                log.error('悬浮窗窗口不存在，显示失败')
                return False
        except Exception as e:
            log.error('悬浮窗显示失败: %s', e, exc_info=True)
            self._visible = False
            return False

    def hide(self):
        """隐藏悬浮窗（不销毁，可再次 show）。"""
        self._save_position()
        self._visible = False
        self._stop_refresh_loop()
        if self._window_exists():
            try:
                self._window.withdraw()
                log.info('悬浮窗已隐藏')
            except Exception as e:
                log.debug('悬浮窗隐藏异常: %s', e)

    def toggle(self, parent: tk.Tk = None):
        """切换悬浮窗显示/隐藏。"""
        if self._visible:
            self.hide()
        else:
            if parent:
                self.show(parent)
            else:
                log.warning('悬浮窗 toggle 缺少 parent')

    def destroy(self):
        """彻底销毁悬浮窗（退出时调用）。"""
        self._save_position()
        self._visible = False
        self._stop_refresh_loop()
        if self._pos_after is not None:
            try:
                if self._window_exists():
                    self._window.after_cancel(self._pos_after)
            except Exception:
                pass
            self._pos_after = None
        if self._window_exists():
            try:
                self._window.destroy()
            except Exception:
                pass
        self._window = None

    def is_visible(self) -> bool:
        """悬浮窗是否可见。"""
        return self._visible and self._window_exists()

    def set_opacity(self, opacity: float):
        self._opacity = max(0.1, min(1.0, opacity))
        if self._window_exists():
            try:
                self._window.attributes('-alpha', self._opacity)
            except Exception as e:
                log.debug('设置透明度异常: %s', e)
        config.set('floating', 'opacity', str(self._opacity))

    @staticmethod
    def _is_main_thread() -> bool:
        """检查当前是否在主线程。"""
        import threading
        return threading.current_thread() is threading.main_thread()


# 全局实例
_floating: Optional[FloatingWindow] = None


def get_floating() -> FloatingWindow:
    global _floating
    if _floating is None:
        _floating = FloatingWindow()
    return _floating
