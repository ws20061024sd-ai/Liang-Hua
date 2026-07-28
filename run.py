#!/usr/bin/env python3
"""
半自动化量化交易系统 —— 主入口

用法:
    python run.py              # 更新数据 + 生成信号
    python run.py --no-update  # 只生成信号，不更新数据
    python run.py --verbose    # 显示详细日志

v0.2.0 功能:
    ✅ 三策略并行（双均线/动量突破/均值回归）
    ✅ 数据质量五层保障（重试+修复+检查+一致+兜底）
    ✅ 大盘择时（四档状态判断+策略权重匹配）
    ✅ 多策略信号交叉确认（⭐⭐/⭐/⚠️）
    ✅ 两道防线过滤（基础风控+大盘择时）
    ✅ 钉钉推送（信号+市场日报）
    ✅ 小资金仓位建议 + 止损价
"""

import os
import sys

# --- 代理绕过（必须在 import akshare 之前设置）---
from data_fetcher.proxy_cleanup import cleanup_proxy
cleanup_proxy()

import argparse
from datetime import datetime


def print_header():
    """打印系统标题"""
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║       📊 半自动化量化交易系统 v0.3.0          ║")
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


def print_signals(aggregated: list[dict], rejected: list[dict], capital: float):
    """美化打印交易信号（支持多策略交叉确认）"""
    from engine.risk_filter import calculate_position

    today_str = datetime.now().strftime("%Y-%m-%d")
    tier_label = get_tier_label(capital)

    print(f"{'='*60}")
    print(f"📊 量化信号 {today_str}")
    print(f"{'='*60}")
    print(f"💰 本金：¥{capital:,} | 档位：{tier_label}")
    strategy_count = len(set(s['name'] for sig in aggregated for s in sig.get('strategies', [])))
    if strategy_count > 1:
        print(f"🔧 策略：{strategy_count}个并行 | ⭐⭐=多策略确认")
    print()

    buy_agg = [s for s in aggregated if s['action'] == 'BUY']
    sell_agg = [s for s in aggregated if s['action'] == 'SELL']

    # 买入信号
    if buy_agg:
        print(f"🟢 买入建议（共{len(buy_agg)}条，已过滤ST/涨停/停牌/高价）：")
        for i, sig in enumerate(buy_agg[:10], 1):
            pos = calculate_position(sig, capital)
            stars = "⭐⭐" if sig['confirm'] >= 2 else "⭐"
            conflict_note = " ⚠️策略冲突" if sig.get('conflict') else ""

            weight_note = ""
            for s in sig['strategies']:
                note = s.get('regime_note', '')
                if '增强' in note:
                    weight_note = " 🔼增强"
                    break
                elif '降权' in note:
                    weight_note = " 🔽降权"
                    break

            print(f"  {i}. {sig['stock_name']}({sig['stock_code']}) {stars}{conflict_note}{weight_note}")
            for s in sig['strategies']:
                note = s.get('regime_note', '')
                note_text = f" ({note})" if note else ""
                print(f"     [{s['name']}] {s['reason']}{note_text}")

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
    if sell_agg:
        total = len(sell_agg)
        show = min(total, 5)
        if total > 5:
            print(f"🔴 卖出建议（前{show}/{total}条）：")
        else:
            print(f"🔴 卖出建议（共{total}条）：")
        for i, sig in enumerate(sell_agg[:show], 1):
            stars = "⭐⭐" if sig['confirm'] >= 2 else "⭐"
            print(f"  {i}. {sig['stock_name']}({sig['stock_code']}) {stars}")
            for s in sig['strategies']:
                print(f"     [{s['name']}] {s['reason']}")
            print()
    else:
        print("🔴 卖出建议：今日无卖出信号")
        print()

    # 被过滤的信号
    if rejected:
        print(f"{'─'*60}")
        print(f"📋 今日过滤（{len(rejected)}条信号被排除）：")
        for i, sig in enumerate(rejected[:10], 1):
            print(f"  {i}. {sig['stock_name']}({sig['stock_code']})")
            print(f"     {sig.get('action', '?')} → {sig.get('reject_reason', '未知原因')}")
        if len(rejected) > 10:
            print(f"  ... 共{len(rejected)}条，以上仅显示前10条")
        print()

    print(f"{'─'*60}")
    print(f"💡 提示：")
    if capital <= 20000:
        print(f"  - 小资金阶段优先考虑ETF（单价低、天然分散）")
        print(f"  - 单票占比偏高是正常的，用严格止损保护")
    print(f"  - ⭐⭐ 双策略确认信号优先关注")
    print(f"  - 以上仅为参考信号，请结合自身判断做决策")
    print(f"{'='*60}")
    print(f"下次运行: python run.py")
    print()


