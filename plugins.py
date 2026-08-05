# -*- coding: utf-8 -*-
"""
FocusFlow 插件系统（v1.0 增强版）

功能：
- 动态加载/卸载插件
- 热加载（hot reload）：自动检测插件文件变更并重载
- 插件删除、编辑（系统默认编辑器打开）
- 插件独立运行，不影响主功能
- 插件出错不影响其他插件和主程序
- 支持插件启用/禁用
- 提供 get_view(parent) 用于 GUI 集成

插件目录：plugins/
每个插件是一个 .py 文件，需要实现：
  - PLUGIN_NAME: 插件名称
  - PLUGIN_DESC: 插件描述
  - PLUGIN_VERSION: 版本
  - PLUGIN_AUTHOR: 作者（可选）
  - init(): 初始化（可选）
  - get_view(parent): 返回 ttk.Frame（可选，用于 GUI 集成）
  - cleanup(): 清理（可选）
"""

import os
import sys
import importlib
import importlib.util
import threading
import time
import subprocess
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field

from config import get_app_dir
from logger import get_logger

log = get_logger('plugins')


PLUGINS_DIR = os.path.join(get_app_dir(), 'plugins')


@dataclass
class PluginInfo:
    """插件信息"""
    name: str
    desc: str
    version: str
    author: str = ''
    file_path: str = ''
    file_mtime: float = 0.0
    enabled: bool = True
    loaded: bool = False
    error: str = ''
    module: Any = None
    has_view: bool = False


