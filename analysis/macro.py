"""
宏观分析 —— 指数全景 + 市场广度
"""
import pandas as pd
import numpy as np
from engine.market_timing import get_market_regime


def analyze(data_date: str = None) -> dict:
    """
    宏观分析
    参数 data_date: 统一使用此日期查数据（和广度一致）
    """
    regime = get_market_regime()

    # 如果没有指定日期，从 DB 获取最新数据日期
    if data_date is None:
        import sqlite3
        from config import settings
        conn = sqlite3.connect(settings.DB_PATH)
        data_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        conn.close()

    breadth = _analyze_breadth()
    indices = _fetch_indices(data_date)

    return {
        'regime': regime,
        'indices': indices,
        'breadth': breadth,
    }


def _fetch_indices(data_date: str = None) -> list[dict]:
    """获取指定日期的四大指数数据。未指定日期则用最新。"""
    try:
        import akshare as ak

        index_map = {
            '沪深300': 'sh000300',
            '上证指数': 'sh000001',
            '深证成指': 'sz399001',
            '创业板指': 'sz399006',
        }

        results = []
        for name, symbol in index_map.items():
            try:
                df = ak.stock_zh_index_daily(symbol=symbol)
                if df is not None and len(df) >= 2:
                    # 按指定日期查找
                    if data_date:
                        target = pd.Timestamp(data_date)
                        row = df[df['date'] == target]
                        if row.empty:
                            continue
                        idx_current = row.index[0]
                    else:
                        idx_current = len(df) - 1

                    latest = df.iloc[idx_current]
                    # 确保 idx_current > 0
                    prev = df.iloc[idx_current - 1] if idx_current > 0 else latest

                    pct = (latest['close'] - prev['close']) / prev['close'] * 100

                    # 5日前
                    pct5 = None
                    if idx_current >= 5:
                        close5 = df.iloc[idx_current - 5]['close']
                        pct5 = (latest['close'] - close5) / close5 * 100

                    results.append({
                        'name': name,
                        'close': round(float(latest['close']), 2),
                        'pct_change': round(pct, 2),
                        'pct_5d': round(pct5, 2) if pct5 else None,
                    })
            except Exception:
                pass

        return results
    except Exception:
        return []


def _analyze_breadth() -> dict:
    """市场广度分析（从本地数据库，只取最新日期）"""
    import sqlite3
    from config import settings

    try:
        conn = sqlite3.connect(settings.DB_PATH)

        # 获取最新数据日期
        max_date = pd.read_sql_query(
            "SELECT MAX(date) as d FROM daily_kline", conn
        )['d'].iloc[0]

        df = pd.read_sql_query("""
            SELECT d.code, d.pct_change, d.amount, d.close, d.date
            FROM daily_kline d
            WHERE d.date = ?
        """, conn, params=(max_date,))
        conn.close()

        if df.empty:
            return {}

        total = len(df)
        # 防御：如果 pct_change 全 NULL，返回数据异常标记
        null_count = int(df['pct_change'].isna().sum())
        if null_count == total:
            return {
                'total': total,
                'up': 0, 'down': 0, 'flat': 0,
                'up_ratio': 0, 'avg_pct': 0, 'med_pct': 0,
                'total_amount': df['amount'].sum(),
                'total_amount_yi': round(df['amount'].sum() / 1e8, 0) if df['amount'].sum() else 0,
                'data_date': str(max_date),
                'data_error': '涨跌幅数据全部缺失，无法统计广度',
            }

        valid = df[df['pct_change'].notna()]
        up = int((valid['pct_change'] > 0).sum())
        down = int((valid['pct_change'] < 0).sum())
        flat = total - up - down - null_count
        avg_pct = round(float(valid['pct_change'].mean()), 2)
        med_pct = round(float(valid['pct_change'].median()), 2)
        total_amount = df['amount'].sum()

        return {
            'total': total,
            'up': up, 'down': down, 'flat': flat,
            'null_count': null_count,
            'up_ratio': round(up / total * 100, 1) if total > 0 else 0,
            'avg_pct': avg_pct,
            'med_pct': med_pct,
            'total_amount': total_amount,
            'total_amount_yi': round(total_amount / 1e8, 0) if total_amount else 0,
            'data_date': str(max_date),
        }
    except Exception:
        return {}
