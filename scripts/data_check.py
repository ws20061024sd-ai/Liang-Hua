#!/usr/bin/env python3
"""
综合数据健康检查 —— 阻断条件触发则退出码非0，阻止后续策略运行

用法:
  python scripts/data_check.py            # 仅检查，输出报告
  python scripts/data_check.py --block    # 阻断条件触发时 exit(1)，阻止 run.py 继续

集成到 run.py:
  import subprocess, sys
  result = subprocess.run([sys.executable, 'scripts/data_check.py', '--block'])
  if result.returncode != 0:
      print("❌ 数据校验未通过，跳过信号生成")
      # 推送钉钉告警
      sys.exit(1)

校验项:
  1. 日线数据: 最新日股票数 ≥ 280, pct_change NULL = 0, 数据新鲜度 ≤ 2天
  2. 财务数据: PE覆盖率 ≥ 80%, PB覆盖率 ≥ 90%
  3. ROE数据: 覆盖 ≥ 255只, 延迟 ≤ 2季度
  4. 数据库文件: 存在且非空
"""
import sqlite3
import sys
import os
from datetime import datetime, timedelta

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'stocks.db')

EXIT_BLOCK = 1
EXIT_OK = 0

# ============================================================
# 阈值配置（修改此处即可调整标准）
# ============================================================
THRESHOLDS = {
    'daily_min_stocks': 280,       # 最新日最少股票数
    'daily_max_nulls': 0,          # pct_change 最大 NULL 数
    'daily_max_age_days': 2,       # 数据最大延迟（天）
    'pe_min_coverage': 0.80,       # PE 最低覆盖率
    'pb_min_coverage': 0.90,       # PB 最低覆盖率
    'roe_min_stocks': 255,         # ROE 最低覆盖股票数 (85%)
    'roe_max_lag_months': 6,       # ROE 最大延迟（月）
    'roe_min_val': -50,            # ROE 合理下限
    'roe_max_val': 100,            # ROE 合理上限
}

errors = []
warnings = []


def fail(msg: str):
    errors.append(msg)
    print(f"  ❌ {msg}")


def warn(msg: str):
    warnings.append(msg)
    print(f"  ⚠️  {msg}")


def ok(msg: str):
    print(f"  ✅ {msg}")


# ============================================================
# 检查项
# ============================================================

def check_db_exists():
    print("\n📦 数据库文件")
    if not os.path.exists(DB):
        fail(f"数据库文件不存在: {DB}")
        return False
    size_mb = os.path.getsize(DB) / (1024 * 1024)
    if size_mb < 1:
        fail(f"数据库文件过小: {size_mb:.1f}MB")
        return False
    ok(f"存在 ({size_mb:.0f}MB)")
    return True


def check_daily_kline(conn: sqlite3.Connection):
    print("\n📈 日线数据 (daily_kline)")
    t = THRESHOLDS

    maxd = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    if not maxd:
        fail("daily_kline 表为空")
        return

    cnt = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?", (maxd,)
    ).fetchone()[0]

    nulls = conn.execute(
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL", (maxd,)
    ).fetchone()[0]

    total = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    min_date = conn.execute("SELECT MIN(date) FROM daily_kline").fetchone()[0]

    # 新鲜度检查
    age = (datetime.now() - datetime.strptime(maxd, '%Y-%m-%d')).days

    print(f"  日期: {maxd} | 股票: {cnt}/300 | NULL: {nulls} | 总行: {total:,} | 起始: {min_date} | 延迟: {age}天")

    if cnt < t['daily_min_stocks']:
        fail(f"股票数 {cnt} < {t['daily_min_stocks']}")
    else:
        ok(f"股票数达标 ({cnt} ≥ {t['daily_min_stocks']})")

    if nulls > t['daily_max_nulls']:
        fail(f"pct_change NULL {nulls} > {t['daily_max_nulls']}")
    else:
        ok(f"pct_change 无NULL")

    if age > t['daily_max_age_days']:
        fail(f"数据延迟 {age}天 > {t['daily_max_age_days']}天")
    else:
        ok(f"数据新鲜 ({age}天前)")


