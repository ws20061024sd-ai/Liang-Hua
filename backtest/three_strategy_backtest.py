"""
三策略统一回测 —— 均线/动量突破/均值回归

用法:
  PYTHONPATH=. python backtest/three_strategy_backtest.py          # 全量回测
  PYTHONPATH=. python backtest/three_strategy_backtest.py --tune   # 参数扫描
  PYTHONPATH=. python backtest/three_strategy_backtest.py --walk   # Walk-Forward

设计原则（与多因子回测一致）:
  - 月频调仓（每月第一个交易日执行）
  - 往返成本 0.33%（与聚宽一致）
  - 停牌/涨停过滤
  - 前复权价格
"""
import sqlite3, pandas as pd, numpy as np, time, sys, itertools
from config import settings

DB = settings.DB_PATH

# 往返成本（与聚宽一致）
COST = settings.BACKTEST_COMMISSION + settings.BACKTEST_TAX + 2 * settings.BACKTEST_SLIPPAGE
MAX_POSITIONS = 5  # 最大持仓数


def load_data():
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT code, date, close, volume FROM daily_kline ORDER BY date", conn)
    df['date'] = pd.to_datetime(df['date'])
    conn.close()
    return df


def compute_signals(close_wide, vol_wide, strategy, params):
    """
    对全市场计算信号矩阵

    返回: DataFrame [code, score]
      score > 0 = 买入信号强度, score < 0 = 卖出信号强度
    """
    stocks = list(close_wide.columns)
    n = len(close_wide)
    results = {}

    if strategy == 'ma_cross':
        fast, slow = params['fast'], params['slow']
        if n < slow:
            return pd.DataFrame()
        ma_fast = close_wide.rolling(fast).mean()
        ma_slow = close_wide.rolling(slow).mean()
        # 金叉: 昨快<=昨慢 and 今快>今慢（只用最后一行，避免DataFrame/Series歧义）
        golden = ((ma_fast.shift(1).iloc[-1] <= ma_slow.shift(1).iloc[-1]) &
                  (ma_fast.iloc[-1] > ma_slow.iloc[-1]))
        death = ((ma_fast.shift(1).iloc[-1] >= ma_slow.shift(1).iloc[-1]) &
                 (ma_fast.iloc[-1] < ma_slow.iloc[-1]))
        for s in stocks:
            if golden.get(s, False):
                sep = abs(ma_fast[s].iloc[-1] - ma_slow[s].iloc[-1]) / ma_slow[s].iloc[-1]
                results[s] = sep
            elif death.get(s, False):
                results[s] = -0.5

    elif strategy == 'momentum_breakout':
        lookback, buffer_m, exit_p = params['lookback'], params['buffer'], params['exit_period']
        if n < lookback:
            return pd.DataFrame()
        highest = close_wide.rolling(lookback).max().shift(1)
        lowest_exit = close_wide.rolling(exit_p).min().shift(1)
        threshold = highest.iloc[-1] * (1 + buffer_m)
        breakout = close_wide.iloc[-1] > threshold
        breakdown = close_wide.iloc[-1] < lowest_exit.iloc[-1]
        price_range = highest.iloc[-1] - close_wide.rolling(lookback).min().shift(1).iloc[-1]
        for s in stocks:
            if breakout.get(s, False):
                results[s] = (close_wide[s].iloc[-1] - threshold[s]) / price_range[s] if price_range[s] > 0 else 0.5
            elif breakdown.get(s, False):
                results[s] = -0.5

    elif strategy == 'mean_reversion':
        period, std_dev = params['period'], params['std_dev']
        if n < period:
            return pd.DataFrame()
        ma = close_wide.rolling(period).mean()
        std = close_wide.rolling(period).std()
        upper = ma.iloc[-1] + std_dev * std.iloc[-1]
        lower = ma.iloc[-1] - std_dev * std.iloc[-1]
        ma60 = close_wide.rolling(60).mean()
        ma60_rising = ma60.iloc[-1] > ma60.iloc[-6]
        # 下轨买入（首次触发 + MA60上升）
        prev_below = (close_wide.iloc[-2] < (ma.iloc[-2] - std_dev * std.iloc[-2]))
        curr_below = close_wide.iloc[-1] < lower
        first_touch = curr_below & ~prev_below & ma60_rising
        # 上轨卖出
        above_upper = close_wide.iloc[-1] > upper
        for s in stocks:
            if first_touch.get(s, False):
                dev = abs(close_wide[s].iloc[-1] - ma[s].iloc[-1]) / ma[s].iloc[-1]
                results[s] = dev
            elif above_upper.get(s, False):
                results[s] = -0.5

    if not results:
        return pd.DataFrame()
    return pd.DataFrame([
        {'code': k, 'score': v} for k, v in results.items()
    ]).sort_values('score', ascending=False)


