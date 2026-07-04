"""
双均线策略单元测试 —— 金叉/死叉/无信号/边界条件
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from strategies.ma_cross import MaCrossStrategy


def _make_df(prices: list, start_date: str = "2026-01-01") -> pd.DataFrame:
    """构造测试 DataFrame，从价格序列生成 OHLCV"""
    dates = pd.date_range(start_date, periods=len(prices), freq="B")
    df = pd.DataFrame({
        "date": dates,
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [1_000_000] * len(prices),
        "amount": [p * 1_000_000 for p in prices],
        "pct_change": [0.0] * len(prices),
        "turnover": [1.0] * len(prices),
    })
    df["is_suspended"] = False
    return df


class TestMaCrossStrategy:
    """双均线策略核心逻辑测试"""

    def test_golden_cross_buy_signal(self):
        """金叉应产生 BUY 信号"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)

        # 平坦价格后涨：MA5 从 =MA10 变成 >MA10 → 金叉
        prices = [10.0] * 30 + [10.5]  # 最后一天涨 5% 触发清晰金叉
        df = _make_df(prices)
        df = strat.calculate(df)
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is not None, "金叉应产生信号"
        assert signal["action"] == "BUY", f"应为 BUY，实际 {signal['action']}"
        assert signal["strength"] > 0, "信号强度应 > 0"

    def test_death_cross_sell_signal(self):
        """死叉应产生 SELL 信号"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)

        # 平坦价格后微跌：MA5 从 =MA10 变成 <MA10 → 死叉
        prices = [10.0] * 30 + [9.99]  # 最后一天微跌触发死叉
        df = _make_df(prices)
        df = strat.calculate(df)
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is not None, "死叉应产生信号"
        assert signal["action"] == "SELL", f"应为 SELL，实际 {signal['action']}"

    def test_no_signal_when_no_cross(self):
        """无交叉时不应产生信号"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)

        # 平稳价格：MA5 和 MA10 基本重合，不会交叉
        prices = [10.0] * 45
        df = _make_df(prices)
        df = strat.calculate(df)
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is None, "无交叉不应产生信号"

    def test_insufficient_data_returns_none(self):
        """数据不足时应返回 None"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)
        df = _make_df([10.0] * 8)  # 少于 slow_period=10
        df = strat.calculate(df)
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is None, "数据不足应返回 None"

    def test_suspended_stock_skipped(self):
        """停牌股应跳过"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)
        prices = (
            [10.0] * 5 + [9.5] * 6 + [10.5] * 12  # 会产生金叉
        )
        df = _make_df(prices)
        df = strat.calculate(df)
        df.loc[df.index[-1], "is_suspended"] = True  # 标记停牌
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is None, "停牌股应跳过"

    def test_signal_contains_required_fields(self):
        """信号应包含所有必要字段"""
        strat = MaCrossStrategy(fast_period=5, slow_period=10)
        prices = [10.0] * 30 + [10.5]  # 产生金叉
        df = _make_df(prices)
        df = strat.calculate(df)
        signal = strat.get_signal("000001", "测试股", df)

        assert signal is not None, "数据应产生信号"
        required = ["stock_code", "stock_name", "action", "strength", "reason", "price"]
        for field in required:
            assert field in signal, f"信号缺少字段: {field}"
