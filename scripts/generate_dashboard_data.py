#!/usr/bin/env python3
"""
仪表盘数据生成器 —— 从 SQLite 提取数据，输出 JSON 供 HTML 仪表盘使用

用法: PYTHONPATH=. python scripts/generate_dashboard_data.py
输出: dashboard/data.json
"""
import sqlite3, json, os, sys
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB = 'data/stocks.db'
OUTPUT = 'dashboard/data.json'


def query_db(conn, sql, params=None):
    return pd.read_sql_query(sql, conn, params=params) if params else pd.read_sql_query(sql, conn)


def build_dashboard():
    conn = sqlite3.connect(DB)

    data = {
        'generated_at': datetime.now().isoformat(),
        'market': _market_overview(conn),
        'data_health': _data_health(conn),
        'strategies': _strategy_performance(),
        'signals': _recent_signals(conn),
        'positions': _current_positions(conn),
        'factors': _factor_top10(conn),
    }

    conn.close()

    os.makedirs('dashboard', exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 仪表盘数据已生成: {OUTPUT}")
    print(f"   更新时间: {data['generated_at']}")


def _market_overview(conn):
    """市场状态概览"""
    # CSI300 最近60天走势
    idx = query_db(conn, """
        SELECT date, close FROM daily_kline
        WHERE code = '000300' AND date >= date('now', '-90 days')
        ORDER BY date
    """)
    if idx.empty:
        idx = query_db(conn, """
            SELECT date, AVG(close) as close FROM daily_kline
            WHERE date >= date('now', '-90 days')
            GROUP BY date ORDER BY date
        """)

    latest_date = query_db(conn, "SELECT MAX(date) FROM daily_kline").iloc[0, 0]
    stock_count = query_db(conn,
        "SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?", (latest_date,)
    ).iloc[0, 0]

    # 计算MA20/MA60判断市场状态
    if len(idx) >= 60:
        ma20 = idx['close'].rolling(20).mean()
        ma60 = idx['close'].rolling(60).mean()
        regime = 'strong' if ma20.iloc[-1] > ma60.iloc[-1] else 'weak'
        last_close = float(idx['close'].iloc[-1])
        ret_20d = float((idx['close'].iloc[-1] / idx['close'].iloc[-20] - 1) * 100) if len(idx) >= 20 else 0
    else:
        regime = 'unknown'
        last_close = 0
        ret_20d = 0

    return {
        'date': str(latest_date),
        'stocks': int(stock_count),
        'regime': regime,
        'regime_label': '🟢 强势' if regime == 'strong' else ('🔴 弱势' if regime == 'weak' else '❓ 未知'),
        'index_close': round(last_close, 1),
        'ret_20d': round(ret_20d, 1),
        'index_trend': [{'date': str(d), 'close': round(float(c), 1)}
                        for d, c in zip(idx['date'].tail(90), idx['close'].tail(90))],
    }


def _data_health(conn):
    """数据健康状态"""
    latest_daily = query_db(conn, "SELECT MAX(date) FROM daily_kline").iloc[0, 0]
    daily_cnt = query_db(conn,
        "SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?", (latest_daily,)
    ).iloc[0, 0]
    nulls = query_db(conn,
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL", (latest_daily,)
    ).iloc[0, 0]

    # PE/PB覆盖
    fin = query_db(conn, """
        SELECT COUNT(*) as total,
               SUM(CASE WHEN pe IS NOT NULL THEN 1 ELSE 0 END) as pe_ok,
               SUM(CASE WHEN pb IS NOT NULL THEN 1 ELSE 0 END) as pb_ok
        FROM financial_data
        WHERE date = (SELECT MAX(date) FROM financial_data WHERE date <= ?)
    """, (latest_daily,))
    pe_pct = round(fin['pe_ok'].iloc[0] / fin['total'].iloc[0] * 100) if fin['total'].iloc[0] > 0 else 0
    pb_pct = round(fin['pb_ok'].iloc[0] / fin['total'].iloc[0] * 100) if fin['total'].iloc[0] > 0 else 0

    # ROE覆盖
    latest_roe = query_db(conn, "SELECT MAX(date) FROM financial_roe").iloc[0, 0]
    roe_cnt = query_db(conn,
        "SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=?", (latest_roe,)
    ).iloc[0, 0]

    db_size = os.path.getsize(DB) / 1024 / 1024

    return {
        'daily': {'date': str(latest_daily), 'stocks': int(daily_cnt), 'nulls': int(nulls),
                   'status': 'ok' if daily_cnt >= 280 and nulls == 0 else 'warn'},
        'pe': {'coverage': int(pe_pct), 'status': 'ok' if pe_pct >= 80 else 'warn'},
        'pb': {'coverage': int(pb_pct), 'status': 'ok' if pb_pct >= 90 else 'warn'},
        'roe': {'date': str(latest_roe), 'stocks': int(roe_cnt), 'status': 'ok' if roe_cnt >= 255 else 'warn'},
        'db_size_mb': round(db_size, 1),
    }


def _strategy_performance():
    """三策略回测性能（硬编码最优参数结果）"""
    return [
        {
            'name': '双均线 MA(20,60)',
            'style': 'trend',
            'ann_ret': 14.5, 'sharpe': 0.57, 'max_dd': -31.6,
            'description': '慢均线减少假信号，捕获中期趋势',
        },
        {
            'name': '动量突破 (10,2%,10)',
            'style': 'trend',
            'ann_ret': 10.7, 'sharpe': 0.39, 'max_dd': -60.9,
            'description': '10日突破更及时，需止损保护',
        },
        {
            'name': '均值回归 BB(10,2.0)',
            'style': 'reversion',
            'ann_ret': 18.9, 'sharpe': 0.65, 'max_dd': -39.9,
            'description': '短周期布林带，捕捉短期超卖',
        },
    ]


def _recent_signals(conn):
    """最近5天的信号"""
    df = query_db(conn, """
        SELECT date, code, name, strategy, action, price, status, filter_reason
        FROM signal_history ORDER BY date DESC, id DESC LIMIT 50
    """)
    signals = []
    for _, r in df.iterrows():
        signals.append({
            'date': str(r['date']),
            'code': r['code'],
            'name': r['name'],
            'strategy': r['strategy'],
            'action': r['action'],
            'price': round(float(r['price']), 2) if r['price'] else 0,
            'status': r['status'],
            'reason': r['filter_reason'] or '',
        })
    return signals


def _current_positions(conn):
    """当前持仓"""
    try:
        from engine.position_tracker import get_positions
        positions = get_positions()
    except ImportError:
        positions = []

    if not positions:
        return []

    codes = [p['code'] for p in positions]
    placeholders = ','.join('?' * len(codes))
    latest_date = query_db(conn, "SELECT MAX(date) FROM daily_kline").iloc[0, 0]
    prices = query_db(conn, f"""
        SELECT code, close FROM daily_kline
        WHERE date = ? AND code IN ({placeholders})
    """, [latest_date] + codes)
    price_map = dict(zip(prices['code'], prices['close']))

    result = []
    for p in positions:
        current = price_map.get(p['code'])
        if current:
            pnl = (current - p['buy_price']) / p['buy_price'] * 100
            peak = query_db(conn,
                "SELECT MAX(close) FROM daily_kline WHERE code=? AND date>=?",
                (p['code'], p['buy_date'])
            ).iloc[0, 0]
            result.append({
                'code': p['code'], 'name': p['name'],
                'buy_date': p['buy_date'],
                'buy_price': round(p['buy_price'], 2),
                'current': round(float(current), 2),
                'pnl_pct': round(pnl, 1),
                'peak': round(float(peak), 2),
                'stop_level': round(float(peak) * 0.95, 2),
            })
    return result


def _factor_top10(conn):
    """多因子引擎 Top10"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from engine.factor_engine import compute_factor_scores
        scores = compute_factor_scores()
        if scores.empty:
            return []
        top = scores.head(10)
        return [{
            'rank': i + 1,
            'code': r['code'], 'name': r['name'],
            'score': round(float(r['score']), 2),
            'momentum': round(float(r['momentum']), 2) if pd.notna(r.get('momentum')) else None,
            'volatility': round(float(r['volatility']), 2) if pd.notna(r.get('volatility')) else None,
        } for i, (_, r) in enumerate(top.iterrows())]
    except Exception as e:
        return [{'error': str(e)}]


if __name__ == '__main__':
    build_dashboard()
