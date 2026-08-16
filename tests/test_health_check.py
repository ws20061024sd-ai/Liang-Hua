"""
健康检查与无信号占位测试

背景（2026-08-16 审查问题9）：run.py 无信号日直接 return 不写库，
21:10 健康检查只查 signal_history 今日记录 → 必误报钉钉告警。
修复：无信号日写入 status='no_signal' 占位记录。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
import pytest
from config import settings
from scripts import health_check


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "DB_PATH", path)
    return path


def test_no_signal_record_prevents_false_alarm(db):
    """run.py 无信号占位记录写入后，健康检查不误报"""
    import run as run_mod
    today = datetime.now().strftime("%Y-%m-%d")
    run_mod.write_no_signal_record(today)

    assert health_check.check_signals_today() is True, "no_signal 记录不应触发误报"


def test_empty_signal_history_is_unhealthy(db):
    """完全无记录 → 判定异常（run.py 可能未执行）"""
    from engine.signal_store import init_signal_table
    init_signal_table()

    assert health_check.check_signals_today() is False
