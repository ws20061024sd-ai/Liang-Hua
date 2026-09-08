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

def test_same_day_double_close_both_persisted(env):
    """最终审查 I-1：同日同股同策略两笔平仓（SELL 信号 FIFO 平第 1 笔 + 移动止损
    平第 2 笔）都必须落库——旧 UNIQUE(date,code,strategy,action,kind,source) 下
    两事件元组相同（strategy=被平 BUY 的 strategy），第二笔被 INSERT OR IGNORE
    静默丢弃，而内存账本已 pop + 快照已持久化 → 真实平仓永久丢失"""
    conn, dates = env
    M = "双均线趋势跟踪"
    ledger = so.Ledger()
    # 同策略同 code 两笔持仓（连续补仓）：A@d1 成交 d2 开盘 10.5，B@d2 成交 d3 开盘 11.0
    ledger.on_signal(conn, dates[0], "000001", "平安", M, "BUY", "replay")
    ledger.process_pending(conn, dates[1])
    ledger.on_signal(conn, dates[1], "000001", "平安", M, "BUY", "replay")
    ledger.process_pending(conn, dates[2])
    assert len(ledger.positions["000001"]) == 2, "前置：应有两笔持仓"

    # d4 收盘暴跌 9.5 < 峰值 11.0×0.95=10.45 → 移动止损平掉第 1 笔（FIFO 首笔）
    conn.execute("UPDATE daily_kline SET close=9.5 WHERE code='000001' AND date=?",
                 (dates[3],))
    conn.commit()
    ledger.check_stop_loss(conn, dates[3])
    # 同日 SELL 信号（收盘后）FIFO 平掉剩余第 2 笔——两事件同键（date/code/strategy）
    ledger.on_signal(conn, dates[3], "000001", "平安", M, "SELL", "replay")
    queued = [p for p in ledger.pending if p['kind'] == 'l2_close']
    assert len(queued) == 2 and {p['exit_reason'] for p in queued} == \
        {'stop_loss', 'sell'}, f"前置：应有两笔待成交平仓: {queued}"

    # d5 开盘 11.2 统一成交 → 两笔都必须写库
    events = ledger.process_pending(conn, dates[4])
    assert len(events) == 2
    ledger.write_events(conn, events)
    rows = conn.execute("SELECT exit_reason, exec_price, strategy FROM signal_outcome"
        " WHERE kind='l2_close' ORDER BY id").fetchall()
    assert len(rows) == 2, \
        f"同日双平仓不得静默丢行——旧 UNIQUE 只会落 1 行: {rows}"
    assert {r[0] for r in rows} == {'stop_loss', 'sell'}, rows
    assert all(abs(r[1] - 11.2) < 1e-9 and r[2] == M for r in rows), rows


def test_same_day_double_sell_cross_strategy_both_persisted(env):
    """复审发现 I-1 残余：同日同股**两笔 'sell' 平仓**——同 strategy M1 两笔在仓
    （补仓），当日 M1 与 M2 各发一条 SELL（FIFO 不校验 SELL 策略，文档化语义）→
    M1 的 SELL 平 A、M2 的 SELL 平 C（同为 M1 的仓）→ 两事件 (date, code, M1,
    SELL, l2_close, source, 'sell') 在 v2 键（含 exit_reason）下仍同键 →
    第二笔被 OR IGNORE 静默丢弃。v3 以被平仓的开仓日 open_date 做位置级区分"""
    conn, dates = env
    M1, M2 = "双均线趋势跟踪", "动量突破"
    ledger = so.Ledger()
    # 同 strategy M1 两笔持仓（连续补仓）：A@d1 成交 d2 开盘 10.5，C@d2 成交 d3 开盘 11.0
    ledger.on_signal(conn, dates[0], "000001", "平安", M1, "BUY", "replay")
    ledger.process_pending(conn, dates[1])
    ledger.on_signal(conn, dates[1], "000001", "平安", M1, "BUY", "replay")
    ledger.process_pending(conn, dates[2])
    assert len(ledger.positions["000001"]) == 2, "前置：应有两笔持仓"

    # d4：M1 SELL FIFO 平 A；M2 同日 SELL（FIFO 出队不校验信号策略）平掉 C
    ledger.on_signal(conn, dates[3], "000001", "平安", M1, "SELL", "replay")
    ledger.on_signal(conn, dates[3], "000001", "平安", M2, "SELL", "replay")
    queued = [p for p in ledger.pending if p['kind'] == 'l2_close']
    assert len(queued) == 2, f"前置：两笔 SELL 都应出队排队: {queued}"

    # d5 开盘 11.2 统一成交 → 两笔都必须写库（v2 键下同 exit_reason 仍会撞）
    events = ledger.process_pending(conn, dates[4])
    assert len(events) == 2
    ledger.write_events(conn, events)
    rows = conn.execute("SELECT exit_reason, strategy, open_date FROM signal_outcome"
        " WHERE kind='l2_close' ORDER BY id").fetchall()
    assert len(rows) == 2, \
        f"同日同 strategy 两笔 sell 平仓不得静默丢行: {rows}"
    assert all(r[0] == 'sell' and r[1] == M1 for r in rows), rows
    assert {r[2] for r in rows} == {dates[0], dates[1]}, \
        f"open_date 应区分两笔持仓（d1/d2）: {rows}"


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

