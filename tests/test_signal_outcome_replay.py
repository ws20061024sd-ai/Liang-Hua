"""回放引擎测试 —— replay() 主函数端到端（打桩策略替换注册表）

覆盖：
  - 主循环组装：逐日 跑策略→风控→L1 结算→Ledger 排队/成交，统计 dict 正确
  - L2 语义：t 日收盘信号 → t+1 开盘价成交（当日开盘价不得成交）
  - 不偷看未来：策略每日收到的 K 线尾部必须恰好是当日
    （未来出现的高价不得让更早日期出信号）
  - 幂等：重复回放不重复写库
"""
import sqlite3
import sys
import os
import datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from scripts import signal_outcome as so

N_DAYS = 90
SPIKE_AT = 60   # 第 60 个交易日 000001 尾盘拉升至 30 元（此前全程 10 元横盘）


def _weekdays(start="2026-06-01", n=N_DAYS):
    """从 start 起跳过周末取 n 个自然日（日历日期仅作标签，策略语义不依赖真实节假日）"""
    dates, d = [], dt.date.fromisoformat(start)
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += dt.timedelta(days=1)
    return dates


@pytest.fixture
def env(tmp_path, monkeypatch):
    """3 只股票 × 90 交易日：000001 尾盘拉升(10→30)，其余横盘 10 元"""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(so, "DB", path)
    # replay() 内的 get_all_stocks 走 cleaner→settings.DB_PATH，指到同一临时库
    monkeypatch.setattr(so.settings, "DB_PATH", path)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE daily_kline (code TEXT, date TEXT, open REAL,
        high REAL, low REAL, close REAL, volume REAL, amount REAL,
        pct_change REAL, turnover REAL, PRIMARY KEY (code, date))""")
    conn.execute("""CREATE TABLE index_daily (date TEXT PRIMARY KEY,
        open REAL, close REAL, high REAL, low REAL, volume REAL, amount REAL)""")
    conn.execute("""CREATE TABLE stock_info (code TEXT PRIMARY KEY, name TEXT,
        market TEXT, listing_date TEXT, is_st INTEGER DEFAULT 0, updated_at TEXT)""")
    dates = _weekdays()
    names = {"000001": "平安银行", "600000": "浦发银行", "300750": "宁德时代"}
    for code, nm in names.items():
        conn.execute("INSERT INTO stock_info VALUES (?,?,?,?,0,?)",
                     (code, nm, "主板", "2010-01-01", "2026-01-01"))
    for i, d in enumerate(dates):
        conn.execute("INSERT INTO index_daily VALUES (?,3000,3000,3000,3000,0,0)", (d,))
        for code in names:
            if code == "000001":
                if i < SPIKE_AT:
                    o, c = 10.0, 10.0
                elif i == SPIKE_AT:
                    o, c = 29.0, 30.0   # 当日开盘仍低：能检验"次日开盘成交"
                else:
                    o, c = 30.0, 30.0
            else:
                o, c = 10.0, 10.0
            conn.execute("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,1e7,1e8,0,0)",
                         (code, d, o, c, c, c))
    conn.commit()
    so.init_outcome_table(conn)
    yield conn, dates
    conn.close()


class SpikeFakeSt:
    """记录 000001 每日收到的 df 尾部日期；收盘≥25 元则发 BUY（回放不偷看未来时
    只能从拉升当日 dates[SPIKE_AT] 起触发）"""
    name = "回放Spike测试"
    version = "1.0"
    records: list = []  # (df 尾部日期, df 长度, decision)

    @classmethod
    def reset(cls):
        cls.records = []

    def run(self, code, name, df):
        if code != "000001":
            return None
        last = df.iloc[-1]
        dstr = str(last['date'])[:10]
        close = float(last['close'])
        decision = 'BUY' if close >= 25 else None
        SpikeFakeSt.records.append((dstr, len(df), decision))
        if decision is None:
            return None
        return {'stock_code': code, 'stock_name': name, 'action': 'BUY',
                'strength': 0.9, 'reason': '测试拉升突破', 'price': close}


class BuySellFakeSt:
    """600000 在指定日发 BUY / SELL（验证 L2 开仓→平仓链路）"""
    name = "回放买卖测试"
    version = "1.0"
    buy_day = sell_day = None

    def run(self, code, name, df):
        if code != "600000":
            return None
        last = df.iloc[-1]
        dstr = str(last['date'])[:10]
        if dstr == BuySellFakeSt.buy_day:
            action, reason = 'BUY', '测试买入'
        elif dstr == BuySellFakeSt.sell_day:
            action, reason = 'SELL', '测试卖出'
        else:
            return None
        return {'stock_code': code, 'stock_name': name, 'action': action,
                'strength': 0.8, 'reason': reason, 'price': float(last['close'])}


class HoldFakeSt:
    """000001 在指定日发 HOLD（回放对未知 action 应跳过撮合，不得误平仓）"""
    name = "回放HOLD测试"
    version = "1.0"
    hold_day = None

    def run(self, code, name, df):
        if code != "000001":
            return None
        dstr = str(df.iloc[-1]['date'])[:10]
        if dstr != HoldFakeSt.hold_day:
            return None
        return {'stock_code': code, 'stock_name': name, 'action': 'HOLD',
                'strength': 0.9, 'reason': '测试持有', 'price': float(df.iloc[-1]['close'])}


def _patch_registry(monkeypatch, *fake_classes):
    """注册表 + ENABLED_STRATEGIES 同步打桩：replay 走 get_enabled_strategies
    （按 settings.ENABLED_STRATEGIES 过滤），只打注册表会让启用的策略为空"""
    import engine.runner
    names = {c.__name__: c for c in fake_classes}
    monkeypatch.setattr(engine.runner, "STRATEGY_REGISTRY", names)
    monkeypatch.setattr(so.settings, "ENABLED_STRATEGIES", list(names))


def test_replay_end_to_end(env, monkeypatch):
    """replay() 主循环：统计 dict / L1 窗口 / L2 次日开盘成交 / SELL 平仓 / 幂等"""
    conn, dates = env
    _patch_registry(monkeypatch, SpikeFakeSt, BuySellFakeSt)
    SpikeFakeSt.reset()
    BuySellFakeSt.buy_day = dates[71]
    BuySellFakeSt.sell_day = dates[75]

    stats = so.replay(conn)
    # 30×Spike(dates[60..89]收盘≥25) + 1×BUY(dates[71]) + 1×SELL(dates[75])
    assert stats["signals"] == 32, stats
    # L1: Spike d60..d84→+5(25) + d60..d79→+10(20) + d60..d69→+20(10) = 55
    #     BUY d71→+5/+10(2)  SELL d75→+5/+10(2)  共 59（+20 窗口超出库内最后一天）
    assert stats["l1"] == 59, stats
    assert stats["l2_close"] == 1, stats

    # L2 次日开盘成交：dates[60] 收盘信号 → dates[61] 开盘 30（当日开盘 29 不可成交）
    row = conn.execute(
        "SELECT exec_price FROM signal_outcome WHERE date=? AND kind='l2_open' AND code='000001'",
        (dates[SPIKE_AT],)).fetchone()
    assert row is not None and abs(row[0] - 30.0) < 1e-9, row
    opens = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_open'").fetchone()[0]
    assert opens == 30  # Spike 29 笔(dates[60..88]的次日) + 600000 1 笔；dates[89] 信号留待下日

    # SELL 平仓：dates[75] 信号 → dates[76] 开盘 10 成交，pnl≈0
    cl = conn.execute(
        "SELECT exec_price, pnl, exit_reason FROM signal_outcome WHERE kind='l2_close'").fetchone()
    assert cl is not None and abs(cl[0] - 10.0) < 1e-9 and abs(cl[1]) < 1e-9, cl
    assert cl[2] == 'sell'

    # L1 最早信号恰好是拉升当日（此前无 ≥25 元的收盘价）
    first = conn.execute("SELECT MIN(date) FROM signal_outcome WHERE kind='l1_5d'").fetchone()[0]
    assert first == dates[SPIKE_AT]

    # 幂等：重复回放不重复写库
    stats2 = so.replay(conn)
    assert stats2 == stats
    n = conn.execute("SELECT COUNT(*) FROM signal_outcome").fetchone()[0]
    assert n == 59 + 30 + 1


def test_replay_day_by_day_no_lookahead(env, monkeypatch):
    """逐日滚动不偷看未来：策略每日收到的 df 尾部必须恰好是当日；
    未来(拉升日之后)的 30 元高价不得让更早日期产生 BUY"""
    conn, dates = env
    _patch_registry(monkeypatch, SpikeFakeSt)
    SpikeFakeSt.reset()

    stats = so.replay(conn)
    assert stats["signals"] == 30, "只有 30 天(拉升当日起)收盘≥25 才应出信号"
    assert stats["l1"] == 55
    assert stats["l2_close"] == 0

    # 每天一条记录（df 满 60 行即调用）：按日期升序，df 尾部必须 == 当日
    got_dates = [d for d, _ln, _dec in SpikeFakeSt.records]
    assert got_dates == dates[59:], \
        "任一记录尾部日期漂移 = 该日收到了未来数据（偷看未来）"
    decisions = [dec for _d, _ln, dec in SpikeFakeSt.records]
    assert decisions == [None] + ["BUY"] * 30

    # 库中不存在拉升日之前的任何结算记录
    cnt = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE date < ?", (dates[SPIKE_AT],)).fetchone()[0]
    assert cnt == 0


def test_replay_rerun_no_duplicates_after_legacy_open_date_wipe(env, monkeypatch):
    """v3 幂等回归：迁移遗留行（open_date=''，历史开仓日不可考）与新算行
    （open_date=真实开仓日）键不同——纯 UNIQUE 去重会把旧行重放成双份（本地
    真实库实证 2699→5471）。回放产物为纯派生数据 → 每次重跑先清后算重写"""
    conn, dates = env
    _patch_registry(monkeypatch, SpikeFakeSt, BuySellFakeSt)
    SpikeFakeSt.reset()
    BuySellFakeSt.buy_day = dates[71]
    BuySellFakeSt.sell_day = dates[75]
    so.replay(conn)
    n1 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='replay'"
        ).fetchone()[0]
    # 模拟 v1→v3 迁移遗留：历史回放行 open_date 全被抹成 ''（迁移无法回填）
    conn.execute("UPDATE signal_outcome SET open_date='' WHERE source='replay'")
    conn.commit()
    so.replay(conn)
    n2 = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='replay'"
        ).fetchone()[0]
    assert n2 == n1, f"重跑不得复制行: {n1} → {n2}"
    back = conn.execute("SELECT COUNT(*) FROM signal_outcome WHERE source='replay'"
        " AND kind='l2_close' AND open_date != ''").fetchone()[0]
    assert back >= 1, "重写后 l2_close 应带真实 open_date"


def test_replay_unknown_action_not_liquidated(env, monkeypatch, capsys):
    """HOLD 类未知 action：回放不得按 SELL 误平仓（防御：非 BUY 一律当 SELL 会静默平仓），
    且应打印跳过告警；L1 窗口样本仍记录"""
    conn, dates = env
    _patch_registry(monkeypatch, SpikeFakeSt, HoldFakeSt)
    SpikeFakeSt.reset()
    HoldFakeSt.hold_day = dates[75]  # 000001 自 dates[60] 起每日 BUY、持仓在身——HOLD 若按 SELL 处理会误平一笔
    stats = so.replay(conn)
    assert "未知 action HOLD" in capsys.readouterr().out, "未知 action 应打印跳过告警"
    assert stats["signals"] == 31, stats  # 30×Spike BUY + 1×HOLD
    n_close = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close'").fetchone()[0]
    assert n_close == 0, "HOLD 不得触发任何平仓（旧逻辑会把 HOLD 当 SELL 误平仓）"
