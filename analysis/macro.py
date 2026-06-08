"""
宏观分析 —— 指数全景 + 市场广度
"""
import pandas as pd
import numpy as np
from engine.market_timing import get_market_regime


def analyze() -> dict:
    """
    宏观分析，返回:
    {
        'regime': { ... },           # 大盘择时结果
        'indices': [ ... ],          # 四大指数
        'breadth': { ... },          # 市场广度
        'volume_trend': '放量下跌',   # 成交量趋势
    }
    """
    regime = get_market_regime()

    indices = _fetch_indices()
    breadth = _analyze_breadth()

    return {
        'regime': regime,
        'indices': indices,
        'breadth': breadth,
    }


def _fetch_indices() -> list[dict]:
    """获取四大指数最新数据"""
    try:
        import akshare as ak

        index_map = {
            '沪深300': ('sh000300', 'ak.stock_zh_index_daily'),
            '上证指数': ('sh000001', 'ak.stock_zh_index_daily'),
            '深证成指': ('sz399001', 'ak.stock_zh_index_daily'),
            '创业板指': ('sz399006', 'ak.stock_zh_index_daily'),
        }

        results = []
        for name, (symbol, _) in index_map.items():
            try:
                df = ak.stock_zh_index_daily(symbol=symbol)
                if df is not None and len(df) >= 5:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2]
                    pct = (latest['close'] - prev['close']) / prev['close'] * 100
                    # 5日涨跌
                    if len(df) >= 5:
                        close5 = df.iloc[-5]['close']
                        pct5 = (latest['close'] - close5) / close5 * 100
                    else:
                        pct5 = None

                    results.append({
                        'name': name,
                        'close': round(float(latest['close']), 2),
                        'pct_change': round(pct, 2),
                        'pct_5d': round(pct5, 2) if pct5 else None,
                        'volume': int(latest.get('volume', 0)),
                    })
            except Exception:
                pass

        return results
    except Exception:
        return []


def _analyze_breadth() -> dict:
    """市场广度分析（从本地数据库）"""
    import sqlite3
    from config import settings

    try:
        conn = sqlite3.connect(settings.DB_PATH)
        df = pd.read_sql_query("""
            SELECT d.code, d.pct_change, d.amount, d.close
            FROM daily_kline d
            WHERE d.date = (SELECT MAX(date) FROM daily_kline)
        """, conn)
        conn.close()

        if df.empty:
            return {}

        total = len(df)
        up = int((df['pct_change'] > 0).sum())
        down = int((df['pct_change'] < 0).sum())
        flat = total - up - down
        avg_pct = round(float(df['pct_change'].mean()), 2)
        med_pct = round(float(df['pct_change'].median()), 2)
        total_amount = df['amount'].sum()

        # 成交量趋势（与昨日对比）
        latest_date = pd.read_sql_query(
            "SELECT MAX(date) as d FROM daily_kline",
            sqlite3.connect(settings.DB_PATH)
        )['d'].iloc[0]

        return {
            'total': total,
            'up': up,
            'down': down,
            'flat': flat,
            'up_ratio': round(up / total * 100, 1),
            'avg_pct': avg_pct,
            'med_pct': med_pct,
            'total_amount': total_amount,
            'total_amount_yi': round(total_amount / 1e8, 0),
        }
    except Exception:
        return {}
