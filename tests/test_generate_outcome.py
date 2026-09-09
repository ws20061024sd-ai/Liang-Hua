"""仪表盘「信号效果」页（outcome.html）测试

覆盖（Task 6）:
- page_outcome 合并视图渲染：L1 表 / L2 表 / 最近结算 / 来源构成注记 / 来源 tab / 导航链接
- 单来源视图过滤（真实 real / 回放 replay）
- 空库（结算表未建）与空表不崩（空态提示）
- 统计与渲染排除 kind='l2_state' 状态快照行（审查遗留 Minor n）
- summary_l2 统计口径（笔数/胜率/平均收益/平均持仓/等权累计/排序/来源过滤）
- recent_settlements 排序/limit/来源过滤/排除 l2_state
"""
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
    yield c
    c.close()


def _row(c, date, code, name, strategy, action, source, kind,
         pnl=None, excess=None, hold_days=None, exit_reason=None):
    c.execute("""INSERT INTO signal_outcome (date, code, name, strategy, action,
        source, kind, pnl, excess, hold_days, exit_reason)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (date, code, name, strategy, action, source, kind,
         pnl, excess, hold_days, exit_reason))


def test_page_outcome_renders_merged(conn):
    """真实+回放两种来源 → 合并视图全出现；L1 命中率 100% 带 ✅"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 2.0, 1.5)
    _row(conn, '2026-08-01', '000002', '万科', '动量突破', 'BUY', 'replay',
         'l1_10d', -1.0, -2.0)
    _row(conn, '2026-08-02', '000003', '茅台', '双均线趋势跟踪', 'SELL', 'real',
         'l2_close', 3.0, None, 10, 'sell')
    _row(conn, '2026-08-02', '000004', '招行', '均值回归', 'SELL', 'replay',
         'l2_close', -1.5, None, 3, 'timeout')
    conn.commit()
    html = page_outcome(conn)
    assert '信号效果' in html
    assert 'L1 信号预测力' in html and 'L2 等权账本' in html
    assert '双均线趋势跟踪' in html and '动量突破' in html and '均值回归' in html
    # L1: BUY 超额>0 命中 → 100.0% ✅；L2: 双均线唯一一笔 pnl>0 → 100.0%
    assert '100.0%' in html and '✅' in html
    assert '3.0%' in html  # L2 平均收益与等权累计
    assert '最近结算' in html
    # 合并视图：表头来源构成注记 + 来源切换 tab + 导航链接
    assert '真实 1 条 · 回放 1 条' in html
    assert 'outcome_real.html' in html and 'outcome_replay.html' in html
    assert 'href="outcome.html"' in html


def test_page_outcome_single_source_filters(conn):
    """source='real'/'replay' 只显示对应来源的策略行"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 2.0, 1.5)
    _row(conn, '2026-08-01', '000002', '万科', '动量突破', 'BUY', 'replay',
         'l1_10d', -1.0, -2.0)
    conn.commit()
    real_html = page_outcome(conn, 'real')
    assert '双均线趋势跟踪' in real_html
    assert '动量突破' not in real_html  # 回放行被过滤
    replay_html = page_outcome(conn, 'replay')
    assert '动量突破' in replay_html
    assert '双均线趋势跟踪' not in replay_html


def test_page_outcome_empty_db_and_empty_table_no_crash(tmp_path):
    """结算表未建（首次部署）或建表无数据 → 空态提示不崩溃"""
    path = str(tmp_path / 'empty.db')
    c = sqlite3.connect(path)
    html = page_outcome(c)
    assert '信号效果' in html and '暂无结算数据' in html
    so.init_outcome_table(c)
    html2 = page_outcome(c)
    assert '暂无结算数据' in html2  # L1 空态
    assert '暂无平仓交易' in html2  # L2 空态
    assert 'outcome_real.html' in html2  # 空库也有来源 tab
    c.close()


def test_page_outcome_and_summaries_exclude_l2_state(conn):
    """l2_state 状态快照行（code/strategy 全 NULL 的单行 JSON 聚合）不得进统计/渲染"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 2.0, 1.5)
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'SELL', 'real',
         'l2_close', 3.0, None, 5, 'sell')
    _row(conn, '2026-08-02', None, None, None, None, 'real',
         'l2_state', None, None, None, 'STATE_MARKER_JSON')
    conn.commit()
    rows = so.summary_l2(conn)
    assert len(rows) == 1 and rows[0]['strategy'] == '双均线趋势跟踪'
    assert rows[0]['n'] == 1 and rows[0]['cum'] == 3.0
    l1 = so.summary_l1(conn)
    assert len(l1) == 1 and l1[0]['total'] == 1
    html = page_outcome(conn)
    assert 'STATE_MARKER_JSON' not in html
    assert 'l2_state' not in html
    assert 'None' not in html  # 快照行 NULL 泄漏会渲染成 None


