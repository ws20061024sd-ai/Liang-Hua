"""
综合回测——三策略 × 三种择时方案 × 组合对比

三种方案:
  A: 无择时 —— 裸策略，任何时候都交易
  B: 二元择时 —— 弱势/极弱时禁买（防线一基础版）
  C: 权重匹配 —— 根据大盘状态动态调节策略权重（当前实盘方案）

优化: 所有策略信号预先计算一次，三种方案共享 → ~3x 速度提升
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import numpy as np
import time
from config import settings
from strategies.ma_cross import MaCrossStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy


# ============================================================
# 工具函数
# ============================================================

def fetch_hs300_index(start_date: str) -> pd.DataFrame | None:
    """从 AKShare 拉取沪深300指数历史日线"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol='sh000300')
        if df is not None and not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df['close'] = df['close'].astype(float)
            df = df[df['date'] >= pd.Timestamp(start_date)].sort_values('date').reset_index(drop=True)
            return df
    except Exception:
        pass
    return None


def get_market_regime(index_df: pd.DataFrame, date_idx: int) -> dict:
    """在回测的某个时间点上判断大盘状态（与实盘一致）"""
    min_bars = 60
    if date_idx < min_bars:
        return {'regime': 'shaky', 'label': '🟡 数据不足', 'position_ratio': 0.3, 'can_buy': True}

    hist = index_df.iloc[:date_idx + 1]
    close = hist['close']
    latest = close.iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    above_ma20 = latest > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma60 = latest < ma60

    consecutive_down = 0
    for i in range(len(close) - 1, max(0, len(close) - 15), -1):
        if close.iloc[i] < close.iloc[i - 1]:
            consecutive_down += 1
        else:
            break

    if above_ma20 and ma20_above_ma60:
        return {'regime': 'strong', 'label': '🟢 强势', 'position_ratio': 1.0, 'can_buy': True}
    elif above_ma20 and not ma20_above_ma60:
        return {'regime': 'shaky', 'label': '🟡 震荡', 'position_ratio': 0.3, 'can_buy': True}
    elif below_ma60 and consecutive_down >= 10:
        return {'regime': 'crash', 'label': '🔴 极弱', 'position_ratio': 0.0, 'can_buy': False}
    else:
        return {'regime': 'weak', 'label': '🟠 弱势', 'position_ratio': 0.0, 'can_buy': False}


def get_weight_by_regime(strategy_style: str, regime: dict) -> float:
    """返回某策略在当前市场状态下的权重系数（与实盘一致）"""
    if regime['regime'] == 'shaky':
        if strategy_style == 'trend': return 0.3
        elif strategy_style == 'reversion': return 1.2
    elif regime['regime'] == 'strong':
        if strategy_style == 'trend': return 1.2
        elif strategy_style == 'reversion': return 0.5
    return 1.0


# ============================================================
# 预计算阶段：所有股票 × 所有日期 × 所有策略的信号
# ============================================================

def precompute_signals(strategies: list, all_data: dict, all_dates: list) -> dict:
    """
    一次性预计算所有策略信号。
    返回: {(date, code, strategy_name): {'action': 'BUY'/'SELL', 'strength': 0.8, 'price': 12.34}}
    """
    print("   ⚡ 预计算所有策略信号（只算一次）...")
    t0 = time.time()
    signal_cache = {}
    total_signals = 0

    for code, df in all_data.items():
        # 按日期推进
        for date_idx, date in enumerate(all_dates):
            row = df[df['date'] == date]
            if row.empty:
                continue
            hist = df[df['date'] <= date].copy()
            current_price = float(row.iloc[0]['close'])

            for st in strategies:
                min_bars = getattr(st, 'slow_period', None) or getattr(st, 'lookback', None) or getattr(st, 'period', 60)
                if len(hist) < min_bars + 1:
                    continue

                sig = st.run(code, '', hist)
                if sig is not None:
                    key = (date, code, st.name, st.style)
                    signal_cache[key] = {
                        'action': sig['action'],
                        'strength': sig['strength'],
                        'price': current_price,
                    }
                    total_signals += 1

    elapsed = time.time() - t0
    print(f"   ✅ {total_signals} 条信号 ({elapsed:.1f}s)")
    return signal_cache


