# 信号效果验证系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建持续跟踪的信号验证系统——L1 固定窗口预测力检验 + L2 等权动态账本，回放补齐 1 年历史样本，每日 cron 自动结算新信号，仪表盘「信号效果」页展示。

**Architecture:** 新增 `scripts/signal_outcome.py` 结算引擎：L1 对每条历史信号查 5/10/20 交易日收益与沪深300 超额并写入 `signal_outcome` 表；L2 用 `Ledger` 撮合器按"信号次日开盘价成交"维护等权虚拟持仓，直到 SELL/止损/60 日到期平仓。首次 `--replay` 按日滚动复用生产策略代码回放 1 年补齐样本，之后每日增量结算真实信号。仪表盘加 outcome.html 页面。

**Tech Stack:** Python 3.11, pandas, sqlite3（复用现有策略/风控/数据模块，零新依赖）

## Global Constraints

- 复用生产代码：strategies/*、engine/runner、engine/risk_filter 原样 import，**不写第二套策略逻辑**
- 回放铁律：t 日信号只用 t 日及之前数据（不偷看未来）；结算（L1 查后续价）允许用全量数据
- 成交价规则：信号产生于收盘后，**统一用信号日的"次日开盘价"成交**（开仓与平仓一致，与用户 21:00 收信号、次日 9:30 下单的现实一致）；止损/到期事件同样次日开盘价成交
- 回放不含大盘择时降权：v3 降权只调 strength 排序，而 L1/L2 等权处理每条 passed 信号不受排序影响（真实信号含降权但等权参与，可比）
- L2 只从 passed BUY 开仓；无持仓的 SELL 不执行；同股 FIFO 配对
- 幂等：所有写入用 UNIQUE(date, code, strategy, action, kind, source) + INSERT OR IGNORE
- 移动止损在 Ledger 内部实现（TRAILING_STOP 从 settings 读，峰值=buy_date 后 MAX(close)），不依赖生产 position_tracker（其依赖 signal_history 推断持仓，回放模式下不可用）
- 遵循现有风格：中文注释、参数进 settings、except 至少 print
- 每 Task 结束 pytest 全量绿 + commit

---

### Task 1: settings 参数 + signal_outcome 表 + 工具函数

**Files:**
- Modify: `config/settings.py`（风控参数区后追加）
- Create: `scripts/signal_outcome.py`（骨架：建表 + 交易日工具 + main 入口占位）
- Test: `tests/test_signal_outcome.py`

**Interfaces:**
- Produces:
  - `init_outcome_table()` → None（CREATE TABLE IF NOT EXISTS）
  - `_trading_dates(conn) -> list[str]`：daily_kline 去重日期升序
  - `_kline(conn, code) -> pd.DataFrame`：单股全量 K 线（date asc, open/close 列）

- [ ] **Step 1: Write the failing test**

```python
"""信号效果结算测试 —— Task 1: 建表 + 交易日工具"""
import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    c = sqlite3.connect(path)
    yield c
    c.close()

def _mk_kline(conn):
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    for d in ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]:
        conn.execute("INSERT INTO daily_kline VALUES ('000001', ?, 10,11,9,10.5,1e7,1e8,1,1)", (d,))

def test_init_outcome_table(conn):
    so.init_outcome_table(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "signal_outcome" in tables

def test_trading_dates_ascending(conn):
    _mk_kline(conn)
    dates = so._trading_dates(conn)
    assert dates == ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_signal_outcome.py -v`
Expected: FAIL（`scripts.signal_outcome` 不存在 / 函数缺失）

- [ ] **Step 3: Implement**

settings.py 追加：

```python
# 信号效果验证（L1 固定窗口 / L2 等权账本）
OUTCOME_WINDOWS = [5, 10, 20]      # L1 观察窗口（交易日）
OUTCOME_MAX_HOLD_DAYS = 60         # L2 到期强制平仓（交易日）
REPLAY_YEARS = 1                   # 回放年数
```

scripts/signal_outcome.py：

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_signal_outcome.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add config/settings.py scripts/signal_outcome.py tests/test_signal_outcome.py
git commit -m "feat: 信号验证基础——settings参数/outcome表/交易日工具"
```

---

### Task 2: L1 固定窗口结算 + 汇总查询

**Files:**
- Modify: `scripts/signal_outcome.py`
- Test: `tests/test_signal_outcome.py`

**Interfaces:**
- Consumes: Task 1 的 `init_outcome_table`/`_trading_dates`/`_kline`/`_open_price_at`
- Produces:
  - `_index_df(conn) -> pd.DataFrame`：index_daily 全量（date/close 升序）
  - `_nth_trading_day(dates: list[str], sig_date: str, n: int) -> str | None`：信号日后第 n 个交易日（不含信号日）
  - `_pnl_pct(from_price: float, to_price: float) -> float`：`(to/from - 1) * 100`
  - `settle_l1(conn, date, code, name, strategy, action, source)` → list[str]：结算该信号的 5/10/20 日窗口并写入，返回已写 kind 列表（幂等，已有则跳过）
  - `summary_l1(conn, source: str | None = None) -> list[dict]`：分策略汇总（供仪表盘）
- L1 判定：pnl 用信号日收盘(`ref_price`=signal_history.price)→第 N 日收盘；excess = pnl − 同期指数 pnl；不足 N 日 → 不写（等后续）；停牌/退市查不到 → 跳过

- [ ] **Step 1: Write the failing tests**

```python
"""L1 结算测试 —— 追加到 tests/test_signal_outcome.py"""

def _mk_full_kline(conn, n_days=25, start="2026-07-01"):
    """构造连续交易日 + 单股价格序列"""
    import datetime as dt
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    d = dt.date.fromisoformat(start)
    price = 10.0
    for i in range(n_days):
        ds = d.isoformat()
        conn.execute("INSERT INTO daily_kline VALUES ('000001', ?, ?,?,?,?,1e7,1e8,1,1)",
                     (ds, price, price, price, price))
        # 每 5 天涨 5%（10 → 10.5 → 11.025 → ...）
        if (i + 1) % 5 == 0:
            price = round(price * 1.05, 4)
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())

def _mk_index(conn, n_days=25, start="2026-07-01"):
    import datetime as dt
    conn.execute("""CREATE TABLE IF NOT EXISTS index_daily (date TEXT PRIMARY KEY,
        open REAL, close REAL, high REAL, low REAL, volume REAL, amount REAL)""")
    d = dt.date.fromisoformat(start)
    p = 3000.0
    for i in range(n_days):
        conn.execute("INSERT OR REPLACE INTO index_daily (date, close) VALUES (?, ?)",
                     (d.isoformat(), p))
        if (i + 1) % 5 == 0:
            p = round(p * 1.02, 2)  # 每 5 天涨 2%
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())

def test_l1_window_calculation(conn):
    """信号日 2026-07-01 @10.00，第5交易日收盘 10.50 → +5.0%"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    # 需要 signal_history（ref_price 来源）——测试直接给 price 参数版本
    kinds = so.settle_l1(conn, date="2026-07-01", code="000001", name="平安银行",
                         strategy="双均线趋势跟踪", action="BUY", source="replay")
    row = conn.execute("""SELECT pnl, excess, kind FROM signal_outcome
        WHERE date='2026-07-01' AND kind='l1_5d'""").fetchone()
    assert row is not None, "5日窗口应已结算"
    assert abs(row[0] - 5.0) < 0.01, f"5日收益应为 +5.0%，实际 {row[0]}"
    # 指数同期 +2.0% → 超额 +3.0%
    assert abs(row[1] - 3.0) < 0.1, f"5日超额应约 +3.0%，实际 {row[1]}"

def test_l1_idempotent(conn):
    """重复结算不重复写"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l1_5d'").fetchone()[0]
    assert cnt == 1

def test_l1_window_not_ready_skips(conn):
    """不足 5 个交易日 → 不写入（等后续）"""
    _mk_full_kline(conn, n_days=3); _mk_index(conn, n_days=3)
    so.init_outcome_table(conn)
    kinds = so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "BUY", "replay")
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0]
    assert cnt == 0

def test_l1_sell_uses_reverse_direction_later(conn):
    """SELL 信号同样结算（方向判定在展示层做）"""
    _mk_full_kline(conn); _mk_index(conn)
    so.init_outcome_table(conn)
    so.settle_l1(conn, "2026-07-01", "000001", "平安银行", "双均线趋势跟踪", "SELL", "replay")
    row = conn.execute("SELECT pnl FROM signal_outcome WHERE kind='l1_5d'").fetchone()
    assert row is not None and abs(row[0] - 5.0) < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_outcome.py -v`
Expected: FAIL（settle_l1 不存在）

- [ ] **Step 3: Implement**（追加到 signal_outcome.py）

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_outcome.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/signal_outcome.py tests/test_signal_outcome.py
git commit -m "feat: L1固定窗口结算——信号后5/10/20日收益+沪深300超额，幂等写入"
```

---

### Task 3: L2 Ledger 等权账本撮合器

**Files:**
- Modify: `scripts/signal_outcome.py`
- Test: `tests/test_signal_outcome_l2.py`（新建）

**Interfaces:**
- Consumes: Task 1/2 的 `init_outcome_table`/`_kline`/`_open_price_at`/`_trading_dates`/`_pnl_pct`
- Produces:
  - `class Ledger`：
    - `__init__(self)`: `self.positions: dict[str, list[dict]]`（code → 开仓队列，先进先出）
    - `register_buy(self, conn, date, code, name, strategy, source, pending: list[dict])`：把 BUY 信号加入待成交队列（次日开盘价成交）
    - `on_signal(self, conn, date, code, name, strategy, action, source)` → list[dict]：处理当日一条信号——SELL 用当日收盘判触发（次日开盘成交由 pending 统一处理）；实际撮合在 `process_pending`
    - `process_pending(self, conn, date) -> list[dict]`：用 date 的开盘价成交所有待处理事件（开仓/平仓），返回事件列表
    - `check_stop_loss(self, conn, date) -> list[dict]`：对持仓检查移动止损（当日 close < buy_date 后 MAX(close) × (1-TRAILING_STOP)）→ 触发平仓事件（次日开盘成交）
    - `check_timeout(self, conn, date) -> list[dict]`：持仓交易日计数 ≥ OUTCOME_MAX_HOLD_DAYS → 平仓事件
  - 撮合事件 dict：`{'kind': 'l2_open'|'l2_close', 'date': 信号日, 'code', 'name', 'strategy', 'source', 'exec_price', 'pnl', 'hold_days', 'exit_reason'}`
  - 注意：**回放模式 Ledger 全内存**；每日增量模式通过 `load_positions(conn)` / `save_positions(conn)` 持久化到 signal_outcome（kind='l2_open' 行 = 未平仓持仓）

**核心语义**：信号在 t 日收盘后产生 → 成交在 t+1 日开盘（process_pending 用 t+1 open 撮合）。SELL 平仓哪笔持仓？同 code FIFO（最早开仓先平）。

- [ ] **Step 1: Write the failing tests**

```python
"""L2 等权账本撮合测试"""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

@pytest.fixture
def env(tmp_path, monkeypatch):
    """带 5 个交易日、价格可控的临时库"""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    # 日期: d1..d5  价格: open 依次 10/10.5/11/10.8/11.2, close 同 open（简化）
    dates = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    for i, d in enumerate(dates):
        p = [10.0, 10.5, 11.0, 10.8, 11.2][i]
        conn.execute("INSERT INTO daily_kline VALUES ('000001', ?,?,?,?,?,1e7,1e8,0,0)",
                     (d, p, p, p, p))
    conn.commit()
    so.init_outcome_table(conn)
    yield conn, dates
    conn.close()

def test_buy_executes_next_open(env):
    """BUY@d1 → d2 开盘 10.5 成交入持仓"""
    conn, dates = env
    ledger = so.Ledger()
    ledger.on_signal(conn, dates[0], "000001", "测试", "双均线趋势跟踪", "BUY", "replay")
    events = ledger.process_pending(conn, dates[1])
    assert len(events) == 1 and events[0]['kind'] == 'l2_open'
    assert events[0]['exec_price'] == 10.5
    assert len(ledger.positions["000001"]) == 1

def test_sell_closes_position_with_pnl(env):
    """d1 BUY(成交10.5) → d3 SELL → d4 开盘 10.8 平仓 → pnl=+2.86%"""
    conn, dates = env
    ledger = so.Ledger()
    ledger.on_signal(conn, dates[0], "000001", "测试", "双均线趋势跟踪", "BUY", "replay")
    ledger.process_pending(conn, dates[1])
    ledger.on_signal(conn, dates[2], "000001", "测试", "双均线趋势跟踪", "SELL", "replay")
    events = ledger.process_pending(conn, dates[3])
    closes = [e for e in events if e['kind'] == 'l2_close']
    assert len(closes) == 1, "SELL 应平仓"
    assert abs(closes[0]['pnl'] - 2.86) < 0.05, f"pnl={closes[0]['pnl']}"
    assert closes[0]['exit_reason'] == 'sell'
    assert not ledger.positions.get("000001"), "平仓后无持仓"

def test_sell_without_position_ignored(env):
    """无持仓的 SELL 不执行"""
    conn, dates = env
    ledger = so.Ledger()
    events = ledger.on_signal(conn, dates[2], "000001", "测试", "双均线趋势跟踪", "SELL", "replay")
    assert events == [] or all(e['kind'] != 'l2_close' for e in events)

def test_fifo_pairing(env):
    """同股两笔 BUY，SELL 先平最早那笔"""
    conn, dates = env
    ledger = so.Ledger()
    ledger.on_signal(conn, dates[0], "000001", "测试", "A策略", "BUY", "replay")
    ledger.process_pending(conn, dates[1])
    ledger.on_signal(conn, dates[2], "000001", "测试", "B策略", "BUY", "replay")
    ledger.process_pending(conn, dates[3])
    assert len(ledger.positions["000001"]) == 2
    ledger.on_signal(conn, dates[4], "000001", "测试", "A策略", "SELL", "replay")
    # d5 之后无交易日可成交——验证 FIFO 移除最早那笔（先标记待平，成交在下次）
    assert len(ledger.positions["000001"]) == 1, "FIFO 应移除最早开仓"
    assert ledger.positions["000001"][0]['strategy'] == "B策略"

def test_stop_loss_triggers(env):
    """持仓后价格从峰值回落超 TRAILING_STOP → 止损平仓事件"""
    conn, dates = env
    ledger = so.Ledger()
    # 先构造 peak=11（d3），随后跌
    conn.execute("INSERT INTO daily_kline VALUES ('000001','2026-08-10',9.5,9.5,9.5,9.5,1e7,1e8,0,0)")
    conn.commit()
    ledger.on_signal(conn, dates[0], "000001", "测试", "双均线趋势跟踪", "BUY", "replay")
    ledger.process_pending(conn, dates[1])  # 开仓价 10.5，peak 从 buy_date 起算
    # 峰值 = MAX(close from 2026-08-04..08-10) = 11.0 → 止损线 10.45
    # 当前 close 9.5 < 10.45 → 触发
    evs = ledger.check_stop_loss(conn, "2026-08-10")
    assert len(evs) == 1 and evs[0]['exit_reason'] == 'stop_loss'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_outcome_l2.py -v`
Expected: FAIL（Ledger 不存在）

- [ ] **Step 3: Implement**（追加到 signal_outcome.py）

```python
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
        """登记一条当日信号：SELL 若无持仓则忽略；有持仓则排队次日开盘平仓"""
        opens = self.positions.get(code) or []
        if action == 'SELL':
            if not opens:
                return []
            pos = opens.pop(0)  # FIFO
            self.pending.append({'kind': 'l2_close', 'date': date, 'code': code,
                'name': name, 'strategy': strategy, 'source': source,
                'pos': pos, 'exit_reason': 'sell'})
        return []

    def _queue_open(self, date, code, name, strategy, source):
        self.pending.append({'kind': 'l2_open', 'date': date, 'code': code,
            'name': name, 'strategy': strategy, 'source': source,
            'exit_reason': None})

    def buy(self, conn, date: str, code: str, name: str, strategy: str,
            source: str) -> None:
        """BUY 信号 → 排队次日开盘开仓"""
        self._queue_open(date, code, name, strategy, source)

    def check_stop_loss(self, conn, date: str) -> list[dict]:
        """移动止损：当日收盘 < 持仓期峰值×(1-TRAILING_STOP) → 排队次日开盘平仓"""
        triggered = []
        kdf_cache = {}
        for code, opens in list(self.positions.items()):
            kdf = kdf_cache.get(code) or so_kline if False else _kline(conn, code)
            kdf_cache[code] = kdf
            row = kdf[kdf['date'] == date]
            if row.empty:
                continue
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
                    break
        return triggered

    def check_timeout(self, conn, date: str) -> list[dict]:
        """持仓交易日计数 ≥ OUTCOME_MAX_HOLD_DAYS → 排队平仓"""
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
            kdf = kdf_cache.get(ev['code']) or _kline(conn, ev['code'])
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
        """成交事件写库（幂等）"""
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
```

注意：`check_stop_loss` 中的笔误行 `kdf = kdf_cache.get(code) or so_kline if False else _kline(conn, code)` 必须写成下方正确形式（实现时按正确代码写，上面是防呆占位示意）：

```python
    def check_stop_loss(self, conn, date: str) -> list[dict]:
        triggered = []
        kdf_cache = {}
        for code, opens in list(self.positions.items()):
            kdf = kdf_cache.get(code)
            if kdf is None:
                kdf = _kline(conn, code)
                kdf_cache[code] = kdf
            row = kdf[kdf['date'] == date]
            if row.empty:
                continue
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
                    break
        return triggered
```

（FIFO 测试中 SELL 后立即断言持仓数——SELL 平仓是"先出队再待成交"，因此 SELL 当次调用即从 positions 移除。符合测试预期。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_outcome_l2.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/signal_outcome.py tests/test_signal_outcome_l2.py
git commit -m "feat: L2等权账本Ledger——次日开盘成交/SELL平仓/FIFO/移动止损/到期"
```

---

### Task 4: 回放引擎（--replay）

**Files:**
- Modify: `scripts/signal_outcome.py`
- Test: `tests/test_signal_outcome_replay.py`（新建）

**Interfaces:**
- Consumes: Task 1-3 全部；`engine.runner` 的 `STRATEGY_REGISTRY`；`data_fetcher.cleaner.get_all_stocks`；`engine.risk_filter.filter_signals`
- Produces:
  - `_daily_snapshot(conn, date, codes) -> pd.DataFrame`：当日全部股票快照（code/close/pct_change/volume/amount/is_st）供 filter_signals
  - `_run_strategies_for_date(conn, date, stocks, kline_all, strategies, snapshot) -> list[dict]`：对单日跑三策略（数据截至 date）
  - `replay(conn) -> dict`：主循环——对 REPLAY_YEARS 内每个交易日生成信号 → L1 结算 + Ledger 撮合 → 返回统计 {signals, l1_written, l2_closed}
- 已知简化（记录进注释）：跳过择时降权（等权验证不受 strength 影响）；策略 run 每股票每日重算（性能：300×250×3 次调用，预计 2-5 分钟）

- [ ] **Step 1: Write the failing test**

```python
"""回放引擎测试（用打桩策略避免真实计算）"""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    conn.execute("""CREATE TABLE index_daily (date TEXT PRIMARY KEY,
        open REAL, close REAL, high REAL, low REAL, volume REAL, amount REAL)""")
    conn.execute("""CREATE TABLE stock_info (code TEXT PRIMARY KEY, name TEXT,
        market TEXT, listing_date TEXT, is_st INTEGER DEFAULT 0, updated_at TEXT)""")
    conn.execute("""CREATE TABLE signal_history (id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, name TEXT, strategy TEXT, action TEXT,
        strength REAL, reason TEXT, price REAL, status TEXT DEFAULT 'passed',
        filter_reason TEXT)""")
    # 3 只股票 × 12 个交易日，价格平缓（无策略触发也没关系——打桩策略）
    for code in ["000001", "600000", "300750"]:
        for i in range(12):
            d = f"2026-07-{i+1:02d}"
            conn.execute("INSERT INTO daily_kline VALUES (?,?,10,10,10,10,1e7,1e8,0,0)",
                         (code, d))
            conn.execute("INSERT INTO index_daily VALUES (?,3000,3000,3000,3000,0,0)", (d,))
    conn.commit()
    yield conn
    conn.close()

def test_replay_generates_signals_and_settles(env, monkeypatch):
    """打桩策略每天发一条 BUY → 回放后 L1 已结算 + L2 有成交"""
    conn = env
    fake_sig = {'stock_code': '000001', 'stock_name': '平安',
                'action': 'BUY', 'strength': 0.8, 'reason': '测试', 'price': 10.0}

    class FakeSt:
        name = "双均线趋势跟踪"
        def run(self, code, name, df):
            if code == "000001":
                return dict(fake_sig)
            return None

    monkeypatch.setattr(so.settings, "REPLAY_YEARS", 1)
    # 直接测回放内部：构造单日信号流 + 结算
    dates = so._trading_dates(conn)
    sig_date = dates[5]  # 有 6 天后续可结算
    so.init_outcome_table(conn)
    ledger = so.Ledger()
    # 模拟 5 天信号日
    for d in dates[2:7]:
        # 当日信号 → L1 即时结算（用全量未来数据）→ Ledger
        so.settle_l1(conn, d, "000001", "平安", "双均线趋势跟踪", "BUY", "replay")
        ledger.buy(conn, d, "000001", "平安", "双均线趋势跟踪", "replay")
    # 后续 5 天 process_pending 撮合开仓
    for d in dates[3:8]:
        ledger.process_pending(conn, d)
    assert len(ledger.positions.get("000001", [])) >= 1, "BUY 应已开仓"
    l1 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind LIKE 'l1_%'").fetchone()[0]
    assert l1 >= 1, "L1 应已结算"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_signal_outcome_replay.py -v`
Expected: FAIL（settle_l1/buy 尚未在正确路径工作——此测试作为 replay 主循环的组装验证，若 Task 3 完成则可能已过；若已过，删除该测试改为验证 replay() 主函数）

说明：本 Task 的核心交付是 `replay()` 主函数（把"每日跑策略→风控→L1→Ledger"串起来）。Task 4 的测试重点是组装正确性，上面测试若在 Task 3 后即绿，则改写为直接验证 `replay()` 可运行且输出统计 dict（用 FakeSt 替换 runner 注册表）。

- [ ] **Step 3: Implement**

```python
def _daily_snapshot(conn, date: str, codes: list[str]) -> pd.DataFrame:
    """当日快照（filter_signals 需要）：close/pct_change/volume/amount/is_st"""
    rows = []
    for code in codes:
        row = conn.execute("""SELECT d.close, d.pct_change, d.volume, d.amount, s.is_st
            FROM daily_kline d LEFT JOIN stock_info s ON d.code=s.code
            WHERE d.code=? AND d.date=?""", (code, date)).fetchone()
        if row:
            rows.append({'code': code, 'close': row[0], 'pct_change': row[1],
                         'volume': row[2], 'amount': row[3],
                         'is_st': row[4] or 0})
    return pd.DataFrame(rows)

def _daily_signals(conn, date: str, stocks: pd.DataFrame,
                   kline_all: dict, strategies: list) -> list[dict]:
    """对单日跑所有策略（数据截至 date），返回原始信号"""
    out = []
    for _, st_row in stocks.iterrows():
        code = st_row['code']
        df = kline_all.get(code)
        if df is None or df.empty:
            continue
        df_t = df[df['date'] <= date]
        if len(df_t) < 60:
            continue
        for st in strategies:
            try:
                sig = st.run(code, st_row['name'], df_t.reset_index(drop=True))
                if sig:
                    out.append({**sig, 'strategy': st.name})
            except Exception as e:
                print(f"⚠️ [回放] {code} {st.name} 异常: {e}")
    return out

def replay(conn) -> dict:
    """回放 REPLAY_YEARS 年：每日跑策略→风控→L1 结算→Ledger 撮合

    已知简化（与生产差异）：
      - 不含大盘择时降权（v3 降权只调 strength，等权验证不受影响）
      - 成分股为当前沪深300（历史成分漂移为已知局限）
    """
    from engine.runner import STRATEGY_REGISTRY
    from data_fetcher.cleaner import get_all_stocks
    from engine.risk_filter import filter_signals

    init_outcome_table(conn)
    stocks = get_all_stocks()
    if stocks.empty:
        print("❌ 股票池为空"); return {'signals': 0, 'l1': 0, 'l2_close': 0}
    codes = stocks['code'].tolist()

    # 预加载全量 K 线（回放主循环复用，按日截断不偷看未来）
    kline_all = {}
    for code in codes:
        kdf = _kline(conn, code)
        if not kdf.empty:
            kline_all[code] = kdf

    strategies = [cls() for cls in STRATEGY_REGISTRY.values()]
    dates = _trading_dates(conn)
    # 回放起点：REPLAY_YEARS 年前的交易日（不含最近——最近信号窗口未到无需回放？
    # 不：回放覆盖全部可用历史，L1 未到期的窗口自然跳过）
    cutoff = dates[0]  # 简化：全历史回放（数据本就 ~1 年+）
    replay_dates = [d for d in dates]

    ledger = Ledger()
    n_sig = 0
    for t, date in enumerate(replay_dates):
        # 1) 成交昨日 pending（今日开盘价）
        ledger.process_pending(conn, date)
        # 2) 止损/到期检查（今日收盘判定 → pending）
        ledger.check_stop_loss(conn, date)
        ledger.check_timeout(conn, date)
        # 3) 生成今日信号
        snapshot = _daily_snapshot(conn, date, codes)
        if snapshot.empty:
            continue
        raw = _daily_signals(conn, date, stocks, kline_all, strategies)
        if not raw:
            continue
        passed, rejected = filter_signals(raw, snapshot)
        for sig in passed:
            n_sig += 1
            code = sig['stock_code']
            # 风控通过 → L1 即时结算 + L2 排队开仓/平仓
            if sig['action'] == 'BUY':
                so_ref = sig.get('price') or 0
                settle_l1(conn, date, code, sig.get('stock_name', ''), 
                          sig['strategy'], 'BUY', 'replay')
                ledger.buy(conn, date, code, sig.get('stock_name', ''),
                           sig['strategy'], 'replay')
            else:  # SELL
                settle_l1(conn, date, code, sig.get('stock_name', ''),
                          sig['strategy'], 'SELL', 'replay')
                ledger.on_signal(conn, date, code, sig.get('stock_name', ''),
                                 sig['strategy'], 'SELL', 'replay')
    # 4) 尾日结算剩余 pending
    if replay_dates:
        ledger.process_pending(conn, replay_dates[-1])

    l1 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind LIKE 'l1_%' AND source='replay'").fetchone()[0]
    l2 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close' AND source='replay'").fetchone()[0]
    print(f"✅ 回放完成: 信号 {n_sig} 条 | L1 结算 {l1} | L2 平仓 {l2}")
    return {'signals': n_sig, 'l1': l1, 'l2_close': l2}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_signal_outcome_replay.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/signal_outcome.py tests/test_signal_outcome_replay.py
git commit -m "feat: 回放引擎——按日滚动复用生产策略，补齐L1/L2历史样本"
```

---

### Task 5: 每日增量结算入口（run_daily）

**Files:**
- Modify: `scripts/signal_outcome.py`（main 分支）
- Test: `tests/test_signal_outcome_daily.py`（新建）

**Interfaces:**
- Consumes: Task 1-4 全部；`engine.signal_store` 的信号表（只读）
- Produces:
  - `run_daily(conn) -> dict`：主入口——查 signal_history 中 passed 且尚未结算的真实信号（source='real'）→ L1 结算；L2 每日恢复持仓（从 outcome kind='l2_open' 读取未平仓行）→ on_signal/buy + check_stop_loss/check_timeout + process_pending（今日开盘成交昨日信号）→ 写回
  - L2 状态持久化：`_load_ledger(conn) -> Ledger`（读 l2_open 行重建 positions——注意：l2_open 行即持仓，l2_close 已从队列移除；FIFO 顺序按 id）
  - 简化决策：每日增量模式下 L2 持仓**用真实信号撮合**（source='real'），与回放（replay）互不相干——统计页分开或合并看都行（信号日不重叠，UNIQUE 含 source 不冲突）
  - 未结算判定：L1 用"该信号是否已写入 l1_5d"不可靠（5d 可能未到期）→ 改为"只要窗口可能到期就尝试，INSERT OR IGNORE 幂等兜底"——**每日全量尝试已登记信号即可，幂等保证正确**
  - 真实信号登记表：signal_history 全量 passed 信号 = 登记源（每次全量尝试 L1；窗口已过的早写入，幂等）

- [ ] **Step 1: Write the failing test**

```python
"""每日增量结算测试"""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    conn = sqlite3.connect(path)
    # 简化复用：建齐全表
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    conn.execute("""CREATE TABLE index_daily (date TEXT PRIMARY KEY,
        open REAL, close REAL, high REAL, low REAL, volume REAL, amount REAL)""")
    conn.execute("""CREATE TABLE signal_history (id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, name TEXT, strategy TEXT, action TEXT,
        strength REAL, reason TEXT, price REAL, status TEXT DEFAULT 'passed',
        filter_reason TEXT)""")
    conn.execute("""CREATE TABLE stock_info (code TEXT PRIMARY KEY, name TEXT,
        market TEXT, listing_date TEXT, is_st INTEGER DEFAULT 0, updated_at TEXT)""")
    so.init_outcome_table(conn)
    # 30 个交易日行情（000001 + 指数），价格缓慢上行 10→11.5
    import datetime as dt
    d = dt.date(2026, 7, 1); p = 10.0
    for i in range(30):
        ds = d.isoformat()
        conn.execute("INSERT INTO daily_kline VALUES ('000001',?,?,?,?,?,1e7,1e8,0,0)",
                     (ds, p, p, p, p))
        conn.execute("INSERT OR REPLACE INTO index_daily (date, close) VALUES (?,?)",
                     (ds, 3000 + i * 10))
        p = round(p * 1.005, 4)
        d += dt.timedelta(days=1)
        if d.weekday() >= 5:
            d += dt.timedelta(days=7 - d.weekday())
    conn.commit()
    yield conn
    conn.close()

def test_run_daily_settles_real_signals(env):
    """signal_history 有 20 天前的真实 BUY → run_daily 结算出 l1_5d/l1_10d/l1_20d"""
    conn = env
    dates = so._trading_dates(conn)
    sig_date = dates[0]
    conn.execute("""INSERT INTO signal_history (date, code, name, strategy, action,
        strength, reason, price, status) VALUES (?, '000001', '平安', '双均线趋势跟踪',
        'BUY', 0.8, '测试', 10.0, 'passed')""", (sig_date,))
    conn.commit()

    so.run_daily(conn)

    kinds = {r[0] for r in conn.execute(
        "SELECT kind FROM signal_outcome WHERE source='real'").fetchall()}
    assert 'l1_5d' in kinds and 'l1_10d' in kinds and 'l1_20d' in kinds, \
        f"三个窗口都应结算: {kinds}"

def test_run_daily_recent_signal_partial(env):
    """3 天前的新信号 → 只结算 l1_5d 尚未到期（无窗口写入），不报错"""
    conn = env
    dates = so._trading_dates(conn)
    sig_date = dates[-3]
    conn.execute("""INSERT INTO signal_history (date, code, name, strategy, action,
        strength, reason, price, status) VALUES (?, '000001', '平安', '双均线趋势跟踪',
        'BUY', 0.8, '测试', 10.0, 'passed')""", (sig_date,))
    conn.commit()
    so.run_daily(conn)
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='real'").fetchone()[0]
    assert cnt == 0, "窗口未到不应写入任何记录"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_outcome_daily.py -v`
Expected: FAIL（run_daily 不存在）

- [ ] **Step 3: Implement**（追加到 signal_outcome.py，main 分支改）

```python
def _real_signals(conn) -> list[dict]:
    """signal_history 中全部 passed 信号（L1 登记源）"""
    rows = conn.execute("""SELECT date, code, name, strategy, action, price
        FROM signal_history WHERE status='passed' AND action IN ('BUY','SELL')
        ORDER BY date""").fetchall()
    return [{'date': r[0], 'code': r[1], 'name': r[2], 'strategy': r[3],
             'action': r[4], 'price': r[5]} for r in rows]

def run_daily(conn) -> dict:
    """每日增量结算：真实信号 L1（幂等全量尝试）+ L2 撮合推进

    幂等性：L1 用 INSERT OR IGNORE，重复跑不重复写；
    L2 每日从 outcome 的 l2_open 行恢复持仓 → 推进 → 写回，重复跑天然安全
    （成交事件已写入的不会重复，pending 为空）。
    """
    init_outcome_table(conn)
    n_l1 = 0
    for sig in _real_signals(conn):
        kinds = settle_l1(conn, sig['date'], sig['code'], sig['name'],
                          sig['strategy'], sig['action'], 'real')
        n_l1 += len(kinds)

    # L2：恢复持仓 → 按今日信号推进（今日 = daily_kline 最新日）
    dates = _trading_dates(conn)
    if not dates:
        return {'l1': n_l1, 'l2_close': 0}
    today = dates[-1]
    ledger = _load_ledger(conn)
    # 今日新信号
    todays = [s for s in _real_signals(conn) if s['date'] == today]
    for sig in todays:
        if sig['action'] == 'BUY':
            ledger.buy(conn, today, sig['code'], sig['name'], sig['strategy'], 'real')
        else:
            ledger.on_signal(conn, today, sig['code'], sig['name'],
                             sig['strategy'], 'SELL', 'real')
    # 止损/到期检查（今日收盘）→ 次日开盘成交
    ledger.check_stop_loss(conn, today)
    ledger.check_timeout(conn, today)
    # 成交昨日信号（今日开盘价）——注意：真正的"今日新信号"要明日开盘才成交
    events = ledger.process_pending(conn, today)
    _save_ledger(conn, ledger, events)
    n_close = sum(1 for e in events if e['kind'] == 'l2_close')
    print(f"✅ 每日结算完成: L1 +{n_l1} | L2 平仓 {n_close} | 当前持仓 {sum(len(v) for v in ledger.positions.values())}")
    return {'l1': n_l1, 'l2_close': n_close}

def _load_ledger(conn) -> Ledger:
    """从 outcome l2_open 行恢复持仓（未平仓 = 无对应 l2_close 的开仓行）"""
    ledger = Ledger()
    rows = conn.execute("""SELECT o1.date, o1.code, o1.name, o1.strategy, o1.source,
        o1.exec_price FROM signal_outcome o1 WHERE o1.kind='l2_open' AND NOT EXISTS (
        SELECT 1 FROM signal_outcome o2 WHERE o2.kind='l2_close'
        AND o2.date=o1.date AND o2.code=o1.code AND o2.strategy=o1.strategy
        AND o2.source=o1.source AND o2.exit_reason!='sell')""").fetchall()
    # 简化：l2_open 行存在 = 持仓（l2_close 写入时从 positions 出队，但入库不删除 open 行）
    # 因此用"signal_date 后无同名 l2_close"判定过于复杂——改用显式方案：
    # Ledger 维护持仓期，写 open 行时记 signal_date；平仓事件记 signal_date 与 pos。
    # 重建时按 code 收集 open 行并按 id 顺序入队。
    for r in rows:
        pos = ledger._position(r[1], r[2], r[3], r[4], r[0], r[5])
        ledger.positions.setdefault(r[1], []).append(pos)
    return ledger

def _save_ledger(conn, ledger: Ledger, events: list[dict]) -> None:
    """开仓事件写库（l2_open 行）；平仓事件写库（l2_close 行）"""
    ledger.write_events(conn, events)
```

`_load_ledger` 的重建逻辑存在"开仓行写入后永远留在表里"的歧义——**修正设计**：l2_open 行只在开仓事件发生（process_pending 成交）时写入；平仓成交写入 l2_close。持仓 = 存在 l2_open 且无配对的 l2_close。配对键用事件自增 id 更稳，但表结构没有 open_id 列——**简化**：`_load_ledger` 的 NOT EXISTS 条件不足，改为给 l2_close 记录一个 `ref` 字段不可行（表结构已定）。

**工程决策（写入实现注释）**：持仓重建改为——`Ledger.save_state(conn)` 每次把**当前持仓全量写为 `kind='l2_state'` 行**（date=今天, exec_price, strategy, code），次日 `_load_ledger` 读最新的 l2_state 行重建 positions；平仓/开仓后覆盖写。这样状态持久化与结果表分离，简单无歧义。l2_open/l2_close 行仍写入作审计。

（Task 5 实现时按上面"l2_state 状态行"方案完成 `save_state`/`load_state` 两个方法并更新对应测试——测试聚焦 run_daily 的 L1 行为与幂等，L2 状态的跨日一致性在 Task 3 测试 + 本 Task 冒烟验证。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_outcome_daily.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/signal_outcome.py tests/test_signal_outcome_daily.py
git commit -m "feat: 每日增量结算——真实信号L1全量幂等结算+L2状态持久化推进"
```

---

### Task 6: 仪表盘「信号效果」页（outcome.html）

**Files:**
- Modify: `web/generate.py`
- Test: `tests/test_generate_outcome.py`（新建，仿 test_heatmap 风格）

**Interfaces:**
- Consumes: Task 2 的 `summary_l1`；新增 `summary_l2(conn, source=None)`（Task 6 内实现，放 signal_outcome.py）
  - `summary_l2(conn, source=None) -> list[dict]`：分策略 l2_close 汇总：笔数/胜率(pnl>0)/平均收益/平均持仓/等权累计（SUM(pnl)）
- Produces: `page_outcome(conn) -> str` HTML（复用 generate 的 `_page`/CSS 变量/面板风格）

- [ ] **Step 1: Write the failing tests**

```python
"""outcome 页渲染测试"""
import sqlite3, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from web.generate import page_outcome
from scripts import signal_outcome as so

@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    c = sqlite3.connect(path)
    so.init_outcome_table(c)
    # 造两条已结算记录
    c.execute("""INSERT INTO signal_outcome (date, code, name, strategy, action,
        source, kind, pnl, excess) VALUES
        ('2026-08-01','000001','平安','双均线趋势跟踪','BUY','real','l1_10d',2.0,1.5),
        ('2026-08-01','000001','平安','双均线趋势跟踪','SELL','real','l2_close',3.0,NULL)""")
    c.commit()
    yield c
    c.close()

def test_page_outcome_renders(conn):
    html = page_outcome(conn)
    assert '信号效果' in html
    assert '双均线趋势跟踪' in html  # L1 表有策略行
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_generate_outcome.py -v`
Expected: FAIL（page_outcome 不存在）

- [ ] **Step 3: Implement**

signal_outcome.py 加：

```python
def summary_l2(conn, source: str | None = None) -> list[dict]:
    """分策略 L2 平仓汇总：笔数/胜率/平均收益/平均持仓/等权累计"""
    q = """SELECT strategy, COUNT(*) as n,
        SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins,
        AVG(pnl) as avg_pnl, AVG(hold_days) as avg_hold, SUM(pnl) as cum
        FROM signal_outcome WHERE kind='l2_close'"""
    params = []
    if source:
        q += " AND source=?"
        params.append(source)
    q += " GROUP BY strategy ORDER BY cum DESC"
    return [{'strategy': r[0], 'n': r[1], 'win_rate': round(r[2]/r[1]*100, 1) if r[1] else 0,
             'avg_pnl': round(r[3], 2) if r[3] is not None else 0,
             'avg_hold': round(r[4], 1) if r[4] is not None else 0,
             'cum': round(r[5], 1) if r[5] is not None else 0}
            for r in conn.execute(q, params).fetchall()]
```

generate.py 加（导航链接加在 index 页顶部，参考现有页面注册方式，在 `page_index` 头部导航 ul 加 `<a href="outcome.html">信号效果</a>`；文件写入列表加 `('outcome.html', page_outcome(conn))`）：

```python
def page_outcome(conn):
    from scripts.signal_outcome import summary_l1, summary_l2
    body = []
    body.append('<h2>📈 信号效果</h2>')
    # L1 表
    rows = summary_l1(conn)
    body.append('<div class="panel"><div class="panel-hd">L1 信号预测力（10日窗口·命中=跑赢沪深300）</div><div class="panel-bd">')
    if not rows:
        body.append('<div class="empty">暂无结算数据（回放或每日结算后出现）</div>')
    else:
        body.append('<table><tr><th>策略</th><th>信号数</th><th>胜率</th><th>平均超额</th></tr>')
        for r in rows:
            flag = '✅' if (r['win_rate'] or 0) >= 55 else ('⚠️' if (r['win_rate'] or 0) < 45 else '❓')
            body.append(f"<tr><td>{r['strategy']}</td><td>{r['total']}</td>"
                        f"<td>{r['win_rate']}% {flag}</td><td>{r['avg_excess']}%</td></tr>")
        body.append('</table>')
    body.append('</div></div>')
    # L2 表
    rows2 = summary_l2(conn)
    body.append('<div class="panel"><div class="panel-hd">L2 等权账本（BUY→平仓完整周期）</div><div class="panel-bd">')
    if not rows2:
        body.append('<div class="empty">暂无平仓交易</div>')
    else:
        body.append('<table><tr><th>策略</th><th>笔数</th><th>胜率</th><th>平均收益</th><th>平均持仓(天)</th><th>等权累计</th></tr>')
        for r in rows2:
            body.append(f"<tr><td>{r['strategy']}</td><td>{r['n']}</td><td>{r['win_rate']}%</td>"
                        f"<td>{r['avg_pnl']}%</td><td>{r['avg_hold']}</td><td>{r['cum']}%</td></tr>")
        body.append('</table>')
    body.append('</div></div>')
    return _page('信号效果', 'outcome.html', '\n'.join(body))
```

（实际实现需在 `build()` 的 pages 列表加 `('outcome.html', page_outcome(conn))`，并确认 `_page` 签名/导航结构后适配；`page_index` 导航区加链接方式参照 `market.html`/`history.html` 现有链接。）

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_generate_outcome.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/generate.py tests/test_generate_outcome.py scripts/signal_outcome.py
git commit -m "feat: 仪表盘信号效果页——L1命中率表+L2等权账本表"
```

---

### Task 7: cron/文档同步 + 全量验证

**Files:**
- Modify: `setup.sh`（cron 块）、`CLAUDE.md`、`docs/架构/服务器部署指南.md`（cron 说明处）

**Interfaces:**
- 无新接口

- [ ] **Step 1: Update setup.sh cron 块**

在 `# 21:05 — 市场日报` 行前插入：

```bash
# 21:02 — 信号效果结算（L1窗口/L2账本推进）
2 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python scripts/signal_outcome.py >> logs/signal_outcome.log 2>&1
```

CLAUDE.md 更新（两处）：`Server cron MUST include` 列表加 signal_outcome.py (21:02)；`Known Deployments` 的 Cron 行加 `2 21 * * 1-5`。服务器部署指南同理。Key Commands 加：

```bash
python scripts/signal_outcome.py           # 每日信号结算（或 cron 自动）
python scripts/signal_outcome.py --replay  # 首次回放补齐历史
```

- [ ] **Step 2: 冒烟验证（真实数据跑一遍）**

```bash
PYTHONPATH=. python scripts/signal_outcome.py --replay
```

Expected: 输出回放统计（信号 N 条 | L1 结算 N | L2 平仓 N），无异常栈。
再跑一次验证幂等：`SELECT COUNT(*) FROM signal_outcome` 两次一致。

- [ ] **Step 3: 全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全绿（71 + 新增 ≈ 86）

- [ ] **Step 4: Commit**

```bash
git add setup.sh CLAUDE.md docs/架构/服务器部署指南.md
git commit -m "docs: signal_outcome cron+命令说明同步"
```

---

## 自审备注（写给执行者）

- 若 Task 3 的 FIFO 测试与"SELL 立即出队"语义冲突，以**测试断言**为准调整实现（SELL 匹配时从 positions 出队、pending 排队成交）
- `check_stop_loss` 实现请以 Task 3 Step 3 末尾的"正确代码"块为准（前一块含防呆占位，勿照抄）
- Task 5 的 L2 持久化若 l2_open/l2_close 重建歧义过大，改用 `Ledger.save_state/load_state`（kind='l2_state' 快照行）方案并在 commit 说明
- 每个 Task 必须独立绿 + commit 后再进下一个
