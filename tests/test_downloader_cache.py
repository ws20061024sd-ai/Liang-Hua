"""
成分股缓存刷新逻辑测试

背景（2026-08-16 审查问题3）：成分股缓存后永远优先读本地，从不刷新，
已调出沪深300的股票永远不会被移除。
修复：缓存超过 CONSTITUENT_REFRESH_DAYS 天自动调 API 刷新。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
from data_fetcher.downloader import _cache_stale


def test_force_refresh_always_stale():
    assert _cache_stale(None, force=True, refresh_days=30) is True
    assert _cache_stale("2026-08-15", force=True, refresh_days=30) is True


def test_no_timestamp_means_stale():
    """旧库没有 updated_at 记录 → 视为过期，触发一次刷新补写"""
    assert _cache_stale(None, force=False, refresh_days=30) is True


def test_recent_cache_is_fresh():
    today = datetime.now().strftime("%Y-%m-%d")
    assert _cache_stale(today, force=False, refresh_days=30) is False


def test_yesterday_cache_is_fresh():
    y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    assert _cache_stale(y, force=False, refresh_days=30) is False


def test_old_cache_is_stale():
    old = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
    assert _cache_stale(old, force=False, refresh_days=30) is True


def test_exact_boundary_within_refresh_days():
    """恰好 refresh_days 天前 → 视为新鲜（未超期）"""
    b = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    assert _cache_stale(b, force=False, refresh_days=30) is False


def test_save_stock_info_removes_dropped_constituents(tmp_path, monkeypatch):
    """API 刷新后：已调出沪深300的股票应从股票池删除（原实现只增不删）"""
    import sqlite3
    import pandas as pd
    from data_fetcher.downloader import save_stock_info

    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE stock_info (
            code TEXT PRIMARY KEY, name TEXT, market TEXT,
            listing_date TEXT, is_st INTEGER DEFAULT 0, updated_at TEXT
        )
    """)
    # 旧池：000001（保留）、600000（将被调出）
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('000001', '平安银行', '2026-07-01')")
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('600000', '浦发银行', '2026-07-01')")
    conn.commit()

    # 新成分列表：只有 000001 + 新股 601398
    new_list = pd.DataFrame([
        {"code": "000001", "name": "平安银行"},
        {"code": "601398", "name": "工商银行"},
    ])
    save_stock_info(conn, new_list)

    codes = {r[0] for r in conn.execute("SELECT code FROM stock_info").fetchall()}
    assert "600000" not in codes, "已调出指数的股票应被删除"
    assert "000001" in codes and "601398" in codes
    conn.close()
