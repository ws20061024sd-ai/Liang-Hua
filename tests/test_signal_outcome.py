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
