"""
信号存储模块 —— 持久化交易信号到 SQLite

去重策略：每个交易日每只股票每个策略同一方向只存一条
唯一键：(date, code, strategy, action)
"""
import sqlite3
from config import settings


def init_signal_table():
    """创建信号历史表（如果不存在）"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT,
            code        TEXT,
            name        TEXT,
            strategy    TEXT,
            action      TEXT,
            strength    REAL,
            reason      TEXT,
            price       REAL,
            status      TEXT DEFAULT 'passed',
            filter_reason TEXT,
            UNIQUE(date, code, strategy, action)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_date
        ON signal_history(date)
    """)
    conn.commit()
    conn.close()


def save_signals(signals: list[dict], status: str = 'passed'):
    """
    保存信号到数据库（自动去重）

    参数:
        signals: 信号列表
        status: 'passed'（通过风控）或 'blocked'（被拦截）
    """
    if not signals:
        return

    conn = sqlite3.connect(settings.DB_PATH)
    for sig in signals:
        conn.execute("""
            INSERT OR REPLACE INTO signal_history
            (date, code, name, strategy, action, strength, reason, price, status, filter_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sig.get('date', ''),
            sig.get('stock_code', ''),
            sig.get('stock_name', ''),
            sig.get('strategy', ''),
            sig.get('action', ''),
            sig.get('strength', 0),
            sig.get('reason', ''),
            sig.get('price', 0),
            status,
            sig.get('reject_reason') or sig.get('block_reason'),
        ))
    conn.commit()
    conn.close()


def get_recent_signals(days: int = 5) -> list[dict]:
    """获取最近N天的信号"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM signal_history
        WHERE date >= date('now', ?)
        ORDER BY date DESC, strength DESC
    """, (f'-{days} days',)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_signal_accuracy(days: int = 30) -> dict:
    """统计信号准确率（需配合后续涨跌数据）"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row

    # 统计各策略的买卖信号数量
    stats = conn.execute("""
        SELECT strategy, action, status, COUNT(*) as cnt
        FROM signal_history
        WHERE date >= date('now', ?)
        GROUP BY strategy, action, status
        ORDER BY strategy, action
    """, (f'-{days} days',)).fetchall()

    conn.close()
    return [dict(r) for r in stats]
