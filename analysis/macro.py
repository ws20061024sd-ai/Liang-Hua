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