def main():
    from config.logging_config import setup_logging
    log = setup_logging("run")

    parser = argparse.ArgumentParser(description="半自动化量化交易系统")
    parser.add_argument("--no-update", action="store_true",
                        help="跳过数据更新，直接生成信号")
    parser.add_argument("--verbose", action="store_true",
                        help="显示详细日志")
    parser.add_argument("--init", action="store_true",
                        help="首次初始化：建库 + 下载全部历史数据")
    args = parser.parse_args()

    print_header()
    log.info("量化交易系统启动")

    # 自动数据库迁移：缺列补列，零手工
    from scripts.ensure_schema import ensure_schema
    ensure_schema()

    # 初始化模式
    if args.init:
        print("🔧 首次初始化模式：下载全部历史数据（预计5-10分钟）...\n")
        from data_fetcher.downloader import init_database, download_all
        init_database()
        download_all(force_update=True)
        print("\n✅ 数据初始化完成！现在可以运行 python run.py 生成信号")
        return

    data_fresh = True  # 数据是否新鲜（今日的）
    max_date = None

    if not args.no_update:
        from config import settings
        from data_fetcher.downloader import init_database, download_all, fix_pct_change, verify_data_quality
        init_database()
        download_all()
        fix_pct_change()

        # 财务数据（估值因子：PE/PB/ROE）
        try:
            from data_fetcher.financial_downloader import download_all_financial, fix_financial_data
            download_all_financial()
            fix_financial_data()
            # 下载后检查数据覆盖率
            fin_ok = verify_financial_coverage()
            if not fin_ok:
                print("   ⚠️ 财务数据覆盖率不足，多因子引擎的估值/质量因子将受影响")
        except Exception as e:
            print(f"⚠️ 财务数据下载跳过: {e}")

        # ROE数据（季度更新，仅覆盖率不足时刷新，避免每次跑154秒）
        try:
            import sqlite3 as _sq
            _c = _sq.connect(settings.DB_PATH)
            _rq = _c.execute("SELECT MAX(date) FROM financial_roe").fetchone()[0]
            _rc = _c.execute(
                f"SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=(SELECT date FROM financial_roe GROUP BY date HAVING COUNT(DISTINCT code)>={settings.ROE_MIN_STOCKS} ORDER BY date DESC LIMIT 1)"
            ).fetchone()[0] if _rq else 0
            _c.close()
            if _rc < settings.ROE_MIN_STOCKS:
                print("   📊 ROE数据覆盖率不足，开始下载...")
                from data_fetcher.roe_downloader import download_all as download_all_roe
                download_all_roe()
            else:
                print(f"   ✅ ROE数据达标（≥255只），跳过下载")
        except Exception as e:
            print(f"   ⚠️ ROE数据下载跳过: {e}")

        # 板块数据缓存（为行业轮动积累历史数据，兜底 report.py 未运行的情况）
        try:
            from analysis.sector_trend import init_sector_table, save_today_sectors
            init_sector_table()
            saved = save_today_sectors()
            if saved:
                print("   📊 板块数据已缓存")
        except Exception as e:
            print(f"   ⚠️ 板块数据缓存失败（不影响信号）: {e}")

        q = verify_data_quality()
        if not q['ok'] and not args.no_update:
            log.warning(f"数据质量检查未通过: {q.get('issues', [])}")
            print("⚠️ 数据质量检查未通过，信号可能基于旧数据生成")
        import sqlite3
        conn = sqlite3.connect("data/stocks.db")
        max_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        conn.close()
        today_str = datetime.now().strftime("%Y-%m-%d")
        if max_date != today_str:
            from data_fetcher.trading_calendar import is_trading_day
            if not is_trading_day():
                print(f"📅 今日非交易日，数据截止 {max_date}，跳过\n")
                return
            else:
                print(f"⚠️ 数据未更新到今日（最新: {max_date}），可能是数据源延迟\n")
                data_fresh = False
    else:
        print("⏩ 跳过数据更新\n")

    # 数据校验（阻断模式：不通过则不推信号，改推告警）
    if not args.no_update:
        import subprocess
        result = subprocess.run(
            [sys.executable, 'scripts/data_check.py', '--block'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            log.error("数据校验未通过，跳过信号生成")
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            from notifier.dingtalk import send
            send(f"## ⚠️ 数据校验未通过\n\n{result.stdout[-300:]}")
            return

    # 2. 大盘择时（防线一）
    from engine.market_timing import get_market_regime, filter_by_regime
    regime = get_market_regime()
    print_market_status(regime)

    # 3. 运行策略
    from engine.runner import run_strategies
    raw_signals = run_strategies(verbose=args.verbose)

    log.info(f"策略运行完成: {len(raw_signals)} 条原始信号")

    # 无信号时也推送
    if not raw_signals:
        print("📭 今日无交易信号\n")
        from notifier.dingtalk import send
        today_s = datetime.now().strftime("%Y-%m-%d")
        status_note = f"数据日期: {max_date}" if not data_fresh else ""
        msg = (
            f"## 📊 量化信号 {today_s}\n\n---\n\n"
            f"### {regime['label']}\n\n"
            f"今日无交易信号（300只股票均无均线交叉）\n\n"
        )
        if not data_fresh:
            msg += f"> ⚠️ {status_note}\n\n"
        msg += "---\n> 量化自动推送"
        send(msg)
        log.info("无信号推送完成")
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

    # 6. 多策略信号汇总（交叉确认/冲突检测）
    from engine.signal_aggregator import aggregate
    aggregated = aggregate(passed)

    # 8. 信号持久化（使用 DB 实际数据日期，而非 datetime.now()）
    from engine.signal_store import init_signal_table, save_signals
    init_signal_table()
    # 从 DB 获取数据日期（优先用已查询的 max_date，否则重新查询）
    if max_date is None:
        import sqlite3
        conn = sqlite3.connect("data/stocks.db")
        max_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        conn.close()
    signal_date = max_date if max_date else datetime.now().strftime("%Y-%m-%d")
    for sig in passed:
        sig['date'] = signal_date
    for sig in rejected:
        sig['date'] = signal_date
    save_signals(passed, status='passed')
    save_signals(rejected, status='blocked')

    # 9. 打印信号（用汇总后的格式）
    print_signals(aggregated, rejected, settings.TOTAL_CAPITAL)

    # 10. 推送钉钉
    if aggregated or rejected:
        from notifier.dingtalk import format_signals, send
        tier_label = get_tier_label(settings.TOTAL_CAPITAL)
        markdown = format_signals(aggregated, rejected, settings.TOTAL_CAPITAL, tier_label, regime)
        ok = send(markdown)
        log.info(f"信号推送: {'成功' if ok else '失败'} | "
                 f"通过{len(passed)}条 拦截{len(rejected)}条 汇总{len(aggregated)}条")
        if not ok:
            log.error("钉钉推送失败！请检查网络或 Webhook 配置")


def get_tier_label(capital: float) -> str:
    if capital <= 20000:
        return "超小资金"
    elif capital <= 50000:
        return "小资金"
    elif capital <= 100000:
        return "中等资金"
    return "标准资金"


def verify_financial_coverage() -> bool:
    """检查 financial_data 表是否覆盖足够的股票"""
    import sqlite3
    from config import settings
    try:
        conn = sqlite3.connect(settings.DB_PATH)
        max_date = conn.execute("SELECT MAX(date) FROM financial_data").fetchone()[0]
        if max_date is None:
            conn.close()
            return False
        stock_cnt = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM financial_data WHERE date=?",
            (max_date,)
        ).fetchone()[0]
        conn.close()
        if stock_cnt < settings.FINANCIAL_MIN_STOCKS:
            print(f"   ⚠️ 财务数据仅覆盖 {stock_cnt}/{settings.FINANCIAL_MIN_STOCKS} 只股票")
            return False
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