class PluginManager:
    """插件管理器"""

    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._lock = threading.Lock()
        self._hot_reload_enabled = False
        self._hot_reload_thread: Optional[threading.Thread] = None
        self._hot_reload_stop = threading.Event()
        self._on_reload_callbacks: List[Callable[[str], None]] = []

    # ---------- 发现 ----------
    def discover(self) -> List[str]:
        """扫描插件目录，返回可用插件文件列表"""
        if not os.path.exists(PLUGINS_DIR):
            os.makedirs(PLUGINS_DIR, exist_ok=True)
            return []
        result = []
        for f in sorted(os.listdir(PLUGINS_DIR)):
            if f.endswith('.py') and not f.startswith('_'):
                result.append(os.path.join(PLUGINS_DIR, f))
        return result

    # ---------- 加载/卸载 ----------
    def load_plugin(self, file_path: str) -> PluginInfo:
        """加载单个插件"""
        mod_name = 'plugin_' + os.path.splitext(os.path.basename(file_path))[0]
        try:
            if PLUGINS_DIR not in sys.path:
                sys.path.insert(0, PLUGINS_DIR)

            # 如果已经加载过（按路径查找），先卸载并从字典中移除
            # 这样在热重载时即使插件名变了，旧条目也会被清理
            existing = self._find_by_path(file_path)
            if existing:
                old_name = existing.name
                self._do_unload(old_name, cleanup=True, remove_dict=True)

            spec = importlib.util.spec_from_file_location(mod_name, file_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)

                name = getattr(module, 'PLUGIN_NAME', mod_name)
                desc = getattr(module, 'PLUGIN_DESC', '')
                version = getattr(module, 'PLUGIN_VERSION', '1.0')
                author = getattr(module, 'PLUGIN_AUTHOR', '')
                has_view = callable(getattr(module, 'get_view', None))

                # 如果新名字已被其他文件占用，先卸载旧的
                if name in self._plugins and self._plugins[name].file_path != file_path:
                    self._do_unload(name, cleanup=True, remove_dict=True)

                info = PluginInfo(
                    name=name, desc=desc, version=version, author=author,
                    file_path=file_path,
                    file_mtime=os.path.getmtime(file_path) if os.path.exists(file_path) else 0,
                    enabled=True, loaded=True, module=module, has_view=has_view
                )
                self._plugins[name] = info

                # 调用 init
                if hasattr(module, 'init'):
                    try:
                        module.init()
                    except Exception as e:
                        log.warning('插件 %s init 失败: %s', name, e)

                log.info('插件加载成功: %s v%s', name, version)
                self._notify_reload(name)
                return info
        except Exception as e:
            import traceback
            err_msg = f'{e}\n{traceback.format_exc()[:500]}'
            mod_name_short = os.path.splitext(os.path.basename(file_path))[0]
            info = PluginInfo(
                name=mod_name_short, desc='', version='',
                file_path=file_path, enabled=False, loaded=False, error=str(e)
            )
            log.error('插件加载失败 %s: %s', file_path, e)
            # 仍然记录到字典，便于在 UI 显示错误
            self._plugins[mod_name_short] = info
            return info

    def load_all(self):
        """加载所有插件"""
        files = self.discover()
        # 重新发现：移除字典中已不存在的插件
        current_files = set(os.path.normpath(p) for p in files)
        to_remove = []
        for name, info in list(self._plugins.items()):
            if info.file_path and os.path.normpath(info.file_path) not in current_files:
                if info.loaded:
                    self._do_unload(name, cleanup=True, remove_dict=False)
                to_remove.append(name)
        for name in to_remove:
            self._plugins.pop(name, None)
        # 加载新文件
        for f in files:
            # 跳过已加载且未变更的
            existing = self._find_by_path(f)
            if existing and existing.loaded and not existing.error:
                continue
            self.load_plugin(f)

    def _find_by_path(self, file_path: str) -> Optional[PluginInfo]:
        """根据文件路径查找插件"""
        norm = os.path.normpath(file_path)
        for info in self._plugins.values():
            if info.file_path and os.path.normpath(info.file_path) == norm:
                return info
        return None

    def _do_unload(self, name: str, cleanup: bool = True, remove_dict: bool = False):
        """内部卸载逻辑（不加锁）"""
        info = self._plugins.get(name)
        if not info or not info.module:
            if remove_dict:
                self._plugins.pop(name, None)
            return
        if cleanup and hasattr(info.module, 'cleanup'):
            try:
                info.module.cleanup()
            except Exception as e:
                log.warning('插件 %s cleanup 失败: %s', name, e)
        mod_name = info.module.__name__
        # 删除模块的所有子模块
        to_del = [k for k in list(sys.modules.keys()) if k == mod_name or k.startswith(mod_name + '.')]
        for k in to_del:
            sys.modules.pop(k, None)
        info.loaded = False
        info.module = None
        info.has_view = False
        if remove_dict:
            self._plugins.pop(name, None)
        log.info('插件已卸载: %s', name)

    def unload_plugin(self, name: str):
        """卸载插件（保留在字典中，loaded=False）"""
        with self._lock:
            self._do_unload(name, cleanup=True, remove_dict=False)

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        return self._plugins.get(name)

    def get_all_plugins(self) -> List[PluginInfo]:
        return list(self._plugins.values())

    def reload_plugin(self, name: str) -> bool:
        """重新加载插件（热加载）"""
        with self._lock:
            info = self._plugins.get(name)
            if not info or not info.file_path:
                return False
            self._do_unload(name, cleanup=True, remove_dict=False)
        # load_plugin 内部会处理冲突并覆盖
        self.load_plugin(info.file_path)
        return True

    def delete_plugin(self, name: str) -> Tuple[bool, str]:
        """删除插件（卸载 + 删除文件）"""
        with self._lock:
            info = self._plugins.get(name)
            if not info:
                return False, f'插件 {name} 不存在'
            file_path = info.file_path
            self._do_unload(name, cleanup=True, remove_dict=False)
            self._plugins.pop(name, None)
        # 删除文件
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                log.info('已删除插件文件: %s', file_path)
            return True, '删除成功'
        except Exception as e:
            return False, f'删除文件失败: {e}'

    def edit_plugin(self, name: str) -> Tuple[bool, str]:
        """用系统默认编辑器打开插件文件"""
        info = self._plugins.get(name)
        if not info or not info.file_path:
            return False, f'插件 {name} 不存在'
        try:
            if sys.platform == 'win32':
                os.startfile(info.file_path)  # type: ignore[attr-defined]
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', info.file_path])
            else:
                subprocess.Popen(['xdg-open', info.file_path])
            return True, '已打开编辑器'
        except Exception as e:
            return False, f'打开编辑器失败: {e}'

    def create_plugin_template(self, name: str, desc: str = '') -> str:
        """创建插件模板文件"""
        if not os.path.exists(PLUGINS_DIR):
            os.makedirs(PLUGINS_DIR, exist_ok=True)
        # 仅允许英文字母、数字、下划线
        safe_name = ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
        if not safe_name:
            safe_name = 'plugin'
        file_name = f'{safe_name}.py'
        file_path = os.path.join(PLUGINS_DIR, file_name)
        if os.path.exists(file_path):
            return file_path

        template = '''# -*- coding: utf-8 -*-
"""
FocusFlow 插件：{name}
{desc}
"""

import tkinter as tk
from tkinter import ttk

PLUGIN_NAME = "{name}"
PLUGIN_DESC = "{desc}"
PLUGIN_VERSION = "1.0"
PLUGIN_AUTHOR = ""


def init():
    """插件初始化"""
    pass


def get_view(parent):
    """返回插件的 GUI 视图

    Args:
        parent: 父容器

    Returns:
        ttk.Frame: 插件的界面
    """
    frame = ttk.Frame(parent)
    ttk.Label(frame, text="{name}", font=("Segoe UI", 14, "bold")).pack(pady=20)
    ttk.Label(frame, text="{desc}").pack()
    return frame


def cleanup():
    """插件清理"""
    pass
'''
        template = template.format(name=name, desc=desc)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(template)
        log.info('创建插件模板: %s', file_path)
        return file_path

    # ---------- 热加载 ----------
    def enable_hot_reload(self, on_change: Optional[Callable[[str], None]] = None):
        """启用热加载：后台线程轮询插件文件 mtime，发现变更自动重载"""
        if on_change:
            self._on_reload_callbacks.append(on_change)
        if self._hot_reload_enabled:
            return
        self._hot_reload_enabled = True
        self._hot_reload_stop.clear()
        self._hot_reload_thread = threading.Thread(
            target=self._hot_reload_loop, name='plugin-hot-reload', daemon=True
        )
        self._hot_reload_thread.start()
        log.info('插件热加载已启用')

    def disable_hot_reload(self):
        """禁用热加载"""
        self._hot_reload_enabled = False
        self._hot_reload_stop.set()
        if self._hot_reload_thread:
            self._hot_reload_thread.join(timeout=2)
        self._hot_reload_thread = None
        log.info('插件热加载已禁用')

    def is_hot_reload_enabled(self) -> bool:
        return self._hot_reload_enabled

    def _hot_reload_loop(self):
        """热加载轮询循环（每 2 秒）"""
        last_full_scan = 0.0
        while not self._hot_reload_stop.wait(2.0):
            try:
                # 1. 检查已加载插件的 mtime
                with self._lock:
                    snapshot = list(self._plugins.values())
                for info in snapshot:
                    if not info.file_path or not os.path.exists(info.file_path):
                        continue
                    try:
                        cur_mtime = os.path.getmtime(info.file_path)
                    except Exception:
                        continue
                    if cur_mtime != info.file_mtime:
                        log.info('检测到插件文件变更: %s，开始重载...', info.name)
                        self.reload_plugin(info.name)
                # 2. 每 5 秒做一次完整目录扫描，发现新插件
                now = time.time()
                if now - last_full_scan >= 5:
                    last_full_scan = now
                    files = self.discover()
                    with self._lock:
                        known_paths = {os.path.normpath(p.file_path) for p in self._plugins.values() if p.file_path}
                    for f in files:
                        if os.path.normpath(f) not in known_paths:
                            log.info('发现新插件文件: %s，开始加载...', f)
                            self.load_plugin(f)
            except Exception as e:
                log.error('热加载循环异常: %s', e)

    def _notify_reload(self, plugin_name: str):
        """通知所有回调"""
        for cb in self._on_reload_callbacks:
            try:
                cb(plugin_name)
            except Exception as e:
                log.warning('热加载回调异常: %s', e)


# 全局单例
_manager: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager


def init_plugins():
    """初始化并加载所有插件"""
    manager = get_plugin_manager()
    manager.load_all()


def shutdown_plugins():
    """卸载所有插件"""
    manager = get_plugin_manager()
    manager.disable_hot_reload()
    for info in list(manager.get_all_plugins()):
        if info.loaded:
            manager.unload_plugin(info.name)
