"""
交易日历 —— 判断今天是否为 A 股交易日

数据源：AKShare 新浪交易日历（可靠、免费）
回退方案：周末判断 + DB 数据日期推断
"""
from datetime import datetime, date
import pandas as pd

_cache_date = None
_cache_result = None


def is_trading_day(check_date: date = None) -> bool:
    """
    判断是否为 A 股交易日

    策略：
      1. 从 AKShare 拉取交易日历 → 精确（含春节/国庆等长假）
      2. AKShare 不可用时 → 周末判断 + DB 数据日期兜底
    """
    global _cache_date, _cache_result
    if check_date is None:
        check_date = date.today()

    # 当天缓存（避免重复 API 调用）
    if _cache_date == check_date:
        return _cache_result

    result = _check_akshare(check_date)
    if result is not None:
        _cache_date = check_date
        _cache_result = result
        return result

    # 回退：周末 + DB 推断
    return _check_fallback(check_date)


def _check_akshare(check_date: date) -> bool | None:
    """通过 AKShare 交易日历判断"""
    try:
        import akshare as ak
        # 拉取近 1 年的交易日历（首次慢，后续有缓存）
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return None
        trade_dates = set(
            pd.to_datetime(df['trade_date']).dt.date
        )
        return check_date in trade_dates
    except Exception:
        return None


def _check_fallback(check_date: date) -> bool:
    """回退方案：周末 + 节假日推断"""
    # 1. 周末一定不是交易日
    if check_date.weekday() >= 5:
        return False

    # 2. 检查 DB 中最新数据日期
    try:
        import sqlite3
        from config import settings
        conn = sqlite3.connect(settings.DB_PATH)
        max_date_str = conn.execute(
            "SELECT MAX(date) FROM daily_kline"
        ).fetchone()[0]
        conn.close()

        if max_date_str is None:
            return check_date.weekday() < 5  # 无数据时仅判断周末

        max_date = datetime.strptime(max_date_str, "%Y-%m-%d").date()

        # 如果今天是工作日但 DB 最新数据比昨天还旧 → 可能是节假日
        # （服务器 cron 在工作日 21:00 运行，正常情况 DB 日期 = 今天）
        if check_date.weekday() < 5:
            days_behind = (check_date - max_date).days
            if days_behind > 1:
                return False  # 数据库落后超过 1 天 → 可能是长假
            return True
        return False
    except Exception:
        return check_date.weekday() < 5


def next_trading_day(from_date: date = None) -> date:
    """获取最近的下一个交易日"""
    import time
    if from_date is None:
        from_date = date.today()

    from datetime import timedelta
    d = from_date + timedelta(days=1)
    # 最多往后找 10 天（覆盖春节/国庆长假）
    for _ in range(10):
        if is_trading_day(d):
            return d
        d = d + timedelta(days=1)
    # 极端情况：返回 3 天后的工作日
    d = from_date + timedelta(days=3)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d