def check_financial_data(conn: sqlite3.Connection):
    print("\n💰 财务数据 (financial_data)")
    t = THRESHOLDS

    # 取最新日线日期，查该日前最新的财务数据
    max_daily = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    total_stocks = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date=?", (max_daily,)
    ).fetchone()[0]

    # 只统计"当日股票池内"的股票（JOIN daily_kline），财务数据里有但已调出池子的不算
    rows = conn.execute("""
        SELECT f.code, f.pe, f.pb
        FROM financial_data f
        JOIN daily_kline d ON f.code = d.code AND d.date = ?
        WHERE f.date = (
            SELECT MAX(f2.date) FROM financial_data f2
            WHERE f2.code = f.code AND f2.date <= ?
        )
    """, (max_daily, max_daily)).fetchall()

    if not rows:
        fail("financial_data 无数据")
        return

    # PE 有效口径与 web/generate.py _health() 一致：亏损股(pe<0)/微利失真(pe>500) 不算有效覆盖
    pe_ok = sum(1 for _, pe, _ in rows if pe is not None and 0 <= pe <= 500)
    pb_ok = sum(1 for _, _, pb in rows if pb is not None)
    # 分母 = 当日股票池总数：300只里只有100只有财务数据时，覆盖率是 33% 而非 100%，
    # 真实反映数据缺失，避免"分母自洽"掩盖问题
    fin_total = total_stocks

    pe_rate = pe_ok / fin_total if fin_total > 0 else 0
    pb_rate = pb_ok / fin_total if fin_total > 0 else 0

    print(f"  股票: {fin_total} | PE有效: {pe_ok} ({pe_rate:.0%}) | PB有效: {pb_ok} ({pb_rate:.0%})")

    # 财务数据仅影响多因子排行，不影响当日买卖信号——覆盖率不足只 warning
    # 不阻断（否则财务下载失败时连止损提醒都会被拦掉，2026-08-16 审查补修2）
    if pe_rate < t['pe_min_coverage']:
        warn(f"PE覆盖率 {pe_rate:.0%} < {t['pe_min_coverage']:.0%}（仅影响多因子排行）")
    else:
        ok(f"PE覆盖率达标 ({pe_rate:.0%} ≥ {t['pe_min_coverage']:.0%})")

    if pb_rate < t['pb_min_coverage']:
        warn(f"PB覆盖率 {pb_rate:.0%} < {t['pb_min_coverage']:.0%}（仅影响多因子排行）")
    else:
        ok(f"PB覆盖率达标 ({pb_rate:.0%} ≥ {t['pb_min_coverage']:.0%})")


def check_financial_roe(conn: sqlite3.Connection):
    print("\n📊 ROE数据 (financial_roe)")
    t = THRESHOLDS

    latest_q = conn.execute("SELECT MAX(date) FROM financial_roe").fetchone()[0]
    if not latest_q:
        fail("financial_roe 表为空")
        return

    # 最新季度通常覆盖率低（财报未到披露截止日），找最近一个达标季度
    check_row = conn.execute("""
        SELECT date, COUNT(DISTINCT code)
        FROM financial_roe
        GROUP BY date
        HAVING COUNT(DISTINCT code) >= ?
        ORDER BY date DESC LIMIT 1
    """, (t['roe_min_stocks'],)).fetchone()

    if check_row:
        check_q, cnt = check_row
    else:
        check_q, cnt = latest_q, conn.execute(
            "SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=?", (latest_q,)
        ).fetchone()[0]

    total = conn.execute("SELECT COUNT(*) FROM financial_roe").fetchone()[0]
    total_codes = conn.execute("SELECT COUNT(DISTINCT code) FROM financial_roe").fetchone()[0]

    months_lag = (datetime.now() - datetime.strptime(check_q, '%Y-%m-%d')).days / 30.44

    # ROE 范围（基于校验季度）
    minr, maxr = conn.execute(
        "SELECT MIN(roe), MAX(roe) FROM financial_roe WHERE date=?", (check_q,)
    ).fetchone()

    skip_note = ""
    if check_q != latest_q:
        latest_cnt = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=?", (latest_q,)
        ).fetchone()[0]
        skip_note = f"（最新{latest_q}仅{latest_cnt}只，财报未到披露截止日，跳过）"

    print(f"  校验季度: {check_q} | 覆盖: {cnt}/300 | 总行: {total:,} | 总股票: {total_codes} | 延迟: {months_lag:.1f}月 | 范围: [{minr:.1f}%, {maxr:.1f}%]{skip_note}")

    if cnt < t['roe_min_stocks']:
        fail(f"ROE覆盖 {cnt} < {t['roe_min_stocks']}")
    else:
        ok(f"ROE覆盖达标 ({cnt} ≥ {t['roe_min_stocks']})")

    if months_lag > t['roe_max_lag_months']:
        fail(f"ROE延迟 {months_lag:.0f}月 > {t['roe_max_lag_months']}月")
    else:
        ok(f"ROE数据新鲜 (延迟{months_lag:.0f}月)")

    if minr is not None and minr < t['roe_min_val']:
        warn(f"ROE最小值 {minr:.1f}% < {t['roe_min_val']}%")
    if maxr is not None and maxr > t['roe_max_val']:
        warn(f"ROE最大值 {maxr:.1f}% > {t['roe_max_val']}%")


# ============================================================
# 主流程
# ============================================================

def main():
    block_mode = '--block' in sys.argv

    print("=" * 55)
    print("  数据健康检查")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {'阻断' if block_mode else '仅报告'}")
    print(f"  数据库: {DB}")
    print("=" * 55)

    if not check_db_exists():
        print(f"\n{'='*55}")
        print(f"结果: ❌ 阻断 — 数据库不可用")
        print(f"错误: {len(errors)} | 警告: {len(warnings)}")
        sys.exit(EXIT_BLOCK)

    conn = sqlite3.connect(DB)
    try:
        check_daily_kline(conn)
        check_financial_data(conn)
        check_financial_roe(conn)
    finally:
        conn.close()

    print(f"\n{'='*55}")

    if errors:
        print(f"结果: ❌ 阻断 — {len(errors)}个阻断项, {len(warnings)}个警告")
        for e in errors:
            print(f"  🔴 {e}")
        for w in warnings:
            print(f"  🟡 {w}")
        sys.exit(EXIT_BLOCK)
    elif warnings:
        print(f"结果: ⚠️  通过(有警告) — {len(warnings)}个警告项")
        for w in warnings:
            print(f"  🟡 {w}")
        sys.exit(EXIT_OK)
    else:
        print(f"结果: ✅ 全部通过 — 数据健康")
        sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
