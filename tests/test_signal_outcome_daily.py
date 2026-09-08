"""每日增量结算入口测试（Task 5）—— run_daily + L2 状态快照持久化

覆盖：
- L1：signal_history 真实信号全量幂等结算；窗口未到期不写入
- L2 跨日推进：t 日信号 → t+1 开盘成交（信号当日不成交）
- L2 状态快照：save_state/load_state 往返保留 FIFO 顺序、同 code 同策略多笔持仓
- 幂等：同日重复 run_daily 不重复成交/登记/写状态
"""
import sqlite3, sys, os, json
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
    # 注：空账本状态水印行（kind='l2_state'）每 run 必落——结算记录计数须排除
    cnt = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='real'"
        " AND kind != 'l2_state'").fetchone()[0]
    assert cnt == 0, "窗口未到不应写入任何结算记录"

def test_run_daily_l1_idempotent(env):
    """重复 run_daily → L1 不重复写（INSERT OR IGNORE 幂等）"""
    conn = env
    dates = so._trading_dates(conn)
    _signal(conn, dates[0])
    so.run_daily(conn)
    cnt1 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='real'"
        " AND kind != 'l2_state'").fetchone()[0]
    assert cnt1 == 3, f"三窗口都应结算: {cnt1}"
    so.run_daily(conn)
    cnt2 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='real'"
        " AND kind != 'l2_state'").fetchone()[0]
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


# ── 晚到信号补登记（状态已推进到当日 + 当日信号晚入库，审查发现）───────────

def _state_payload(conn):
    """当前 l2_state 快照的 JSON 负载"""
    row = conn.execute("SELECT exit_reason FROM signal_outcome WHERE kind='l2_state'"
        " ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "应有 l2_state 状态行"
    return json.loads(row[0])

def test_daily_l2_late_buy_registered_after_state_advanced(env):
    """状态已推进到 d4（持仓成交在库）后，d4 的 BUY 信号才写入 signal_history
    （run.py 下载慢/失败后手动补跑的真实场景）→ 补登记排队、自然成交日=下一交易日：
    当日不得错误成交（不 lookahead 到当日开盘）；同日重复 run_daily 幂等不重复登记"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 3)
    _signal(conn, dates[3])                  # BUY@d3
    so.run_daily(conn)                       # d3 晚：登记 BUY@d3 → 排队
    _extend(conn, dates, 4)
    so.run_daily(conn)                       # d4 晚：d3 排队单于 d4 开盘成交 → 持仓
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 1, "前置：d3 开仓应已成交在库"

    _signal(conn, dates[4])                  # 晚到：状态推进到 d4 之后 d4 信号才入库
    so.run_daily(conn)                       # 状态==今日 → 补登记分支：只排队不成交
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 1, "补登记不得在当日开盘错误成交"
    st = _state_payload(conn)
    opens = [e for e in st['pending'] if e['kind'] == 'l2_open']
    assert [e['date'] for e in opens] == [dates[4]], \
        f"晚到 BUY 应补登记进 pending: {st['pending']}"

    so.run_daily(conn)                       # 幂等：同日再跑不重复登记
    st = _state_payload(conn)
    assert sum(1 for e in st['pending'] if e['kind'] == 'l2_open'
               and e['date'] == dates[4]) == 1, "重复 run_daily 不得重复登记"

    _extend(conn, dates, 5)
    so.run_daily(conn)                       # d5 晚：补登记单于 d5 开盘成交（自然成交日）
    rows = conn.execute("SELECT date, exec_price FROM signal_outcome"
        " WHERE kind='l2_open' AND source='real' ORDER BY date").fetchall()
    assert [r[0] for r in rows] == [dates[3], dates[4]], \
        f"两笔开仓（d3 正常推进 + d4 补登记）应都在账本: {rows}"
    assert abs(rows[1][1] - _open_at(conn, dates[5])) < 1e-9, \
        f"补登记开仓应以 d5 开盘价成交: {rows[1]}"

def test_daily_l2_late_sell_closes_next_open(env):
    """持仓在库后 SELL@d4 晚入库（状态已推进到 d4）→ 补登记 FIFO 出队排队次日
    开盘平仓：当日不成交；重跑幂等；自然成交日=d5（exit sell）"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 3)
    _signal(conn, dates[3])                  # BUY@d3
    so.run_daily(conn)
    _extend(conn, dates, 4)
    so.run_daily(conn)                       # d4：开盘成交 BUY@d3 → 持仓 1 笔

    _signal(conn, dates[4], action="SELL")   # 晚到 SELL@d4
    so.run_daily(conn)                       # 状态==今日 → 补登记：出队排队，不成交
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'"
        " AND source='real'").fetchone()[0] == 0, "补登记 SELL 当日不得成交"
    st = _state_payload(conn)
    sells = [e for e in st['pending'] if e['kind'] == 'l2_close'
             and e['date'] == dates[4]]
    assert len(sells) == 1 and sells[0]['exit_reason'] == 'sell', \
        f"晚到 SELL 应排队 l2_close(sell): {st['pending']}"
    assert not st['positions'].get('000001'), "FIFO 出队后持仓应清空"

    so.run_daily(conn)                       # 幂等
    st = _state_payload(conn)
    assert sum(1 for e in st['pending'] if e['kind'] == 'l2_close'
               and e['date'] == dates[4]) == 1, "重复 run_daily 不得重复平仓排队"

    _extend(conn, dates, 5)
    so.run_daily(conn)                       # d5：开盘平仓
    c = conn.execute("SELECT exec_price, exit_reason FROM signal_outcome"
        " WHERE kind='l2_close' AND source='real'").fetchone()
    assert c is not None and c[1] == 'sell', f"应有 sell 平仓: {c}"
    assert abs(c[0] - _open_at(conn, dates[5])) < 1e-9, \
        f"补登记 SELL 应以 d5 开盘价平仓: {c}"


