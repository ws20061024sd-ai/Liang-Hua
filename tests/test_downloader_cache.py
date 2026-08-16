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
