#!/usr/bin/env python3
"""
半自动化量化交易系统 —— 主入口

用法:
    python run.py              # 更新数据 + 生成信号
    python run.py --no-update  # 只生成信号，不更新数据
    python run.py --verbose    # 显示详细日志

MVP v0.1 功能:
    ✅ 下载/更新沪深300成分股日线数据
    ✅ 双均线策略（MA10/MA30）生成买卖信号
    ✅ 基础风控过滤（ST/涨停/停牌/股价/流动性）
    ✅ 终端美化输出信号列表
    ✅ 小资金仓位建议 + 止损价
"""

import os
import sys

# --- 代理绕过（必须在 import akshare 之前设置）---
# 国内金融数据源直连，不走系统代理（否则 Clash 等代理可能连不上）
_NO_PROXY = (
    "eastmoney.com,sina.com.cn,qq.com,10jqka.com.cn,"
    "csindex.com.cn,tushare.pro,baostock.com"
)
os.environ["NO_PROXY"] = _NO_PROXY
os.environ["no_proxy"] = _NO_PROXY
# macOS 系统代理（Clash/V2Ray等）可能被 requests 自动读取，
# 强制清除让 requests 不使用代理
for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(key, None)

import argparse
from datetime import datetime


def print_header():
    """打印系统标题"""
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║       📊 半自动化量化交易系统 v0.1            ║")
    print("║       方案A：信号生成 → 人工确认 → 手动下单   ║")
    print("╚══════════════════════════════════════════════╝")
    print()


def print_market_status(regime: dict):
    """打印当前大盘择时状态"""
    print(f"🌤  大盘择时")
    if regime['index_close']:
        print(f"   沪深300: {regime['index_close']} | MA20: {regime['ma20']} | MA60: {regime['ma60']}")
    print(f"   状态: {regime['label']} | 仓位系数: {regime['position_ratio']}")
    print(f"   {regime['detail']}")
    if regime['consecutive_down'] >= 3:
        print(f"   ⚠️ 已连续下跌 {regime['consecutive_down']} 天")
    print()


def print_signals(passed: list[dict], rejected: list[dict], capital: float):
    """美化打印交易信号"""
    from engine.risk_filter import calculate_position

    today_str = datetime.now().strftime("%Y-%m-%d")
    tier_label = "超小资金" if capital <= 20000 else \
                 "小资金" if capital <= 50000 else \
                 "中等资金" if capital <= 100000 else "标准资金"

    print(f"{'='*60}")
    print(f"📊 量化信号 {today_str}")
    print(f"{'='*60}")
    print(f"💰 本金：¥{capital:,} | 档位：{tier_label}")
    print()

    # 分类信号
    buy_signals = [s for s in passed if s['action'] == 'BUY']
    sell_signals = [s for s in passed if s['action'] == 'SELL']

    # 买入信号
    if buy_signals:
        print(f"🟢 买入建议（共{len(buy_signals)}条，已过滤ST/涨停/停牌/高价）：")
        for i, sig in enumerate(buy_signals[:10], 1):
            pos = calculate_position(sig, capital)

            print(f"  {i}. {sig['stock_name']}({sig['stock_code']})")
            print(f"     信号：{sig['reason']}")
            print(f"     强度：{sig['strength']:.3f} | 策略：{sig.get('strategy', '-')}")

            if pos['actionable']:
                print(f"     建议：{pos['shares']}股 = ¥{pos['amount']:,.0f}（占{pos['pct']:.1%}）")
                print(f"     🛑 止损：¥{pos['stop_loss']:.2f}（-{pos['stop_loss_pct']:.0%}）")
                if pos.get('warning'):
                    print(f"     ⚠️ {pos['warning']}")
            else:
                print(f"     ❌ {pos['reason']}")
            print()
    else:
        print("🟢 买入建议：今日无买入信号")
        print()

    # 卖出信号
    if sell_signals:
        print(f"🔴 卖出建议（共{len(sell_signals)}条）：")
        for i, sig in enumerate(sell_signals[:10], 1):
            print(f"  {i}. {sig['stock_name']}({sig['stock_code']})")
            print(f"     信号：{sig['reason']}")
            print(f"     强度：{sig['strength']:.3f} | 策略：{sig.get('strategy', '-')}")
            print()
    else:
        print("🔴 卖出建议：今日无卖出信号")
        print()

    # 被过滤的信号
    if rejected:
        print(f"{'─'*60}")
        print(f"📋 今日过滤（{len(rejected)}条信号被排除）：")
        for i, sig in enumerate(rejected[:20], 1):
            print(f"  {i}. {sig['stock_name']}({sig['stock_code']})")
            print(f"     {sig.get('action', '?')} → {sig.get('reject_reason', '未知原因')}")
        if len(rejected) > 20:
            print(f"  ... 共{len(rejected)}条，以上仅显示前20条")
        print()

    print(f"{'─'*60}")
    print(f"💡 提示：")
    if capital <= 20000:
        print(f"  - 小资金阶段优先考虑ETF（单价低、天然分散）")
        print(f"  - 单票占比偏高是正常的，用严格止损保护")
    print(f"  - 以上仅为参考信号，请结合自身判断做决策")
    print(f"  - 下单后记得记录成交信息")
    print(f"{'='*60}")
    print(f"下次运行: python run.py")
    print()