# ── 跨日晚到信号补登记（最终审查 I-2 竞态：状态已越过信号日 ≥1 天）─────────

def test_daily_l2_cross_day_late_signal_fills_two_days_later(env):
    """真实竞态：d2 晚 run_daily 推进状态到 d2 时 d2 信号尚未入库（run.py 慢、
    21:05 才写入），次日（d3）才可见——此时 state(d2) < today(d3)，catch-up 循环
    只登记 >state_date 的信号，d2 信号永不进 L2 账本（整日样本丢失窗口）。
    修复：对 state_date 那天的未登记信号补登记——排队不回溯成交（d3 开盘已在
    catch-up 处理过），自然成交日 = 下一交易日（d4 开盘成交）；重复运行幂等"""
    conn = env
    dates = so._trading_dates(conn)
    # 前置：让状态推进到 d2，且 d2 晚账本非空（持仓在身 → 快照把 state_date 钉在 d2）
    _clip(conn, dates, 0)
    _signal(conn, dates[0])              # BUY@d0 正常入库
    so.run_daily(conn)                   # d0 晚：登记 BUY@d0 → 排队
    _extend(conn, dates, 1)
    so.run_daily(conn)                   # d1 晚：d0 排队单于 d1 开盘成交 → 持仓
    _extend(conn, dates, 2)
    so.run_daily(conn)                   # d2 晚：竞态——d2 信号未入库，状态推进到 d2
    st = _state_payload(conn)
    assert len(st['positions'].get('000001', [])) == 1, \
        f"前置：持仓应在（快照钉在 d2）: {st['positions']}"

    _signal(conn, dates[2])              # d2 的 BUY 晚到一天（d3 才入库）
    _extend(conn, dates, 3)
    so.run_daily(conn)                   # d3 晚：state(d2) < today(d3) → 补登记
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 1, "补登记不得在 d3 开盘回溯成交"
    st = _state_payload(conn)
    assert [e['date'] for e in st['pending'] if e['kind'] == 'l2_open'] == \
        [dates[2]], f"d2 晚到 BUY 应补登记进 pending: {st['pending']}"

    so.run_daily(conn)                   # 幂等：同日重跑不重复登记
    st = _state_payload(conn)
    assert sum(1 for e in st['pending'] if e['kind'] == 'l2_open'
               and e['date'] == dates[2]) == 1, "重复 run_daily 不得重复补登记"

    _extend(conn, dates, 4)
    so.run_daily(conn)                   # d4 晚：补登记单于 d4 开盘成交（自然成交日）
    rows = conn.execute("SELECT date, exec_price FROM signal_outcome"
        " WHERE kind='l2_open' AND source='real' ORDER BY date").fetchall()
    assert [r[0] for r in rows] == [dates[0], dates[2]], \
        f"两笔开仓（d0 正常推进 + d2 补登记）都应成交: {rows}"
    assert abs(rows[1][1] - _open_at(conn, dates[4])) < 1e-9, \
        f"补登记开仓应以 d4（X+2）开盘价成交: {rows[1]}"
    assert abs(rows[0][1] - _open_at(conn, dates[1])) < 1e-9, f"正常推进不受扰: {rows[0]}"


