# -*- coding: utf-8 -*-
"""PyInstaller hook for key_counter（主入口）

强制收集所有 FocusFlow 自定义模块，避免 ModuleNotFoundError
"""

import os
import glob

# hook 所在目录的上一级就是项目根目录
hook_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(hook_dir)

# 收集所有本地 .py 模块名（排除 __pycache__、测试文件等）
hiddenimports = []
for py_file in glob.glob(os.path.join(project_dir, '*.py')):
    mod_name = os.path.splitext(os.path.basename(py_file))[0]
    # 排除不需要的
    if mod_name.startswith('_') or mod_name.startswith('test_'):
        continue
    hiddenimports.append(mod_name)
