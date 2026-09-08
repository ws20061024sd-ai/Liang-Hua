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

def test_kline_full_history(conn):
    """单股全量 K 线：date 升序，含 open/close 列（计划任务 2 的输入依赖）"""
    _mk_kline(conn)
    df = so._kline(conn, "000001")
    assert list(df.columns) == ["date", "open", "close"]
    assert list(df["date"]) == ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
    assert float(df.iloc[0]["open"]) == 10.0


# ── Task 2: L1 固定窗口结算 ──────────────────────────────────────────────

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

# ── 最终审查 I-1：v1 → v2 schema 迁移（UNIQUE 纳入 exit_reason）────────────

def _mk_v1_outcome_table(conn):
    """按 v1 定义手工建表（UNIQUE 无 exit_reason，模拟已上线的旧库）"""
    conn.execute("""CREATE TABLE signal_outcome (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, code TEXT, name TEXT,
        strategy TEXT, action TEXT,
        source TEXT, kind TEXT,
        ref_price REAL, exec_price REAL, exit_price REAL,
        pnl REAL, excess REAL, hold_days INTEGER,
        exit_reason TEXT, status TEXT DEFAULT 'done',
        UNIQUE(date, code, strategy, action, kind, source))""")

def test_migrate_v1_to_v3_preserves_rows_and_unique(conn):
    """迁移后：行全保留（l2_state JSON 负载不碰）、l1 NULL exit_reason 归一为 ''、
    open_date 新列走 DEFAULT ''、UNIQUE 变 8 列（exit_reason + open_date）——
    同 tuple 双平仓仅差 exit_reason 或开仓日都可并存、同键重复插入仍被
    OR IGNORE 幂等去重"""
    _mk_v1_outcome_table(conn)
    payload = '{"__v": 1, "positions": {}, "pending": []}'
    conn.executemany("""INSERT INTO signal_outcome (date, code, name, strategy,
            action, source, kind, ref_price, pnl, exit_reason) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        [('2026-07-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'replay',
          'l1_10d', 10.0, 5.0, None),                       # v1 历史 l1 行：NULL
         ('2026-07-05', '000001', '平安', '双均线趋势跟踪', 'SELL', 'replay',
          'l2_close', None, None, 'sell'),                  # v1 历史平仓行
         ('2026-07-06', None, None, None, None, 'real',
          'l2_state', None, None, payload)])                # 状态快照行
    conn.commit()
    so._migrate_outcome_schema(conn)
    idx = [r for r in conn.execute(
        "PRAGMA index_list('signal_outcome')").fetchall() if r[3] == 'u']
    assert len(idx) == 1, f"应只有 UNIQUE 约束索引: {idx}"
    col_by_cid = {r[0]: r[1] for r in conn.execute(
        "PRAGMA table_info('signal_outcome')").fetchall()}
    cols = [col_by_cid[r[1]] for r in conn.execute(
        f'PRAGMA index_info("{idx[0][1]}")').fetchall()]
    assert cols == ['date', 'code', 'strategy', 'action', 'kind', 'source',
                    'exit_reason', 'open_date'], f"v3 键应为 8 列: {cols}"
    rows = conn.execute("SELECT date, kind, exit_reason, open_date FROM signal_outcome"
        " ORDER BY id").fetchall()
    assert len(rows) == 3, f"迁移必须保留全部 3 行: {rows}"
    assert rows[0] == ('2026-07-01', 'l1_10d', '', ''), \
        f"l1 行 NULL exit_reason 应归一为 ''（保持 OR IGNORE 幂等）: {rows[0]}"
    assert rows[1] == ('2026-07-05', 'l2_close', 'sell', ''), \
        f"历史平仓行原值保留、open_date 走默认 '': {rows[1]}"
    assert rows[2][2] == payload, f"l2_state JSON 负载不得被改动: {rows[2][2][:40]}"

    # 迁移后幂等语义不破：同键（含 '' 占位）重复插被 IGNORE
    conn.execute("INSERT OR IGNORE INTO signal_outcome (date, code, name, strategy,"
        " action, source, kind, ref_price, pnl, exit_reason) VALUES"
        " ('2026-07-01','000001','平安','双均线趋势跟踪','BUY','replay','l1_10d',"
        " 10.0, 5.0, '')")
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0] == 3, \
        "重复 L1 结算不得因 NULL→'' 迁移而重复写行"
    # v3 键让同日同股同策略双平仓并存：仅差 exit_reason（sell/stop_loss）……
    conn.execute("INSERT OR IGNORE INTO signal_outcome (date, code, name, strategy,"
        " action, source, kind, exec_price, pnl, hold_days, exit_reason, open_date)"
        " VALUES ('2026-07-05','000001','平安','双均线趋势跟踪','SELL','replay',"
        " 'l2_close', 10.8, 3.0, 2, 'stop_loss', '2026-07-02')")
    # ……或仅差 open_date（跨策略双 SELL 平同 strategy 两笔，复审残余）
    conn.execute("INSERT OR IGNORE INTO signal_outcome (date, code, name, strategy,"
        " action, source, kind, exec_price, pnl, hold_days, exit_reason, open_date)"
        " VALUES ('2026-07-05','000001','平安','双均线趋势跟踪','SELL','replay',"
        " 'l2_close', 10.8, 3.0, 2, 'sell', '2026-07-03')")
    n = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'"
        ).fetchone()[0]
    assert n == 3, f"三种平仓事件（sell+stop_loss+异开仓日 sell）都应落库: {n}"

def test_init_outcome_table_migrates_existing_v1(conn):
    """init_outcome_table 对已存在的 v1 表（CREATE IF NOT EXISTS 跳过建表）
    自动执行迁移——本地 58068 行库与服务器旧库无感升级路径"""
    _mk_v1_outcome_table(conn)
    conn.execute("INSERT INTO signal_outcome (date, code, name, strategy, action,"
        " source, kind, ref_price, pnl) VALUES ('2026-07-01','000001','平安',"
        " '双均线趋势跟踪','BUY','replay','l1_10d',10.0,5.0)")
    conn.commit()
    so.init_outcome_table(conn)
    idx = [r for r in conn.execute(
        "PRAGMA index_list('signal_outcome')").fetchall() if r[3] == 'u']
    col_by_cid = {r[0]: r[1] for r in conn.execute(
        "PRAGMA table_info('signal_outcome')").fetchall()}
    cols = [col_by_cid[r[1]] for r in conn.execute(
        f'PRAGMA index_info("{idx[0][1]}")').fetchall()]
    assert 'exit_reason' in cols and 'open_date' in cols, "init 后应为 v3"
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0] == 1
    # 迁移幂等：再跑一次不再重建（行不翻倍）
    so.init_outcome_table(conn)
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0] == 1

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
