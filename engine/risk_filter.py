"""
风控过滤器 —— 实现防线二：基础规则过滤

过滤规则：
  1. 排除 ST/*ST 股票
  2. 排除涨停股（买不到）
  3. 排除跌停股（流动性冻结，次日大概率继续跌）
  4. 排除停牌股（不可交易）
  5. 排除股价超出小资金承受范围的股票
  6. 排除流动性太差的股票
"""

import pandas as pd
from config import settings


def filter_signals(
    signals: list[dict],
    stock_snapshot: pd.DataFrame
) -> tuple[list[dict], list[dict]]:
    """
    对策略信号执行基础规则过滤

    参数:
        signals: 策略生成的原始信号列表
        stock_snapshot: 当日所有股票的快照数据（含价格、涨跌幅、ST标记等）

    返回:
        (passed, rejected): 通过过滤的信号列表, 被拒绝的信号列表
    """
    passed = []
    rejected = []

    # 建立快照索引（code → row）
    snapshot_map = {}
    for _, row in stock_snapshot.iterrows():
        snapshot_map[row['code']] = row

    for sig in signals:
        code = sig['stock_code']
        snap = snapshot_map.get(code)

        if snap is None:
            sig['reject_reason'] = '不在股票池中（可能已退市或调出指数）'
            rejected.append(sig)
            continue

        # 规则1: ST 过滤
        if snap.get('is_st', 0):
            sig['reject_reason'] = 'ST股票'
            rejected.append(sig)
            continue

        # 规则2: 涨停过滤
        pct = snap.get('pct_change', 0) or 0
        if pct >= 9.8:
            sig['reject_reason'] = '涨停，无法买入'
            rejected.append(sig)
            continue

        # 规则3: 跌停过滤
        if pct <= -9.8:
            sig['reject_reason'] = '跌停，流动性风险'
            rejected.append(sig)
            continue

        # 规则4: 停牌过滤
        if snap.get('volume', 0) is None or snap.get('volume', 0) == 0:
            sig['reject_reason'] = '疑似停牌（成交量为0）'
            rejected.append(sig)
            continue

        # 规则5: 股价过滤（小资金买不起1手）
        max_price = settings.MAX_STOCK_PRICE
        if max_price > 0:
            price = snap.get('close', 0) or 0
            if price > max_price:
                sig['reject_reason'] = f'股价{price:.0f}元，超过上限{max_price}元（1手需{price*100:.0f}元）'
                rejected.append(sig)
                continue

        # 规则6: 流动性过滤
        amount = snap.get('amount', 0) or 0
        if amount < settings.MIN_DAILY_AMOUNT:
            sig['reject_reason'] = f'日成交额{amount/1e4:.0f}万，流动性偏低'
            rejected.append(sig)
            continue

        # 全部通过
        passed.append(sig)

    return passed, rejected


def calculate_position(sig: dict, capital: float) -> dict:
    """
    根据资金规模计算建议仓位（简化版，后续会按分档完善）

    返回:
        {
            'shares': 建议买入股数（整百数）,
            'amount': 建议买入金额,
            'pct': 占总资金比例,
            'stop_loss': 止损价,
            'warning': 警告信息（或None）
        }
    """
    # 获取当前股价
    price = sig['price']

    # 根据资金档位确定单票上限
    if capital <= 20000:
        max_single_pct = 0.50
    elif capital <= 50000:
        max_single_pct = 0.30
    elif capital <= 100000:
        max_single_pct = 0.20
    else:
        max_single_pct = 0.15

    lots = int(capital * max_single_pct / (price * 100))

    # 动态导入防止循环引用
    warning = None

    if lots == 0:
        return {
            'actionable': False,
            'reason': f'资金不足：1手需{price*100:.0f}元',
            'shares': 0,
            'amount': 0,
            'pct': 0,
            'stop_loss': None,
            'warning': warning,
        }

    shares = lots * 100
    amount = shares * price
    pct = amount / capital

    # 小资金警告
    if pct > 0.5 and capital <= 20000:
        warning = f'单票占比{pct:.0%}偏高（小资金正常）'

    # 止损价（小资金-3%，标准-5%）
    stop_loss_pct = 0.03 if capital <= 20000 else 0.05
    stop_loss = round(price * (1 - stop_loss_pct), 2)

    return {
        'actionable': True,
        'shares': shares,
        'amount': amount,
        'pct': pct,
        'stop_loss': stop_loss,
        'stop_loss_pct': stop_loss_pct,
        'warning': warning,
    }
