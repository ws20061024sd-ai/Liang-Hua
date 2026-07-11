"""
策略运行器 —— 遍历所有启用的策略，对每只股票运行，收集信号
v2: 集成止损检查，当前持仓触发移动止损时自动生成 STOP_LOSS 信号
"""
import pandas as pd
from typing import Type
from strategies.base_strategy import BaseStrategy
from strategies.ma_cross import MaCrossStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy
from data_fetcher.cleaner import get_batch_stock_data, get_all_stocks
from config import settings


# 策略注册表（类名 → 类对象）
STRATEGY_REGISTRY = {
    "MaCrossStrategy": MaCrossStrategy,
    "MomentumBreakoutStrategy": MomentumBreakoutStrategy,
    "MeanReversionStrategy": MeanReversionStrategy,
}


def get_enabled_strategies() -> list[BaseStrategy]:
    """根据配置创建启用的策略实例"""
    strategies = []
    for cls_name in settings.ENABLED_STRATEGIES:
        if cls_name in STRATEGY_REGISTRY:
            cls = STRATEGY_REGISTRY[cls_name]
            instance = cls()
            strategies.append(instance)
            print(f"🔧 已加载策略: {instance.name} v{instance.version}")
        else:
            print(f"⚠️ 未找到策略: {cls_name}")
    return strategies


def run_strategies(verbose: bool = False) -> list[dict]:
    """
    对所有股票运行所有启用策略，收集交易信号

    返回: 信号列表 [{'stock_code': ..., 'action': 'BUY'/'SELL', ...}, ...]
    """
    strategies = get_enabled_strategies()
    if not strategies:
        print("❌ 没有启用的策略，请在 config/settings.py 中配置")
        return []

    stocks = get_all_stocks()
    if stocks.empty:
        print("❌ 股票池为空，请先运行数据下载: python -m data_fetcher.downloader")
        return []

    all_signals = []
    total_stocks = len(stocks)
    buy_count = 0
    sell_count = 0

    print(f"\n🚀 开始运行策略（{len(strategies)}个策略 × {total_stocks}只股票）...\n")

    # 批量加载所有股票数据（复用连接，避免300次打开/关闭）
    codes = stocks['code'].tolist()
    batch_data = get_batch_stock_data(codes, days=settings.STRATEGY_DATA_DAYS)

    for i, (_, stock) in enumerate(stocks.iterrows()):
        code = stock['code']
        name = stock['name']

        df = batch_data.get(code)
        if df is None or df.empty:
            continue

        for st in strategies:
            try:
                signal = st.run(code, name, df)
                if signal:
                    all_signals.append({
                        **signal,
                        'strategy': st.name,
                    })
                    if signal['action'] == 'BUY':
                        buy_count += 1
                    else:
                        sell_count += 1
            except Exception as e:
                if verbose:
                    print(f"   ⚠️ {code} {name} [{st.name}] 计算异常: {e}")

        if (i + 1) % 50 == 0:
            print(f"   进度: [{i+1}/{total_stocks}]  买入{buy_count}  卖出{sell_count}")

    print(f"\n   完成: [{total_stocks}/{total_stocks}]  买入{buy_count}  卖出{sell_count}")

    # 按信号强度排序（买入排前面，卖出只保留最强的）
    buy_signals = [s for s in all_signals if s['action'] == 'BUY']
    sell_signals = [s for s in all_signals if s['action'] == 'SELL']
    buy_signals.sort(key=lambda s: s['strength'], reverse=True)
    sell_signals.sort(key=lambda s: s['strength'], reverse=True)

    # 限制卖出信号数量（多策略时卖出信号会很多）
    sell_signals = sell_signals[:settings.SELL_SIGNAL_LIMIT]

    # ---- 止损检查：当前持仓触发移动止损 → 强制卖出 ----
    stop_signals = _generate_stop_signals(batch_data)
    if stop_signals:
        print(f"\n   ⚠️  移动止损触发: {len(stop_signals)}只")
        for s in stop_signals:
            n = s.get('stock_name', s.get('name', s['stock_code']))
            print(f"      {s['stock_code']} {n}: {s['reason']}")

    # 止损信号优先级最高，放在卖出列表最前面
    return buy_signals + stop_signals + sell_signals


def _generate_stop_signals(batch_data: dict) -> list[dict]:
    """
    检查当前持仓是否触发移动止损

    batch_data: {code: DataFrame} 批量加载的日线数据
    返回: STOP_LOSS 信号列表
    """
    try:
        from engine.position_tracker import get_positions, check_stop_loss
    except ImportError:
        return []

    positions = get_positions()
    if not positions:
        return []

    # 从已加载的数据中提取最新价格（避免重复查库）
    price_map = {}
    for code in [p['code'] for p in positions]:
        if code in batch_data:
            df = batch_data[code]
            if not df.empty:
                price_map[code] = float(df.iloc[-1]['close'])

    stops = check_stop_loss(positions, price_map=price_map)
    if not stops:
        return []

    # 转换为信号格式
    signals = []
    for s in stops:
        signals.append({
            'stock_code': s['code'],
            'stock_name': s['name'],
            'action': 'SELL',
            'strength': 1.0,  # 止损信号最高强度
            'reason': s['reason'],
            'price': s['current'],
            'strategy': '移动止损',
        })
    return signals
