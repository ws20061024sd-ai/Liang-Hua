"""信号效果结算测试 —— Task 1: 建表 + 交易日工具"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    c = sqlite3.connect(path)
    yield c
    c.close()

def _mk_kline(conn):
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    for d in ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]:
        conn.execute("INSERT INTO daily_kline VALUES ('000001', ?, 10,11,9,10.5,1e7,1e8,1,1)", (d,))

def test_init_outcome_table(conn):
    so.init_outcome_table(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "signal_outcome" in tables

def test_trading_dates_ascending(conn):
    _mk_kline(conn)
    dates = so._trading_dates(conn)
    assert dates == ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]

def test_kline_full_history(conn):
    """单股全量 K 线：date 升序，含 open/close 列（计划任务 2 的输入依赖）"""
    _mk_kline(conn)
    df = so._kline(conn, "000001")
    assert list(df.columns) == ["date", "open", "close"]
    assert list(df["date"]) == ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    assert float(df.iloc[0]["open"]) == 10.0


# ── Task 2: L1 固定窗口结算 ──────────────────────────────────────────────

def _mk_full_kline(conn, n_days=25, start="2026-07-01"):
    """构造连续交易日 + 单股价格序列"""
    import datetime as dt
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    d = dt.date.fromisoformat(start)
    price = 10.0
    for i in range(n_days):
        ds = d.isoformat()
        conn.execute("INSERT INTO daily_kline VALUES ('000001', ?, ?,?,?,?,1e7,1e8,1,1)",
                     (ds, price, price, price, price))
        # 每 5 天涨 5%（10 → 10.5 → 11.025 → ...）
        if (i + 1) % 5 == 0:
            price = round(price * 1.05, 4)
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())

def _mk_index(conn, n_days=25, start="2026-07-01"):
    import datetime as dt
    conn.execute("""CREATE TABLE IF NOT EXISTS index_daily (date TEXT PRIMARY KEY,
        open REAL, close REAL, high REAL, low REAL, volume REAL, amount REAL)""")
    d = dt.date.fromisoformat(start)
    p = 3000.0
    for i in range(n_days):
        conn.execute("INSERT OR REPLACE INTO index_daily (date, close) VALUES (?, ?)",
                     (d.isoformat(), p))
        if (i + 1) % 5 == 0:
            p = round(p * 1.02, 2)  # 每 5 天涨 2%
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())

def test_l1_window_calculation(conn):
    """信号日 2026-07-01 @10.00，第5交易日收盘 10.50 → +5.0%"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    # 需要 signal_history（ref_price 来源）——测试直接给 price 参数版本
    kinds = so.settle_l1(conn, date="2026-07-01", code="000001", name="平安银行",
                         strategy="双均线趋势跟踪", action="BUY", source="replay")
    row = conn.execute("""SELECT pnl, excess, kind FROM signal_outcome
        WHERE date='2026-07-01' AND kind='l1_5d'""").fetchone()
    assert row is not None, "5日窗口应已结算"
    assert abs(row[0] - 5.0) < 0.01, f"5日收益应为 +5.0%，实际 {row[0]}"
    # 指数同期 +2.0% → 超额 +3.0%
    assert abs(row[1] - 3.0) < 0.1, f"5日超额应约 +3.0%，实际 {row[1]}"

def test_l1_idempotent(conn):
    """重复结算不重复写"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l1_5d'").fetchone()[0]
    assert cnt == 1

def test_l1_window_not_ready_skips(conn):
    """不足 5 个交易日 → 不写入（等后续）"""
    _mk_full_kline(conn, n_days=3); _mk_index(conn, n_days=3)
    so.init_outcome_table(conn)
    kinds = so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0]
    assert cnt == 0

def test_l1_sell_uses_reverse_direction_later(conn):
    """SELL 信号同样结算（方向判定在展示层做）"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "SELL", "replay")
    row = conn.execute("SELECT pnl FROM signal_outcome WHERE kind='l1_5d'").fetchone()
    assert row is not None and abs(row[0] - 5.0) < 0.01
