"""
单因子对比测试 —— 一次回测同时测7个因子

每个因子虚拟选股，用实际收盘价算月收益（不依赖 get_current_data）。
跑完看日志末尾的对比表。

使用方法：聚宽 → 新建策略 → 粘贴 → 回测 2019-01-01 ~ 2026-06-01 → 运行
"""

import numpy as np
import pandas as pd

FACTORS = ['momentum', 'volatility', 'reversal', 'turnover', 'pe', 'pb', 'roe']
TOP_N = 15


def initialize(context):
    set_benchmark('000300.XSHG')
    set_option("avoid_future_data", True)
    set_option("use_real_price", True)
    for f in FACTORS:
        setattr(g, f'ret_{f}', [])
    g.month = 0
    g.picks = {f: [] for f in FACTORS}
    g.buy_close = {f: {} for f in FACTORS}
    run_monthly(test_factors, 1, time='open')


def test_factors(context):
    prev_day = context.previous_date
    stocks = get_index_stocks('000300.XSHG')
    if len(stocks) < 20:
        return

    # ---- 拉数据 ----
    raw_c = get_price(stocks,
        start_date=prev_day - pd.Timedelta(days=500),
        end_date=prev_day, fields=['close'], fq='pre', panel=False)
    raw_v = get_price(stocks,
        start_date=prev_day - pd.Timedelta(days=500),
        end_date=prev_day, fields=['volume'], fq='pre', panel=False)

    if raw_c is None or len(raw_c) < 5000:
        return

    close_wide = raw_c.pivot(index='time', columns='code', values='close')
    vol_wide  = raw_v.pivot(index='time', columns='code', values='volume')
    close_wide = close_wide.dropna(axis=1, how='all')
    vol_wide   = vol_wide.dropna(axis=1, how='all')

    if len(close_wide) < 260:
        return
    close_wide = close_wide.iloc[-260:]

    # 只保留两表都有的股票
    common = [s for s in stocks if s in close_wide.columns and s in vol_wide.columns]
    if len(common) < 20:
        return

    close_wide = close_wide[common]
    vol_wide = vol_wide[common]

    # 当前收盘价 = 上交易日收盘
    cur_close = close_wide.iloc[-1]

    # ---- 结算上月收益 ----
    if g.month > 0:
        for f in FACTORS:
            buy_prices = g.buy_close[f]
            if buy_prices:
                rets = []
                for s, buy_price in buy_prices.items():
                    sell_price = cur_close.get(s)
                    if (sell_price is not None and buy_price is not None
                            and sell_price > 0 and buy_price > 0):
                        ret = (sell_price - buy_price) / buy_price - 0.001
                        rets.append(ret)
                if rets:
                    getattr(g, f'ret_{f}').append(np.mean(rets))

    # ---- 算因子 + 选股 + 记录买入价 ----
    fv = _compute_all_factor_values(close_wide, vol_wide, prev_day)

    for f in FACTORS:
        ranked = fv.sort_values(f, ascending=_is_negative(f), na_position='last')
        picks = list(ranked.head(TOP_N)['code'])
        g.picks[f] = picks
        # 用收盘价做买入参考价
        g.buy_close[f] = {s: cur_close.get(s) for s in picks if cur_close.get(s) is not None}

    g.month += 1

    # 最后一个月输出
    if g.month == 90:  # 2019-01~2026-06 = 90次月频调仓
        _print_results()


def _compute_all_factor_values(close_df, vol_df, prev_day):
    stocks = list(close_df.columns)
    n = len(close_df)

    if n >= 252:
        mom = (close_df.iloc[-21] - close_df.iloc[-252]) / close_df.iloc[-252]
    else:
        mom = pd.Series(np.nan, index=stocks)

    if n >= 60:
        rets = close_df.pct_change().iloc[-60:]
        vol = rets.std() * np.sqrt(252)
    else:
        vol = pd.Series(np.nan, index=stocks)

    if n >= 6:
        rev = -(close_df.iloc[-1] - close_df.iloc[-6]) / close_df.iloc[-6]
    else:
        rev = pd.Series(np.nan, index=stocks)

    if len(vol_df) >= 60:
        t20 = vol_df.iloc[-20:].mean()
        t60 = vol_df.iloc[-60:].mean()
        tur = t20 / t60.replace(0, np.nan)
    else:
        tur = pd.Series(np.nan, index=stocks)

    fin_map = {}
    try:
        q = query(valuation.code, valuation.pe_ratio, valuation.pb_ratio, indicator.roe
        ).filter(valuation.code.in_(stocks))
        fin_df = get_fundamentals(q, date=prev_day)
        if fin_df is not None and not fin_df.empty:
            for _, row in fin_df.iterrows():
                fin_map[row['code']] = {
                    'pe': row['pe_ratio'] if pd.notna(row['pe_ratio']) else np.nan,
                    'pb': row['pb_ratio'] if pd.notna(row['pb_ratio']) else np.nan,
                    'roe': row['roe'] if pd.notna(row['roe']) else np.nan,
                }
    except Exception:
        pass

    return pd.DataFrame({
        'code': stocks,
        'momentum': mom.values,
        'volatility': vol.values,
        'reversal': rev.values,
        'turnover': tur.values,
        'pe': [fin_map.get(s, {}).get('pe', np.nan) for s in stocks],
        'pb': [fin_map.get(s, {}).get('pb', np.nan) for s in stocks],
        'roe': [fin_map.get(s, {}).get('roe', np.nan) for s in stocks],
    })


def _is_negative(factor):
    return factor in ('volatility', 'turnover', 'pe', 'pb')


def _print_results():
    log.info("\n" + "=" * 70)
    log.info("单因子对比测试结果（2019-2026）")
    log.info("=" * 70)
    log.info(f"{'因子':<12} {'月数':>5} {'年化':>8} {'夏普':>8} {'最大回撤':>8} {'月胜率':>7}")
    log.info("-" * 70)

    for f in FACTORS:
        rets = np.array(getattr(g, f'ret_{f}'))
        n = len(rets)
        if n < 12:
            log.info(f"{f:<12} {n:>5} {'数据不足'}")
            continue

        ann_ret = (np.prod(1 + rets) ** (12 / n) - 1) * 100
        ann_vol = np.std(rets) * np.sqrt(12) * 100
        sharpe = (ann_ret - 2) / ann_vol if ann_vol > 0 else 0

        cum = np.cumprod(1 + rets)
        peak = np.maximum.accumulate(cum)
        max_dd = np.min((cum - peak) / peak) * 100

        win_rate = np.sum(rets > 0) / n * 100

        log.info(f"{f:<12} {n:>5} {ann_ret:>7.2f}% {sharpe:>7.3f} {max_dd:>8.2f}% {win_rate:>6.1f}%")

    log.info("=" * 70)
