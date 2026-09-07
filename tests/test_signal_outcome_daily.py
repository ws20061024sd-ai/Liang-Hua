"""每日增量结算入口测试（Task 5）—— run_daily + L2 状态快照持久化

覆盖：
- L1：signal_history 真实信号全量幂等结算；窗口未到期不写入
- L2 跨日推进：t 日信号 → t+1 开盘成交（信号当日不成交）
- L2 状态快照：save_state/load_state 往返保留 FIFO 顺序、同 code 同策略多笔持仓
- 幂等：同日重复 run_daily 不重复成交/登记/写状态
"""
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


def _prices(n):
    """与 env 完全一致的逐日价格序列（同款逐次 round）"""
    out, p = [], 10.0
    for _ in range(n):
        out.append(p)
        p = round(p * 1.005, 4)
    return out

def _clip(conn, dates, upto):
    """行情只保留到 dates[upto]（模拟"数据只到某日"）"""
    conn.execute("DELETE FROM daily_kline WHERE date > ?", (dates[upto],))
    conn.execute("DELETE FROM index_daily WHERE date > ?", (dates[upto],))
    conn.commit()

def _extend(conn, dates, upto):
    """补齐行情到 dates[upto]（价格与 env 一致，已有行不覆盖）"""
    prices = _prices(upto + 1)
    for i in range(upto + 1):
        p = prices[i]
        conn.execute("INSERT OR IGNORE INTO daily_kline VALUES ('000001',?,?,?,?,?,1e7,1e8,0,0)",
                     (dates[i], p, p, p, p))
        conn.execute("INSERT OR REPLACE INTO index_daily (date, close) VALUES (?,?)",
                     (dates[i], 3000 + i * 10))
    conn.commit()

def _signal(conn, d, action="BUY"):
    conn.execute("""INSERT INTO signal_history (date, code, name, strategy, action,
        strength, reason, price, status) VALUES (?, '000001', '平安', '双均线趋势跟踪',
        ?, 0.8, '测试', 10.0, 'passed')""", (d, action))
    conn.commit()

def _open_at(conn, d):
    return conn.execute(
        "SELECT open FROM daily_kline WHERE code='000001' AND date=?", (d,)).fetchone()[0]


# ── L1：真实信号全量结算 ────────────────────────────────────────────────

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

def test_run_daily_l1_idempotent(env):
    """重复 run_daily → L1 不重复写（INSERT OR IGNORE 幂等）"""
    conn = env
    dates = so._trading_dates(conn)
    _signal(conn, dates[0])
    so.run_daily(conn)
    cnt1 = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE source='real'").fetchone()[0]
    assert cnt1 == 3, f"三窗口都应结算: {cnt1}"
    so.run_daily(conn)
    cnt2 = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE source='real'").fetchone()[0]
    assert cnt2 == cnt1, "重复 run_daily 不得重复写 L1"


# ── L2：状态快照跨日推进 ────────────────────────────────────────────────

def test_daily_l2_buy_fills_next_open_and_idempotent_rerun(env):
    """BUY@d4 当日只排队；d5 的 run_daily 以 d5 开盘价成交；
    同数据重跑幂等（状态已推进到当日 → L2 跳过）"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 4)
    _signal(conn, dates[4])            # d4 收盘信号
    so.run_daily(conn)                 # 晚间 d4：登记 → 排队，不成交
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 0, "信号当日不得成交"
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_state'"
        ).fetchone()[0] == 1, "应写入状态快照（排队信息）"

    _extend(conn, dates, 5)
    so.run_daily(conn)                 # 晚间 d5：开盘成交昨日 BUY
    row = conn.execute("SELECT date, exec_price FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()
    assert row is not None, "应已成交开仓"
    assert row[0] == dates[4], f"开仓事件日期=信号日: {row}"
    exp = _open_at(conn, dates[5])
    assert abs(row[1] - exp) < 1e-9, f"应以 d5 开盘价成交: {row} vs {exp}"

    # 幂等重跑：不重复成交、不新增状态行
    n_state = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_state'"
        ).fetchone()[0]
    so.run_daily(conn)
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 1, "重跑不得重复成交"
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_state'"
        ).fetchone()[0] == n_state, "重跑不得重复写状态快照"

def test_daily_l2_sell_closes_next_open(env):
    """持仓后 SELL@d5（d5 当日登记）→ FIFO 出队排队，d6 开盘平仓成交"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 4)
    _signal(conn, dates[4])            # BUY@d4
    so.run_daily(conn)                 # d4 晚：登记 BUY → 排队
    _extend(conn, dates, 5)
    _signal(conn, dates[5], action="SELL")   # SELL@d5（d5 晚 run 前入库）
    so.run_daily(conn)                 # d5 晚：开盘成交 BUY@d4 + 登记 SELL@d5
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 1, "BUY 应已于 d5 开盘成交"
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'"
        " AND source='real'").fetchone()[0] == 0, "SELL 当日只排队不成交"

    _extend(conn, dates, 6)
    so.run_daily(conn)                 # d6 晚：开盘平仓 SELL
    c = conn.execute("SELECT exec_price, exit_reason FROM signal_outcome"
        " WHERE kind='l2_close' AND source='real'").fetchone()
    assert c is not None and c[1] == 'sell', f"应有 sell 平仓: {c}"
    exp = _open_at(conn, dates[6])
    assert abs(c[0] - exp) < 1e-9, f"平仓价应为 d6 开盘价: {c} vs {exp}"

