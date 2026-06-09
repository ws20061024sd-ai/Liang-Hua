"""
板块趋势与轮动分析

数据来源：同花顺行业API（每日缓存至SQLite）
分析维度：
  1. 板块趋势 —— 各板块5日/10日涨跌幅排名
  2. 板块轮动 —— 强势板块转换（哪些在走强/走弱）
  3. 板块内个股 —— 关键行业的前3涨跌股
"""

import sqlite3
import pandas as pd
import numpy as np
from config import settings
from datetime import datetime


def init_sector_table():
    """创建板块历史数据表"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sector_history (
            date TEXT,
            name TEXT,
            pct_change REAL,
            up_count INTEGER,
            down_count INTEGER,
            net_flow REAL,
            PRIMARY KEY (date, name)
        )
    """)
    conn.commit()
    conn.close()


def save_today_sectors():
    """从同花顺API拉取今日板块数据并缓存"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()
        if df is None or df.empty:
            return False

        today = datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(settings.DB_PATH)

        for _, row in df.iterrows():
            pct = float(str(row['涨跌幅']).replace('%', ''))
            flow = float(row['净流入']) if row['净流入'] else 0
            conn.execute("""
                INSERT OR REPLACE INTO sector_history
                (date, name, pct_change, up_count, down_count, net_flow)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (today, row['板块'], pct,
                  int(row['上涨家数']), int(row['下跌家数']), flow))

        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def get_sector_trends() -> dict:
    """
    板块趋势分析

    返回:
        momentum: 5日动量最强/最弱的板块
        rotation: 板块轮动信号（哪些板块在加速/减速）
        leaders: 持续强势的板块
    """
    conn = sqlite3.connect(settings.DB_PATH)

    # 获取最近5个交易日
    dates = pd.read_sql_query("""
        SELECT DISTINCT date FROM sector_history
        ORDER BY date DESC LIMIT 5
    """, conn)['date'].tolist()

    if len(dates) < 2:
        conn.close()
        return {'error': '板块历史数据不足，需要至少2天'}

    df = pd.read_sql_query("""
        SELECT date, name, pct_change FROM sector_history
        WHERE date IN ({})
    """.format(','.join(f"'{d}'" for d in dates)), conn)
    conn.close()

    if df.empty:
        return {'error': '无板块数据'}

    # 计算各板块的1日/5日涨跌
    latest = dates[0]
    df_latest = df[df['date'] == latest]
    df_5d_ago = df[df['date'] == dates[-1]] if len(dates) >= 5 else df[df['date'] == dates[-1]]

    # 5日累积涨跌（近似：今日% - 5日前%）
    merged = df_latest[['name', 'pct_change']].merge(
        df_5d_ago[['name', 'pct_change']],
        on='name', how='inner', suffixes=('_today', '_5d')
    )
    merged['momentum_5d'] = (merged['pct_change_today'] - merged['pct_change_5d']).round(2)

    # 动量最强/最弱
    top_momentum = merged.nlargest(5, 'momentum_5d')[
        ['name', 'momentum_5d', 'pct_change_today']
    ].to_dict('records')
    bottom_momentum = merged.nsmallest(5, 'momentum_5d')[
        ['name', 'momentum_5d', 'pct_change_today']
    ].to_dict('records')

    # 持续强势（5日内保持上涨的板块）
    # 检查每个板块在5天中上涨的天数
    df['up'] = df['pct_change'] > 0
    consistency = df.groupby('name')['up'].sum().reset_index()
    consistency.columns = ['name', 'up_days']
    consistent = consistency[consistency['up_days'] >= 3].nlargest(5, 'up_days')
    consistent_leaders = consistent.to_dict('records')

    # 轮动信号：今日涨但5日动量弱的 = 新热点
    #          今日跌但5日动量强的 = 在退潮
    rotation_up = merged[
        (merged['pct_change_today'] > 1) & (merged['momentum_5d'] < 0)
    ].nlargest(3, 'pct_change_today')
    rotation_down = merged[
        (merged['pct_change_today'] < -0.5) & (merged['momentum_5d'] > 0)
    ].nsmallest(3, 'pct_change_today')

    return {
        'top_momentum': [{'name': r['name'], 'm5': r['momentum_5d'], 'today': r['pct_change_today']} for r in top_momentum],
        'bottom_momentum': [{'name': r['name'], 'm5': r['momentum_5d'], 'today': r['pct_change_today']} for r in bottom_momentum],
        'consistent': [{'name': r['name'], 'days': r['up_days']} for r in consistent_leaders],
        'rotation_new': [{'name': r['name'], 'today': r['pct_change_today']} for _, r in rotation_up.iterrows()],
        'rotation_fading': [{'name': r['name'], 'today': r['pct_change_today']} for _, r in rotation_down.iterrows()],
        'data_days': len(dates),
    }


def get_sector_stocks() -> dict:
    """
    关键行业的个股表现（从本地数据库，用名称关键词匹配）
    """
    conn = sqlite3.connect(settings.DB_PATH)
    max_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]

    df = pd.read_sql_query("""
        SELECT d.code, d.pct_change, d.close, d.amount, s.name
        FROM daily_kline d
        JOIN stock_info s ON d.code = s.code
        WHERE d.date = ? AND d.pct_change IS NOT NULL
    """, conn, params=(max_date,))
    conn.close()

    # 定义关键行业及匹配关键词
    sectors = {
        '半导体': ['半导体', '芯片', '微电子', '中芯', '海光', '寒武', '澜起',
                   '韦尔', '瑞芯', '圣邦', '兆易', '华大'],
        '光伏': ['光伏', '太阳能', '晶科', '晶澳', '天合', '隆基'],
        '锂电池': ['锂', '电池'],
        '白酒': ['酒', '茅台', '五粮'],
        '医药': ['医药', '恒瑞', '迈瑞', '爱尔', '片仔', '同仁堂'],
        '电力': ['电力', '华能', '华电', '三峡', '长江', '核电'],
    }

    result = {}
    for sector_name, keywords in sectors.items():
        mask = df['name'].apply(lambda n: any(kw in n for kw in keywords))
        sub = df[mask]
        if len(sub) >= 2:
            top2 = sub.nlargest(2, 'pct_change')
            bot2 = sub.nsmallest(2, 'pct_change')
            # 去重：如果 best 和 worst 有重叠，worst 取不同的
            top_codes = set(top2['code'])
            worst_unique = bot2[~bot2['code'].isin(top_codes)]
            result[sector_name] = {
                'count': len(sub),
                'avg_pct': round(sub['pct_change'].mean(), 2),
                'best': [{'name': r['name'], 'code': r['code'], 'pct': round(r['pct_change'], 2)} for _, r in top2.iterrows()],
                'worst': [{'name': r['name'], 'code': r['code'], 'pct': round(r['pct_change'], 2)} for _, r in worst_unique.iterrows()],
            }

    return result
