"""
大盘择时测试 —— 覆盖四档状态分类，重点验证回调场景不误判 weak
"""
import pandas as pd
import numpy as np
import pytest
from engine import market_timing


@pytest.fixture(autouse=True)
def _stub_stock_data(monkeypatch):
    """打桩 get_stock_data + _fetch_index_data，避免真实数据库/网络依赖"""
    def _fake(code, days=90):
        return market_timing._last_df
    monkeypatch.setattr(market_timing, 'get_stock_data', _fake)
    monkeypatch.setattr(market_timing, '_fetch_index_data', lambda days=90: None)


def _build(pieces):
    """拼接多段行情构造测试数据"""
    n = sum(len(p) for p in pieces)
    close = np.concatenate(pieces)
    return pd.DataFrame({'date': pd.date_range('2026-01-01', periods=n), 'close': close})


def test_strong_bull_market():
    """🟢 强势：指数>MA20 且 MA20>MA60"""
    np.random.seed(42)
    market_timing._last_df = _build([
        np.random.uniform(3700, 3800, 60),
        np.linspace(3800, 4000, 30),
    ])
    r = market_timing.get_market_regime()
    assert r['regime'] == 'strong'


def test_pullback_not_weak():
    """回调场景：跌破MA20但MA20>MA60 → shaky（历史 bug：误判 weak 禁买）"""
    np.random.seed(7)
    market_timing._last_df = _build([
        np.random.uniform(3700, 3800, 60),
        np.linspace(3800, 4000, 30),
        np.linspace(3990, 3850, 5),
    ])
    r = market_timing.get_market_regime()
    assert r['regime'] == 'shaky', f"回调被误判为 {r['regime']}（历史 bug）"
    assert r['position_ratio'] > 0, "回调场景不应禁买"


def test_bearish_weak():
    """🟠 弱势：指数<MA20 且 MA20<MA60（空头排列）"""
    np.random.seed(11)
    market_timing._last_df = _build([
        np.random.uniform(4000, 4100, 60),
        np.linspace(4000, 3800, 30),
        np.linspace(3820, 3750, 5),
    ])
    r = market_timing.get_market_regime()
    # 若连续下跌<10天 → weak；若≥10天 → crash
    assert r['regime'] in ('weak', 'crash')


def test_insufficient_data_defaults_conservative():
    """数据不足 → 默认保守（shaky, 可买但降权）"""
    np.random.seed(3)
    market_timing._last_df = _build([np.random.uniform(3700, 3800, 30)])
    r = market_timing.get_market_regime()
    assert r['regime'] == 'shaky'
    assert r['can_buy'] is True


def test_filter_by_regime_does_not_block():
    """v3 降权不拦截：weak 市买入信号保留但强度降权"""
    signals = [{'action': 'BUY', 'strategy': '双均线趋势跟踪', 'strength': 0.8}]
    regime = {'regime': 'weak', 'label': '🟠 弱势'}
    passed, blocked = market_timing.filter_by_regime(signals, regime)
    assert len(passed) == 1, "v3 不应拦截信号"
    assert len(blocked) == 0
    assert passed[0]['strength'] < 0.8, "weak 市应降权"