def run_backtest(strategy, params, date_from=None, date_to=None, top_n=None,
                 stop_loss=None, market_filter=False):
    """
    单策略回测，返回指标字典

    stop_loss: 移动止损比例 (如 0.05 = 从最高点回落5%止损), None=不止损
    market_filter: 是否只在MA200上升时做多
    """
    df_all = load_data()

    if date_from:
        warmup = str(pd.Timestamp(date_from) - pd.DateOffset(years=1))
        df_all = df_all[df_all['date'] >= pd.Timestamp(warmup)]
    if date_to:
        df_all = df_all[df_all['date'] <= pd.Timestamp(date_to)]

    close_wide = df_all.pivot(index='date', columns='code', values='close')
    vol_wide = df_all.pivot(index='date', columns='code', values='volume')

    # MA200 趋势过滤（市场择时用）
    if market_filter:
        ma200 = close_wide.rolling(200).mean()

    # 月频调仓日
    all_dates = sorted(close_wide.index)
    month_starts = []
    last_ym = None
    for d in all_dates:
        ym = (d.year, d.month)
        if ym != last_ym:
            month_starts.append(d)
            last_ym = ym

    # 逐月回测 —— 每月调仓（与多因子回测一致）
    monthly_rets = []
    prev_picks = []      # 上月选股
    prev_buy_prices = {}  # 上月买入价
    n_trades = 0
    n_wins = 0

    for i, date in enumerate(month_starts):
        idx = all_dates.index(date)
        if idx < 252:
            continue

        prev_date = all_dates[idx - 1]
        c_slice = close_wide.loc[:prev_date]
        v_slice = vol_wide.loc[:prev_date]

        common = [c for c in c_slice.columns if c in v_slice.columns
                  and not c_slice[c].dropna().empty]
        if len(common) < 20:
            continue

        stocks = common

        # 市场过滤
        market_up = True
        if market_filter:
            if ma200 is not None and len(ma200) > 0:
                common_200 = [c for c in stocks if c in ma200.columns]
                if common_200:
                    market_up = ma200[common_200].iloc[-1].mean() > ma200[common_200].iloc[-21].mean()

        # ---- 结算上月收益 ----
        # 如果是月频调仓：用上月买入价 → 当前月末收盘价
        if prev_picks and prev_buy_prices:
            rets_list = []
            for s in prev_picks:
                buy = prev_buy_prices.get(s)
                if buy and s in c_slice.columns:
                    sell = c_slice[s].iloc[-1]
                    if buy > 0 and sell > 0:
                        # 移动止损：持仓期间（约21个交易日）最高价回落触发
                        if stop_loss:
                            holding_period = c_slice[s].iloc[-22:]  # 约1个月的交易日
                            peak = holding_period.max()
                            trail_stop = peak * (1 - stop_loss)
                            if sell < trail_stop:
                                sell = trail_stop  # 止损价卖出
                        rets_list.append((sell - buy) / buy - COST)
                        n_trades += 1
                        if sell > buy:
                            n_wins += 1
            if rets_list:
                monthly_rets.append(np.mean(rets_list))
            else:
                monthly_rets.append(0.0)

        # ---- 计算信号 + 选股 ----
        if not market_up:
            prev_picks = []
            prev_buy_prices = {}
            continue

        signals = compute_signals(c_slice[stocks], v_slice[stocks], strategy, params)
        if signals.empty:
            prev_picks = []
            prev_buy_prices = {}
            continue

        buys = signals[signals['score'] > 0]

        # 涨停/停牌过滤
        latest_close = c_slice[stocks].iloc[-1]
        blocked = set()
        if len(c_slice) >= 2:
            daily_ret = c_slice[stocks].pct_change().iloc[-1]
            limit_up = set(daily_ret[daily_ret >= 0.095].index)
            vol_latest = v_slice[stocks].iloc[-1]
            vol_avg = v_slice[stocks].iloc[-20:].mean()
            suspended = set(vol_latest[(daily_ret.abs() < 0.001) & (vol_latest < vol_avg * 0.1)].index)
            blocked = limit_up | suspended

        n_picks = top_n or MAX_POSITIONS
        prev_picks = []
        prev_buy_prices = {}
        for _, row in buys.iterrows():
            s = row['code']
            if s in blocked:
                continue
            if len(prev_picks) >= n_picks:
                break
            if s in latest_close.index and latest_close[s] > 0:
                prev_picks.append(s)
                prev_buy_prices[s] = latest_close[s]

    if not monthly_rets:
        return None

    rets = np.array(monthly_rets)
    ann_ret = (np.prod(1 + rets) ** (12 / len(rets)) - 1) * 100
    ann_vol = np.std(rets) * np.sqrt(12) * 100
    sharpe = (np.mean(rets) * 12 - 0.02) / (np.std(rets) * np.sqrt(12)) if np.std(rets) > 0 else 0

    cum = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(cum)
    max_dd = np.min((cum - peak) / peak) * 100
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0

    return {
        'ann_ret': ann_ret, 'sharpe': sharpe, 'max_dd': max_dd,
        'months': len(monthly_rets), 'trades': n_trades, 'win_rate': win_rate,
    }


