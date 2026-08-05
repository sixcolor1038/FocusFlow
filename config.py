# -*- coding: utf-8 -*-
"""
FocusFlow 配置管理模块
- 所有可调参数集中到 config.ini
- 首次运行自动生成默认配置
- 支持运行时修改并持久化
"""

import os
import sys
import configparser
import threading
from typing import Optional


# ==================== 应用元信息 ====================
APP_NAME = "FocusFlow"
APP_DISPLAY_NAME = "FocusFlow - 效率追踪器"
APP_DESCRIPTION = "FocusFlow - 效率与专注力分析工具"
APP_VERSION = "1.1.0"
APP_AUTHOR = "FocusFlow"
APP_UPDATE_DATE = "2026-08-06"


def get_app_dir() -> str:
    """获取程序所在目录（exe 或脚本目录）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_config_path() -> str:
    return os.path.join(get_app_dir(), 'config.ini')


def get_data_dir() -> str:
    """数据目录（存放年度数据库）"""
    data_dir = os.path.join(get_app_dir(), 'data')
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


# 默认配置
DEFAULT_CONFIG = {
    'database': {
        'batch_size': '50',            # 批量写入阈值（降低延迟）
        'flush_interval': '10',        # 定时刷新间隔（秒）
        'backup_on_exit': 'true',      # 退出时自动备份
        'max_backups': '5',            # 最大备份数
        'auto_vacuum_days': '7',       # 每 N 天自动 VACUUM 一次（0=禁用）
        'yearly_archive': 'true',      # 启用年度归档
    },
    'stats': {
        'cpm_window': '60',            # CPM 计算窗口（秒）
        'today_count_cache_ttl': '10', # 今日计数缓存有效期（秒）
    },
    'listener': {
        'ignore_modifier_keys': 'false',  # 是否忽略修饰键
        'ignore_function_keys': 'false',  # 是否忽略 F1-F12
        'ignore_key_repeat': 'true',      # 是否过滤长按自动重复（游戏/长按某键时不重复计数）
        'key_repeat_stale_seconds': '15', # release 丢失后，超过该秒数才允许重新计数
    },
    'gui': {
        'refresh_interval': '2',       # GUI 增量刷新间隔（秒）
        'full_refresh_interval': '10', # 全量刷新间隔（秒，降低延迟）
        'show_first_run_tip': 'true',  # 是否显示首次提示
        'theme': 'light',              # light / dark
        'show_trend_chart': 'true',    # 是否显示趋势图
        'show_key_groups': 'true',     # 是否显示按键分组统计
        'start_to_tray': 'false',      # 启动时直接进入系统托盘（不显示主窗口）
    },
    'hotkey': {
        'toggle_window': 'ctrl+shift+f',  # 显示/隐藏主窗口热键
    },
    'floating': {
        'enabled': 'true',             # 启动时自动显示悬浮窗（可手动开关）
        'opacity': '0.85',             # 透明度 0.1-1.0
    },
    'tray': {
        'tooltip_interval': '5',       # 托盘 tooltip 刷新间隔（秒）
    },
    'pomodoro': {
        'enabled': 'true',             # 启用番茄钟
        'work_minutes': '25',          # 工作时长（分钟）
        'break_minutes': '5',          # 休息时长（分钟）
        'auto_break': 'true',          # 工作结束后自动进入休息
    },
    'rest': {
        'enabled': 'true',             # 启用护眼提醒
        'window_minutes': '30',        # 检测窗口（分钟）
        'key_threshold': '10000',      # 窗口内按键阈值
        'cooldown_minutes': '10',      # 提醒冷却（分钟）
        'rest_seconds': '20',          # 提醒倒计时（秒）
        'check_interval': '10',        # 检测间隔（秒）
    },
}


class Config:
    """线程安全的配置管理器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._parser = configparser.ConfigParser()
        self._load()

    def _load(self):
        path = get_config_path()
        if os.path.exists(path):
            try:
                self._parser.read(path, encoding='utf-8')
            except Exception:
                pass
        # 补全缺失的 section/key
        changed = False
        for section, items in DEFAULT_CONFIG.items():
            if not self._parser.has_section(section):
                self._parser.add_section(section)
                changed = True
            for key, val in items.items():
                if not self._parser.has_option(section, key):
                    self._parser.set(section, key, val)
                    changed = True
        if changed:
            self._save()

    def _save(self):
        path = get_config_path()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                self._parser.write(f)
        except Exception:
            pass

    # ---------- 读取 API ----------
    def get(self, section: str, key: str, default: Optional[str] = None) -> str:
        with self._lock:
            try:
                return self._parser.get(section, key)
            except Exception:
                return default if default is not None else DEFAULT_CONFIG.get(section, {}).get(key, '')

    def getint(self, section: str, key: str, default: int = 0) -> int:
        try:
            return int(self.get(section, key))
        except (ValueError, TypeError):
            return default

    def getfloat(self, section: str, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(section, key))
        except (ValueError, TypeError):
            return default

    def getbool(self, section: str, key: str, default: bool = False) -> bool:
        try:
            return self._parser.getboolean(section, key)
        except Exception:
            return default

    # ---------- 写入 API ----------
    def set(self, section: str, key: str, value: str):
        with self._lock:
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            self._parser.set(section, key, str(value))
            self._save()


