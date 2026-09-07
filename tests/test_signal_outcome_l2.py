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
