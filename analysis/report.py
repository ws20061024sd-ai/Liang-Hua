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


def main():
    print("📈 正在生成市场日报...\n")

    # 1. 宏观
    print("🌤  宏观分析...")
    macro_data = macro.analyze()

    # 2. 中观
    print("🏭 板块分析...")
    sector_data = sector.analyze()

    # 3. 微观
    print("🎯 个股分析...")
    stock_data = stock.analyze()

    # 4. 格式化
    markdown = format_report(macro_data, sector_data, stock_data)

    # 5. 终端输出
    print()
    print(markdown)
    print()

    # 6. 推送钉钉
    send_report(markdown)


if __name__ == "__main__":
    main()
