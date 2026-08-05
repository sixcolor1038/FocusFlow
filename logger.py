# -*- coding: utf-8 -*-
"""
FocusFlow 日志模块
- RotatingFileHandler 按大小轮转（5MB × 3 备份）
- 默认 INFO 级别（DEBUG 仅在开发时开启）
- 全局异常钩子
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from config import get_app_dir


_LOG_DIR = os.path.join(get_app_dir(), 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, 'focusflow.log')

# 日志格式
_FORMAT = '%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'

# 单文件 5MB，保留 3 个备份
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def _build_logger() -> logging.Logger:
    logger = logging.getLogger('focusflow')
    logger.setLevel(logging.INFO)  # 默认 INFO，减少冗余
    logger.propagate = False

    if logger.handlers:
        return logger

    # 文件 handler
    file_handler = RotatingFileHandler(
        _LOG_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    # 控制台 handler（仅 ERROR）
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


# 全局 logger
log = _build_logger()


def install_global_excepthook():
    """安装全局未捕获异常钩子"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical('未捕获异常', exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook

    import threading
    def _threading_hook(args):
        log.critical(
            f'线程 {args.thread.name} 未捕获异常: {args.exc_value}',
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )
    threading.excepthook = _threading_hook


def get_logger(name: str) -> logging.Logger:
    """子模块获取子 logger"""
    return log.getChild(name)


def set_debug(enabled: bool):
    """动态切换 DEBUG 级别（开发调试用）"""
    level = logging.DEBUG if enabled else logging.INFO
    log.setLevel(level)
    for h in log.handlers:
        h.setLevel(level)
