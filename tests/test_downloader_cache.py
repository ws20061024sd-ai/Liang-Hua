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


def test_save_stock_info_refresh_removes_dropped_constituents(tmp_path):
    """refresh=True（API 刷新成功）：已调出股票删除 + 更新时间戳"""
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
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('000001', '平安银行', '2026-07-01')")
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('600000', '浦发银行', '2026-07-01')")
    conn.commit()

    new_list = pd.DataFrame([
        {"code": "000001", "name": "平安银行"},
        {"code": "601398", "name": "工商银行"},
    ])
    save_stock_info(conn, new_list, refresh=True)

    codes = {r[0] for r in conn.execute("SELECT code FROM stock_info").fetchall()}
    assert "600000" not in codes, "refresh=True 应删除调出股"
    assert "000001" in codes and "601398" in codes
    updated = conn.execute("SELECT updated_at FROM stock_info WHERE code='000001'").fetchone()[0]
    assert updated == datetime.now().strftime("%Y-%m-%d"), "refresh=True 应更新时间戳"
    conn.close()


def test_save_stock_info_without_refresh_does_not_touch_stock_pool(tmp_path):
    """refresh=False（缓存/降级路径）：不更新时间戳、不删除旧股——防止 API 失败
    后系统再等 30 天才重试（风险A），也防止部分列表误删（风险B）"""
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
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('000001', '平安银行', '2026-07-01')")
    conn.execute("INSERT INTO stock_info (code, name, updated_at) VALUES ('600000', '浦发银行', '2026-07-01')")
    conn.commit()

    # 模拟"部分列表"（API 异常返回）——refresh=False 时必须原样保留
    partial = pd.DataFrame([{"code": "000001", "name": "平安银行"}])
    save_stock_info(conn, partial, refresh=False)

    codes = {r[0] for r in conn.execute("SELECT code FROM stock_info").fetchall()}
    assert "600000" in codes, "refresh=False 不应删除旧股"
    updated = conn.execute("SELECT updated_at FROM stock_info WHERE code='000001'").fetchone()[0]
    assert updated == "2026-07-01", "refresh=False 不应更新时间戳"
    conn.close()


def test_accept_result_requires_minimum_count():
    """API 结果数量不足（部分列表）→ 不接受：不更新缓存、不删除旧股"""
    import pandas as pd
    from data_fetcher.downloader import _accept_result

    small = pd.DataFrame([{"code": f"{i:06d}", "name": "x"} for i in range(50)])
    assert _accept_result(small, min_n=250) is False, "50 条部分列表应拒绝"

    full = pd.DataFrame([{"code": f"{i:06d}", "name": "x"} for i in range(280)])
    assert _accept_result(full, min_n=250) is True, "280 条完整列表应接受"
