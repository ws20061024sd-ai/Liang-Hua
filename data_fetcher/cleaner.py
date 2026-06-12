"""
数据清洗器 —— 处理复权、停牌标记、异常检测
"""
import sqlite3
import pandas as pd
from config import settings


def get_db_connection():
    conn = sqlite3.connect(settings.DB_PATH)
    return conn


def get_stock_data(code: str, days: int = 100) -> pd.DataFrame | None:
    """
    获取单只股票最近 N 天的数据（每次独立打开/关闭连接）
    单次调用可用此函数。批量调用请用 get_stock_data_batch()。
    """
    conn = get_db_connection()
    result = _get_stock_data_from_conn(conn, code, days)
    conn.close()
    return result


def _get_stock_data_from_conn(conn, code: str, days: int = 100) -> pd.DataFrame | None:
    """使用已有连接获取数据，供批量调用复用连接"""
    query = """
        SELECT date, open, high, low, close, volume, amount, pct_change, turnover
        FROM daily_kline
        WHERE code = ?
        ORDER BY date DESC
        LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=(code, days))

    if df.empty:
        return None

    df = df.sort_values('date').reset_index(drop=True)
    df['date'] = pd.to_datetime(df['date'])

    df['is_suspended'] = False
    pct = df['pct_change'].fillna(0)
    mask = (df['volume'] < 100) & (pct.abs() < 0.001)
    df.loc[mask, 'is_suspended'] = True

    df['is_abnormal'] = pct.abs() > 15

    return df


def get_batch_stock_data(codes: list[str], days: int = 100) -> dict[str, pd.DataFrame]:
    """
    批量获取多只股票的数据（复用同一个数据库连接）

    返回: {code: DataFrame, ...}
    """
    conn = get_db_connection()
    result = {}
    for code in codes:
        df = _get_stock_data_from_conn(conn, code, days)
        if df is not None:
            result[code] = df
    conn.close()
    return result


def get_stock_info(code: str) -> dict | None:
    """获取股票基本信息"""
    conn = get_db_connection()
    cursor = conn.execute(
        "SELECT code, name, is_st FROM stock_info WHERE code = ?", (code,)
    )
    row = cursor.fetchone()
    conn.close()

    if row is None:
        return None
    return {'code': row[0], 'name': row[1], 'is_st': bool(row[2])}


def get_all_stocks() -> pd.DataFrame:
    """获取股票池中所有股票的基本信息"""
    conn = get_db_connection()
    df = pd.read_sql_query(
        "SELECT code, name, is_st FROM stock_info ORDER BY code", conn
    )
    conn.close()
    return df


def get_latest_kline_for_all() -> pd.DataFrame:
    """获取所有股票的最新一行日线数据（用于当日过滤）"""
    conn = get_db_connection()
    query = """
        SELECT d.code, d.date, d.close, d.pct_change, d.volume,
               d.amount, d.turnover, s.name, s.is_st
        FROM daily_kline d
        JOIN stock_info s ON d.code = s.code
        WHERE d.date = (SELECT MAX(date) FROM daily_kline WHERE code = d.code)
        ORDER BY d.code
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    return df
