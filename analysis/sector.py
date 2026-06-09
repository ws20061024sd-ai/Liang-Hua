"""
中观分析 —— 行业板块 + 交易所板块 + 股价分层 + 流动性分层 + 风格诊断
"""
import sqlite3
import pandas as pd
import numpy as np
from config import settings


def analyze() -> dict:
    """返回板块分析结果"""
    try:
        conn = sqlite3.connect(settings.DB_PATH)
        df = pd.read_sql_query("""
            SELECT d.code, d.close, d.pct_change, d.amount, s.name
            FROM daily_kline d
            JOIN stock_info s ON d.code = s.code
            WHERE d.date = (SELECT MAX(date) FROM daily_kline)
        """, conn)
        conn.close()

        if df.empty:
            return {}

        if df['pct_change'].isna().all():
            return {'error': '涨跌幅数据全部缺失'}

        # 行业分析（同花顺API）
        industries = _ths_industry_analysis()
        # 交易所板块
        board = _board_analysis(df)
        # 股价/流动性分层
        price_tier = _price_tier_analysis(df)
        amount_tier = _amount_tier_analysis(df)
        # 风格诊断
        style = _style_diagnosis(board, price_tier, amount_tier)

        return {
            'industries': industries,
            'board': board,
            'price_tier': price_tier,
            'amount_tier': amount_tier,
            'style': style,
        }
    except Exception:
        return {}


def _ths_industry_analysis() -> dict:
    """同花顺行业分析——前5/后5行业"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_summary_ths()
        if df is None or df.empty:
            return None

        # 重点行业（用户关注的）
        focus = ['半导体', '光伏设备', '电池', '白酒', '电力', '银行',
                 '证券', '汽车整车', '医药商业', '煤炭开采加工', '通信设备']
        focus_data = []
        for _, row in df.iterrows():
            if row['板块'] in focus:
                focus_data.append({
                    'name': row['板块'],
                    'pct': float(str(row['涨跌幅']).replace('%', '')),
                    'up': int(row['上涨家数']),
                    'down': int(row['下跌家数']),
                    'flow_yi': round(float(row['净流入']) / 1e8, 1) if row['净流入'] else 0,
                })

        # Top 5 / Bottom 5（传统行业，排除太细分的）
        traditional = ['半导体', '银行', '证券', '白酒', '汽车整车', '电力',
                       '光伏设备', '电池', '医药商业', '通信设备', '军工电子',
                       '煤炭开采加工', '房地产开发', '保险', '钢铁', '工业金属',
                       '化学制药', '消费电子']
        industry_subset = df[df['板块'].isin(traditional)]
        top5 = industry_subset.nlargest(5, '涨跌幅')[['板块', '涨跌幅']].to_dict('records')
        bottom5 = industry_subset.nsmallest(5, '涨跌幅')[['板块', '涨跌幅']].to_dict('records')

        return {
            'top5': [{'name': r['板块'], 'pct': r['涨跌幅']} for r in top5],
            'bottom5': [{'name': r['板块'], 'pct': r['涨跌幅']} for r in bottom5],
            'focus': sorted(focus_data, key=lambda x: x['pct'], reverse=True),
        }
    except Exception:
        return None


def _board_analysis(df: pd.DataFrame) -> list[dict]:
    """按代码前缀分四大板块"""
    def classify(code):
        if str(code).startswith('688'):
            return '科创板'
        elif str(code).startswith(('300', '301')):
            return '创业板'
        elif str(code).startswith(('600', '601', '603', '605')):
            return '上海主板'
        elif str(code).startswith(('000', '001', '002', '003')):
            return '深圳主板'
        return '其他'

    df = df.copy()
    df['board'] = df['code'].apply(classify)
    results = []
    for board in ['上海主板', '深圳主板', '创业板', '科创板']:
        sub = df[df['board'] == board]
        if len(sub) == 0:
            continue
        results.append({
            'name': board,
            'count': len(sub),
            'up': int((sub['pct_change'] > 0).sum()),
            'down': int((sub['pct_change'] < 0).sum()),
            'avg_pct': round(float(sub['pct_change'].mean()), 2),
            'med_pct': round(float(sub['pct_change'].median()), 2),
        })
    return results


def _price_tier_analysis(df: pd.DataFrame) -> list[dict]:
    """按股价分层"""
    bins = [0, 10, 30, 50, 100, 99999]
    labels = ['<10元', '10-30元', '30-50元', '50-100元', '>100元']
    df = df.copy()
    df['tier'] = pd.cut(df['close'], bins=bins, labels=labels)

    results = []
    for label in labels:
        sub = df[df['tier'] == label]
        if len(sub) == 0:
            continue
        results.append({
            'name': label,
            'count': len(sub),
            'avg_pct': round(float(sub['pct_change'].mean()), 2),
        })
    return results


def _amount_tier_analysis(df: pd.DataFrame) -> list[dict]:
    """按成交额分层"""
    df = df.copy()
    df['rank'] = pd.qcut(df['amount'].rank(method='first'), q=4,
                         labels=['Q1低', 'Q2', 'Q3', 'Q4高'])
    results = []
    for q in ['Q1低', 'Q2', 'Q3', 'Q4高']:
        sub = df[df['rank'] == q]
        if len(sub) == 0:
            continue
        results.append({
            'name': q,
            'count': len(sub),
            'avg_pct': round(float(sub['pct_change'].mean()), 2),
        })
    return results


def _style_diagnosis(board, price_tier, amount_tier) -> str:
    """根据板块/股价/流动性数据，判断当前市场风格"""
    points = []

    # 价值 vs 成长
    if board:
        sh = next((b for b in board if '上海主板' in b['name']), None)
        kcb = next((b for b in board if '科创板' in b['name']), None)
        if sh and kcb and sh['avg_pct'] - kcb['avg_pct'] > 2:
            points.append('价值防御（主板抗跌，科创重挫）')
        elif sh and kcb and kcb['avg_pct'] - sh['avg_pct'] > 2:
            points.append('成长进攻（科创领涨）')

    # 大盘 vs 小盘
    if price_tier:
        low = price_tier[0]['avg_pct']  # <10元
        high = price_tier[-1]['avg_pct']  # >100元
        if low - high > 2:
            points.append('偏好低价股')
        elif high - low > 2:
            points.append('偏好高价股')

    # 活跃 vs 冷门
    if amount_tier:
        high_amt = amount_tier[-1]['avg_pct']  # Q4高
        low_amt = amount_tier[0]['avg_pct']  # Q1低
        if high_amt - low_amt > 2:
            points.append('大资金活跃')
        elif low_amt - high_amt > 2:
            points.append('资金偏防御（小票受青睐）')

    return '；'.join(points) if points else '无明显风格偏向'
