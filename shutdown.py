# -*- coding: utf-8 -*-
"""
FocusFlow 优雅退出模块

独立模块，避免 key_counter ↔ gui 循环导入。
负责协调各模块的关闭顺序：监听 → 热键 → 托盘 → 数据库 → GUI
"""

import threading

from logger import get_logger

log = get_logger('shutdown')

_shutting_down = False
_shutdown_lock = threading.Lock()


def graceful_shutdown():
    """优雅退出流程：刷新缓存 → 备份 → 停止各模块 → 退出

    顺序：
    1. 停止键盘监听（不再产生新数据）
    2. 停止全局热键
    3. 停止托盘
    4. 数据库关闭（flush + 备份）
    5. 销毁 GUI 并退出进程
    """
    global _shutting_down
    with _shutdown_lock:
        if _shutting_down:
            return
        _shutting_down = True

    log.info('开始优雅退出...')
    try:
        # 1. 停止键盘监听
        try:
            from listener import get_listener
            get_listener().stop()
            log.info('键盘监听已停止')
        except Exception as e:
            log.warning('停止键盘监听失败: %s', e)

        # 1.5 销毁悬浮窗（v3.9.2：确保退出时清理）
        try:
            from floating_window import get_floating
            get_floating().destroy()
            log.info('悬浮窗已销毁')
        except Exception as e:
            log.debug('销毁悬浮窗失败: %s', e)

        # 2. 停止全局热键
        try:
            from hotkey import stop_hotkey
            stop_hotkey()
            log.info('全局热键已停止')
        except Exception as e:
            log.warning('停止全局热键失败: %s', e)

        # 3. 停止托盘
        try:
            from tray import get_tray
            get_tray().stop()
            log.info('托盘已停止')
        except Exception as e:
            log.warning('停止托盘失败: %s', e)

        # 4. 数据库关闭（flush + 备份）
        try:
            import database
            database.shutdown()
        except Exception as e:
            log.error('数据库关闭失败: %s', e, exc_info=True)

        # 5. 销毁 GUI 并退出
        log.info('优雅退出完成，正在终止进程')

    except Exception as e:
        log.error('优雅退出过程异常: %s', e, exc_info=True)
    finally:
        # 强制退出（daemon 线程会自动终止）
        import os
        os._exit(0)


def is_shutting_down() -> bool:
    """是否正在关闭中"""
    return _shutting_down
