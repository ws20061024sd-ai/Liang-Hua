"""
因子引擎 —— 多因子选股评分系统（v2.1 权重）

七个因子：
  价格因子（从 daily_kline）：
    1. 动量因子：12-1动量 = (P[-21]-P[-252])/P[-252]
    2. 低波动因子：60日波动率（越低越好）
    3. 短期反转因子：5日涨幅取负（跌多反弹）
    4. 换手率因子：成交量比率 v20/v60（越低越好）

  估值/质量因子（从 financial_data）：
    5. PE：市盈率（越低越好）
    6. PB：市净率（已砍——单因子回测负收益-0.74%）
    7. ROE：净资产收益率（越高越好）

权重(v2.1)：动量 30% + 低波 20% + 反转 15% + 换手 10% + PE 10% + PB 0% + ROE 15%

用法：
  scores = compute_factor_scores(date='2026-06-12')
  top30 = scores.nlargest(30, 'score')
"""
import sqlite3
import pandas as pd
import numpy as np
from config import settings


# ============================================================
# 单因子计算 —— 统一从 engine.factors 导入
# ============================================================
from engine.factors import momentum as _compute_momentum
from engine.factors import volatility as _compute_volatility
from engine.factors import reversal as _compute_reversal


def _fetch_financial_factors(conn, date: str) -> pd.DataFrame:
    """
    从 financial_data 表获取最新可用的 PE/PB

    策略：取 ≤ date 的最新一条记录（季度数据有滞后）
    返回 DataFrame: [code, pe, pb]
    """
    df = pd.read_sql_query("""
        SELECT f.code, f.pe, f.pb, f.date as fin_date
        FROM financial_data f
        WHERE f.date <= ?
          AND f.date = (
              SELECT MAX(f2.date) FROM financial_data f2
              WHERE f2.code = f.code AND f2.date <= ?
          )
    """, conn, params=(date, date))
    return df


def _fetch_roe(conn, date: str) -> dict:
    """
    从 financial_roe 表获取最新 ROE（季度数据）

    财报披露延迟：Q1(3/31)约4月底披露, Q2/半年报(6/30)约8月底, Q3(9/30)约10月底, Q4/年报(12/31)约次年4月底
    统一滞后 120 天，避免未来数据泄露

    返回 {code: roe} 字典
    """
    from datetime import datetime, timedelta
    lagged = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=120)).strftime('%Y-%m-%d')
    df = pd.read_sql_query("""
        SELECT r.code, r.roe, r.date
        FROM financial_roe r
        WHERE r.date <= ?
          AND r.date = (
              SELECT MAX(r2.date) FROM financial_roe r2
              WHERE r2.code = r.code AND r2.date <= ?
          )
    """, conn, params=(lagged, lagged))
    if df.empty:
        return {}
    return dict(zip(df['code'], df['roe']))


# ============================================================
# 多因子合成
# ============================================================

