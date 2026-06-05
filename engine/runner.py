"""
策略运行器 —— 遍历所有启用的策略，对每只股票运行，收集信号
"""
import pandas as pd
from typing import Type
from strategies.base_strategy import BaseStrategy
from strategies.ma_cross import MaCrossStrategy
from data_fetcher.cleaner import get_stock_data, get_all_stocks
from config import settings


# 策略注册表（类名 → 类对象）
STRATEGY_REGISTRY = {
    "MaCrossStrategy": MaCrossStrategy,
    # 后续添加新策略只需在这里注册：
    # "MomentumBreakoutStrategy": MomentumBreakoutStrategy,
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

    for i, (_, stock) in enumerate(stocks.iterrows()):
        code = stock['code']
        name = stock['name']

        # 获取该股票的日线数据（需要足够长以计算均线）
        df = get_stock_data(code, days=200)

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

        # 进度显示
        if (i + 1) % 50 == 0:
            print(f"   进度: [{i+1}/{total_stocks}]  买入{buy_count}  卖出{sell_count}")

    print(f"\n   完成: [{total_stocks}/{total_stocks}]  买入{buy_count}  卖出{sell_count}")

    # 按信号强度排序（买入信号排前面，同方向按强度降序）
    buy_signals = [s for s in all_signals if s['action'] == 'BUY']
    sell_signals = [s for s in all_signals if s['action'] == 'SELL']
    buy_signals.sort(key=lambda s: s['strength'], reverse=True)
    sell_signals.sort(key=lambda s: s['strength'], reverse=True)

    return buy_signals + sell_signals
