"""
共享因子计算函数 —— factor_engine 和 local_factor_backtest 统一使用

每只股票独立计算（用于 factor_engine 逐股循环），
以及向量化批量计算（用于回测 pivot 表）。
"""
import pandas as pd
import numpy as np


# ============================================================
# 逐股因子计算（factor_engine 使用）
# ============================================================

def momentum(df: pd.DataFrame, date: str) -> float | None:
    """动量因子：过去12个月涨幅（剔除最近1个月）"""
    df = df[df['date'] <= date].copy()
    if len(df) < 252:
        return None
    one_month = 21
    if len(df) < one_month:
        return None
    recent = df['close'].iloc[-one_month]
    past = df['close'].iloc[-252]
    if past <= 0:
        return None
    total_ret = (df['close'].iloc[-1] - past) / past
    recent_ret = (df['close'].iloc[-1] - recent) / recent
    return round(total_ret - recent_ret, 4)


def volatility(df: pd.DataFrame, date: str) -> float | None:
    """低波动因子：60日年化波动率"""
    df = df[df['date'] <= date].copy()
    if len(df) < 60:
        return None
    returns = df['close'].pct_change().dropna().tail(60)
    if len(returns) < 30:
        return None
    return round(returns.std() * np.sqrt(252), 4)


def reversal(df: pd.DataFrame, date: str) -> float | None:
    """短期反转因子：5日涨幅取负（跌得多→分数高）"""
    df = df[df['date'] <= date].copy()
    if len(df) < 6:
        return None
    ret_5d = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
    return round(-ret_5d, 4)


def turnover_factor(df: pd.DataFrame, date: str) -> float | None:
    """换手率因子：20日平均换手率（越低越好）"""
    df = df[df['date'] <= date].copy()
    if 'turnover' not in df.columns or len(df) < 20:
        return None
    avg = df['turnover'].tail(20).mean()
    return round(-avg, 4) if pd.notna(avg) else None


# ============================================================
# 向量化因子计算（回测使用，基于 pivot 表）
# ============================================================

def momentum_vectorized(close_wide: pd.DataFrame) -> pd.Series:
    """动量因子（向量化版）：12个月涨幅剔除近1个月"""
    n = len(close_wide)
    if n < 252:
        return pd.Series(np.nan, index=close_wide.columns)
    total_ret = (close_wide.iloc[-1] - close_wide.iloc[-252]) / close_wide.iloc[-252]
    recent_ret = (close_wide.iloc[-1] - close_wide.iloc[-21]) / close_wide.iloc[-21]
    return total_ret - recent_ret


def volatility_vectorized(close_wide: pd.DataFrame) -> pd.Series:
    """低波动因子（向量化版）：60日年化波动率"""
    n = len(close_wide)
    if n < 60:
        return pd.Series(np.nan, index=close_wide.columns)
    rets = close_wide.pct_change().iloc[-60:]
    return rets.std() * np.sqrt(252)


def reversal_vectorized(close_wide: pd.DataFrame) -> pd.Series:
    """短期反转因子（向量化版）：5日涨幅取负"""
    n = len(close_wide)
    if n < 6:
        return pd.Series(np.nan, index=close_wide.columns)
    return -(close_wide.iloc[-1] - close_wide.iloc[-6]) / close_wide.iloc[-6]


def turnover_vectorized(vol_wide: pd.DataFrame) -> pd.Series:
    """换手率因子（向量化版）：20日/60日均量比"""
    if len(vol_wide) < 60:
        return pd.Series(np.nan, index=vol_wide.columns)
    v20 = vol_wide.iloc[-20:].mean()
    v60 = vol_wide.iloc[-60:].mean()
    return -(v20 / v60.replace(0, np.nan))
