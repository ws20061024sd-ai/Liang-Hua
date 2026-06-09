#!/usr/bin/env python3
"""
市场日报 —— 独立脚本
用法: python analysis/report.py
输出: 终端打印 + 钉钉推送
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import macro, sector, stock
from notifier.dingtalk_report import format_report, send_report
from datetime import datetime


def get_data_date():
    """获取数据库实际的最新数据日期"""
    import sqlite3
    from config import settings
    try:
        conn = sqlite3.connect(settings.DB_PATH)
        r = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        conn.close()
        return r
    except:
        return None


def main():
    print("📈 正在生成市场日报...\n")

    # 0. 数据质量保障（日报独立检查，不依赖 run.py）
    from data_fetcher.downloader import fix_pct_change, verify_data_quality
    fix_pct_change()
    quality = verify_data_quality()

    # 确定数据日期
    data_date = get_data_date()
    today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
    if data_date and data_date != today:
        print(f"⚠️ 数据库最新: {data_date}（非今日 {today}）\n")

    # 1. 宏观
    print("🌤  宏观分析...")
    macro_data = macro.analyze(data_date)

    # 2. 中观
    print("🏭 板块分析...")
    sector_data = sector.analyze()

    # 3. 微观
    print("🎯 个股分析...")
    stock_data = stock.analyze()

    # 4. 格式化（传入质量检查结果）
    markdown = format_report(macro_data, sector_data, stock_data, data_date, quality)

    # 5. 终端输出
    print()
    print(markdown)
    print()

    # 6. 推送钉钉
    send_report(markdown)


if __name__ == "__main__":
    main()
