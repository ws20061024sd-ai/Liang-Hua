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


# ── Task 2: L1 固定窗口结算 ──────────────────────────────────────────────

def _index_df(conn) -> pd.DataFrame:
    """沪深300指数全量（date/close 升序）"""
    return pd.read_sql_query(
        "SELECT date, close FROM index_daily ORDER BY date", conn)

def _nth_trading_day(dates: list[str], sig_date: str, n: int) -> str | None:
    """dates 中信号日之后的第 n 个交易日；不足返回 None"""
    try:
        i = dates.index(sig_date)
    except ValueError:
        return None
    j = i + n
    return dates[j] if j < len(dates) else None

def _pnl_pct(from_price: float, to_price: float) -> float:
    return (to_price / from_price - 1) * 100

def settle_l1(conn, date: str, code: str, name: str, strategy: str,
              action: str, source: str) -> list[str]:
    """结算单条信号的 L1 固定窗口（5/10/20 交易日），幂等

    收益 = 第N交易日收盘 / 信号日收盘 - 1（信号日收盘 = signal_history.price，
    由调用方传入 date/code 后此处从 daily_kline 取该日收盘，与 price 同源）
    超额 = 收益 - 同期沪深300收益
    """
    dates = _trading_dates(conn)
    kdf = _kline(conn, code)
    idx = _index_df(conn)
    if kdf.empty or idx.empty:
        return []
    sig_row = kdf[kdf['date'] == date]
    if sig_row.empty:
        return []
    ref_price = float(sig_row.iloc[0]['close'])
    idx_map = dict(zip(idx['date'], idx['close']))
    if date not in idx_map:
        return []
    idx_ref = idx_map[date]

    written = []
    for n in settings.OUTCOME_WINDOWS:
        target = _nth_trading_day(dates, date, n)
        if target is None:
            continue  # 窗口未到，等后续每日结算
        row = kdf[kdf['date'] == target]
        idx_row = idx_map.get(target)
        if row.empty or idx_row is None:
            continue  # 该日无数据（停牌/退市）→ 跳过
        pnl = round(_pnl_pct(ref_price, float(row.iloc[0]['close'])), 3)
        excess = round(pnl - _pnl_pct(idx_ref, idx_row), 3)
        conn.execute("""INSERT OR IGNORE INTO signal_outcome
            (date, code, name, strategy, action, source, kind, ref_price, pnl, excess)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (date, code, name, strategy, action, source, f'l1_{n}d', ref_price, pnl, excess))
        written.append(f'l1_{n}d')
    conn.commit()
    return written

def summary_l1(conn, source: str | None = None) -> list[dict]:
    """分策略 L1 汇总（胜率按 BUY 超额>0、SELL 超额<0 计命中）

    返回 [{'strategy', 'total', 'win_rate', 'avg_excess'}] 按 10 日窗口
    """
    q = """SELECT strategy, action, kind, COUNT(*) as n,
        AVG(excess) as avg_ex, SUM(CASE WHEN
            (action='BUY' AND excess>0) OR (action='SELL' AND excess<0)
            THEN 1 ELSE 0 END) as hit
        FROM signal_outcome WHERE kind='l1_10d'"""
    params = []
    if source:
        q += " AND source=?"
        params.append(source)
    q += " GROUP BY strategy, action"
    rows = conn.execute(q, params).fetchall()
    out = {}
    for strategy, action, kind, n, avg_ex, hit in rows:
        d = out.setdefault(strategy, {'strategy': strategy, 'total': 0,
                                      'win_rate': None, 'avg_excess': None})
        d['total'] += n
        d['hit_count'] = d.get('hit_count', 0) + (hit or 0)
        d['ex_sum'] = d.get('ex_sum', 0.0) + (avg_ex or 0) * n
    result = []
    for s, d in out.items():
        result.append({'strategy': s, 'total': d['total'],
            'win_rate': round(d['hit_count'] / d['total'] * 100, 1) if d['total'] else None,
            'avg_excess': round(d['ex_sum'] / d['total'], 2) if d['total'] else None})
    return sorted(result, key=lambda r: -(r['avg_excess'] or 0))

if __name__ == '__main__':
    init_outcome_table()
    print("✅ signal_outcome 表已就绪")
