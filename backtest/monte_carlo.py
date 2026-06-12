"""
蒙特卡洛模拟 —— 用随机打乱交易顺序来验证策略的统计可靠性

原理：
  回测只跑了一条路径（实际发生的历史）。
  把回测中所有交易的盈亏%随机打乱 → 产生一条新的假想路径。
  重复 1000 次 → 得到策略在所有可能结果中的分布。

回答三个问题：
  1. 回测的收益是中位数水平还是运气爆棚？
  2. 最差情况下会亏多少？
  3. 策略亏钱的概率有多大？

用法：
  python backtest/monte_carlo.py               # 从保存的 JSON 加载
  python backtest/monte_carlo.py --recompute   # 重新跑回测再模拟
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import random
import numpy as np
import pandas as pd
from collections import defaultdict


# ============================================================
# 模拟引擎
# ============================================================

def _split_into_blocks(pnl_sequence: list, block_size: int = 20) -> list:
    """将交易序列分成固定大小的块（约一个月交易量）"""
    blocks = []
    for i in range(0, len(pnl_sequence), block_size):
        block = pnl_sequence[i:i + block_size]
        if len(block) >= 5:  # 忽略太小的尾块
            blocks.append(block)
    return blocks


def _block_compound(block: list) -> float:
    """计算一个块内交易的复合收益率"""
    result = 1.0
    for pnl in block:
        result *= (1 + pnl / 100)
    return (result - 1) * 100  # 返回百分比


def run_simulation(pnl_sequence: list, num_sims: int = 1000, years: int = 3) -> dict:
    """
    月度块 bootstrap 蒙特卡洛模拟

    方法：
      1. 将交易按 20 笔一组分成块（模拟一个月）
      2. 计算每块的复合收益率
      3. 有放回地抽样 years×12 个月 → 模拟一条年度收益路径
      4. 重复 num_sims 次

    这比逐笔 bootstrap 更保守、更真实——保留了块内交易的相关性，
    模拟了"市场环境变化"而非"单笔交易随机发生"。

    返回:
        annual_returns: [num_sims 个年化收益率%]
        max_drawdowns:  [num_sims 个最大回撤%]
    """
    block_size = 20
    blocks = _split_into_blocks(pnl_sequence, block_size)

    if len(blocks) < 12:
        # 块太少，缩小块大小
        block_size = max(5, len(pnl_sequence) // 36)
        blocks = _split_into_blocks(pnl_sequence, block_size)

    block_returns = [_block_compound(b) for b in blocks]
    months_per_year = 12
    total_months = years * months_per_year

    annual_returns = []
    max_drawdowns = []

    random.seed(42)
    np.random.seed(42)

    for sim in range(num_sims):
        # 有放回抽取月度块
        sampled_blocks = np.random.choice(block_returns, size=total_months, replace=True)

        # 计算月度权益曲线
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        monthly_values = [1.0]

        for mr in sampled_blocks:
            equity *= (1 + mr / 100)
            monthly_values.append(equity)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        total_return = (equity - 1) * 100
        ann_return = ((equity) ** (1 / years) - 1) * 100

        annual_returns.append(ann_return)
        max_drawdowns.append(max_dd * 100)

    annual_returns = np.array(annual_returns)
    max_drawdowns = np.array(max_drawdowns)

    # 统计
    median = np.percentile(annual_returns, 50)
    worst5 = np.percentile(annual_returns, 5)
    worst1 = np.percentile(annual_returns, 1)
    best5 = np.percentile(annual_returns, 95)
    best1 = np.percentile(annual_returns, 99)

    pct_positive = np.sum(annual_returns > 0) / num_sims * 100

    return {
        'annual_returns': annual_returns,
        'max_drawdowns': max_drawdowns,
        'n_trades': len(pnl_sequence),
        'n_blocks': len(blocks),
        'block_size': block_size,
        'num_sims': num_sims,
        'median': median,
        'worst5': worst5,
        'worst1': worst1,
        'best5': best5,
        'best1': best1,
        'pct_positive': pct_positive,
        'mean': np.mean(annual_returns),
        'std': np.std(annual_returns),
        'median_dd': np.median(max_drawdowns),
        'worst_dd': np.max(max_drawdowns),
        'block_returns': block_returns,  # 供 debug
    }


# ============================================================
# 按策略分解模拟
# ============================================================

def simulate_by_strategy(trades: list, num_sims: int = 500) -> dict:
    """按策略分别模拟（块 bootstrap）"""
    by_strat = defaultdict(list)
    for t in trades:
        s = t.get('strategy', 'unknown')
        by_strat[s].append(t['pnl_pct'])

    results = {}
    for name, pnl_list in by_strat.items():
        if len(pnl_list) >= 30:  # 至少 30 笔才有统计意义
            results[name] = run_simulation(pnl_list, num_sims=num_sims, years=3)
            results[name]['n_trades'] = len(pnl_list)
    return results


# ============================================================
# 终端报告
# ============================================================

def print_report(mc: dict, strat_mc: dict = None, backtest_annual: float = None):
    """打印蒙特卡洛分析报告（年化收益率%）"""
    print()
    print("=" * 65)
    print("🎲 蒙特卡洛模拟报告（月度块 Bootstrap）")
    print("=" * 65)
    print(f"   交易笔数: {mc['n_trades']} → {mc['n_blocks']} 个块（每块 ~{mc['block_size']} 笔）")
    print(f"   模拟次数: {mc['num_sims']} 条年化路径")
    print()

    # 收益分布
    print("📊 年化收益率分布:")
    print(f"   最好 1%:  {mc['best1']:+.1f}%")
    print(f"   最好 5%:  {mc['best5']:+.1f}%")
    print(f"   中位数:   {mc['median']:+.1f}%")
    print(f"   平均值:   {mc['mean']:+.1f}%")
    print(f"   最差 5%:  {mc['worst5']:+.1f}%")
    print(f"   最差 1%:  {mc['worst1']:+.1f}%")
    print(f"   标准差:   {mc['std']:.1f}%")
    print()

    # 关键结论
    print("🎯 关键结论:")
    if mc['pct_positive'] > 90:
        rating = "✅ 策略统计上非常可靠"
    elif mc['pct_positive'] > 80:
        rating = "✅ 策略大概率有效"
    elif mc['pct_positive'] > 60:
        rating = "⚠️ 策略有优势但风险不低"
    else:
        rating = "❌ 策略接近随机，需改进"
    print(f"   盈利概率: {mc['pct_positive']:.1f}%  → {rating}")

    if mc['worst5'] > 10:
        print(f"   最差 5% 年化: {mc['worst5']:+.1f}%  → ✅ 倒霉年份也赚不少")
    elif mc['worst5'] > 0:
        print(f"   最差 5% 年化: {mc['worst5']:+.1f}%  → ✅ 倒霉年份仍能盈利")
    else:
        print(f"   最差 5% 年化: {mc['worst5']:+.1f}%  → ⚠️ 有亏损可能，控制仓位")

    print(f"   回撤中位数: {mc['median_dd']:.1f}%")
    print(f"   回撤最差:   {mc['worst_dd']:.1f}%")
    print()

    # 回测对比
    if backtest_annual is not None:
        percentile = np.sum(mc['annual_returns'] <= backtest_annual) / mc['num_sims'] * 100
        print(f"📈 回测年化 {backtest_annual:+.1f}% vs 模拟分布:")
        if percentile > 90:
            print(f"   回测优于 {percentile:.0f}% 的模拟路径 → ⚠️ 可能高估（回测运气好）")
        elif percentile > 50:
            print(f"   回测优于 {percentile:.0f}% 的模拟路径 → 偏乐观但合理")
        elif percentile > 20:
            print(f"   回测优于 {percentile:.0f}% 的模拟路径 → 偏保守，实盘可能更好")
        else:
            print(f"   回测优于 {percentile:.0f}% 的模拟路径 → 非常保守的估计")
        print()

    # 收益分布直方图
    print("📊 年化收益率分布直方图:")
    bins = _make_histogram(mc['annual_returns'], bins=10)
    for label, bar in bins:
        print(f"   {label}% {bar}")

    print()
    print("─" * 65)

    # 按策略分解
    if strat_mc:
        print()
        print("🔍 按策略分解（年化收益率）:")
        print(f"   {'策略':<16} {'笔数':>5} {'中位数':>8} {'最差5%':>8} {'盈利%':>7}")
        print("   " + "-" * 50)
        for name, r in strat_mc.items():
            print(f"   {name:<16} {r['n_trades']:>5} {r['median']:>+7.1f}% "
                  f"{r['worst5']:>+7.1f}% {r['pct_positive']:>6.1f}%")

    print()
    print("💡 解读指南:")
    print("   1. 盈利概率 > 90% → 策略优势是真实的，不是碰运气")
    print("   2. 中位数 > 0   → 策略长期期望为正")
    print("   3. 最差 5% > 0  → 即使连续倒霉大概率也能盈利")
    print("   4. 标准差越小  → 策略越稳定（不同市场环境表现一致）")
    print("=" * 65)


def _make_histogram(values: np.ndarray, bins: int = 10):
    """生成 ASCII 直方图"""
    hist, edges = np.histogram(values, bins=bins)
    max_h = max(hist) if max(hist) > 0 else 1
    result = []
    for i in range(len(hist)):
        label = f"{edges[i]:>+6.0f}~{edges[i+1]:>+6.0f}"
        bar = "█" * int(hist[i] / max_h * 40)
        result.append((label, bar))
    return result


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="蒙特卡洛模拟")
    parser.add_argument("--recompute", action="store_true",
                       help="重新运行回测（否则从 JSON 加载）")
    parser.add_argument("--sims", type=int, default=1000,
                       help="模拟次数（默认 1000）")
    parser.add_argument("--mode", type=str, default="full",
                       choices=['none', 'binary', 'full'],
                       help="回测方案（默认 full=权重匹配）")
    args = parser.parse_args()

    json_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'backtest_trades.json')

    # 尝试加载已有交易记录
    if not args.recompute and os.path.exists(json_path):
        print("📂 从缓存加载交易记录...")
        with open(json_path) as f:
            data = json.load(f)
        trades_pnl = data['pnl_sequence']
        trades_full = [{'pnl_pct': p, 'strategy': ''} for p in trades_pnl]
        print(f"   已加载 {len(trades_pnl)} 笔交易")
    else:
        # 运行回测获取交易记录
        print("🔄 运行综合回测...")
        import sqlite3
        from config import settings
        from strategies.ma_cross import MaCrossStrategy
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        from strategies.mean_reversion import MeanReversionStrategy
        from data_fetcher.cleaner import get_batch_stock_data

        conn = sqlite3.connect(settings.DB_PATH)
        codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()
        conn.close()

        all_data = get_batch_stock_data(codes, days=9999)
        all_dates_set = set()
        for code, df in all_data.items():
            all_dates_set.update(df['date'].tolist())
        all_dates = sorted(all_dates_set)
        all_dates = [d for d in all_dates if d >= pd.Timestamp('2020-01-01')]

        from backtest.comprehensive_backtest import fetch_hs300_index
        start_str = all_dates[0].strftime('%Y-%m-%d') if all_dates else '2020-01-01'
        index_df = fetch_hs300_index(start_str)

        strategies = [MaCrossStrategy(), MomentumBreakoutStrategy(), MeanReversionStrategy()]

        from backtest.comprehensive_backtest import run_backtest
        r = run_backtest(strategies, index_df, all_data, all_dates,
                        100000, 10, 0.10, 0.0008, args.mode)

        trades_full = r['_trades']
        trades_pnl = [t['pnl_pct'] for t in trades_full]

        # 保存
        with open(json_path, 'w') as f:
            json.dump({
                'mode': args.mode,
                'total_trades': len(trades_pnl),
                'pnl_sequence': trades_pnl,
                'summary': {
                    'annual_return': r['annual_return'],
                    'max_dd': r['max_dd'],
                    'sharpe': r['sharpe'],
                    'win_rate': r['win_rate'],
                    'profit_factor': r['profit_factor'],
                }
            }, f)
        print(f"   已保存 → {json_path}")

    # 运行蒙特卡洛
    print(f"\n🎲 开始 {args.sims} 次模拟...")
    mc = run_simulation(trades_pnl, num_sims=args.sims)

    # 按策略分解（如果有策略信息）
    strat_mc = None
    if trades_full and any(t.get('strategy') for t in trades_full):
        strat_mc = simulate_by_strategy(trades_full, num_sims=min(args.sims, 500))

    backtest_annual = data.get('summary', {}).get('annual_return')
    print_report(mc, strat_mc, backtest_annual)


if __name__ == "__main__":
    main()