# ============================================================
# 回测引擎（轻量版——复用预计算信号）
# ============================================================

def run_backtest_from_cache(
    strategies: list,
    index_df: pd.DataFrame,
    all_data: dict,
    all_dates: list,
    signal_cache: dict,
    capital: float,
    max_positions: int,
    per_position_pct: float,
    commission: float,
    timing_mode: str,
):
    """基于预计算信号的回测引擎（不重复计算策略）"""
    cash = capital
    holdings = {}
    trades = []
    daily_values = []
    strategy_styles = {st.name: st.style for st in strategies}

    max_value = capital
    max_dd = 0
    win_trades = 0
    total_trades = 0

    for date_idx, date in enumerate(all_dates):
        regime = get_market_regime(index_df, date_idx) if index_df is not None else {'regime': 'unknown', 'can_buy': True}

        # ---- 止损 ----
        for code in list(holdings.keys()):
            if code in all_data:
                df = all_data[code]
                row = df[df['date'] == date]
                if row.empty:
                    continue
                current_price = float(row.iloc[0]['close'])
                cost = holdings[code]['cost']
                pnl_pct = (current_price - cost) / cost
                if pnl_pct <= -0.05:
                    shares = holdings[code]['shares']
                    cash += shares * current_price * (1 - commission)
                    trades.append({
                        'date': date, 'code': code, 'action': '止损',
                        'price': current_price, 'pnl_pct': round(pnl_pct * 100, 1),
                        'strategy': holdings[code].get('strategy', ''),
                    })
                    total_trades += 1
                    if pnl_pct > 0:
                        win_trades += 1
                    del holdings[code]

        # ---- 从缓存查找信号 ----
        buy_candidates = []
        sell_candidates = []

        for code, df in all_data.items():
            row = df[df['date'] == date]
            if row.empty:
                continue
            current_price = float(row.iloc[0]['close'])

            for st in strategies:
                key = (date, code, st.name, st.style)
                sig = signal_cache.get(key)
                if sig is None:
                    continue

                style = st.style
                action = sig['action']
                strength = sig['strength']

                # 权重调节（full 模式）
                if timing_mode == 'full':
                    weight = get_weight_by_regime(style, regime)
                    strength = round(strength * weight, 3)

                if action == 'BUY' and code not in holdings:
                    if timing_mode in ('binary', 'full') and not regime['can_buy']:
                        continue
                    buy_candidates.append((code, current_price, strength, st.name))
                elif action == 'SELL' and code in holdings:
                    sell_candidates.append((code, current_price, st.name))

        # ---- 执行卖出 ----
        for code, price, strat_name in sell_candidates:
            if code in holdings:
                shares = holdings[code]['shares']
                cost = holdings[code]['cost']
                pnl_pct = (price - cost) / cost
                cash += shares * price * (1 - commission)
                trades.append({
                    'date': date, 'code': code, 'action': '卖出',
                    'price': price, 'pnl_pct': round(pnl_pct * 100, 1),
                    'strategy': strat_name,
                })
                total_trades += 1
                if pnl_pct > 0:
                    win_trades += 1
                del holdings[code]

        # ---- 执行买入 ----
        buy_candidates.sort(key=lambda x: x[2], reverse=True)
        slots = max_positions - len(holdings)
        for code, price, strength, strat_name in buy_candidates[:slots]:
            amount = capital * per_position_pct
            shares = int(amount / price / 100) * 100
            if shares >= 100 and cash >= shares * price * (1 + commission):
                cash -= shares * price * (1 + commission)
                holdings[code] = {'shares': shares, 'cost': price, 'strategy': strat_name}

        # ---- 计算权益 ----
        equity_value = cash
        for code, h in holdings.items():
            if code in all_data:
                row = all_data[code][all_data[code]['date'] == date]
                if not row.empty:
                    equity_value += h['shares'] * float(row.iloc[0]['close'])
        daily_values.append(equity_value)
        max_value = max(max_value, equity_value)
        dd = (max_value - equity_value) / max_value
        max_dd = max(max_dd, dd)

    # ---- 清仓 ----
    final_date = all_dates[-1]
    for code in list(holdings.keys()):
        row = all_data[code][all_data[code]['date'] == final_date]
        if not row.empty:
            price = float(row.iloc[0]['close'])
            shares = holdings[code]['shares']
            cost = holdings[code]['cost']
            pnl_pct = (price - cost) / cost
            cash += shares * price * (1 - commission)
            trades.append({
                'date': final_date, 'code': code, 'action': '清仓',
                'price': price, 'pnl_pct': round(pnl_pct * 100, 1),
                'strategy': holdings[code].get('strategy', ''),
            })
            total_trades += 1
            if pnl_pct > 0:
                win_trades += 1
            del holdings[code]

    # ---- 指标 ----
    final_value = cash
    total_return = (final_value - capital) / capital * 100
    years = len(all_dates) / 252
    annual_return = ((final_value / capital) ** (1 / years) - 1) * 100 if years > 0 else 0

    daily_series = pd.Series(daily_values)
    daily_returns = daily_series.pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0

    win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0
    pnl_list = [t['pnl_pct'] for t in trades]
    avg_win = np.mean([p for p in pnl_list if p > 0]) if any(p > 0 for p in pnl_list) else 0
    avg_loss = abs(np.mean([p for p in pnl_list if p < 0])) if any(p < 0 for p in pnl_list) else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')

    strat_trades = {}
    for t in trades:
        s = t.get('strategy', '')
        strat_trades[s] = strat_trades.get(s, 0) + 1

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'max_dd': max_dd * 100,
        'sharpe': sharpe,
        'win_rate': win_rate,
        'win_trades': win_trades,
        'total_trades': total_trades,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'final_value': final_value,
        'years': years,
        'strat_trades': strat_trades,
        '_trades': trades,
    }