def compute_factor_scores(date: str = None) -> pd.DataFrame:
    """
    计算某一天所有股票的多因子得分（v2.1）

    因子权重：
      动量 30% + 低波动 20% + 反转 15% + 换手率 10% + PE 10% + PB 0% + ROE 15%

    返回 DataFrame: [code, name, momentum, volatility, reversal, turnover, pe, pb, roe, score]
    """
    conn = sqlite3.connect(settings.DB_PATH)

    if date is None:
        date = pd.read_sql_query(
            "SELECT MAX(date) FROM daily_kline", conn
        ).iloc[0, 0]

    codes = pd.read_sql_query("SELECT code, name FROM stock_info", conn)
    code_name_map = dict(zip(codes['code'], codes['name']))

    # 价格过滤：排除买不起的股票（1手 > 单仓位资金）
    # 查询所有股票的最新价格
    latest_prices = pd.read_sql_query(
        "SELECT code, close FROM daily_kline WHERE date=?", conn, params=(date,)
    )
    price_map = dict(zip(latest_prices['code'], latest_prices['close']))

    # 价格过滤：排除买不起的股票（1手 > 单仓位资金）
    if settings.MAX_STOCK_PRICE > 0:
        affordable = {c for c, p in price_map.items() if p and p <= settings.MAX_STOCK_PRICE}
        codes = codes[codes['code'].isin(affordable)]

    # 获取财务因子数据（PE/PB 从 financial_data，ROE 从 financial_roe）
    fin_df = _fetch_financial_factors(conn, date)
    roe_map = _fetch_roe(conn, date)

    fin_map = {}
    if not fin_df.empty:
        for _, r in fin_df.iterrows():
            fin_map[r['code']] = {
                'pe': r['pe'] if pd.notna(r['pe']) else None,
                'pb': r['pb'] if pd.notna(r['pb']) else None,
                'roe': None,
            }
    # 合并 ROE
    for code, roe_val in roe_map.items():
        if code in fin_map:
            fin_map[code]['roe'] = roe_val if pd.notna(roe_val) else None
        else:
            fin_map[code] = {'pe': None, 'pb': None, 'roe': roe_val if pd.notna(roe_val) else None}

    # 批量加载所有股票的日线数据（1次查询替代300次）
    all_kline = pd.read_sql_query(
        "SELECT code, date, close, volume FROM daily_kline WHERE date <= ? ORDER BY code, date",
        conn, params=(date,)
    )
    conn.close()

    if all_kline.empty:
        return pd.DataFrame()

    # 只计算买得起的股票（已在上面过滤过 codes）
    affordable_set = set(codes['code'].tolist())
    all_kline = all_kline[all_kline['code'].isin(affordable_set)]

    if all_kline.empty:
        return pd.DataFrame()

    results = []

    for code, df in all_kline.groupby('code'):
        name = code_name_map.get(code, code)
        if df.empty:
            continue

        mom = _compute_momentum(df, date)
        vol = _compute_volatility(df, date)
        rev = _compute_reversal(df, date)
        # 换手率因子 = 成交量比率 v20/v60（与聚宽一致）
        if len(df) >= 60:
            v20 = df['volume'].tail(20).mean()
            v60 = df['volume'].tail(60).mean()
            tur = round(-(v20 / v60), 4) if v60 > 0 else None
        else:
            tur = None

        # 财务因子
        fin = fin_map.get(code, {})
        pe = fin.get('pe')
        pb = fin.get('pb')
        roe = fin.get('roe')

        # 至少需要 3 个因子才计算得分
        valid = sum(1 for v in [mom, vol, rev, tur, pe, pb, roe] if v is not None)
        if valid < 3:
            continue

        results.append({
            'code': code,
            'name': name,
            'close': round(float(price_map.get(code, 0)), 2),
            'momentum': mom,
            'volatility': vol,
            'reversal': rev,
            'turnover': tur,
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'valid_factors': valid,
        })

    if not results:
        return pd.DataFrame()

    df_score = pd.DataFrame(results)

    # 价格因子 z-score 标准化
    price_factors = [
        ('momentum', 0.30),    # 动量（正向）—— v2.1 提权，单因子收益王
        ('volatility', 0.20),  # 低波动（负向）—— v2.1 提权，组合防御核心
        ('reversal', 0.15),    # 反转（正向）
        ('turnover', 0.10),    # 低换手（负向）
    ]
    for col, weight in price_factors:
        vals = df_score[col].dropna()
        if len(vals) < 10:
            df_score[f'{col}_z'] = 0
            continue
        mean, std = vals.mean(), vals.std()
        if std == 0:
            df_score[f'{col}_z'] = 0
        else:
            df_score[f'{col}_z'] = df_score[col].apply(
                lambda x: (x - mean) / std if pd.notna(x) else 0
            )

    # 估值因子 z-score（如果数据可用）
    # v2.1: PE 10%, PB 0%（单因子回测负收益，已砍）
    value_weight = 0.10   # 全部给 PE
    quality_weight = 0.15  # ROE
    has_financial = df_score['pe'].notna().sum() > 10
    scale = 1.0  # 无财务数据时价格因子归一化系数，默认不缩放

    if has_financial:
        # PE: 越低越好（负向）
        for col in ['pe', 'pb']:
            vals = df_score[col].dropna()
            if len(vals) >= 10:
                mean, std = vals.mean(), vals.std()
                if std > 0:
                    df_score[f'{col}_z'] = df_score[col].apply(
                        lambda x: (x - mean) / std if pd.notna(x) else 0
                    )
                else:
                    df_score[f'{col}_z'] = 0
            else:
                df_score[f'{col}_z'] = 0

        # ROE: 越高越好（正向）
        roe_vals = df_score['roe'].dropna()
        if len(roe_vals) >= 10:
            mean, std = roe_vals.mean(), roe_vals.std()
            if std > 0:
                df_score['roe_z'] = df_score['roe'].apply(
                    lambda x: (x - mean) / std if pd.notna(x) else 0
                )
            else:
                df_score['roe_z'] = 0
        else:
            df_score['roe_z'] = 0
    else:
        # 无财务数据 → 权重分配给价格因子
        df_score['pe_z'] = 0
        df_score['pb_z'] = 0
        df_score['roe_z'] = 0
        # 重新分配：动量+5%, 波动+5%, 反转+5%, 换手+5%
        # (简化处理：保持现有价格因子权重不变，缺少的零值不影响)
        value_weight = 0
        quality_weight = 0
        # 财务数据缺失 → 将估值/质量权重按比例分配给价格因子
        slack = 0.25  # value_weight(0.10) + quality_weight(0.15)
        price_total = 0.75  # 四个价格因子权重之和 (0.30+0.20+0.15+0.10)
        if price_total > 0:
            scale = 1.0 / price_total  # 归一化到 1.0

    # 合成总分（scale 仅在没有财务数据时生效，默认 1.0 不缩放）
    w_mom = 0.30 * (scale if not has_financial else 1.0)
    w_vol = 0.20 * (scale if not has_financial else 1.0)
    w_rev = 0.15 * (scale if not has_financial else 1.0)
    w_tur = 0.10 * (scale if not has_financial else 1.0)
    w_pe  = value_weight if has_financial else 0
    w_pb  = 0   # v2.1: PB 因子已砍（单因子回测负收益-0.74%）
    w_roe = quality_weight if has_financial else 0

    df_score['score'] = (
        df_score['momentum_z'].fillna(0) * w_mom +
        -df_score['volatility_z'].fillna(0) * w_vol +
        df_score['reversal_z'].fillna(0) * w_rev +
        -df_score['turnover_z'].fillna(0) * w_tur +
        -df_score['pe_z'].fillna(0) * w_pe +
        -df_score['pb_z'].fillna(0) * w_pb +
        df_score['roe_z'].fillna(0) * w_roe
    )

    df_score = df_score.sort_values('score', ascending=False).reset_index(drop=True)
    return df_score


# ============================================================
# 选股
# ============================================================

def get_top_stocks(date: str = None, n: int = 30) -> list[dict]:
    """获取某天得分最高的 N 只股票"""
    df = compute_factor_scores(date)
    if df.empty:
        return []
    top = df.head(n)
    return top[['code', 'name', 'score', 'momentum', 'volatility', 'reversal']].to_dict('records')


def get_top_for_capital(capital: float, date: str = None) -> list[dict]:
    """根据资金档位自动确定持仓数"""
    n = 2 if capital <= 20000 else (5 if capital <= 50000 else (10 if capital <= 100000 else 30))
    return get_top_stocks(date, n)


if __name__ == "__main__":
    # 快速测试
    scores = compute_factor_scores()
    if not scores.empty:
        print(f"✅ {len(scores)} 只有效因子得分")
        print(scores[['code', 'name', 'score', 'momentum', 'volatility']].head(10))
        print(f"\n当前 ¥10,000 推荐（Top 2）：")
        for s in get_top_for_capital(10000):
            print(f"  {s['code']} {s['name']}: score={s['score']:.2f}")
    else:
        print("❌ 无数据")
