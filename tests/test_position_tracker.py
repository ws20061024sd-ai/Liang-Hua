"""
持仓追踪单元测试 —— 移动止损 SELL 确认期逻辑

背景（2026-08-16 审查问题2）：系统假设每条 SELL 信号都被用户真实执行。
用户没卖止损时，SELL 写入后系统就认为平仓，止损提醒只出现一次。
修复：移动止损 SELL 写入后有确认期（STOP_CONFIRM_DAYS 天），
确认期内视为"待确认"，持仓保留、止损继续触发提醒。
"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from config import settings
from engine import position_tracker

# 测试用确认期
CONFIRM_DAYS = 3


@pytest.fixture
def db(tmp_path, monkeypatch):
    """临时数据库 + signal_history 表"""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(position_tracker, "DB", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, code TEXT, name TEXT, strategy TEXT,
            action TEXT, strength REAL, reason TEXT, price REAL,
            status TEXT DEFAULT 'passed', filter_reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE daily_kline (
            code TEXT, date TEXT, open REAL, high REAL, low REAL,
            close REAL, volume REAL, amount REAL, pct_change REAL, turnover REAL,
            PRIMARY KEY (code, date)
        )
    """)
    # 模拟最近 3 个交易日（确认期窗口取自 daily_kline 交易日历）
    for d in ("2026-08-12", "2026-08-13", "2026-08-14"):
        conn.execute("""
            INSERT INTO daily_kline (code, date, close)
            VALUES ('000001', ?, 10.0)
        """, (d,))
    conn.commit()
    return conn


def _ins(conn, date, code="000001", action="BUY", strategy="MA交叉",
         price=10.0, status="passed"):
    conn.execute("""
        INSERT INTO signal_history (date, code, name, strategy, action, price, status)
        VALUES (?, ?, '测试股', ?, ?, ?, ?)
    """, (date, code, strategy, action, price, status))
    conn.commit()


def test_stop_loss_sell_within_confirm_window_stays_position(db):
    """确认期内：移动止损 SELL 不算平仓，持仓保留（用户可能没卖）"""
    _ins(db, "2026-08-12", action="BUY", strategy="MA交叉")
    _ins(db, "2026-08-14", action="SELL", strategy="移动止损")

    positions = position_tracker.get_positions()
    assert len(positions) == 1, "确认期内的移动止损 SELL 不应导致平仓"
    assert positions[0]["code"] == "000001"


def test_regular_sell_immediately_closes_position(db):
    """普通卖出（MA死叉等）：用户收到即视为已执行，立即平仓"""
    _ins(db, "2026-08-12", action="BUY", strategy="MA交叉")
    _ins(db, "2026-08-14", action="SELL", strategy="MA交叉")

    positions = position_tracker.get_positions()
    assert len(positions) == 0, "普通 SELL 应立即平仓"


def test_stop_loss_sell_past_confirm_window_closes_position(db):
    """确认期过后：移动止损 SELL 视为已执行，平仓"""
    _ins(db, "2026-08-01", action="BUY", strategy="MA交叉")
    _ins(db, "2026-08-10", action="SELL", strategy="移动止损")  # 已过确认期

    positions = position_tracker.get_positions()
    assert len(positions) == 0, "确认期过后的移动止损 SELL 应平仓"


def test_stop_loss_sell_then_new_buy_is_position(db):
    """确认期内 SELL 后又出现新 BUY → 持仓（按最新记录）"""
    _ins(db, "2026-08-08", action="BUY", strategy="MA交叉")
    _ins(db, "2026-08-13", action="SELL", strategy="移动止损")
    _ins(db, "2026-08-14", action="BUY", strategy="MA交叉")

    positions = position_tracker.get_positions()
    assert len(positions) == 1, "最新记录是 BUY 应持仓"