def test_stop_loss_no_lookahead_future_peak(env):
    """DB 存在未来更高价也不触发止损（回放不偷看未来）"""
    conn, dates = env
    # 08-10 当日价 11.2（正常，与既有峰值持平）；08-11 是"未来"高价 20，
    # 推进到 08-10 时不可见——若峰值查询无上界会把它算进去 → 止损线 19 → 错误触发
    conn.execute("INSERT INTO daily_kline VALUES ('000001','2026-08-10',11.2,11.2,11.2,11.2,1e7,1e8,0,0)")
    conn.execute("INSERT INTO daily_kline VALUES ('000001','2026-08-11',20.0,20.0,20.0,20.0,1e7,1e8,0,0)")
    conn.commit()
    ledger = so.Ledger()
    ledger.on_signal(conn, dates[0], "000001", "测试", "双均线趋势跟踪", "BUY", "replay")
    ledger.process_pending(conn, dates[1])  # 08-04 开盘 10.5 成交，buy_date=08-03
    # 修复后峰值 = MAX(08-03..08-10) = 11.2 → 止损线 10.64，11.2 不低于 → 不触发
    evs = ledger.check_stop_loss(conn, "2026-08-10")
    assert evs == [], "08-10 当日价格正常，不得因未来(08-11)高价而错误触发止损"
    assert ledger.positions.get("000001"), "持仓不应被错误平掉"


def test_stop_loss_peak_cache_rise_then_fall(env):
    """峰值缓存增量分支（max(peak, cur)）：价格先升后落两次检查——
    第一次价格处高位不触发并缓存峰值；价格跌穿止损线后第二次走缓存分支正确触发"""
    conn, dates = env
    ledger = so.Ledger()
    ledger.on_signal(conn, dates[0], "000001", "测试", "双均线趋势跟踪", "BUY", "replay")
    ledger.process_pending(conn, dates[1])  # 08-04 开盘 10.5 成交，buy_date=08-03
    # 第一次检查：08-05 收盘 11.0 = 持仓期最高 → 不触发；峰值经全窗口计算并缓存
    evs1 = ledger.check_stop_loss(conn, dates[2])
    assert evs1 == [], "价格处峰值(11.0)不应触发止损"
    assert ledger.positions["000001"][0]['peak_close'] == 11.0, \
        "首次检查应缓存峰值，第二次检查才能走 max(peak, cur) 增量分支"
    # 价格回落：追加 08-10 收盘 9.5 < 止损线(峰值 11.0×0.95=10.45)
    conn.execute("INSERT INTO daily_kline VALUES ('000001','2026-08-10',9.5,9.5,9.5,9.5,1e7,1e8,0,0)")
    conn.commit()
    # 第二次检查：peak 已缓存 → max(11.0, 9.5)=11.0 → 9.5 < 10.45 触发止损
    evs2 = ledger.check_stop_loss(conn, "2026-08-10")
    assert len(evs2) == 1 and evs2[0]['exit_reason'] == 'stop_loss', evs2
    assert not ledger.positions.get("000001"), "触发止损后持仓应已出队"
    closes = [p for p in ledger.pending if p['kind'] == 'l2_close']
    assert closes and closes[0]['exit_reason'] == 'stop_loss', "应排队次日开盘平仓"