def test_daily_l2_cross_day_late_signal_empty_ledger_day(env):
    """复审 I-2 残余变体：竞态日账本为空（无持仓无 pending）——save_state 旧语义
    空账本不落行 → state_date 归 None → 补登记守卫（state_date is not None）跳过，
    晚到信号同样永不进 L2。修复：空账本也落状态行（date=已处理日水印）→ 空仓日
    竞态与持仓日同路径补登记，X 日信号晚到一天在 X+2 开盘成交"""
    conn = env
    dates = so._trading_dates(conn)
    _clip(conn, dates, 2)
    so.run_daily(conn)                   # d2 晚：空账本推进到 d2（无任何信号）
    st = _state_payload(conn)
    assert st['positions'] == {} and st['pending'] == [], \
        f"前置：d2 应为空账本水印行: {st}"

    _signal(conn, dates[2])              # d2 的 BUY 晚到一天（d3 才入库）
    _extend(conn, dates, 3)
    so.run_daily(conn)                   # d3 晚：state(d2) < today(d3) → 补登记
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'"
        " AND source='real'").fetchone()[0] == 0, "空账本日竞态的晚到信号也不得回溯成交"
    st = _state_payload(conn)
    assert [e['date'] for e in st['pending'] if e['kind'] == 'l2_open'] == \
        [dates[2]], f"晚到 BUY 应补登记进 pending: {st['pending']}"

    so.run_daily(conn)                   # 幂等：同日重跑不重复登记
    st = _state_payload(conn)
    assert sum(1 for e in st['pending'] if e['kind'] == 'l2_open'
               and e['date'] == dates[2]) == 1, "重复 run_daily 不得重复补登记"

    _extend(conn, dates, 4)
    so.run_daily(conn)                   # d4 晚：补登记单于 d4 开盘成交
    rows = conn.execute("SELECT date, exec_price FROM signal_outcome"
        " WHERE kind='l2_open' AND source='real'").fetchall()
    assert [r[0] for r in rows] == [dates[2]], f"空仓日竞态的晚到信号应成交: {rows}"
    assert abs(rows[0][1] - _open_at(conn, dates[4])) < 1e-9, \
        f"应以 d4（X+2）开盘价成交: {rows[0]}"


