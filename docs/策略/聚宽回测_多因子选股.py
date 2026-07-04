"""
002 多因子选股回测 v2.1 — 最终版（月频调仓）

v2.1 权重（用户手动优化——首次超越v2原版）:
  动量 30% | 低波动 20% | 反转 15% | 换手率 10% | PE 10% | PB 0% | ROE 15%
  → 7.5年回测: 年化 10.05% | 夏普 0.395 | 回撤 23.93% | 盈亏比 1.51
  → 变化: PB砍掉(单因子回测为负收益), 动量提至30%(单因子收益王), 低波提至20%

使用方法：聚宽 → 新建策略 → 粘贴 → 回测 2019-01-01 ~ 2026-06-01 → 运行
"""

import numpy as np
import pandas as pd

# ============================================================
# 参数
# ============================================================
COMMISSION = 0.0008
SLIPPAGE = 0.001
TAX_COST = 0.0005

# 002 v2.1 最终权重（基于单因子回测数据驱动 + 手动优化——不要改）
# ┌──────────────┬──────┬──────────────────────────────────────────┐
# │ 因子          │ 权重  │ 含义                                      │
# ├──────────────┼──────┼──────────────────────────────────────────┤
# │ momentum     │ 0.30 │ 动量因子——单因子收益王(7.4%年化).提权      │
# │ volatility   │ 0.20 │ 低波动因子——组合防御核心.提权              │
# │ reversal     │ 0.15 │ 短期反转因子——保持                          │
# │ turnover     │ 0.10 │ 换手率因子——保持                            │
# │ pe           │ 0.10 │ 市盈率因子——保持                            │
# │ pb           │ 0.00 │ 市净率因子——单因子负收益(-0.74%).砍掉      │
# │ roe          │ 0.15 │ 净资产收益率——保持                          │
# ├──────────────┼──────┼──────────────────────────────────────────┤
# │ 收益端(进攻)  │ 45%  │ 动量30+反转15                              │
# │ 风险端(防御)  │ 30%  │ 低波20+PE10                                │
# │ 质量端        │ 15%  │ ROE15                                      │
# │ 辅助          │ 10%  │ 换手率10                                    │
# └──────────────┴──────┴──────────────────────────────────────────┘
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
MAX_SINGLE_PCT = 0.10
MIN_STOCKS_FOR_SCORE = 3


def initialize(context):
    set_benchmark('000300.XSHG')
    set_option("avoid_future_data", True)   # 防未来数据
    set_option("use_real_price", True)       # 动态复权
    g.top_n = TOP_N
    g.max_single_pct = MAX_SINGLE_PCT
    g.min_factors = MIN_STOCKS_FOR_SCORE
    set_order_cost(OrderCost(
        open_tax=0, close_tax=TAX_COST,
        open_commission=COMMISSION / 2, close_commission=COMMISSION / 2,
        min_commission=5,
    ), type='stock')
    set_slippage(FixedSlippage(SLIPPAGE))
    run_monthly(rebalance, 1, time='open')


def rebalance(context):
    prev_day = context.previous_date
    stocks = get_index_stocks('000300.XSHG')
    if len(stocks) < 20:
        return

    # ---- 拉取长格式数据 ----
    # panel=False → columns=['time','code','close']，每行是(日期, 股票, 价格)
    raw_c = get_price(stocks,
        start_date=prev_day - pd.Timedelta(days=500),
        end_date=prev_day,
        fields=['close'], fq='pre', panel=False)
    raw_v = get_price(stocks,
        start_date=prev_day - pd.Timedelta(days=500),
        end_date=prev_day,
        fields=['volume'], fq='pre', panel=False)

    if raw_c is None or len(raw_c) < 5000:
        return

    # ---- 转宽表：index=date, columns=stock ----
    close_wide = raw_c.pivot(index='time', columns='code', values='close')
    vol_wide = raw_v.pivot(index='time', columns='code', values='volume')

    close_wide = close_wide.dropna(axis=1, how='all')  # 丢掉全是NaN的列
    vol_wide = vol_wide.dropna(axis=1, how='all')

    if len(close_wide) < 260:
        return
    close_wide = close_wide.iloc[-260:]

    # ---- 只保留两个表都有的股票 ----
    common = [s for s in stocks if s in close_wide.columns and s in vol_wide.columns]
    if len(common) < 20:
        log.warn(f"可用股票不足: {len(common)}")
        return

    # ---- 批量算因子 ----
    scores = batch_factor_scores(close_wide[common], vol_wide[common], prev_day)
    if scores is None or len(scores) < 5:
        log.warn(f"有效得分过少: {len(scores) if scores is not None else 0}")
        return

    top = select_top_stocks(scores, g.top_n, context)
    if not top:
        return
    execute_rebalance(top, context)


