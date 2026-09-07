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
