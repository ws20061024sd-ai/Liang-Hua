"""
指数数据下载器测试 —— backfill_amount 回填逻辑

背景（2026-08-16 审查问题6）：原实现 has_amount > 100 就整体跳过回填，
导致历史有数据后每天新增的指数行 amount 永远 = 0（仪表盘成交额失真）。
修复：只回填 amount=0/IS NULL 的新行，历史行不动。
"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data_fetcher import index_downloader


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(index_downloader, "DB", path)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE index_daily (
            date TEXT PRIMARY KEY, open REAL, close REAL,
            high REAL, low REAL, volume REAL, amount REAL
        )
    """)
    conn.execute("CREATE TABLE daily_kline (code TEXT, date TEXT, amount REAL)")
    return conn


def test_backfill_fills_new_zero_rows(db):
    """历史有 100+ 天成交额后，新增的 amount=0 行仍应被回填"""
    # 历史 101 天有成交额（超过旧实现的跳过阈值 100）
    for i in range(101):
        db.execute("INSERT INTO index_daily (date, close, amount) VALUES (?, 3000, 100)",
                   (f"2026-{i//28+3:02d}-{i%28+1:02d}",))
    # 今天新增行 amount=0
    db.execute("INSERT INTO index_daily (date, close, amount) VALUES ('2026-08-14', 4000, 0)")
    # 当日成分股成交额汇总
    db.execute("INSERT INTO daily_kline VALUES ('000001', '2026-08-14', 500000000)")
    db.commit()

    index_downloader.backfill_amount()

    amt = db.execute(
        "SELECT amount FROM index_daily WHERE date='2026-08-14'"
    ).fetchone()[0]
    assert amt == 500000000, f"新增行应被回填，实际 {amt}"
    old = db.execute(
        "SELECT amount FROM index_daily WHERE date='2026-03-01'"
    ).fetchone()[0]
    assert old == 100, "历史行不应被改动"


def test_backfill_skips_rows_without_stock_data(db):
    """成分股无当日汇总的日期保持 amount=0（不写入）"""
    db.execute("INSERT INTO index_daily (date, close, amount) VALUES ('2026-08-13', 4000, 0)")
    db.commit()

    index_downloader.backfill_amount()

    amt = db.execute(
        "SELECT amount FROM index_daily WHERE date='2026-08-13'"
    ).fetchone()[0]
    assert amt == 0, "无汇总数据的日期不应被写入"