def test_summary_l2_stats_math_and_order(conn):
    """summary_l2: 笔数/胜率/平均收益/平均持仓/等权累计 + 累计降序 + 来源过滤"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l2_close', 4.0, None, 10, 'sell')
    _row(conn, '2026-08-02', '000001', '平安', '双均线趋势跟踪', 'SELL', 'real',
         'l2_close', -1.0, None, 2, 'stop_loss')
    _row(conn, '2026-08-03', '000002', '万科', '动量突破', 'SELL', 'replay',
         'l2_close', 5.0, None, 1, 'timeout')
    conn.commit()
    rows = so.summary_l2(conn)
    assert [r['strategy'] for r in rows] == ['动量突破', '双均线趋势跟踪']  # 累计降序
    a = rows[1]
    assert a['n'] == 2 and a['win_rate'] == 50.0 and a['avg_pnl'] == 1.5
    assert a['avg_hold'] == 6.0 and a['cum'] == 3.0
    b = rows[0]
    assert b['n'] == 1 and b['win_rate'] == 100.0 and b['avg_hold'] == 1.0 and b['cum'] == 5.0
    real_only = so.summary_l2(conn, source='real')
    assert len(real_only) == 1 and real_only[0]['strategy'] == '双均线趋势跟踪'
    assert len(so.summary_l2(conn, source='replay')) == 1
    empty = sqlite3.connect(':memory:')
    so.init_outcome_table(empty)
    assert so.summary_l2(empty) == []
    empty.close()


def test_recent_settlements_orders_limits_filters(conn):
    """最近结算：日期降序 + limit + 来源过滤 + 排除 l2_state"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 1.0, 0.5)
    _row(conn, '2026-08-02', '000002', '万科', '动量突破', 'SELL', 'replay',
         'l2_close', 2.0, None, 4, 'sell')
    _row(conn, '2026-08-03', '000003', '茅台', '均值回归', 'SELL', 'real',
         'l1_10d', -1.0, -0.5)
    # 状态快照行日期最新，但不得混入"最近结算"
    _row(conn, '2026-08-04', None, None, None, None, 'real', 'l2_state')
    conn.commit()
    rows = so.recent_settlements(conn)
    assert len(rows) == 3
    assert rows[0]['date'] == '2026-08-03'
    assert all(r['kind'] in ('l1_10d', 'l2_close') for r in rows)
    limited = so.recent_settlements(conn, limit=2)
    assert [r['date'] for r in limited] == ['2026-08-03', '2026-08-02']
    replay_only = so.recent_settlements(conn, source='replay')
    assert len(replay_only) == 1 and replay_only[0]['kind'] == 'l2_close'
    assert replay_only[0]['strategy'] == '动量突破'


def test_page_outcome_brief_fixture_rows(conn):
    """brief 示例数据（L1 10日 + L2 平仓各一条）→ 策略行/胜率/收益都渲染"""
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 2.0, 1.5)
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'SELL', 'real',
         'l2_close', 3.0, None)
    conn.commit()
    html = page_outcome(conn)
    assert '双均线趋势跟踪' in html and '平安' in html
    assert '100.0%' in html  # L1 命中 1/1 · L2 盈利 1/1
    assert '3.0%' in html    # L2 平均收益/等权累计
    assert '真实 1 条 · 回放 0 条' in html  # 合并视图来源构成注记


# ── 审查发现 F1/F2：L1 按 BUY/SELL 拆行 + SELL 反向命中与翻转呈现 ─────────

