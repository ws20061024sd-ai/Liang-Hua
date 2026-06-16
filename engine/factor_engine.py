"""
因子引擎 —— 多因子选股评分系统

六个因子：
  价格因子（从 daily_kline）：
    1. 动量因子：12个月涨幅（剔除最近1个月）
    2. 低波动因子：60日波动率（越低越好）
    3. 短期反转因子：5日涨幅取负（跌多反弹）
    4. 换手率因子：20日平均换手率（越低越好）

  估值因子（从 financial_data）：
    5. 价值因子：PE + PB（越低越好）
    6. 质量因子：ROE（越高越好）

权重：动量 25% + 低波 15% + 反转 15% + 换手 10% + 价值 20% + 质量 15%

用法：
  scores = compute_factor_scores(date='2026-06-12')
  top30 = scores.nlargest(30, 'score')
"""
import sqlite3
import pandas as pd
import numpy as np
from config import settings


# ============================================================
# 单因子计算
# ============================================================

def _compute_momentum(df: pd.DataFrame, date: str) -> float | None:
    """
    动量因子：过去12个月涨幅（剔除最近1个月）

    学术标准：t-12 到 t-1 月的累计收益。
    这避免了短期反转效应的干扰，是A股最稳定的因子之一。
    """
    df = df[df['date'] <= date].copy()
    if len(df) < 252:
        return None

    # 最近1个月（~21个交易日）+ 之前11个月
    one_month = 21
    twelve_months = 252

    if len(df) < twelve_months:
        return None

    recent = df['close'].iloc[-one_month] if len(df) >= one_month else df['close'].iloc[0]
    past = df['close'].iloc[-twelve_months]

    if past <= 0:
        return None

    # 12个月收益 - 1个月收益 = 剔除近1个月的动量
    total_ret = (df['close'].iloc[-1] - past) / past
    recent_ret = (df['close'].iloc[-1] - recent) / recent
    momentum = total_ret - recent_ret

    return round(momentum, 4)


def _compute_volatility(df: pd.DataFrame, date: str) -> float | None:
    """
    低波动因子：60日年化波动率

    越低越好（低波动股票长期跑赢高波动）。
    """
    df = df[df['date'] <= date].copy()
    if len(df) < 60:
        return None

    returns = df['close'].pct_change().dropna().tail(60)
    if len(returns) < 30:
        return None

    daily_vol = returns.std()
    annual_vol = daily_vol * np.sqrt(252)
    return round(annual_vol, 4)


def _compute_reversal(df: pd.DataFrame, date: str) -> float | None:
    """
    短期反转因子：5日涨幅

    负值 = 最近跌了 → 可能反弹（A 股短期反转效应强）
    取负号使高分 = 跌得多（反弹潜力大）
    """
    df = df[df['date'] <= date].copy()
    if len(df) < 6:
        return None

    ret_5d = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
    return round(-ret_5d, 4)  # 负号：跌得多→分数高


def _fetch_financial_factors(conn, date: str) -> pd.DataFrame:
    """
    从 financial_data 表获取最新可用的 PE/PB/ROE

    策略：取 ≤ date 的最新一条记录（季度数据有滞后）
    返回 DataFrame: [code, pe, pb, roe]
    """
    df = pd.read_sql_query("""
        SELECT f.code, f.pe, f.pb, f.roe, f.date as fin_date
        FROM financial_data f
        WHERE f.date <= ?
          AND f.date = (
              SELECT MAX(f2.date) FROM financial_data f2
              WHERE f2.code = f.code AND f2.date <= ?
          )
    """, conn, params=(date, date))
    return df


def _compute_turnover(df: pd.DataFrame, date: str) -> float | None:
    """
    换手率因子：20日平均换手率

    低换手 = 筹码稳定（可选附加因子）
    """
    df = df[df['date'] <= date].copy()
    if 'turnover' not in df.columns or len(df) < 20:
        return None

    avg_turnover = df['turnover'].tail(20).mean()
    return round(-avg_turnover, 4) if pd.notna(avg_turnover) else None


# ============================================================
# 多因子合成
# ============================================================

def compute_factor_scores(date: str = None) -> pd.DataFrame:
    """
    计算某一天所有股票的多因子得分

    因子权重（等权）：
      动量 40% + 低波动 30% + 反转 20% + 换手率 10%

    返回 DataFrame: [code, momentum, volatility, reversal, turnover, score]
    """
    conn = sqlite3.connect(settings.DB_PATH)

    if date is None:
        date = pd.read_sql_query(
            "SELECT MAX(date) FROM daily_kline", conn
        ).iloc[0, 0]

    codes = pd.read_sql_query("SELECT code, name FROM stock_info", conn)

    # 获取财务因子数据
    fin_df = _fetch_financial_factors(conn, date)
    fin_map = {}
    if not fin_df.empty:
        for _, r in fin_df.iterrows():
            fin_map[r['code']] = {
                'pe': r['pe'] if pd.notna(r['pe']) else None,
                'pb': r['pb'] if pd.notna(r['pb']) else None,
                'roe': r['roe'] if pd.notna(r['roe']) else None,
            }

    results = []

    for _, row in codes.iterrows():
        code = row['code']
        df = pd.read_sql_query(
            "SELECT date, close, turnover FROM daily_kline WHERE code=? ORDER BY date",
            conn, params=(code,)
        )
        if df.empty:
            continue

        mom = _compute_momentum(df, date)
        vol = _compute_volatility(df, date)
        rev = _compute_reversal(df, date)
        tur = _compute_turnover(df, date)

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
            'name': row['name'],
            'momentum': mom,
            'volatility': vol,
            'reversal': rev,
            'turnover': tur,
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'valid_factors': valid,
        })

    conn.close()

    if not results:
        return pd.DataFrame()

    df_score = pd.DataFrame(results)

    # 价格因子 z-score 标准化
    price_factors = [
        ('momentum', 0.25),    # 动量（正向）
        ('volatility', 0.15),  # 低波动（负向）
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
    value_weight = 0.20
    quality_weight = 0.15
    has_financial = df_score['pe'].notna().sum() > 10

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

    # 合成总分
    df_score['score'] = (
        df_score['momentum_z'].fillna(0) * 0.25 +
        -df_score['volatility_z'].fillna(0) * 0.15 +
        df_score['reversal_z'].fillna(0) * 0.15 +
        -df_score['turnover_z'].fillna(0) * 0.10 +
        -df_score['pe_z'].fillna(0) * (value_weight / 2) +     # PE 越低越好
        -df_score['pb_z'].fillna(0) * (value_weight / 2) +     # PB 越低越好
        df_score['roe_z'].fillna(0) * quality_weight            # ROE 越高越好
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
