"""
信号汇总模块 —— 多策略信号去重、交叉确认、冲突检测
"""
from collections import defaultdict


def aggregate(signals: list[dict]) -> list[dict]:
    """
    将多个策略的信号按股票代码汇总

    输入: 策略列表 [{'stock_code': '600039', 'action': 'BUY', 'strategy': '双均线'}, ...]
    输出: 汇总后列表 [{'stock_code': '600039', 'action': 'BUY', 'strategies': [...], 'confirm': 2}, ...]
    """
    # 按 code 分组
    groups = defaultdict(list)
    for sig in signals:
        groups[sig['stock_code']].append(sig)

    result = []
    for code, sigs in groups.items():
        # 统计这个股票上的买卖信号
        buy_sigs = [s for s in sigs if s['action'] == 'BUY']
        sell_sigs = [s for s in sigs if s['action'] == 'SELL']

        # 冲突检测
        if buy_sigs and sell_sigs:
            # 买卖同时存在——取更强的方向
            total_buy = sum(s['strength'] for s in buy_sigs)
            total_sell = sum(s['strength'] for s in sell_sigs)
            if total_buy >= total_sell:
                action = 'BUY'
                confirm = len(buy_sigs)
                strategies = buy_sigs
            else:
                action = 'SELL'
                confirm = len(sell_sigs)
                strategies = sell_sigs
            conflict = True
        elif buy_sigs:
            action = 'BUY'
            strategies = buy_sigs
            confirm = len(buy_sigs)
            conflict = False
        else:
            action = 'SELL'
            strategies = sell_sigs
            confirm = len(sell_sigs)
            conflict = False

        # 取最强的策略信号作为主信号
        best = max(strategies, key=lambda s: s['strength'])

        result.append({
            'stock_code': code,
            'stock_name': sigs[0]['stock_name'],
            'action': action,
            'strength': round(best['strength'], 3),
            'price': best['price'],
            'confirm': confirm,                    # 确认策略数
            'total_strategies': len(set(s['strategy'] for s in sigs)),
            'conflict': conflict,
            'strategies': [{
                'name': s['strategy'],
                'action': s['action'],
                'reason': s['reason'],
                'strength': s['strength'],
                'regime_note': s.get('regime_note', ''),
            } for s in sigs],
        })

    # 排序：买入在前，同方向确认数多者在前
    buy = [r for r in result if r['action'] == 'BUY']
    sell = [r for r in result if r['action'] == 'SELL']
    buy.sort(key=lambda r: (r['confirm'], r['strength']), reverse=True)
    sell.sort(key=lambda r: (r['confirm'], r['strength']), reverse=True)

    return buy + sell