# ============================================================
# 因子计算（全 pandas 批处理）
# ============================================================
def batch_factor_scores(close_df, vol_df, prev_day):
    """
    close_df: index=date, columns=stocks (聚宽格式 '000001.XSHE')
    vol_df:   同上
    """
    stocks = list(close_df.columns)
    n = len(close_df)

    if n >= 252:
        momentum_series = (close_df.iloc[-21] - close_df.iloc[-252]) / close_df.iloc[-252]
    else:
        momentum_series = pd.Series(np.nan, index=stocks)

    if n >= 60:
        rets = close_df.pct_change().iloc[-60:]
        volatility_series = rets.std() * np.sqrt(252)
    else:
        volatility_series = pd.Series(np.nan, index=stocks)

    if n >= 6:
        reversal_series = -(close_df.iloc[-1] - close_df.iloc[-6]) / close_df.iloc[-6]
    else:
        reversal_series = pd.Series(np.nan, index=stocks)

    if len(vol_df) >= 60:
        v20 = vol_df.iloc[-20:].mean()
        v60 = vol_df.iloc[-60:].mean()
        turnover_series = -(v20 / v60.replace(0, np.nan))
    else:
        turnover_series = pd.Series(np.nan, index=stocks)

    fin_map = _fetch_financials(stocks, prev_day)

    df = pd.DataFrame({
        'code': stocks,
        'momentum': momentum_series.values,
        'volatility': volatility_series.values,
        'reversal': reversal_series.values,
        'turnover': turnover_series.values,
        'pe': [fin_map.get(s, {}).get('pe') for s in stocks],
        'pb': [fin_map.get(s, {}).get('pb') for s in stocks],
        'roe': [fin_map.get(s, {}).get('roe') for s in stocks],
    })

    factor_cols = ['momentum', 'volatility', 'reversal', 'turnover', 'pe', 'pb', 'roe']
    df['valid_count'] = df[factor_cols].notna().sum(axis=1)
    df = df[df['valid_count'] >= g.min_factors].copy()
    if len(df) < 5:
        return None

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

    w = FACTOR_WEIGHTS
    df['score'] = (
        df['momentum_z'].fillna(0)   * w['momentum'] +
        -df['volatility_z'].fillna(0) * w['volatility'] +
        df['reversal_z'].fillna(0)    * w['reversal'] +
        -df['turnover_z'].fillna(0)   * w['turnover'] +
        -df['pe_z'].fillna(0)         * w['pe'] +
        -df['pb_z'].fillna(0)         * w['pb'] +
        df['roe_z'].fillna(0)         * w['roe']
    )
    return df.sort_values('score', ascending=False).reset_index(drop=True)


def _fetch_financials(stocks, prev_day):
    try:
        q = query(valuation.code, valuation.pe_ratio, valuation.pb_ratio, indicator.roe
        ).filter(valuation.code.in_(stocks))
        fin_df = get_fundamentals(q, date=prev_day)
        fin_map = {}
        if fin_df is not None and not fin_df.empty:
            for _, row in fin_df.iterrows():
                fin_map[row['code']] = {
                    'pe': row['pe_ratio'] if pd.notna(row['pe_ratio']) else None,
                    'pb': row['pb_ratio'] if pd.notna(row['pb_ratio']) else None,
                    'roe': row['roe'] if pd.notna(row['roe']) else None,
                }
        return fin_map
    except Exception:
        return {}


# ============================================================
# 选股与调仓
# ============================================================
def select_top_stocks(scores_df, n, context):
    selected = []
    for _, row in scores_df.iterrows():
        stock = row['code']
        if is_limit_up(stock, context):
            continue
        cur = get_current_data()[stock]
        if cur is None or cur.last_price is None or cur.last_price <= 0:
            continue
        selected.append(stock)
        if len(selected) >= n:
            break
    return selected


def is_limit_up(stock, context):
    try:
        cur = get_current_data()[stock]
        if cur is None or cur.last_price is None or cur.high_limit is None:
            return False
        return cur.last_price >= cur.high_limit * 0.995
    except Exception:
        return False


def execute_rebalance(stocks, context):
    total_value = context.portfolio.total_value
    n = len(stocks)
    if n == 0:
        return
    target_value = min(total_value * g.max_single_pct, total_value / n)
    for stock in list(context.portfolio.positions.keys()):
        if stock not in stocks:
            order_target_value(stock, 0)
    for stock in stocks:
        order_target_value(stock, target_value)
