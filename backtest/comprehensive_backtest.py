"""
综合回测——三策略 × 三种择时方案 × 组合对比

三种方案:
  A: 无择时 —— 裸策略，任何时候都交易
  B: 二元择时 —— 弱势/极弱时禁买（当前 simple_backtest 的逻辑）
  C: 权重匹配 —— 根据大盘状态动态调节策略权重（当前实盘 market_timing 逻辑）

这是对实盘 market_timing.filter_by_regime() 的精准回测验证。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import numpy as np
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
    """
    在回测的某个时间点上判断大盘状态
    和实盘 market_timing.get_market_regime() 逻辑完全一致
    """
    min_bars = 60
    if date_idx < min_bars:
        return {
            'regime': 'shaky', 'label': '🟡 数据不足',
            'position_ratio': 0.3, 'can_buy': True,
        }

    hist = index_df.iloc[:date_idx + 1]
    close = hist['close']

    latest = close.iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]

    above_ma20 = latest > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma60 = latest < ma60

    # 连续下跌
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
    """
    返回某策略在当前市场状态下的权重系数
    和实盘 market_timing.filter_by_regime() 逻辑完全一致
    """
    if regime['regime'] == 'shaky':
        if strategy_style == 'trend':
            return 0.3
        elif strategy_style == 'reversion':
            return 1.2
    elif regime['regime'] == 'strong':
        if strategy_style == 'trend':
            return 1.2
        elif strategy_style == 'reversion':
            return 0.5
    return 1.0


# ============================================================
# 回测引擎
# ============================================================

def run_backtest(
    strategies: list,
    index_df: pd.DataFrame,
    all_data: dict,
    all_dates: list,
    capital: float,
    max_positions: int,
    per_position_pct: float,
    commission: float,
    timing_mode: str,  # 'none' | 'binary' | 'full'
):
    """
    回测引擎

    timing_mode:
      'none'   — 完全不做择时
      'binary' — 二元择时：弱势/极弱禁买
      'full'   — 权重匹配：根据 regime 动态调节各策略权重
    """
    cash = capital
    holdings = {}  # {code: {'shares': int, 'cost': float, 'strategy': str}}
    trades = []
    daily_values = []

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

        # ---- 计算信号 ----
        buy_signals = []
        sell_signals = []

        for code, df in all_data.items():
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
                if sig is None:
                    continue

                # 权重调节（仅在 full 模式下生效）
                weight = get_weight_by_regime(st.style, regime) if timing_mode == 'full' else 1.0
                sig['strength'] = round(sig['strength'] * weight, 3)

                if sig['action'] == 'BUY' and code not in holdings:
                    # 二元/full 择时：禁买时跳过
                    if timing_mode in ('binary', 'full') and not regime['can_buy']:
                        continue
                    buy_signals.append((code, current_price, sig['strength'], st.name))
                elif sig['action'] == 'SELL' and code in holdings:
                    sell_signals.append((code, current_price, st.name))

        # ---- 执行卖出 ----
        for code, price, strat_name in sell_signals:
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
        buy_signals.sort(key=lambda x: x[2], reverse=True)
        slots = max_positions - len(holdings)
        for code, price, strength, strat_name in buy_signals[:slots]:
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

    # 盈亏比
    pnl_list = [t['pnl_pct'] for t in trades]
    avg_win = np.mean([p for p in pnl_list if p > 0]) if any(p > 0 for p in pnl_list) else 0
    avg_loss = abs(np.mean([p for p in pnl_list if p < 0])) if any(p < 0 for p in pnl_list) else 0
    profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')

    # 各策略交易数
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
    }


# ============================================================
# 主流程
# ============================================================

def main():
    print("📊 综合回测：三策略 × 三种择时方案对比")
    print("=" * 70)

    # ---- 加载数据 ----
    conn = sqlite3.connect(settings.DB_PATH)
    codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()
    conn.close()
    print(f"股票池: {len(codes)} 只")

    from data_fetcher.cleaner import get_batch_stock_data
    print("加载股票数据...")
    all_data = get_batch_stock_data(codes, days=9999)

    # 构建日期列表
    all_dates = set()
    for code, df in all_data.items():
        all_dates.update(df['date'].tolist())
    all_dates = sorted(all_dates)
    all_dates = [d for d in all_dates if d >= pd.Timestamp('2020-01-01')]

    # ---- 加载沪深300指数 ----
    print("加载沪深300指数数据...")
    start_str = all_dates[0].strftime('%Y-%m-%d') if all_dates else '2020-01-01'
    index_df = fetch_hs300_index(start_str)
    if index_df is not None:
        print(f"   指数数据: {len(index_df)} 根K线")
        # 对齐日期范围
        idx_start = index_df['date'].iloc[0]
        idx_end = index_df['date'].iloc[-1]
        all_dates = [d for d in all_dates if idx_start <= d <= idx_end]
    else:
        print("   ⚠️ 无法获取指数数据，回退到无择时模式")

    print(f"回测区间: {all_dates[0].strftime('%Y-%m-%d')} ~ {all_dates[-1].strftime('%Y-%m-%d')}")
    print(f"交易天数: {len(all_dates)}\n")

    # ---- 策略 ----
    strategies = [
        MaCrossStrategy(),
        MomentumBreakoutStrategy(),
        MeanReversionStrategy(),
    ]
    print(f"策略: {', '.join(s.name for s in strategies)}\n")

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
        r = run_backtest(strategies, index_df, all_data, all_dates,
                        capital, max_positions, per_position_pct, commission, mode)
        results[label] = r
        print(f"   年化 {r['annual_return']:+.1f}% | 回撤 {r['max_dd']:.1f}% | 夏普 {r['sharpe']:.2f}")
        print(f"   交易 {r['total_trades']} 笔 | 胜率 {r['win_rate']:.1f}% | 盈亏比 {r['profit_factor']:.2f}\n")

    # ---- 输出对比表 ----
    print("=" * 70)
    print("📈 三方案对比")
    print("=" * 70)
    print(f"{'方案':<16} {'年化':>8} {'回撤':>8} {'夏普':>7} {'胜率':>7} {'盈亏比':>7} {'交易':>6}")
    print("-" * 70)
    for label, r in results.items():
        print(f"{label:<16} {r['annual_return']:>+7.1f}% {r['max_dd']:>7.1f}% "
              f"{r['sharpe']:>6.2f} {r['win_rate']:>6.1f}% {r['profit_factor']:>6.2f} {r['total_trades']:>5}")

    # ---- 改善幅度 ----
    base = results.get('A: 无择时', {})
    print("\n📊 择时改善幅度（vs 无择时）：")
    for label, r in results.items():
        if label == 'A: 无择时':
            continue
        delta_return = r['annual_return'] - base.get('annual_return', 0)
        delta_dd = r['max_dd'] - base.get('max_dd', 0)
        delta_sharpe = r['sharpe'] - base.get('sharpe', 0)
        print(f"  {label}: 年化 {delta_return:+.1f}pp | 回撤 {delta_dd:+.1f}pp | 夏普 {delta_sharpe:+.2f}")

    # ---- 市场状态统计 ----
    if index_df is not None:
        print("\n📊 回测期市场状态分布：")
        regime_counts = {'strong': 0, 'shaky': 0, 'weak': 0, 'crash': 0}
        for i in range(60, len(all_dates)):
            r = get_market_regime(index_df, list(index_df['date']).index(all_dates[i])
                                  if all_dates[i] in index_df['date'].values else i)
            regime_counts[r['regime']] = regime_counts.get(r['regime'], 0) + 1
        total_days = sum(regime_counts.values())
        for reg, cnt in regime_counts.items():
            if cnt > 0:
                labels = {'strong': '🟢 强势', 'shaky': '🟡 震荡', 'weak': '🟠 弱势', 'crash': '🔴 极弱'}
                print(f"  {labels.get(reg, reg)}: {cnt} 天 ({cnt/total_days*100:.0f}%)")

    print("\n" + "=" * 70)
    print("💡 解释：")
    print("  A→B 的改善 = 大盘择时的纯价值（躲过下跌市）")
    print("  B→C 的改善 = 策略权重匹配的增量价值（在正确市况用正确策略）")
    print("=" * 70)


if __name__ == "__main__":
    main()
