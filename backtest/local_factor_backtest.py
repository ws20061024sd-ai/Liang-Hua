"""
本地多因子回测 —— 与聚宽 002 策略逻辑完全一致

用法: python backtest/local_factor_backtest.py
      改 TOP_N / FACTOR_WEIGHTS 后重跑即可对比
"""
import sqlite3, pandas as pd, numpy as np, time
from config import settings

DB = settings.DB_PATH

FACTOR_WEIGHTS = {
    'momentum':   0.30,
    'volatility': 0.20,
    'reversal':   0.15,
    'turnover':   0.10,
    'pe':         0.10,
    'pb':         0.00,
    'roe':        0.15,
}
TOP_N = 15
# 往返成本（与聚宽一致）: 买佣金+卖佣金+印花税+双边滑点
COMMISSION = settings.BACKTEST_COMMISSION + settings.BACKTEST_TAX + 2 * settings.BACKTEST_SLIPPAGE


def load_data():
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query("SELECT code, date, close, volume FROM daily_kline ORDER BY date", conn)
    df['date'] = pd.to_datetime(df['date'])
    fin = pd.read_sql_query("SELECT code, date, pe, pb FROM financial_data", conn)
    # ROE 从独立表加载（季度数据）
    roe_df = pd.read_sql_query("SELECT code, date, roe FROM financial_roe", conn)
    conn.close()
    return df, fin, roe_df


def compute_factors(close_wide, vol_wide, fin_map, roe_map, stocks, date_str, fw):
    """单月因子计算——统一使用 engine.factors 向量化函数"""
    from engine.factors import (
        momentum_vectorized, volatility_vectorized,
        reversal_vectorized, turnover_vectorized,
    )
    window = close_wide.iloc[-260:]

    momentum = momentum_vectorized(window)
    volatility = volatility_vectorized(window)
    reversal = reversal_vectorized(window)
    turnover = turnover_vectorized(vol_wide)

    # 取 date_str 之前最新的财务数据
    pe_vals, pb_vals, roe_vals = {}, {}, {}
    for s in stocks:
        if s in fin_map:
            rows = fin_map[s]
            # 找 <= date_str 的最新一条
            valid = [r for r in rows if r['date'] <= date_str]
            if valid:
                latest = max(valid, key=lambda x: x['date'])
                pe_vals[s] = latest['pe']
                pb_vals[s] = latest['pb']
        # ROE 从独立数据源取（滞后120天防止未来数据泄露）
        from datetime import datetime, timedelta
        roe_cutoff = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d')
        if s in roe_map:
            rows = roe_map[s]
            valid = [r for r in rows if r['date'] <= roe_cutoff]
            if valid:
                latest = max(valid, key=lambda x: x['date'])
                roe_vals[s] = latest['roe']

    df = pd.DataFrame({
        'code': stocks,
        'momentum': momentum.values,
        'volatility': volatility.values,
        'reversal': reversal.values,
        'turnover': turnover.values,
        'pe': [pe_vals.get(s) for s in stocks],
        'pb': [pb_vals.get(s) for s in stocks],
        'roe': [roe_vals.get(s) for s in stocks],
    })

    # Z-score
    for col in ['momentum', 'reversal', 'roe']:
        vals = df[col].dropna()
        if len(vals) >= 5:
            m, s = vals.mean(), vals.std()
            df[f'{col}_z'] = (df[col] - m) / s if s > 0 else 0.0
        else:
            df[f'{col}_z'] = 0.0

    for col in ['volatility', 'turnover', 'pe', 'pb']:
        vals = df[col].dropna()
        if len(vals) >= 5:
            m, s = vals.mean(), vals.std()
            df[f'{col}_z'] = (df[col] - m) / s if s > 0 else 0.0
        else:
            df[f'{col}_z'] = 0.0

    df['score'] = (
        df['momentum_z'].fillna(0)   * fw['momentum'] +
        -df['volatility_z'].fillna(0) * fw['volatility'] +
        df['reversal_z'].fillna(0)    * fw['reversal'] +
        -df['turnover_z'].fillna(0)   * fw['turnover'] +
        -df['pe_z'].fillna(0)         * fw['pe'] +
        -df['pb_z'].fillna(0)         * fw['pb'] +
        df['roe_z'].fillna(0)         * fw['roe']
    )
    return df.sort_values('score', ascending=False)