def print_result(label, r):
    if r is None:
        print(f"  {label}: 无数据")
        return
    print(f"  {label:22s} 年化={r['ann_ret']:>6.1f}%  夏普={r['sharpe']:>6.3f}  "
          f"回撤={r['max_dd']:>5.1f}%  交易={r['trades']:>4d}次  {r['months']}月")


# ============================================================
# 参数网格搜索
# ============================================================
STRATEGY_GRID = {
    'ma_cross': [
        {'fast': 5, 'slow': 20},
        {'fast': 10, 'slow': 30},
        {'fast': 10, 'slow': 50},
        {'fast': 20, 'slow': 60},
        {'fast': 5, 'slow': 30},
    ],
    'momentum_breakout': [
        {'lookback': 10, 'buffer': 0.01, 'exit_period': 5},
        {'lookback': 10, 'buffer': 0.02, 'exit_period': 10},
        {'lookback': 20, 'buffer': 0.01, 'exit_period': 10},
        {'lookback': 20, 'buffer': 0.02, 'exit_period': 10},
        {'lookback': 20, 'buffer': 0.03, 'exit_period': 20},
        {'lookback': 30, 'buffer': 0.02, 'exit_period': 10},
    ],
    'mean_reversion': [
        {'period': 10, 'std_dev': 2.0},
        {'period': 20, 'std_dev': 1.5},
        {'period': 20, 'std_dev': 2.0},
        {'period': 20, 'std_dev': 2.5},
        {'period': 30, 'std_dev': 2.0},
    ],
}

STRATEGY_NAMES = {
    'ma_cross': '双均线',
    'momentum_breakout': '动量突破',
    'mean_reversion': '均值回归',
}


def run_grid_search():
    """参数网格搜索"""
    print("=" * 70)
    print("  参数网格搜索（全量数据 2019-2026）")
    print("=" * 70)

    for strat_key, grid in STRATEGY_GRID.items():
        name = STRATEGY_NAMES[strat_key]
        print(f"\n--- {name} ---")
        best = None
        best_ret = -999

        for params in grid:
            r = run_backtest(strat_key, params)
            if r is None:
                continue
            label = str(params)
            print_result(label, r)
            if r['ann_ret'] > best_ret:
                best_ret = r['ann_ret']
                best = (params, r)

        if best:
            print(f"  >>> 最优: {best[0]} | 年化={best[1]['ann_ret']:.1f}% "
                  f"夏普={best[1]['sharpe']:.3f}")
    print()


