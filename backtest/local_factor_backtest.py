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
COMMISSION = settings.BACKTEST_SLIPPAGE + settings.BACKTEST_COMMISSION / 2  # 单边


def load_data():
    conn = sqlite3.connect(DB)
    df = pd.read_sql_query("SELECT code, date, close, volume FROM daily_kline ORDER BY date", conn)
    df['date'] = pd.to_datetime(df['date'])
    fin = pd.read_sql_query("SELECT code, date, pe, pb FROM financial_data", conn)
    conn.close()
    return df, fin


def compute_factors(close_wide, vol_wide, fin_map, stocks, date_str, fw):
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
                roe_vals[s] = latest.get('roe')

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


def run(weights=None, top_n=None, show_time=False):
    n_picks = top_n or TOP_N
    _t0 = time.time()

    # 局部权重，不污染全局
    w = dict(FACTOR_WEIGHTS)
    if weights:
        w.update(weights)

    df_all, fin_df = load_data()

    # pivot
    close_wide = df_all.pivot(index='date', columns='code', values='close')
    vol_wide = df_all.pivot(index='date', columns='code', values='volume')

    # 财务数据索引
    fin_map = {}
    for _, r in fin_df.iterrows():
        fin_map.setdefault(r['code'], []).append({
            'date': str(r['date']), 'pe': r['pe'], 'pb': r['pb']
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

        # ---- 计算因子 + 选股 + 记录买入价 ----
        # 买入价 = 上月末收盘价（选股时的参考价）
        latest_close = close_slice[stocks].iloc[-1]
        scores = compute_factors(close_slice[stocks], vol_slice[stocks], fin_map,
                                 stocks, str(prev_date.date()), w)
        prev_picks = list(scores.head(n_picks)['code'])
        prev_buy_prices = {s: latest_close[s] for s in prev_picks
                          if s in latest_close.index and latest_close[s] > 0}

    if not returns:
        print("无回测数据")
        return

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

    print(f"TOP_N={n_picks} | "
          f"年化={ann_ret:.2f}% | 夏普={sharpe:.3f} | "
          f"回撤={max_dd:.1f}% | 盈亏比={pnl_ratio:.2f} | "
          f"胜率={wins/total*100:.1f}% | 交易={total}月 | "
          f"耗时={time.time()-_t0:.1f}s")


if __name__ == '__main__':
    t0 = time.time()
    print("002 本地多因子回测\n")

    # TOP_N 对比
    for n in [5, 10, 15, 20, 30]:
        run(top_n=n)

    print(f"\n总耗时: {time.time()-t0:.1f}s")