def run(weights=None, top_n=None, show_time=False, date_from=None, date_to=None):
    n_picks = top_n or TOP_N
    _t0 = time.time()

    # 局部权重，不污染全局
    w = dict(FACTOR_WEIGHTS)
    if weights:
        w.update(weights)

    df_all, fin_df, roe_df = load_data()

    # 日期范围过滤（保留 date_from 前 1 年做预热数据）
    if date_from:
        warmup_start = (pd.Timestamp(date_from) - pd.DateOffset(years=1)).strftime('%Y-%m-%d')
        df_all = df_all[df_all['date'] >= pd.Timestamp(warmup_start)]
    if date_to:
        df_all = df_all[df_all['date'] <= pd.Timestamp(date_to)]

    if df_all.empty:
        print(f"  {date_from}~{date_to}: 无数据")
        return None

    # pivot
    close_wide = df_all.pivot(index='date', columns='code', values='close')
    vol_wide = df_all.pivot(index='date', columns='code', values='volume')

    # 财务数据索引（PE/PB 从 financial_data）
    fin_map = {}
    for _, r in fin_df.iterrows():
        fin_map.setdefault(r['code'], []).append({
            'date': str(r['date']), 'pe': r['pe'], 'pb': r['pb']
        })

    # ROE 索引（从 financial_roe，季度数据）
    roe_map = {}
    for _, r in roe_df.iterrows():
        roe_map.setdefault(r['code'], []).append({
            'date': str(r['date']), 'roe': r['roe']
        })

    # 取调仓日：每月第一个交易日
    all_dates = sorted(close_wide.index)
    month_starts = []
    last_ym = None
    for d in all_dates:
        ym = (d.year, d.month)
        if ym != last_ym:
            month_starts.append(d)
            last_ym = ym

    # 逐月回测
    returns = []
    prev_picks = None
    prev_buy_prices = {}

    for i, date in enumerate(month_starts):
        # 跳过数据不足的月份
        idx = all_dates.index(date)
        if idx < 252:
            continue

        # 截至上一交易日的数据（模拟 T 日收盘后选股）
        prev_date = all_dates[idx - 1]
        close_slice = close_wide.loc[:prev_date]
        vol_slice = vol_wide.loc[:prev_date]

        common = [c for c in close_slice.columns
                  if c in vol_slice.columns and not close_slice[c].dropna().empty]
        if len(common) < 20:
            continue

        stocks = common

        # ---- 结算上月选股收益 ----
        # 上月选股时记录的买入价 vs 本月上月末收盘价
        if prev_picks and prev_buy_prices:
            rets_list = []
            for s in prev_picks:
                buy = prev_buy_prices.get(s)
                if buy and s in close_slice.columns:
                    sell = close_slice[s].iloc[-1]  # 本月上月末收盘
                    if buy > 0 and sell > 0:
                        rets_list.append((sell - buy) / buy - COMMISSION)
            if rets_list:
                returns.append(np.mean(rets_list))
            else:
                returns.append(0.0)

        # ---- 计算因子 + 选股 + 涨停过滤 ----
        # 买入价 = 上月末收盘价（选股时的参考价）
        latest_close = close_slice[stocks].iloc[-1]
        scores = compute_factors(close_slice[stocks], vol_slice[stocks], fin_map,
                                 roe_map, stocks, str(prev_date.date()), w)

        # 涨停过滤：排除选股日涨跌幅 ≥ 9.5% 的股票（实际买不到）
        daily_ret = close_slice[stocks].pct_change().iloc[-1] if len(close_slice) >= 2 else None
        limit_up_stocks = set()
        if daily_ret is not None:
            limit_up_stocks = set(daily_ret[daily_ret >= 0.095].index)

        # 停牌过滤：价格无变化且成交量骤降（vol < 10% * 20日均量）
        price_chg = close_slice[stocks].pct_change().iloc[-1]
        vol_latest = vol_slice[stocks].iloc[-1]
        vol_avg = vol_slice[stocks].iloc[-20:].mean()
        suspended = (price_chg.abs() < 0.001) & (vol_latest < vol_avg * 0.1)
        suspended_stocks = set(suspended[suspended].index)

        blocked = limit_up_stocks | suspended_stocks

        filtered_picks = []
        for s in scores['code']:
            if s not in blocked:
                filtered_picks.append(s)
            if len(filtered_picks) >= n_picks:
                break
        prev_picks = filtered_picks
        prev_buy_prices = {s: latest_close[s] for s in prev_picks
                          if s in latest_close.index and latest_close[s] > 0}

    if not returns:
        print("无回测数据")
        return None

    rets = np.array(returns)
    ann_ret = (np.prod(1 + rets) ** (12 / len(rets)) - 1) * 100
    ann_vol = np.std(rets) * np.sqrt(12) * 100
    sharpe = (np.mean(rets) * 12 - 0.02) / (np.std(rets) * np.sqrt(12)) if np.std(rets) > 0 else 0

    cum = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(cum)
    max_dd = np.min((cum - peak) / peak) * 100

    wins = np.sum(rets > 0)
    total = len(rets)
    avg_win = np.mean(rets[rets > 0]) * 100 if wins > 0 else 0
    avg_loss = abs(np.mean(rets[rets < 0])) * 100 if wins < total else 0
    pnl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')

    result = {
        'top_n': n_picks,
        'ann_ret': ann_ret,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'pnl_ratio': pnl_ratio,
        'win_rate': wins/total*100,
        'months': total,
    }
    print(f"TOP_N={n_picks} | "
          f"年化={ann_ret:.2f}% | 夏普={sharpe:.3f} | "
          f"回撤={max_dd:.1f}% | 盈亏比={pnl_ratio:.2f} | "
          f"胜率={wins/total*100:.1f}% | 交易={total}月 | "
          f"耗时={time.time()-_t0:.1f}s")
    return result