# ==================== 界面状态独立文件 ====================
# 主窗口尺寸/位置等"易变"状态单独存到 window_state.ini，
# 避免用户复制/替换 config.ini 时被重置。
_WINDOW_STATE_FILE = 'window_state.ini'
_state_lock = threading.Lock()


def get_window_state_path() -> str:
    """窗口状态文件路径（与 config.ini 同目录）"""
    return os.path.join(get_app_dir(), _WINDOW_STATE_FILE)


def get_window_geometry() -> str:
    """获取上次保存的主窗口尺寸与位置（如 '880x720+120+90'），无则返回 ''"""
    return get_ui_state('window', 'geometry')


def set_window_geometry(geo: str) -> None:
    """保存主窗口尺寸与位置到独立文件"""
    set_ui_state('window', 'geometry', geo)


def get_ui_state(section: str, option: str = 'geometry') -> str:
    """从独立状态文件读取任意状态（如插件窗口尺寸），无则返回 ''"""
    try:
        p = get_window_state_path()
        if os.path.exists(p):
            with _state_lock:
                parser = configparser.ConfigParser()
                parser.read(p, encoding='utf-8')
                value = parser.get(section, option, fallback='').strip()
            if value:
                return value
    except Exception:
        pass
    # 兼容旧版主窗口：若 config.ini 中仍有历史 geometry，迁移到独立文件
    if section == 'window' and option == 'geometry':
        try:
            legacy = config.get('gui', 'window_geometry', '').strip()
            if legacy:
                set_ui_state('window', 'geometry', legacy)
                return legacy
        except Exception:
            pass
    return ''


def set_ui_state(section: str, option: str, value: str) -> None:
    """保存任意状态到独立文件（窗口尺寸/位置等易变状态）"""
    if not value:
        return
    try:
        p = get_window_state_path()
        with _state_lock:
            parser = configparser.ConfigParser()
            if os.path.exists(p):
                try:
                    parser.read(p, encoding='utf-8')
                except Exception:
                    pass
            if not parser.has_section(section):
                parser.add_section(section)
            parser.set(section, option, str(value))
            with open(p, 'w', encoding='utf-8') as f:
                parser.write(f)
    except Exception:
        pass


def get_start_to_tray() -> bool:
    """读取"启动时直接进入托盘"设置（独立状态文件优先，兼容旧版 config.ini）"""
    val = get_ui_state('prefs', 'start_to_tray')
    if val:
        return val.lower() in ('true', '1', 'yes')
    return config.getbool('gui', 'start_to_tray', False)


def set_start_to_tray(enabled: bool) -> None:
    """保存"启动时直接进入托盘"设置到独立状态文件（更新软件时不会被 config.ini 覆盖）"""
    set_ui_state('prefs', 'start_to_tray', 'true' if enabled else 'false')


# 全局单例
config = Config()
