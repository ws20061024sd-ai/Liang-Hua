#!/usr/bin/env python3
"""
健康检查 + 数据库备份脚本

用法:
    python scripts/health_check.py          # 检查今日信号是否正常
    python scripts/health_check.py --backup # 同时备份数据库

服务器 crontab:
    # 健康检查（21:10，run.py 之后 10 分钟）
    10 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python scripts/health_check.py >> logs/health.log 2>&1

    # 数据库备份（21:15，日报之后）
    15 21 * * 1-5 cd /root/Liang-Hua && ./venv/bin/python scripts/health_check.py --backup >> logs/health.log 2>&1
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import shutil
from datetime import datetime
from config import settings


def check_signals_today() -> bool:
    """检查 signal_history 表是否有今天的记录"""
    conn = sqlite3.connect(settings.DB_PATH)
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        count = conn.execute(
            "SELECT COUNT(*) FROM signal_history WHERE date = ?", (today,)
        ).fetchone()[0]

        if count > 0:
            print(f"✅ 健康: signal_history 今日有 {count} 条记录")
            return True
        else:
            print(f"⚠️ 异常: signal_history 今日无记录！run.py 可能未执行或崩溃")
            return False
    finally:
        conn.close()


def backup_database():
    """备份 stocks.db 到 data/backups/（保留最近 7 天）"""
    backup_dir = "data/backups"
    os.makedirs(backup_dir, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    src = settings.DB_PATH
    dst = f"{backup_dir}/stocks_{today}.db"

    if os.path.exists(dst):
        print(f"⏭ 今日已备份: {dst}")
        return

    shutil.copy2(src, dst)
    size_mb = os.path.getsize(dst) / 1024 / 1024
    print(f"💾 备份完成: {dst} ({size_mb:.1f} MB)")

    # 清理 8 天前的旧备份
    import glob
    backups = sorted(glob.glob(f"{backup_dir}/stocks_*.db"))
    cutoff = datetime.now().timestamp() - 8 * 86400
    for b in backups:
        if os.path.getmtime(b) < cutoff:
            os.remove(b)
            print(f"🗑 清理旧备份: {os.path.basename(b)}")


def send_alert(message: str):
    """发送钉钉告警"""
    if not settings.DINGTALK_WEBHOOK:
        return
    import requests
    import socket
    host = socket.gethostname()
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "量化系统告警",
            "text": f"## 🚨 量化系统告警\n\n{message}\n\n---\n📍 {host} | {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        },
    }
    try:
        resp = requests.post(settings.DINGTALK_WEBHOOK, json=payload, timeout=10)
        if resp.json().get("errcode") == 0:
            print("   📤 告警已推送")
        else:
            print(f"   ⚠️ 告警推送被拒: {resp.json()}")
    except Exception as e:
        print(f"   ⚠️ 告警推送失败: {e}")


if __name__ == "__main__":
    from data_fetcher.trading_calendar import is_trading_day
    if not is_trading_day():
        print(f"📅 今日非交易日，跳过健康检查")
        sys.exit(0)

    ok = check_signals_today()
    if not ok:
        send_alert("run.py 今日未产生交易信号，可能执行失败或崩溃。请检查服务器！")

    if "--backup" in sys.argv:
        backup_database()