# ============================================================
# 主流程
# ============================================================

def main():
    print("📊 综合回测：三策略 × 三种择时方案对比")
    print("=" * 70)

    t_start = time.time()

    # ---- 加载数据 ----
    conn = sqlite3.connect(settings.DB_PATH)
    codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()
    conn.close()
    print(f"股票池: {len(codes)} 只")

    from data_fetcher.cleaner import get_batch_stock_data
    print("加载股票数据...")
    t0 = time.time()
    all_data = get_batch_stock_data(codes, days=9999)
    print(f"   ✅ {time.time() - t0:.1f}s")

    # 构建日期列表
    all_dates = set()
    for code, df in all_data.items():
        all_dates.update(df['date'].tolist())
    all_dates = sorted(all_dates)

    # 用户可调整回测起点
    START_DATE = '2019-01-01'
    all_dates = [d for d in all_dates if d >= pd.Timestamp(START_DATE)]

    # ---- 加载指数 ----
    print("加载沪深300指数数据...")
    t0 = time.time()
    start_str = all_dates[0].strftime('%Y-%m-%d')
    index_df = fetch_hs300_index(start_str)
    if index_df is not None:
        idx_start = index_df['date'].iloc[0]
        idx_end = index_df['date'].iloc[-1]
        all_dates = [d for d in all_dates if idx_start <= d <= idx_end]
        print(f"   ✅ {len(index_df)} 根K线 ({time.time() - t0:.1f}s)")
    else:
        print("   ⚠️ 无法获取指数数据")

    years = len(all_dates) / 252
    print(f"回测区间: {all_dates[0].strftime('%Y-%m-%d')} ~ {all_dates[-1].strftime('%Y-%m-%d')}")
    print(f"交易天数: {len(all_dates)} ({years:.1f}年)\n")

    # ---- 策略 ----
    strategies = [
        MaCrossStrategy(),
        MomentumBreakoutStrategy(),
        MeanReversionStrategy(),
    ]
    print(f"策略: {', '.join(s.name for s in strategies)}")

    # ---- 预计算信号 ----
    signal_cache = precompute_signals(strategies, all_data, all_dates)

    # ---- 回测参数 ----
    capital = 100000
    max_positions = 10
    per_position_pct = 0.10
    commission = 0.0008

    # ---- 三种方案 ----
    modes = [
        ('A: 无择时', 'none', '裸策略，任何市况都交易'),
        ('B: 二元择时', 'binary', '弱势/极弱禁买（防线一基础版）'),
        ('C: 权重匹配', 'full', '根据大盘状态动态调节策略权重（当前实盘方案）'),
    ]

    results = {}
    for label, mode, desc in modes:
        print(f"正在回测: {label} — {desc}")
        t0 = time.time()
        r = run_backtest_from_cache(strategies, index_df, all_data, all_dates,
                                     signal_cache, capital, max_positions,
                                     per_position_pct, commission, mode)
        results[label] = r
        print(f"   ⚡ {time.time() - t0:.1f}s | 年化 {r['annual_return']:+.1f}% | "
              f"回撤 {r['max_dd']:.1f}% | 夏普 {r['sharpe']:.2f} | "
              f"交易 {r['total_trades']} 笔\n")

    total_elapsed = time.time() - t_start
    print(f"⏱️  总耗时: {total_elapsed:.1f}s ({total_elapsed/60:.1f}分钟)")

    # ---- 输出对比表 ----
    print("\n" + "=" * 70)
    print("📈 三方案对比")
    print("=" * 70)
    print(f"{'方案':<16} {'年化':>8} {'回撤':>8} {'夏普':>7} {'胜率':>7} {'盈亏比':>7} {'交易':>6}")
    print("-" * 70)
    for label, r in results.items():
        print(f"{label:<16} {r['annual_return']:>+7.1f}% {r['max_dd']:>7.1f}% "
              f"{r['sharpe']:>6.2f} {r['win_rate']:>6.1f}% {r['profit_factor']:>6.2f} {r['total_trades']:>5}")

    # ---- 改善幅度 ----
    base = results.get('A: 无择时', {})
    if base:
        print("\n📊 择时改善幅度（vs 无择时）：")
        for label, r in results.items():
            if label == 'A: 无择时':
                continue
            dr = r['annual_return'] - base.get('annual_return', 0)
            dd = r['max_dd'] - base.get('max_dd', 0)
            ds = r['sharpe'] - base.get('sharpe', 0)
            print(f"  {label}: 年化 {dr:+.1f}pp | 回撤 {dd:+.1f}pp | 夏普 {ds:+.2f}")

    # ---- 市场状态 ----
    if index_df is not None and len(all_dates) > 60:
        print("\n📊 回测期市场状态分布：")
        rc = {'strong': 0, 'shaky': 0, 'weak': 0, 'crash': 0}
        for i in range(60, len(all_dates)):
            r = get_market_regime(index_df, i)
            rc[r['regime']] = rc.get(r['regime'], 0) + 1
        total_d = sum(rc.values())
        labels_map = {'strong': '🟢 强势', 'shaky': '🟡 震荡', 'weak': '🟠 弱势', 'crash': '🔴 极弱'}
        for reg, cnt in rc.items():
            if cnt > 0:
                print(f"  {labels_map.get(reg, reg)}: {cnt} 天 ({cnt/total_d*100:.0f}%)")

    # ---- 导出蒙特卡洛数据 ----
    full_result = results.get('C: 权重匹配')
    if full_result:
        export_trades_for_monte_carlo(full_result['_trades'], full_result)

    print("\n" + "=" * 70)
    print("💡 A→B = 择时纯价值 | B→C = 权重匹配增量")
    print("=" * 70)


def export_trades_for_monte_carlo(trades: list, result: dict, filepath: str = None):
    """保存回测交易记录供蒙特卡洛模拟"""
    import json
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), '..', 'data', 'backtest_trades.json')
    pnl_list = [t['pnl_pct'] for t in trades]
    with open(filepath, 'w') as f:
        json.dump({
            'mode': 'full',
            'total_trades': len(pnl_list),
            'pnl_sequence': pnl_list,
            'trades': [{'pnl_pct': t['pnl_pct'], 'strategy': t.get('strategy', ''),
                         'action': t['action'], 'date': str(t['date'])} for t in trades],
            'summary': {
                'annual_return': result['annual_return'],
                'max_dd': result['max_dd'],
                'sharpe': result['sharpe'],
                'win_rate': result['win_rate'],
                'profit_factor': result['profit_factor'],
                'total_trades': result['total_trades'],
            }
        }, f, ensure_ascii=False)
    print(f"📦 交易记录已保存 → {filepath}")


if __name__ == "__main__":
    main()
