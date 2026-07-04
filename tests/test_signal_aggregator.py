"""
信号汇总模块单元测试 —— 多策略交叉确认 / 冲突检测 / 排序
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.signal_aggregator import aggregate


def _signal(code="000001", name="测试股", action="BUY", strategy="双均线",
            strength=0.8, price=10.0, reason="测试信号", regime_note=""):
    """构造单条策略信号"""
    return {
        "stock_code": code,
        "stock_name": name,
        "action": action,
        "strategy": strategy,
        "strength": strength,
        "price": price,
        "reason": reason,
        "regime_note": regime_note,
    }


class TestSignalAggregator:
    """信号汇总逻辑测试"""

    def test_single_strategy_signal(self):
        """单策略单信号 → 确认数 = 1"""
        signals = [_signal()]
        result = aggregate(signals)

        assert len(result) == 1
        assert result[0]["confirm"] == 1
        assert result[0]["conflict"] is False

    def test_multi_strategy_confirm(self):
        """两策略同向确认 → confirm = 2"""
        signals = [
            _signal(strategy="双均线", strength=0.8),
            _signal(strategy="动量突破", strength=0.6),
        ]
        result = aggregate(signals)

        assert len(result) == 1
        assert result[0]["confirm"] == 2
        assert result[0]["conflict"] is False
        assert len(result[0]["strategies"]) == 2

    def test_buy_sell_conflict_resolves_to_stronger(self):
        """买卖冲突 → 取强度总和更大的方向"""
        signals = [
            _signal(action="BUY", strength=0.9),
            _signal(action="SELL", strength=0.3),
        ]
        result = aggregate(signals)

        assert len(result) == 1
        assert result[0]["action"] == "BUY", "买入强度更大应取买入"
        assert result[0]["conflict"] is True

    def test_sell_wins_when_stronger(self):
        """卖出强度更大时取卖出"""
        signals = [
            _signal(action="BUY", strength=0.3),
            _signal(action="SELL", strength=0.9),
        ]
        result = aggregate(signals)

        assert result[0]["action"] == "SELL", "卖出强度更大应取卖出"
        assert result[0]["conflict"] is True

    def test_different_stocks_separated(self):
        """不同股票的信号分开汇总"""
        signals = [
            _signal(code="000001", name="股票A"),
            _signal(code="000002", name="股票B"),
        ]
        result = aggregate(signals)

        assert len(result) == 2, "不同股票不应合并"
        codes = {r["stock_code"] for r in result}
        assert codes == {"000001", "000002"}

    def test_buy_signals_sorted_before_sell(self):
        """买入信号应排在卖出前面"""
        signals = [
            _signal(code="000001", action="SELL", strength=0.9),
            _signal(code="000002", action="BUY", strength=0.3),
        ]
        result = aggregate(signals)

        assert result[0]["action"] == "BUY", "买入应排在前面"

    def test_empty_signals(self):
        """空信号列表返回空"""
        result = aggregate([])
        assert result == []

    def test_highest_strength_used(self):
        """汇总后 strength 取最强策略的值"""
        signals = [
            _signal(strategy="双均线", strength=0.5),
            _signal(strategy="动量突破", strength=0.9),
        ]
        result = aggregate(signals)

        assert result[0]["strength"] == 0.9, "应取最强信号的 strength"

    def test_total_strategies_count(self):
        """total_strategies 应等于不同策略的数量"""
        signals = [
            _signal(strategy="双均线", strength=0.8),
            _signal(strategy="双均线", strength=0.5),  # 同策略重复（不同价格买入）
            _signal(strategy="动量突破", strength=0.6),
        ]
        result = aggregate(signals)

        assert result[0]["total_strategies"] == 2, "应有 2 个不同策略"
