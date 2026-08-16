"""
风控过滤器单元测试 —— ST/涨停/跌停/高价/流动性过滤 + 仓位计算
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from engine.risk_filter import filter_signals, calculate_position
from config import settings


def _make_snapshot(overrides: list[dict]) -> pd.DataFrame:
    """构造当日股票快照 DataFrame"""
    rows = []
    for o in overrides:
        row = {
            "code": o.get("code", "000001"),
            "name": o.get("name", "测试股"),
            "close": o.get("close", 10.0),
            "pct_change": o.get("pct_change", 0.0),
            "volume": o.get("volume", 10_000_000),
            "amount": o.get("amount", 100_000_000),
            "is_st": o.get("is_st", 0),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _make_signal(code="000001", action="BUY", price=10.0, name="测试股"):
    """构造单条信号"""
    return {
        "stock_code": code,
        "stock_name": name,
        "action": action,
        "strength": 0.8,
        "reason": "测试信号",
        "price": price,
    }


class TestFilterSignals:
    """基础风控过滤测试"""

    def test_st_filtered(self):
        """ST 股票应被过滤"""
        sig = _make_signal(code="000001")
        snap = _make_snapshot([{"code": "000001", "is_st": 1}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0, "ST 不应通过"
        assert len(rejected) == 1, "ST 应被拒绝"
        assert "ST" in rejected[0]["reject_reason"]

    def test_limit_up_filtered(self):
        """涨停股买入应被过滤"""
        sig = _make_signal(action="BUY")
        snap = _make_snapshot([{"pct_change": 10.02}])  # 涨停
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0, "涨停不应通过"
        assert "涨停" in rejected[0]["reject_reason"]

    def test_limit_down_filtered(self):
        """跌停股应被过滤"""
        sig = _make_signal()
        snap = _make_snapshot([{"pct_change": -10.01}])  # 跌停
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0, "跌停不应通过"
        assert "跌停" in rejected[0]["reject_reason"]

    def test_normal_stock_passes(self):
        """正常股票应通过过滤"""
        sig = _make_signal()
        snap = _make_snapshot([{}])  # 全部用默认值（正常股票）
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 1, "正常股票应通过"
        assert len(rejected) == 0

    def test_price_over_max_filtered(self):
        """超过价格上限应被过滤"""
        sig = _make_signal(price=51.0)
        snap = _make_snapshot([{"close": 51.0}])
        passed, rejected = filter_signals([sig], snap)

        # MAX_STOCK_PRICE = 50
        assert len(passed) == 0, "高价股不应通过"
        assert "超过上限" in rejected[0]["reject_reason"]

    def test_not_in_snapshot_filtered(self):
        """不在快照中的股票应被过滤"""
        sig = _make_signal(code="999999")
        snap = _make_snapshot([{"code": "000001"}])  # 快照里没有 999999
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0
        assert "不在股票池" in rejected[0]["reject_reason"]

    def test_zero_volume_filtered(self):
        """成交量为 0 应被过滤（疑似停牌）"""
        sig = _make_signal()
        snap = _make_snapshot([{"volume": 0}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0
        assert "停牌" in rejected[0]["reject_reason"]


class TestSellSignalExemption:
    """卖出信号只拦物理上无法卖出的情况（跌停/停牌），不套用买入限制"""

    def test_sell_on_limit_up_passes(self):
        """涨停日卖出信号应通过（涨停是卖出好时机，不是买入限制）"""
        sig = _make_signal(action="SELL")
        snap = _make_snapshot([{"pct_change": 10.02}])  # 涨停
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 1, "涨停日 SELL 不应被拦"
        assert len(rejected) == 0

    def test_sell_high_price_passes(self):
        """高价股卖出信号应通过（持仓涨到50元以上更需要卖出提醒）"""
        sig = _make_signal(action="SELL", price=51.0)
        snap = _make_snapshot([{"close": 51.0}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 1, "高价股 SELL 不应被拦"
        assert len(rejected) == 0

    def test_sell_st_passes(self):
        """ST 股卖出信号应通过（持仓变 ST 更要提醒离场）"""
        sig = _make_signal(action="SELL")
        snap = _make_snapshot([{"is_st": 1}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 1, "ST 股 SELL 不应被拦"
        assert len(rejected) == 0

    def test_sell_low_liquidity_passes(self):
        """低流动性卖出信号应通过（流动性差更需要提前离场）"""
        sig = _make_signal(action="SELL")
        snap = _make_snapshot([{"amount": 5_000_000}])  # 低于 2000 万
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 1, "低流动性 SELL 不应被拦"
        assert len(rejected) == 0

    def test_sell_on_limit_down_rejected(self):
        """跌停日卖出信号应被拦（跌停封死无法卖出）"""
        sig = _make_signal(action="SELL")
        snap = _make_snapshot([{"pct_change": -10.01}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0, "跌停 SELL 应被拦"
        assert "跌停" in rejected[0]["reject_reason"]

    def test_sell_zero_volume_rejected(self):
        """停牌日卖出信号应被拦（无法成交）"""
        sig = _make_signal(action="SELL")
        snap = _make_snapshot([{"volume": 0}])
        passed, rejected = filter_signals([sig], snap)

        assert len(passed) == 0
        assert "停牌" in rejected[0]["reject_reason"]


class TestCalculatePosition:
    """仓位计算测试"""

    def test_small_capital_one_lot(self):
        """1 万资金 10 元股——应能买至少 100 股"""
        sig = _make_signal(price=10.0)
        result = calculate_position(sig, capital=10_000)

        assert result["actionable"], "1 万应买得起 10 元股"
        assert result["shares"] >= 100, "至少应买 100 股"
        assert result["stop_loss"] is not None, "应设止损价"

    def test_insufficient_capital(self):
        """资金不足以买 1 手"""
        sig = _make_signal(price=500.0)
        result = calculate_position(sig, capital=3_000)

        assert not result["actionable"], "资金不足应返回 actionable=False"
        assert "资金不足" in result["reason"]

    def test_position_within_limit(self):
        """单票占比不超过档位上限"""
        sig = _make_signal(price=10.0)
        result = calculate_position(sig, capital=10_000)
        # 1-2 万档位：单票 ≤ 50%
        assert result["pct"] <= 0.52, f"单票占比 {result['pct']:.1%} 超过 50% 上限"  # 允许少量取整溢出

    def test_stop_loss_calculation(self):
        """止损价计算正确"""
        sig = _make_signal(price=20.0)
        result = calculate_position(sig, capital=10_000)
        # 1-2 万档位：止损 -3%
        expected_stop = round(20.0 * 0.97, 2)
        assert result["stop_loss"] == expected_stop, \
            f"止损价应为 {expected_stop}，实际 {result['stop_loss']}"
