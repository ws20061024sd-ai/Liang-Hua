"""
数据健康检查测试 —— PE/PB 覆盖率分母

背景（2026-08-16 审查问题4）：原实现分母是"有财务数据的股票数"，
300 只里 100 只有数据时覆盖率显示 100%，掩盖真实数据缺失。
修复：分母改为当日股票池总数。
"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import scripts.data_check as dc


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "test.db")
    c = sqlite3.connect(path)
    c.execute("""
        CREATE TABLE daily_kline (
            code TEXT, date TEXT, close REAL, pct_change REAL,
            volume REAL, amount REAL, turnover REAL,
            PRIMARY KEY (code, date)
        )
    """)
    c.execute("""
        CREATE TABLE financial_data (
            code TEXT, date TEXT, close REAL, pe REAL, pb REAL,
            PRIMARY KEY (code, date)
        )
    """)
    # 当日股票池：300 只
    for i in range(300):
        code = f"{i:06d}"
        c.execute(
            "INSERT INTO daily_kline (code, date, close, pct_change, volume, amount) "
            "VALUES (?, '2026-08-14', 10, 0, 1000000, 100000000)",
            (code,))
    c.commit()
    dc.errors.clear()
    dc.warnings.clear()
    yield c
    c.close()


def test_coverage_denominator_is_total_stock_pool(conn):
    """只有 100 只有财务数据（PE 全有效）→ 覆盖率应为 33%，阻断"""
    for i in range(100):
        code = f"{i:06d}"
        conn.execute(
            "INSERT INTO financial_data (code, date, close, pe, pb) "
            "VALUES (?, '2026-08-14', 10, 15, 2)",
            (code,))
    conn.commit()

    dc.check_financial_data(conn)

    assert dc.errors, "100/300 覆盖率不足应阻断（fail）"
    assert any("PE覆盖率" in e for e in dc.errors)


def test_full_financial_coverage_passes(conn):
    """280 只有财务数据 → 覆盖率 93%，通过"""
    for i in range(280):
        code = f"{i:06d}"
        conn.execute(
            "INSERT INTO financial_data (code, date, close, pe, pb) "
            "VALUES (?, '2026-08-14', 10, 15, 2)",
            (code,))
    conn.commit()

    dc.check_financial_data(conn)

    assert not dc.errors, f"280/300 覆盖率应通过，实际失败: {dc.errors}"