def test_daily_no_phantom_close_after_cross_strategy_sell(env):
    """复审幻影回归：X 日两策略同日 SELL（FIFO 各平掉 M1 的两笔，跨策略消费），
    次日正常运行不得把已消费的 SELL 当"晚到未登记"再次出队（l2_close 行 strategy
    = 被平 BUY 的策略 ≠ SELL 信号策略，DB 反推 seen 会错配 → 幻影第三次平仓）"""
    conn = env
    dates = so._trading_dates(conn)

    def sig(d, action, strategy):
        conn.execute("""INSERT INTO signal_history (date, code, name, strategy,
            action, strength, reason, price, status) VALUES (?, '000001', '平安',
            ?, ?, 0.8, '测试', 10.0, 'passed')""", (d, strategy, action))
        conn.commit()

    # 三笔持仓：A(M1)@d0、B(M1)@d1、C(M2)@d2（逐日正常登记、次日开盘成交）
    _clip(conn, dates, 0)
    sig(dates[0], "BUY", "双均线趋势跟踪")
    so.run_daily(conn)
    for i in (1, 2):
        _extend(conn, dates, i)
        sig(dates[i], "BUY", "双均线趋势跟踪" if i < 2 else "动量突破")
        so.run_daily(conn)
    # C@d2 需 d3 开盘才成交——先补到 d3 让三笔全部落仓
    _extend(conn, dates, 3)
    so.run_daily(conn)
    assert len(_state_payload(conn)['positions']['000001']) == 3, "前置：三笔持仓"
    # X=d3：M1 与 M2 同日各发 SELL——FIFO：M1 SELL 平 A、M2 SELL 平 B
    # （跨策略消费：l2_close 行 strategy=被平仓位 M1 ≠ SELL 信号策略，DB 反推
    # seen 必错配 → 靠 processed 权威键防幻影）；C(M2) 仍在仓。
    # 模拟"晚到"：状态已推进到 d3 后信号才写入（补登记路径，天然覆盖跨日错配）
    sig(dates[3], "SELL", "双均线趋势跟踪")
    sig(dates[3], "SELL", "动量突破")
    so.run_daily(conn)
    _extend(conn, dates, 4)
    so.run_daily(conn)             # d4：两笔 SELL 于 d4 开盘成交 + 补登记检查
    rows = conn.execute("SELECT strategy FROM signal_outcome WHERE kind='l2_close'"
        " AND source='real' ORDER BY id").fetchall()
    assert len(rows) == 2, f"两笔 SELL 只应产生两行平仓（不得幻影第三笔）: {rows}"
    _extend(conn, dates, 5)
    so.run_daily(conn)
    rows = conn.execute("SELECT strategy, exit_reason FROM signal_outcome"
        " WHERE kind='l2_close' AND source='real' ORDER BY id").fetchall()
    assert len(rows) == 2 and all(r[1] == 'sell' for r in rows), \
        f"次日运行也不得幻影第三次平仓（C(M2) 应仍持仓）: {rows}"
    st = _state_payload(conn)
    held = [p['strategy'] for p in st['positions'].get('000001', [])]
    assert held == ['动量突破'], f"C(M2) 应仍在仓: {st['positions']}"


# ── 快照 schema 版本（Minor 3：__v + 未知版本防御）──────────────────────

def test_load_state_unknown_schema_version_defensive(env):
    """__v=3（未来 schema）快照 → 不按 v1 字段瞎解析，按空账本继续"""
    conn = env
    conn.execute("INSERT INTO signal_outcome (date, source, kind, exit_reason)"
        " VALUES ('2026-07-20', 'real', 'l2_state', ?)",
        ('{"__v": 3, "positions": {"000001": [{"exec_price": 99}]},'
         ' "pending": [{"kind": "l2_open"}]}',))
    conn.commit()
    fresh = so.Ledger()
    assert fresh.load_state(conn) == '2026-07-20'
    assert fresh.positions == {} and fresh.pending == [], \
        "未知 schema 版本不得按 v1 字段加载"

def test_load_state_v2_snapshot_with_processed(env):
    """__v=2 快照（含 processed 权威键）→ 正常加载并恢复 processed"""
    conn = env
    conn.execute("INSERT INTO signal_outcome (date, source, kind, exit_reason)"
        " VALUES ('2026-07-20', 'real', 'l2_state', ?)",
        ('{"__v": 2, "positions": {}, "pending": [],'
         ' "processed": [["2026-07-20", "SELL", "000001", "动量突破"]]}',))
    conn.commit()
    fresh = so.Ledger()
    assert fresh.load_state(conn) == '2026-07-20'
    assert ('2026-07-20', 'SELL', '000001', '动量突破') in fresh.processed, \
        "v2 快照应恢复 processed 集合"

def test_load_state_legacy_snapshot_without_version(env):
    """旧版部署的 v1 快照无 __v 字段（历史唯一格式）→ 兼容加载，不误判为未知"""
    conn = env
    dates = so._trading_dates(conn)
    pos = [{'code': '000001', 'name': '平安', 'strategy': '动量突破策略',
            'source': 'real', 'buy_date': dates[1], 'exec_price': 10.0,
            'hold_days': 2, 'signal_date': dates[1]}]
    conn.execute("INSERT INTO signal_outcome (date, source, kind, exit_reason)"
        " VALUES (?, 'real', 'l2_state', ?)",
        (dates[2], json.dumps({'positions': {'000001': pos}, 'pending': []})))
    conn.commit()
    fresh = so.Ledger()
    assert fresh.load_state(conn) == dates[2]
    assert len(fresh.positions['000001']) == 1, "旧快照应兼容加载"