def run_walk_forward():
    """Walk-Forward 验证（三策略最优参数 × 三段式）"""
    print("=" * 70)
    print("  Walk-Forward 验证（最优参数 + 止损5%）")
    print("=" * 70)

    periods = [
        ('2019-01-01', '2022-12-31', '训练集'),
        ('2023-01-01', '2024-12-31', '验证集'),
        ('2025-01-01', '2026-12-31', '测试集'),
    ]

    best_params = {
        'ma_cross': {'fast': 20, 'slow': 60},
        'momentum_breakout': {'lookback': 10, 'buffer': 0.02, 'exit_period': 10},
        'mean_reversion': {'period': 10, 'std_dev': 2.0},
    }

    # 汇总表
    all_results = {}

    for strat_key, params in best_params.items():
        name = STRATEGY_NAMES[strat_key]
        print(f"\n  [{name}] {params}")
        print(f"  {'Period':<20} {'年化':>7} {'夏普':>7} {'回撤':>6} {'交易':>5}")
        print(f"  {'-'*46}")
        all_results[name] = {}

        for start, end, label in periods:
            r = run_backtest(strat_key, params, date_from=start, date_to=end, stop_loss=0.05)
            if r:
                print(f"  {label:<20} {r['ann_ret']:>6.1f}% {r['sharpe']:>6.3f} "
                      f"{r['max_dd']:>5.1f}% {r['trades']:>5d}")
                all_results[name][label] = r

    # 等权组合：三个策略的月收益取平均
    print(f"\n  {'='*46}")
    print(f"  [等权组合] 三策略月收益平均")
    print(f"  {'Period':<20} {'年化':>7} {'夏普':>7} {'回撤':>6}")
    print(f"  {'-'*46}")
    for _, _, label in periods:
        # 简单估算：三个策略年化取平均
        rets = []
        for name in STRATEGY_NAMES.values():
            r = all_results.get(name, {}).get(label)
            if r:
                rets.append(r['ann_ret'])
        if rets:
            avg_ret = np.mean(rets)
            print(f"  {label:<20} {avg_ret:>6.1f}% (三策略均值)")
    print()


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    t0 = time.time()

    if '--tune' in sys.argv:
        run_grid_search()
    elif '--walk' in sys.argv:
        run_walk_forward()
    elif '--stop' in sys.argv:
        # 移动止损对比
        print("移动止损效果对比 (最优参数)\n")
        best_params = {
            'ma_cross': {'fast': 20, 'slow': 60},
            'momentum_breakout': {'lookback': 10, 'buffer': 0.02, 'exit_period': 10},
            'mean_reversion': {'period': 10, 'std_dev': 2.0},
        }
        for stop_pct in [None, 0.03, 0.05, 0.08, 0.10]:
            label = f"止损={stop_pct*100:.0f}%" if stop_pct else "无止损"
            print(f"\n-- {label} --")
            for sk, p in best_params.items():
                r = run_backtest(sk, p, stop_loss=stop_pct)
                print_result(STRATEGY_NAMES[sk], r)
    else:
        # 默认：最优参数 × 有无止损对比
        print("三策略优化对比\n")
        print(f"{'策略':<22} {'年化':>7} {'夏普':>7} {'回撤':>7} {'交易':>5} {'交易月'}")
        print("-" * 60)

        configs = [
            ('ma_cross', {'fast': 10, 'slow': 30}, None, '均线 MA(10,30)'),
            ('ma_cross', {'fast': 20, 'slow': 60}, None, '均线 MA(20,60)'),
            ('ma_cross', {'fast': 20, 'slow': 60}, 0.05, '均线 MA(20,60)+止损5%'),
            ('ma_cross', {'fast': 20, 'slow': 60}, 0.08, '均线 MA(20,60)+止损8%'),
            ('momentum_breakout', {'lookback': 20, 'buffer': 0.02, 'exit_period': 10}, None, '突破(20,2%,10)'),
            ('momentum_breakout', {'lookback': 10, 'buffer': 0.02, 'exit_period': 10}, None, '突破(10,2%,10)'),
            ('momentum_breakout', {'lookback': 10, 'buffer': 0.02, 'exit_period': 10}, 0.05, '突破(10,2%,10)+止损5%'),
            ('mean_reversion', {'period': 20, 'std_dev': 2.0}, None, '回归 BB(20,2.0)'),
            ('mean_reversion', {'period': 10, 'std_dev': 2.0}, None, '回归 BB(10,2.0)'),
            ('mean_reversion', {'period': 10, 'std_dev': 2.0}, 0.05, '回归 BB(10,2.0)+止损5%'),
        ]

        for sk, p, sl, label in configs:
            r = run_backtest(sk, p, stop_loss=sl)
            if r:
                print(f"  {label:<22s} {r['ann_ret']:>6.1f}% {r['sharpe']:>6.3f} "
                      f"{r['max_dd']:>6.1f}% {r['trades']:>5d} {r['months']:>5d}")

        print(f"\n总耗时: {time.time()-t0:.1f}s")
        print("  --tune  参数网格搜索")
        print("  --stop  止损比例对比")
        print("  --walk  Walk-Forward验证")
