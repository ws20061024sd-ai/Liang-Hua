"""
微观分析 —— 信号复盘 + 异动个股
"""
import sqlite3
import pandas as pd
from config import settings


def analyze() -> dict:
    """返回个股分析结果"""
    try:
        conn = sqlite3.connect(settings.DB_PATH)
        df = pd.read_sql_query("""
            SELECT d.code, d.date, d.close, d.pct_change, d.amount, s.name
            FROM daily_kline d
            JOIN stock_info s ON d.code = s.code
            WHERE d.date = (SELECT MAX(date) FROM daily_kline)
        """, conn)
        conn.close()

        if df.empty:
            return {}

        top_gainers = _top_movers(df, n=5, ascending=False)
        top_losers = _top_movers(df, n=5, ascending=True)
        signal_review = _review_signals(conn)

        return {
            'top_gainers': top_gainers,
            'top_losers': top_losers,
            'signal_review': signal_review,
            'data_date': str(df['date'].iloc[0]) if len(df) > 0 else None,
        }
    except Exception as e:
        return {'error': str(e)}


def _top_movers(df: pd.DataFrame, n: int = 5, ascending: bool = True) -> list[dict]:
    """涨跌前N"""
    subset = df.nlargest(n, 'pct_change') if not ascending else df.nsmallest(n, 'pct_change')
    return [
        {
            'name': r['name'],
            'code': r['code'],
            'close': round(float(r['close']), 2),
            'pct': round(float(r['pct_change']), 2),
        }
        for _, r in subset.iterrows()
    ]


def _review_signals(conn) -> list[dict]:
    """复盘前一日信号表现"""
    try:
        # 获取最近两天的日期
        dates = pd.read_sql_query("""
            SELECT DISTINCT date FROM daily_kline
            ORDER BY date DESC LIMIT 2
        """, conn)
        if len(dates) < 2:
            return []

        latest = dates['date'].iloc[0]
        prev = dates['date'].iloc[1]

        # 获取前一日所有数据
        prev_df = pd.read_sql_query(
            "SELECT code, close, pct_change FROM daily_kline WHERE date = ?",
            conn, params=(prev,)
        )
        # 获取最新日所有数据
        latest_df = pd.read_sql_query(
            "SELECT code, close, pct_change FROM daily_kline WHERE date = ?",
            conn, params=(latest,)
        )

        # 信号记录：取前一日策略发出的信号
        sig_df = pd.read_sql_query("""
            SELECT code, action, date FROM signal_history
            WHERE date = ?
        """, conn, params=(prev,))
        # 如果没有 signal_history 数据，尝试从策略逻辑中找
        if sig_df.empty:
            return []

        results = []
        for _, sig in sig_df.iterrows():
            code = sig['code']
            prev_row = prev_df[prev_df['code'] == code]
            latest_row = latest_df[latest_df['code'] == code]
            if prev_row.empty or latest_row.empty:
                continue
            prev_close = float(prev_row.iloc[0]['close'])
            latest_close = float(latest_row.iloc[0]['close'])
            change = (latest_close - prev_close) / prev_close * 100
            results.append({
                'code': code,
                'name': '',  # can join with stock_info
                'action': sig['action'],
                'prev_close': round(prev_close, 2),
                'latest_close': round(latest_close, 2),
                'change': round(change, 2),
                'hit': (sig['action'] == 'BUY' and change > 0) or
                       (sig['action'] == 'SELL' and change < 0),
            })

        return results
    except Exception:
        return []