def run_walk_forward():
    """Walk-Forward 验证：训练/验证/测试三段式，全 TOP_N 对比"""
    periods = [
        ('2019-01-01', '2022-12-31', '训练集'),
        ('2023-01-01', '2024-12-31', '验证集'),
        ('2025-01-01', '2026-12-31', '测试集'),
    ]
    top_ns = [5, 10, 15, 20, 30]

    print("\n" + "=" * 75)
    print("  Walk-Forward 验证（训练→验证→测试）")
    print("=" * 75)

    # 收集每个周期×每个TOP_N的结果
    table = {}  # {period_label: {top_n: ann_ret}}
    for start, end, label in periods:
        print(f"\n  [{label}] {start} → {end}")
        table[label] = {}
        for n in top_ns:
            r = run(date_from=start, date_to=end, top_n=n)
            if r:
                table[label][n] = r['ann_ret']

    # 汇总表
    print(f"\n{'='*75}")
    print(f"  {'TOP_N':<8}", end='')
    for _, _, label in periods:
        print(f"{label:>10}", end='')
    print(f"  {'极差':>8}")
    print(f"  {'-'*52}")

    for n in top_ns:
        print(f"  {n:<8}", end='')
        vals = []
        for _, _, label in periods:
            v = table[label].get(n)
            if v is not None:
                print(f"{v:>9.1f}%", end='')
                vals.append(v)
            else:
                print(f"{'N/A':>10}", end='')
        spread = max(vals) - min(vals) if vals else 0
        flag = '⚠️' if spread > 15 else ('🟡' if spread > 8 else '✅')
        print(f"  {spread:>5.1f}pp {flag}")

    # 总结
    print(f"  {'='*52}")
    all_spreads = []
    for n in top_ns:
        vals = [table[label][n] for _, _, label in periods if table[label].get(n) is not None]
        if vals:
            all_spreads.append(max(vals) - min(vals))
    avg_spread = sum(all_spreads) / len(all_spreads) if all_spreads else 0

    if avg_spread > 15:
        print(f"  ⚠️ 平均极差 {avg_spread:.1f}pp — 跨周期差异大，可能过拟合")
    elif avg_spread > 8:
        print(f"  🟡 平均极差 {avg_spread:.1f}pp — 跨周期有一定差异")
    else:
        print(f"  ✅ 平均极差 {avg_spread:.1f}pp — 跨周期表现一致")
    print()


if __name__ == '__main__':
    import sys
    t0 = time.time()

    if '--walk-forward' in sys.argv:
        run_walk_forward()
    else:
        print("002 本地多因子回测\n")
        for n in [5, 10, 15, 20, 30]:
            run(top_n=n)
        print(f"\n总耗时: {time.time()-t0:.1f}s")
        print("  (加 --walk-forward 运行 Walk-Forward 验证)")
