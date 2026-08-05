# -*- coding: utf-8 -*-
"""
FocusFlow 命令行接口
用法：
  FocusFlow.exe --stats 7          # 输出最近 7 天统计摘要
  FocusFlow.exe --stats today      # 今日统计
  FocusFlow.exe --stats all        # 总计
  FocusFlow.exe --stats-year 2025  # 指定年度统计
  FocusFlow.exe --export csv       # 导出 CSV
  FocusFlow.exe --export html      # 导出 HTML
  FocusFlow.exe --reset            # 清空所有记录（需二次确认）
  FocusFlow.exe --vacuum           # 压缩数据库
  FocusFlow.exe --cleanup 30       # 清理 30 天前的数据
  FocusFlow.exe --list-years       # 列出所有有数据的年份
"""

import sys
import os
from datetime import date

from logger import get_logger

log = get_logger('cli')


def _print_stats(days_str: str):
    """打印统计摘要"""
    import database
    if days_str.lower() == 'today':
        total, key_stats = database.get_stats_by_date(date.today())
        period = '今日'
    elif days_str.lower() == 'all':
        total, key_stats = database.get_stats(None)
        period = '总计'
    else:
        try:
            days = int(days_str)
        except ValueError:
            print(f"无效的参数: {days_str}（应为数字、today 或 all）")
            return 1
        total, key_stats = database.get_stats(days)
        period = f'最近 {days} 天'

    print(f"\n{'=' * 50}")
    print(f"  FocusFlow 活跃统计 - {period}")
    print(f"{'=' * 50}")
    print(f"  总活跃次数: {total:,}")
    print(f"{'-' * 50}")
    print(f"  {'排名':<6}{'按键':<12}{'次数':<12}{'占比':<10}")
    print(f"  {'-' * 40}")
    for rank, (key_name, count) in enumerate(key_stats.items(), 1):
        percent = f"{count / total * 100:.1f}%" if total > 0 else '0%'
        print(f"  {rank:<6}{key_name:<12}{count:<12,}{percent:<10}")
        if rank >= 20:
            print(f"  ... 共 {len(key_stats)} 种按键")
            break
    print(f"{'=' * 50}\n")
    return 0


def _print_year_stats(year_str: str):
    import database
    try:
        year = int(year_str)
    except ValueError:
        print(f"无效的年份: {year_str}")
        return 1
    total, key_stats = database.get_stats(None, year=year)
    print(f"\n{'=' * 50}")
    print(f"  FocusFlow 活跃统计 - {year} 年度")
    print(f"{'=' * 50}")
    print(f"  总活跃次数: {total:,}")
    print(f"{'-' * 50}")
    for rank, (key_name, count) in enumerate(key_stats.items(), 1):
        percent = f"{count / total * 100:.1f}%" if total > 0 else '0%'
        print(f"  {rank:<6}{key_name:<12}{count:<12,}{percent:<10}")
        if rank >= 20:
            break
    print(f"{'=' * 50}\n")
    return 0


def _list_years():
    import database
    years = database.get_available_years()
    if not years:
        print("暂无数据")
        return 0
    print("\n有数据的年份：")
    for y in years:
        print(f"  {y}")
    print()
    return 0


def _export(fmt: str):
    import database
    from exporter import export_csv, export_html
    ext = fmt
    filepath = os.path.join(os.getcwd(), f"focusflow_export.{ext}")
    if fmt == 'csv':
        ok = export_csv(None, filepath)
    elif fmt == 'html':
        ok = export_html(None, filepath)
    else:
        print(f"不支持的格式: {fmt}（可选 csv 或 html）")
        return 1
    if ok:
        print(f"已导出到: {filepath}")
        return 0
    else:
        print("导出失败")
        return 1


def _reset():
    import database
    confirm = input("警告：将清空所有记录！输入 yes 确认: ")
    if confirm.strip().lower() != 'yes':
        print("已取消")
        return 0
    try:
        database.reset_all_data()
        print("所有记录已清空")
        return 0
    except Exception as e:
        print(f"重置失败: {e}")
        return 1


def _vacuum():
    import database
    try:
        database.vacuum()
        print("压缩完成")
        return 0
    except Exception as e:
        print(f"压缩失败: {e}")
        return 1


def _cleanup(days: int):
    import database
    try:
        deleted = database.cleanup_old_data(days)
        print(f"已删除 {days} 天前的记录 {deleted:,} 条")
        return 0
    except Exception as e:
        print(f"清理失败: {e}")
        return 1


def run_cli() -> int:
    """解析命令行参数并执行，返回退出码"""
    args = sys.argv[1:]
    if not args:
        return -1  # 无 CLI 参数，进入 GUI 模式

    import database
    database.init_db()

    if '--stats' in args:
        idx = args.index('--stats')
        if idx + 1 >= len(args):
            print("用法: --stats <天数|today|all>")
            return 1
        return _print_stats(args[idx + 1])

    if '--stats-year' in args:
        idx = args.index('--stats-year')
        if idx + 1 >= len(args):
            print("用法: --stats-year <年份>")
            return 1
        return _print_year_stats(args[idx + 1])

    if '--list-years' in args:
        return _list_years()

    if '--export' in args:
        idx = args.index('--export')
        if idx + 1 >= len(args):
            print("用法: --export <csv|html>")
            return 1
        return _export(args[idx + 1].lower())

    if '--reset' in args:
        return _reset()

    if '--vacuum' in args:
        return _vacuum()

    if '--cleanup' in args:
        idx = args.index('--cleanup')
        if idx + 1 >= len(args):
            print("用法: --cleanup <保留天数>")
            return 1
        try:
            days = int(args[idx + 1])
        except ValueError:
            print("天数必须为整数")
            return 1
        return _cleanup(days)

    print(f"未知参数: {args}")
    print(__doc__)
    return 1