def main():
    parser = argparse.ArgumentParser(description="半自动化量化交易系统")
    parser.add_argument("--no-update", action="store_true",
                        help="跳过数据更新，直接生成信号")
    parser.add_argument("--verbose", action="store_true",
                        help="显示详细日志")
    parser.add_argument("--init", action="store_true",
                        help="首次初始化：建库 + 下载全部历史数据")
    args = parser.parse_args()

    print_header()

    # 初始化模式
    if args.init:
        print("🔧 首次初始化模式：下载全部历史数据（预计5-10分钟）...\n")
        from data_fetcher.downloader import init_database, download_all
        init_database()
        download_all(force_update=True)
        print("\n✅ 数据初始化完成！现在可以运行 python run.py 生成信号")
        return

    # 1. 更新数据
    if not args.no_update:
        from data_fetcher.downloader import init_database, download_all
        init_database()
        download_all()
    else:
        print("⏩ 跳过数据更新\n")

    # 2. 大盘择时（防线一）
    from engine.market_timing import get_market_regime, filter_by_regime
    regime = get_market_regime()
    print_market_status(regime)

    # 3. 运行策略
    from engine.runner import run_strategies
    raw_signals = run_strategies(verbose=args.verbose)

    if not raw_signals:
        print("📭 今日无交易信号")
        print("   （可能原因：所有股票均无均线交叉，或数据尚未下载）")
        print(f"\n   如果是首次运行，请先执行: python run.py --init")
        return

    # 4. 防线二：基础风控过滤
    from engine.risk_filter import filter_signals
    from data_fetcher.cleaner import get_latest_kline_for_all
    from config import settings

    snapshot = get_latest_kline_for_all()
    passed, rejected = filter_signals(raw_signals, snapshot)

    # 5. 防线一：大盘择时过滤（在基础过滤之后）
    passed, regime_blocked = filter_by_regime(passed, regime)
    for sig in regime_blocked:
        sig['reject_reason'] = sig.get('block_reason', '大盘择时拦截')
    rejected.extend(regime_blocked)

    if args.verbose:
        print(f"\n📋 原始信号: {len(raw_signals)} 条")
        print(f"✅ 通过过滤: {len(passed)} 条")
        print(f"❌ 被拒绝:   {len(rejected)} 条")
        if regime_blocked:
            print(f"  其中大盘择时拦截: {len(regime_blocked)} 条\n")

    # 6. 信号持久化
    from engine.signal_store import init_signal_table, save_signals
    init_signal_table()
    today_str = datetime.now().strftime("%Y-%m-%d")
    for sig in passed:
        sig['date'] = today_str
    for sig in rejected:
        sig['date'] = today_str
    save_signals(passed, status='passed')
    save_signals(rejected, status='blocked')

    # 7. 打印信号
    print_signals(passed, rejected, settings.TOTAL_CAPITAL)

    # 8. 推送钉钉
    if passed or rejected:
        from notifier.dingtalk import format_signals, send
        tier_label = get_tier_label(settings.TOTAL_CAPITAL)
        markdown = format_signals(passed, rejected, settings.TOTAL_CAPITAL, tier_label, regime)
        send(markdown)


def get_tier_label(capital: float) -> str:
    if capital <= 20000:
        return "超小资金"
    elif capital <= 50000:
        return "小资金"
    elif capital <= 100000:
        return "中等资金"
    return "标准资金"


if __name__ == "__main__":
    main()