def test_daily_l2_stop_loss_fills_next_open(env):
    """持仓后某日收盘跌破止损线 → 当日只排队；次日开盘成交（exit_reason=stop_loss）"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 4)
    _signal(conn, dates[4])            # BUY@d4
    so.run_daily(conn)                 # d4 晚：排队
    _extend(conn, dates, 5)
    so.run_daily(conn)                 # d5 晚：开盘成交 → 持仓（价格上行不触发）
    # d6 收盘暴跌至 9.0（峰值 ~10.25×0.95≈9.74 > 9.0 → 触发止损）
    _extend(conn, dates, 6)
    conn.execute("INSERT OR REPLACE INTO daily_kline VALUES"
        " ('000001',?,9.0,9.0,9.0,9.0,1e7,1e8,0,0)", (dates[6],))
    conn.commit()
    so.run_daily(conn)                 # d6 晚：收盘判定止损 → 排队次日
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'"
        " AND source='real'").fetchone()[0] == 0, "止损当日不成交"

    _extend(conn, dates, 7)
    so.run_daily(conn)                 # d7 晚：开盘成交止损单
    c = conn.execute("SELECT exec_price, exit_reason FROM signal_outcome"
        " WHERE kind='l2_close' AND source='real'").fetchone()
    assert c is not None and c[1] == 'stop_loss', f"应有 stop_loss 平仓: {c}"
    exp = _open_at(conn, dates[7])
    assert abs(c[0] - exp) < 1e-9, f"平仓价应为 d7 开盘价: {c} vs {exp}"

    # 后续正常推进：无新事件
    _extend(conn, dates, 8)
    so.run_daily(conn)
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'"
        " AND source='real'").fetchone()[0] == 1

def test_ledger_state_roundtrip_preserves_fifo_and_pending(env):
    """save_state → load_state：多笔持仓 FIFO 顺序、pending 队列完整保留"""
    conn = env
    dates = so._trading_dates(conn)
    ledger = so.Ledger()
    ledger.buy(conn, dates[0], "000001", "平安", "动量突破策略", "real")
    ledger.process_pending(conn, dates[1])      # 第一笔 d1 开盘成交
    ledger.buy(conn, dates[1], "000001", "平安", "双均线趋势跟踪", "real")
    ledger.process_pending(conn, dates[2])      # 第二笔 d2 开盘成交
    assert len(ledger.positions["000001"]) == 2
    ledger.save_state(conn, dates[2])

    fresh = so.Ledger()
    prev = fresh.load_state(conn)
    assert prev == dates[2], f"状态日期应为最后快照日: {prev}"
    assert len(fresh.positions["000001"]) == 2
    assert [p['strategy'] for p in fresh.positions["000001"]] == \
        ["动量突破策略", "双均线趋势跟踪"], "FIFO 顺序应保留"
    assert [p['buy_date'] for p in fresh.positions["000001"]] == [dates[0], dates[1]]

def test_state_snapshot_keeps_same_code_strategy_multi_positions(env):
    """同 code 同 strategy 两笔同时持仓 → 快照往返不丢持仓（单行 JSON 快照
    是每笔一行方案的替代——后者会撞 UNIQUE(date,code,strategy,...) 静默丢行）"""
    conn = env
    dates = so._trading_dates(conn)
    ledger = so.Ledger()
    for d0, d1 in ((dates[0], dates[1]), (dates[1], dates[2])):
        ledger.buy(conn, d0, "000001", "平安", "动量突破策略", "real")
        ledger.process_pending(conn, d1)
    assert len(ledger.positions["000001"]) == 2
    ledger.save_state(conn, dates[2])
    fresh = so.Ledger()
    fresh.load_state(conn)
    assert len(fresh.positions["000001"]) == 2, "同 code/strategy 双持仓不得丢"
    assert [p['buy_date'] for p in fresh.positions["000001"]] == [dates[0], dates[1]]
