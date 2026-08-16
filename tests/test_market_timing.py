"""
大盘择时测试 —— 覆盖四档状态分类，重点验证回调场景不误判 weak
"""
import sqlite3
import pandas as pd
import numpy as np
import pytest
from engine import market_timing

# 保存真实本地读取实现（autouse fixture 会打桩掉它，本地表测试需要恢复）
_ORIG_LOCAL_INDEX = market_timing._load_local_index


@pytest.fixture(autouse=True)
def _stub_stock_data(monkeypatch):
    """打桩本地查库 + 网络拉取，避免真实数据库/网络依赖"""
    def _fake(code, days=90):
        return market_timing._last_df
    monkeypatch.setattr(market_timing, '_load_local_index', lambda days=90: None)
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


def test_uses_local_index_daily_first(tmp_path, monkeypatch):
    """本地 index_daily 有足够数据时优先使用，不依赖网络"""
    from config import settings
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE index_daily (
            date TEXT PRIMARY KEY, open REAL, close REAL,
            high REAL, low REAL, volume REAL, amount REAL
        )
    """)
    np.random.seed(42)
    closes = np.concatenate([
        np.random.uniform(3700, 3800, 60),
        np.linspace(3800, 4000, 30),
    ])
    dates = pd.date_range(end=pd.Timestamp.now(), periods=len(closes), freq='B')
    for d, c in zip(dates, closes):
        conn.execute("INSERT INTO index_daily (date, close) VALUES (?, ?)",
                     (d.strftime('%Y-%m-%d'), float(c)))
    conn.commit()
    conn.close()

    # 恢复真实本地读取（autouse fixture 默认打桩为 None）
    monkeypatch.setattr(market_timing, '_load_local_index', _ORIG_LOCAL_INDEX)
    # 网络路径打桩为抛错：若仍能返回强市，说明用的是本地数据
    def _boom(*a, **k):
        raise AssertionError("不应调用网络拉取")
    monkeypatch.setattr(market_timing, '_fetch_index_data', _boom)
    monkeypatch.setattr(market_timing, 'get_stock_data', _boom)

    r = market_timing.get_market_regime()
    assert r['regime'] == 'strong', f"应使用本地指数数据，实际 {r['regime']}"
    assert r['index_close'] is not None


def test_stale_local_index_falls_back(tmp_path, monkeypatch):
    """本地 index_daily 陈旧（超过 INDEX_MAX_STALE_DAYS）→ 不应采用，走兜底"""
    from config import settings
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE index_daily (
            date TEXT PRIMARY KEY, open REAL, close REAL,
            high REAL, low REAL, volume REAL, amount REAL
        )
    """)
    # 最新的数据是 10 天前（陈旧）——但数据量充足，会被陈旧采用
    np.random.seed(42)
    closes = np.concatenate([
        np.random.uniform(3700, 3800, 60),
        np.linspace(3800, 4000, 30),
    ])
    end = pd.Timestamp.now() - pd.Timedelta(days=10)
    dates = pd.date_range(end=end, periods=len(closes), freq='B')
    for d, c in zip(dates, closes):
        conn.execute("INSERT INTO index_daily (date, close) VALUES (?, ?)",
                     (d.strftime('%Y-%m-%d'), float(c)))
    conn.commit()
    conn.close()

    # 所有真实数据路径都不可用（daily_kline 无指数、网络失败）→
    # 若返回保守状态，说明陈旧本地数据未被采用
    monkeypatch.setattr(market_timing, '_load_local_index', _ORIG_LOCAL_INDEX)
    monkeypatch.setattr(market_timing, '_fetch_index_data', lambda days=90: None)
    monkeypatch.setattr(market_timing, 'get_stock_data', lambda code, days=90: None)

    r = market_timing.get_market_regime()
    assert r['regime'] == 'shaky', \
        f"陈旧本地数据不应被采用（应保守兜底），实际 {r['regime']}"
    assert r['index_close'] is None
