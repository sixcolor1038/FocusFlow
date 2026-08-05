# -*- coding: utf-8 -*-
"""
FocusFlow 数据导出模块
- CSV 导出
- HTML 导出（带样式）
"""

import csv
import os
from datetime import datetime
from typing import Optional

from logger import get_logger
import database

log = get_logger('exporter')


def export_csv(days: Optional[int], filepath: str, year: Optional[int] = None) -> bool:
    """导出统计为 CSV"""
    try:
        total, key_stats = database.get_stats(days, year=year)
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if year is not None:
                period = f'{year} 年度'
            elif days is None:
                period = '总计'
            else:
                period = f'最近{days}天'
            writer.writerow(['# FocusFlow 键盘活跃统计导出'])
            writer.writerow(['# 统计周期', period])
            writer.writerow(['# 总活跃次数', total])
            writer.writerow(['# 导出时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
            writer.writerow([])
            writer.writerow(['排名', '按键', '次数', '占比(%)'])
            for rank, (key_name, count) in enumerate(key_stats.items(), 1):
                percent = f"{count / total * 100:.2f}" if total > 0 else '0.00'
                writer.writerow([rank, key_name, count, percent])
        log.info('CSV 导出成功: %s', filepath)
        return True
    except Exception as e:
        log.error('CSV 导出失败: %s', e, exc_info=True)
        return False


def export_html(days: Optional[int], filepath: str, year: Optional[int] = None) -> bool:
    """导出统计为 HTML（带样式）"""
    try:
        total, key_stats = database.get_stats(days, year=year)
        if year is not None:
            period = f'{year} 年度'
        elif days is None:
            period = '总计'
        else:
            period = f'最近{days}天'
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rows_html = []
        for rank, (key_name, count) in enumerate(key_stats.items(), 1):
            percent = f"{count / total * 100:.2f}%" if total > 0 else '0.00%'
            bar_width = (count / total * 100) if total > 0 else 0
            rows_html.append(f'''
                <tr>
                    <td class="rank">{rank}</td>
                    <td class="key">{_escape(key_name)}</td>
                    <td class="count">{count:,}</td>
                    <td class="percent">
                        <div class="bar-container">
                            <div class="bar" style="width:{bar_width:.1f}%"></div>
                            <span>{percent}</span>
                        </div>
                    </td>
                </tr>''')

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>FocusFlow 活跃统计报告</title>
<style>
    body {{ font-family: "Segoe UI", "Microsoft YaHei", "微软雅黑", sans-serif; margin: 40px; background: #f5f5f5; }}
    .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    h1 {{ color: #0078d4; border-bottom: 2px solid #0078d4; padding-bottom: 10px; }}
    .meta {{ color: #666; margin-bottom: 20px; font-size: 14px; }}
    .total {{ font-size: 28px; font-weight: bold; color: #0078d4; margin: 20px 0; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th {{ background: #0078d4; color: white; padding: 12px; text-align: left; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    tr:hover {{ background: #e8f4fd; }}
    .rank {{ text-align: center; font-weight: bold; color: #666; }}
    .key {{ font-family: monospace; font-size: 14px; }}
    .count {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .bar-container {{ position: relative; min-width: 200px; }}
    .bar {{ background: #0078d4; height: 20px; border-radius: 3px; opacity: 0.3; }}
    .bar-container span {{ position: absolute; left: 8px; top: 2px; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
    <h1>FocusFlow 活跃统计报告</h1>
    <div class="meta">
        <div>统计周期：{period}</div>
        <div>导出时间：{now_str}</div>
    </div>
    <div class="total">总活跃次数：{total:,}</div>
    <table>
        <thead>
            <tr><th>排名</th><th>按键</th><th>次数</th><th>占比</th></tr>
        </thead>
        <tbody>
            {''.join(rows_html)}
        </tbody>
    </table>
</div>
</body>
</html>'''
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        log.info('HTML 导出成功: %s', filepath)
        return True
    except Exception as e:
        log.error('HTML 导出失败: %s', e, exc_info=True)
        return False


def _escape(s: str) -> str:
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))
