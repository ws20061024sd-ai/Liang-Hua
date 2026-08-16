"""
仪表盘健康页测试 —— _health() 的 PE/PB 覆盖率口径

背景（复查问题1）：data_check.py 已把分母改为股票池总数，
但 web/generate.py 的 _health() 仍是"有财务数据的股票数"，
线上 health.html 与 data_check 显示不一致。
"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from web.generate import _health


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    c = sqlite3.connect(path)
    c.execute("""
        CREATE TABLE daily_kline (
            code TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, amount REAL, pct_change REAL, turnover REAL,
            PRIMARY KEY (code, date)
        )
    """)
    c.execute("""
        CREATE TABLE financial_data (
            code TEXT, date TEXT, close REAL, pe REAL, pb REAL,
            PRIMARY KEY (code, date)
        )
    """)
    c.execute("""
        CREATE TABLE financial_roe (
            code TEXT, date TEXT, roe REAL,
            PRIMARY KEY (code, date)
        )
    """)
    # 当日股票池 300 只
    for i in range(300):
        code = f"{i:06d}"
        c.execute(
            "INSERT INTO daily_kline (code, date, close, pct_change, volume, amount) "
            "VALUES (?, '2026-08-14', 10, 0, 1000000, 100000000)",
            (code,))
    c.commit()
    yield c
    c.close()


def test_health_pe_coverage_denominator_is_stock_pool(conn):
    """100 只有财务数据 → health 页 PE 覆盖率应显示 33%（与 data_check 一致）"""
    for i in range(100):
        conn.execute(
            "INSERT INTO financial_data (code, date, close, pe, pb) "
            "VALUES (?, '2026-08-14', 10, 15, 2)",
            (f"{i:06d}",))
    conn.commit()

    h = _health(conn)
    assert h['pe_pct'] == 33, f"PE 覆盖率应为 33%（分母=股票池300），实际 {h['pe_pct']}"
    assert h['pb_pct'] == 33, f"PB 覆盖率应为 33%，实际 {h['pb_pct']}"
