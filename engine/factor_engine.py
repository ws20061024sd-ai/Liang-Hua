"""
因子引擎 —— 从已有日线数据计算多因子得分（纯价格因子，零额外下载）

三个因子：
  1. 动量因子：12个月涨幅（剔除最近1个月）—— A股最强因子之一
  2. 低波动因子：60日波动率（越低越好）—— 防御型因子
  3. 短期反转因子：5日涨幅（跌多了反弹）—— A股短期反转效应强

使用方法：
  scores = compute_factor_scores(date='2026-06-12')  # 某一天的因子得分
  top30 = scores.nlargest(30, 'score')                # 买得分最高的30只
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

        # 至少需要两个因子才计算得分
        valid = sum(1 for v in [mom, vol, rev, tur] if v is not None)
        if valid < 2:
            continue

        # 标准化（Z-score）后加权
        results.append({
            'code': code,
            'name': row['name'],
            'momentum': mom,
            'volatility': vol,
            'reversal': rev,
            'turnover': tur,
            'valid_factors': valid,
        })

    conn.close()

    if not results:
        return pd.DataFrame()

    df_score = pd.DataFrame(results)

    # 对每个因子做 cross-sectional z-score 标准化
    for col, weight in [('momentum', 0.4), ('volatility', 0.3),
                          ('reversal', 0.2), ('turnover', 0.1)]:
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

    # 合成总分（注意低波动和低换手是负相关，用 -z）
    df_score['score'] = (
        df_score['momentum_z'].fillna(0) * 0.4 +
        -df_score['volatility_z'].fillna(0) * 0.3 +   # 低波动=高分
        df_score['reversal_z'].fillna(0) * 0.2 +
        -df_score['turnover_z'].fillna(0) * 0.1         # 低换手=高分
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
