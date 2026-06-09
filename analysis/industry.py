"""
行业分类模块 —— 基于股票名称关键词 + 本地数据，无需网络API

设计原因：东财/同花顺行业API在服务器上不稳定
改用规则引擎，覆盖沪深300所有主要行业
"""

import sqlite3
import pandas as pd
from config import settings


# 行业关键词映射表（按优先级排列）
INDUSTRY_RULES = {
    '银行':       ['银行'],
    '保险':       ['保险'],
    '证券':       ['证券'],
    '白酒':       ['酒', '茅台', '五粮'],
    '光伏':       ['光伏', '太阳能', '晶科', '晶澳', '天合', '隆基'],
    '锂电池':     ['锂', '电池', '亿纬', '国轩'],
    '新能源汽车':  ['汽车', '比亚迪', '长城', '上汽', '长安', '广汽', '赛力斯'],
    '半导体':     ['半导体', '芯片', '微电子', '华润微', '中芯', '海光', '寒武',
                   '澜起', '韦尔', '瑞芯', '圣邦', '兆易', '华大', '芯', '中微'],
    '消费电子':   ['电子', '立讯', '歌尔', '蓝思', '领益', '东山'],
    '医药':       ['医药', '药', '恒瑞', '迈瑞', '爱尔', '康龙', '泰格',
                   '片仔', '华润三九', '云南白药', '上海莱士', '同仁堂'],
    '电力':       ['电力', '电建', '核电', '华能', '华电', '三峡', '国投',
                   '长江电力', '浙能', '国电'],
    '煤炭':       ['煤', '兖矿', '神华', '中煤'],
    '石油':       ['石油', '石化', '中海油', '海油'],
    '钢铁':       ['钢', '宝钢', '包钢'],
    '有色金属':   ['铜', '铝', '金', '稀土', '钼', '紫金', '中金黄金',
                   '山东黄金', '洛阳钼', '中国铝', '云铝', '南山铝'],
    '建筑建材':   ['建筑', '建材', '水泥', '中铁', '铁建', '交建', '中建',
                   '海螺', '北新', '中国化学', '中国中冶', '东方雨虹'],
    '化工':       ['化工', '化学', '万华', '恒力', '荣盛', '合盛', '华鲁'],
    '地产':       ['地产', '万科', '保利'],
    '通信':       ['通信', '联通', '电信', '移动', '卫通', '中兴'],
    '软件服务':   ['软件', '科大讯飞', '用友', '恒生电子', '三六零', '宝信'],
    '家电':       ['电器', '家电', '海尔', '美的', '格力', '公牛',
                   '石头科技', '海信', '苏泊尔'],
    '军工':       ['航', '中航', '船舶', '动力', '沈飞', '西飞', '兵装'],
    '交通运输':   ['交通', '运输', '港口', '高速', '航空', '机场', '铁路',
                   '中远', '上港', '宁波港', '青岛港', '京沪'],
    '食品饮料':   ['食品', '饮料', '乳', '伊利', '海天', '双汇', '东鹏',
                   '金龙鱼', '安井', '绝味'],
}

def classify_stock(name: str, code: str = '') -> str:
    """根据股票名称返回行业分类"""
    for industry, keywords in INDUSTRY_RULES.items():
        for kw in keywords:
            if kw in name:
                return industry
    return '其他'


def get_industry_analysis() -> dict:
    """
    行业分析 —— 返回今日各行业平均涨跌幅

    返回:
        {
            'industries': [{'name': '半导体', 'count': 15, 'avg_pct': 3.5}, ...],
            'top3': [...], 'bottom3': [...],
        }
    """
    conn = sqlite3.connect(settings.DB_PATH)

    # 获取最新数据
    max_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    df = pd.read_sql_query("""
        SELECT d.code, d.close, d.pct_change, d.amount, s.name
        FROM daily_kline d
        JOIN stock_info s ON d.code = s.code
        WHERE d.date = ?
    """, conn, params=(max_date,))

    # 过滤掉 pct_change 为 NULL 的
    df = df[df['pct_change'].notna()]
    if df.empty:
        return {'industries': [], 'top3': [], 'bottom3': [], 'data_error': True}

    # 分类
    df['industry'] = df.apply(lambda r: classify_stock(r['name'], r['code']), axis=1)

    # 按行业统计
    stats = df.groupby('industry').agg(
        count=('code', 'count'),
        avg_pct=('pct_change', 'mean'),
        total_amount=('amount', 'sum'),
    ).reset_index()
    stats['avg_pct'] = stats['avg_pct'].round(2)
    stats['total_amount_yi'] = (stats['total_amount'] / 1e8).round(0)
    stats = stats.sort_values('avg_pct', ascending=False)

    industries = stats.to_dict('records')

    # 取top/bottom（至少3只股票的行业）
    qualified = stats[stats['count'] >= 3]
    top3 = qualified.head(3)[['name', 'avg_pct']].to_dict('records')
    bottom3 = qualified.tail(3)[['name', 'avg_pct']].to_dict('records')

    conn.close()
    return {
        'industries': industries,
        'top3': top3,
        'bottom3': bottom3,
        'data_date': str(max_date),
    }
