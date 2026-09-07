#!/usr/bin/env python3
"""
信号效果验证 —— L1 固定窗口预测力 + L2 等权动态账本

用法:
  python scripts/signal_outcome.py           # 每日增量结算
  python scripts/signal_outcome.py --replay  # 首次回放补齐历史

设计见 docs/superpowers/specs/2026-08-16-signal-outcome-design.md
"""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from config import settings

DB = settings.DB_PATH

def init_outcome_table(conn=None):
    """创建结算结果表（幂等）"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcome (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, code TEXT, name TEXT,
            strategy TEXT, action TEXT,
            source TEXT, kind TEXT,
            ref_price REAL, exec_price REAL, exit_price REAL,
            pnl REAL, excess REAL, hold_days INTEGER,
            exit_reason TEXT, status TEXT DEFAULT 'done',
            UNIQUE(date, code, strategy, action, kind, source)
        )
    """)
    conn.commit()
    if own:
        conn.close()

def _trading_dates(conn) -> list[str]:
    """daily_kline 去重日期（升序）——真实交易日历"""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline ORDER BY date").fetchall()]

def _kline(conn, code: str) -> pd.DataFrame:
    """单股全量日线（date/open/close，升序）"""
    return pd.read_sql_query(
        "SELECT date, open, close FROM daily_kline WHERE code=? ORDER BY date",
        conn, params=(code,))

def _open_price_at(df: pd.DataFrame, date: str) -> float | None:
    """某日期开盘价（成交用）；无该日数据返回 None"""
    row = df[df['date'] == date]
    return float(row.iloc[0]['open']) if not row.empty else None

if __name__ == '__main__':
    init_outcome_table()
    print("✅ signal_outcome 表已就绪")