def test_summary_l1_sell_reverse_hits_split(conn):
    """summary_l1 按 (strategy, action) 拆行、SELL 反向命中计入胜率

    审查发现：原来把 BUY/SELL 混在同一行统计——命中判定各自方向正确（SELL
    超额<0 才算命中），但 SELL 全对的策略会显示"胜率 100% + 平均超额为负"
    的矛盾观感。现在每策略至多两行（BUY/SELL 各一），各行的 total/win_rate/
    avg_excess 只统计本方向；avg_excess 保留原始符号（SELL 负值 = 回避的跌幅，
    翻转在展示层做）。
    """
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 2.0, 2.0)            # BUY 命中
    _row(conn, '2026-08-02', '000002', '万科', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', -5.0, -5.0)          # BUY 未中
    _row(conn, '2026-08-03', '000003', '茅台', '双均线趋势跟踪', 'SELL', 'real',
         'l1_10d', -3.0, -3.0)          # SELL 命中（回避下跌 → 超额<0）
    _row(conn, '2026-08-04', '000004', '招行', '双均线趋势跟踪', 'SELL', 'real',
         'l1_10d', -1.0, -1.0)          # SELL 命中
    _row(conn, '2026-08-05', '000005', '五粮液', '双均线趋势跟踪', 'SELL', 'real',
         'l1_10d', 2.0, 2.0)            # SELL 未中（卖出后仍跑赢大盘）
    conn.commit()
    rows = so.summary_l1(conn)
    by_act = {r['action']: r for r in rows if r['strategy'] == '双均线趋势跟踪'}
    assert set(by_act) == {'BUY', 'SELL'}  # 拆行而非合并成一行
    assert all('action' in r for r in rows)
    assert by_act['BUY']['total'] == 2 and by_act['BUY']['win_rate'] == 50.0
    assert by_act['BUY']['avg_excess'] == -1.5  # 只统计本方向行
    s = by_act['SELL']
    assert s['total'] == 3 and s['win_rate'] == 66.7  # 3 中命中 2（超额<0 才计）
    assert s['avg_excess'] == -0.67  # 原始符号保留：负 = 回避的跌幅


def test_page_outcome_l1_sell_flipped_display(conn):
    """L1 表渲染：SELL 行超额翻转（正数 = 回避的跌幅）+ 颜色随翻转值 + 方向标注

    全命中的 SELL 行（超额<0）不再显示"胜率 100% + 负超额绿色"的矛盾观感——
    超额列显示翻转后的正数（up=红=好，与 ✅ 同向）；BUY 行保持原符号；
    未中 SELL 行翻转后为负（dn=绿=差）。原始负值不得泄漏到展示。
    """
    _row(conn, '2026-08-01', '000001', '平安', '双均线趋势跟踪', 'SELL', 'real',
         'l1_10d', -2.0, -2.0)          # SELL 命中
    _row(conn, '2026-08-02', '000002', '万科', '双均线趋势跟踪', 'SELL', 'real',
         'l1_10d', -0.5, -0.5)          # SELL 命中
    _row(conn, '2026-08-03', '000003', '茅台', '双均线趋势跟踪', 'BUY', 'real',
         'l1_10d', 3.0, 3.0)            # BUY 命中 → 原样 +3.0%
    _row(conn, '2026-08-04', '000004', '招行', '均值回归', 'SELL', 'replay',
         'l1_10d', 1.0, 1.0)            # SELL 未中 → 翻转展示 -1.0%
    conn.commit()
    html = page_outcome(conn)
    # SELL 全命中行：胜率 100% + 超额翻转正值 1.25%（up 红）——矛盾观感消除
    assert 'ta-r up">1.25%' in html, 'SELL 超额应翻转为正且红色'
    assert '-1.25%' not in html, '原始负超额不得出现在 L1 展示'
    assert 'ta-r up">3.0%' in html, 'BUY 行保持原符号'
    assert 'ta-r dn">-1.0%' in html, 'SELL 未中翻转后为负（dn 绿）'
    # 方向列标注：SELL 行注明"回避跌幅"、表头含方向列与口径注记
    # （口径注记 + SELL 方向单元格各出现一次"回避跌幅"）
    assert '<span class="tag t-sell">SELL</span>' in html
    assert 'tag t-buy">BUY</span>' in html
    assert '方向' in html and html.count('回避跌幅') >= 2


# ── 策略页买卖条件结构化（2026-09-09）──────────────────────────────────

def test_page_strategy_shows_buy_sell_conditions():
    """策略页每个策略必须分别列出买入/卖出条件（原来原理段只讲买入）"""
    from html import escape
    from web.generate import page_strategy, STRATS
    html = page_strategy([])
    assert '买入条件' in html and '卖出条件' in html, "应有买卖条件分列"
    for s in STRATS:
        assert s.get('buy'), f"{s['n']} 缺 buy 字段"
        assert s.get('sell'), f"{s['n']} 缺 sell 字段"
        assert escape(s['buy']) in html and escape(s['sell']) in html, \
            f"{s['n']} 的买卖条件未渲染（按转义后比对）"


def test_strategy_sell_conditions_match_code():
    """卖出条件文案必须与策略代码一致（防文案漂移）"""
    from web.generate import STRATS
    by_key = {s['key']: s for s in STRATS}
    # 双均线：死叉卖出
    assert 'MA60' in by_key['ma']['sell'] and '下穿' in by_key['ma']['sell']
    # 动量突破：跌破过去10日最低收盘
    assert '10' in by_key['mb']['sell'] and '最低' in by_key['mb']['sell']
    # 均值回归：站上布林带上轨
    assert '上轨' in by_key['mr']['sell']


def test_strategy_conditions_html_escaped():
    """买卖条件含 < > 必须转义——裸 < 会让浏览器把后续 </div> 吞掉，
    导致卡片 div 延伸到页面底部（色条横跨三个卡片，2026-09-09 线上事故）"""
    from web.generate import page_strategy
    html = page_strategy([])
    # 正文中的 < 必须是 &lt; 实体
    assert 'MA20&lt;MA60' in html, "卖出条件的 < 未转义"
    assert 'MA20&gt;MA60' in html, "买入条件的 > 未转义"
    assert 'MA20<MA60' not in html, "存在裸的 < 字符"
