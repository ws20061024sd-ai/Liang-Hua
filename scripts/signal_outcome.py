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

# ── Task 3: L2 等权账本撮合 ──────────────────────────────────────────────

class Ledger:
    """L2 等权账本撮合器

    语义：信号在 t 日收盘后产生 → t+1 开盘价成交。
    事件（pending）在 process_pending 用当日开盘价统一成交。
    止损/到期在 check_stop_loss/check_timeout 用当日收盘判定 → 次日开盘成交。
    同 code 多笔持仓 FIFO 配对。
    """

    def __init__(self):
        self.positions: dict[str, list[dict]] = {}  # code → [open 记录]
        self.pending: list[dict] = []               # 待次日开盘成交事件

    def _position(self, code: str, name: str, strategy: str, source: str,
                  date: str, exec_price: float) -> dict:
        return {'code': code, 'name': name, 'strategy': strategy,
                'source': source, 'buy_date': date, 'exec_price': exec_price,
                'hold_days': 0, 'signal_date': date}

    def on_signal(self, conn, date: str, code: str, name: str, strategy: str,
                  action: str, source: str) -> list[dict]:
        """登记一条当日信号：BUY → 排队次日开盘开仓；SELL 无持仓则忽略，
        有持仓则 FIFO 出队并排队次日开盘平仓（先出队再待成交，符合 FIFO 测试语义）"""
        if action == 'SELL':
            opens = self.positions.get(code) or []
            if not opens:
                return []
            pos = opens.pop(0)  # FIFO：平最早开仓的那笔
            self.pending.append({'kind': 'l2_close', 'date': date, 'code': code,
                'name': name, 'strategy': strategy, 'source': source,
                'pos': pos, 'exit_reason': 'sell'})
        elif action == 'BUY':
            self._queue_open(date, code, name, strategy, source)
        return []

    def _queue_open(self, date, code, name, strategy, source):
        self.pending.append({'kind': 'l2_open', 'date': date, 'code': code,
            'name': name, 'strategy': strategy, 'source': source,
            'exit_reason': None})

    def buy(self, conn, date: str, code: str, name: str, strategy: str,
            source: str) -> None:
        """BUY 信号 → 排队次日开盘开仓（等价 on_signal 的 BUY 分支）"""
        self._queue_open(date, code, name, strategy, source)

    def check_stop_loss(self, conn, date: str) -> list[dict]:
        """移动止损：当日收盘 < 持仓期峰值×(1-TRAILING_STOP) → 排队次日开盘平仓"""
        triggered = []
        kdf_cache = {}
        for code, opens in list(self.positions.items()):
            kdf = kdf_cache.get(code)
            if kdf is None:
                kdf = _kline(conn, code)
                kdf_cache[code] = kdf
            row = kdf[kdf['date'] == date]
            if row.empty:
                continue  # 当日无行情（停牌）→ 跳过
            cur = float(row.iloc[0]['close'])
            for i, pos in enumerate(opens):
                peak_rows = kdf[kdf['date'] >= pos['buy_date']]
                if peak_rows.empty:
                    continue
                peak = float(peak_rows['close'].max())
                if cur < peak * (1 - settings.TRAILING_STOP):
                    pos = opens.pop(i)
                    self.pending.append({'kind': 'l2_close', 'date': date,
                        'code': code, 'name': pos['name'],
                        'strategy': pos['strategy'], 'source': pos['source'],
                        'pos': pos, 'exit_reason': 'stop_loss'})
                    triggered.append({'exit_reason': 'stop_loss', 'code': code})
                    break  # 该 code 当日只触发一笔，其余下次检查
        return triggered

    def check_timeout(self, conn, date: str) -> list[dict]:
        """持仓交易日计数 ≥ OUTCOME_MAX_HOLD_DAYS → 排队次日开盘平仓"""
        out = []
        for code, opens in list(self.positions.items()):
            for i, pos in enumerate(opens):
                pos['hold_days'] += 1
                if pos['hold_days'] >= settings.OUTCOME_MAX_HOLD_DAYS:
                    pos = opens.pop(i)
                    self.pending.append({'kind': 'l2_close', 'date': date,
                        'code': code, 'name': pos['name'],
                        'strategy': pos['strategy'], 'source': pos['source'],
                        'pos': pos, 'exit_reason': 'timeout'})
                    out.append({'exit_reason': 'timeout', 'code': code})
                    break
        return out

    def process_pending(self, conn, date: str) -> list[dict]:
        """用当日开盘价成交所有待处理事件（开仓/平仓），返回成交事件"""
        events = []
        kdf_cache = {}
        for ev in list(self.pending):
            kdf = kdf_cache.get(ev['code'])
            if kdf is None:
                kdf = _kline(conn, ev['code'])
                kdf_cache[ev['code']] = kdf
            op = _open_price_at(kdf, date)
            if op is None:
                continue  # 当日无开盘（停牌/数据缺）→ 保留待下次
            self.pending.remove(ev)
            if ev['kind'] == 'l2_open':
                pos = self._position(ev['code'], ev['name'], ev['strategy'],
                                     ev['source'], ev['date'], op)
                self.positions.setdefault(ev['code'], []).append(pos)
                events.append({'kind': 'l2_open', 'date': ev['date'],
                    'code': ev['code'], 'name': ev['name'],
                    'strategy': ev['strategy'], 'source': ev['source'],
                    'exec_price': op, 'signal_date': ev['date']})
            else:
                pos = ev['pos']
                pnl = _pnl_pct(pos['exec_price'], op)
                events.append({'kind': 'l2_close', 'date': ev['date'],
                    'code': ev['code'], 'name': ev['name'],
                    'strategy': pos['strategy'], 'source': ev['source'],
                    'exec_price': op, 'pnl': round(pnl, 3),
                    'hold_days': pos['hold_days'], 'exit_reason': ev['exit_reason'],
                    'signal_date': ev['date']})
        return events

    def write_events(self, conn, events: list[dict]) -> None:
        """成交事件写库（幂等：UNIQUE(date, code, strategy, action, kind, source)）"""
        for ev in events:
            conn.execute("""INSERT OR IGNORE INTO signal_outcome
                (date, code, name, strategy, action, source, kind,
                 exec_price, pnl, hold_days, exit_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ev['date'], ev['code'], ev['name'], ev['strategy'],
                 'BUY' if ev['kind'] == 'l2_open' else 'SELL',
                 ev['source'], ev['kind'], ev.get('exec_price'),
                 ev.get('pnl'), ev.get('hold_days'), ev.get('exit_reason')))
        conn.commit()

if __name__ == '__main__':
    init_outcome_table()
    print("✅ signal_outcome 表已就绪")
