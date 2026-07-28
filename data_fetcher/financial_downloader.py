"""
财务数据下载器 —— Baostock 顺序下载版

数据源: Baostock（完全免费，无需注册，K线接口直接返回 PE/PB/PS/PCF）
速度: 顺序下载，300只 × 7年 ≈ 90秒（Baostock C扩展不支持多线程并发登录）
"""
import sqlite3
import time
import pandas as pd
import numpy as np
from config import settings

# --- Baostock + Pandas 3.x 兼容补丁 ---
# Baostock 0.9.2 内部使用了 DataFrame.append()，该方法在 Pandas 2.0+ 中已被移除。
# 必须在 import baostock 之前给 pandas 打全局补丁，因为 baostock 内部 import 后直接使用。
def _make_append_compat():
    """为 pandas DataFrame 恢复已移除的 append 方法"""
    if not hasattr(pd.DataFrame, 'append'):
        def _append(self, other, ignore_index=False, sort=False):
            return pd.concat([self, other], ignore_index=ignore_index, sort=sort)
        pd.DataFrame.append = _append

_make_append_compat()

try:
    import baostock as bs
except ImportError:
    bs = None


def init_financial_table():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_data (
            code TEXT, date TEXT, close REAL, pe REAL, pb REAL, ps REAL, pcf REAL,
            roe REAL, roa REAL, gross_margin REAL, net_margin REAL,
            revenue_yoy REAL, profit_yoy REAL, market_cap REAL, circ_mv REAL, total_assets REAL,
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_financial_code_date ON financial_data(code, date)")
    conn.commit()
    conn.close()


def _bs_code(code: str) -> str:
    return f'sh.{code}' if code.startswith('6') else f'sz.{code}' if code.startswith(('0','3')) else None


def _safe_float(val) -> float | None:
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _download_one(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """
    下载单只股票 Baostock 数据（在主线程登录后调用）

    自动重试3次（退避0.5s/1.0s），返回 DataFrame 或 None。
    """
    bs_code = _bs_code(code)
    if bs_code is None:
        return None

    for retry in range(3):
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,close,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2"
            )
            if rs.error_code == '0':
                df = rs.get_data()
                if df is not None and not df.empty:
                    df = df.rename(columns={
                        'date': 'date', 'close': 'close',
                        'peTTM': 'pe', 'pbMRQ': 'pb',
                        'psTTM': 'ps', 'pcfNcfTTM': 'pcf',
                    })
                    df['code'] = code
                    keep = ['code', 'date', 'close', 'pe', 'pb', 'ps', 'pcf']
                    df = df[[c for c in keep if c in df.columns]]
                    for col in ['close', 'pe', 'pb', 'ps', 'pcf']:
                        if col in df.columns:
                            df[col] = df[col].apply(_safe_float)
                    return df
            else:
                if retry < 2:
                    time.sleep(0.5 * (retry + 1))
        except Exception:
            if retry < 2:
                time.sleep(0.5 * (retry + 1))
    return None


def download_financial_data(codes: list, start_date: str = None) -> pd.DataFrame | None:
    """
    顺序下载 Baostock PE/PB/PS/PCF 估值数据

    采用单线程顺序下载（Baostock 的 C 扩展不支持多线程并发登录）。
    300只 × 7年 ≈ 90秒，对月频财务数据完全可接受。
    失败股票自动重试3次（退避0.5s/1.0s）。
    """
    if bs is None:
        print("   ❌ Baostock 未安装: pip install baostock")
        return None
    if start_date is None:
        start_date = settings.FINANCIAL_START_DATE

    end_date = pd.Timestamp.now().strftime('%Y-%m-%d')

    # 登录一次，顺序下载所有股票
    lg = bs.login()
    if lg.error_code != '0':
        print(f"   ❌ Baostock 登录失败: {lg.error_msg}")
        return None

    total = len(codes)
    print(f"   📡 Baostock 顺序下载（{total} 只，{start_date} 起）...")

    all_rows, fail_count = [], 0

    try:
        for i, code in enumerate(codes):
            df = _download_one(code, start_date, end_date)
            if df is not None and not df.empty:
                all_rows.append(df)
            else:
                fail_count += 1

            # 进度显示
            if (i + 1) % 50 == 0:
                print(f"      [{i+1}/{total}] 完成（失败 {fail_count}）")
    finally:
        bs.logout()

    if not all_rows:
        print(f"   ❌ 全部失败（{fail_count}/{total}）")
        return None

    result = pd.concat(all_rows, ignore_index=True)
    result['date'] = pd.to_datetime(result['date']).dt.strftime('%Y-%m-%d')
    print(f"   ✅ {len(all_rows)} 只成功，{len(result):,} 条（失败 {fail_count}）")
    return result


def save_financial_data(df: pd.DataFrame):
    conn = sqlite3.connect(settings.DB_PATH)
    try:
        for col in ['close', 'ps', 'pcf', 'roe', 'roa', 'gross_margin', 'net_margin',
                    'revenue_yoy', 'profit_yoy', 'market_cap', 'circ_mv', 'total_assets']:
            if col not in df.columns:
                df[col] = None
        cols = ['code', 'date', 'close', 'pe', 'pb', 'ps', 'pcf',
                'roe', 'roa', 'gross_margin', 'net_margin',
                'revenue_yoy', 'profit_yoy', 'market_cap',
                'circ_mv', 'total_assets']
        rows = df[cols].values.tolist()
        conn.executemany(f"INSERT OR REPLACE INTO financial_data ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", rows)
        conn.commit()
        print(f"   💾 保存 {len(rows)} 条")
    finally:
        conn.close()


def fix_financial_data():
    conn = sqlite3.connect(settings.DB_PATH)
    try:
        pe = conn.execute(f"UPDATE financial_data SET pe=NULL WHERE pe IS NOT NULL AND (pe<{settings.PE_MIN_VALID} OR pe>{settings.PE_MAX_VALID})").rowcount
        pb = conn.execute(f"UPDATE financial_data SET pb=NULL WHERE pb IS NOT NULL AND (pb<0 OR pb>{settings.PB_MAX_VALID})").rowcount
        conn.commit()
    finally:
        conn.close()
    if pe + pb > 0:
        print(f"   🔧 清洗异常值：PE {pe} 条，PB {pb} 条")


def verify_financial_data(max_date: str = None) -> dict:
    conn = sqlite3.connect(settings.DB_PATH)
    issues = []
    if max_date is None:
        max_date = conn.execute("SELECT MAX(date) FROM financial_data").fetchone()[0]
    if max_date is None:
        conn.close()
        return {'ok': False, 'issues': ['财务数据为空']}
    total = conn.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0]
    stock_cnt = conn.execute("SELECT COUNT(DISTINCT code) FROM financial_data WHERE date=?", (max_date,)).fetchone()[0]
    pe_total = conn.execute("SELECT COUNT(*) FROM financial_data WHERE date=?", (max_date,)).fetchone()[0]
    pe_valid = conn.execute("SELECT COUNT(*) FROM financial_data WHERE date=? AND pe IS NOT NULL", (max_date,)).fetchone()[0]
    pe_null_rate = 1 - pe_valid / pe_total if pe_total > 0 else 0
    pb_valid = conn.execute("SELECT COUNT(*) FROM financial_data WHERE date=? AND pb IS NOT NULL", (max_date,)).fetchone()[0]
    pb_null_rate = 1 - pb_valid / pe_total if pe_total > 0 else 0
    pe_bad = conn.execute(f"SELECT COUNT(*) FROM financial_data WHERE date=? AND pe IS NOT NULL AND (pe<{settings.PE_MIN_VALID} OR pe>{settings.PE_MAX_VALID})", (max_date,)).fetchone()[0]
    conn.close()

    if total < 50000: issues.append(f"总量偏低（{total}条）")
    if stock_cnt < settings.FINANCIAL_MIN_STOCKS: issues.append(f"覆盖不足（{stock_cnt}/{settings.FINANCIAL_MIN_STOCKS}只）")
    if pe_null_rate > settings.FINANCIAL_MAX_NULL_PCT: issues.append(f"PE缺失{pe_null_rate:.0%}")
    if pb_null_rate > settings.FINANCIAL_MAX_NULL_PCT: issues.append(f"PB缺失{pb_null_rate:.0%}")
    if pe_bad > 0: issues.append(f"PE异常残留{pe_bad}条")

    ok = len(issues) == 0
    if ok:
        print(f"✅ 财务数据正常（{total}条，{stock_cnt}只，PE缺失{pe_null_rate:.0%}）")
    else:
        for i in issues: print(f"  - {i}")
    return {'ok': ok, 'issues': issues, 'max_date': max_date, 'stock_count': stock_cnt, 'pe_null_rate': pe_null_rate}


def download_all_financial(force: bool = False):
    init_financial_table()
    conn = sqlite3.connect(settings.DB_PATH)
    codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()

    # 增量模式：已有数据时只下载最近30天（首次全量下载约90秒，增量约5秒）
    if not force:
        last_date = conn.execute("SELECT MAX(date) FROM financial_data").fetchone()[0]
        if last_date:
            start_date = (pd.Timestamp(last_date) - pd.DateOffset(days=7)).strftime('%Y-%m-%d')
        else:
            start_date = settings.FINANCIAL_START_DATE
    else:
        start_date = settings.FINANCIAL_START_DATE

    conn.close()
    if not codes:
        print("❌ 股票池为空")
        return
    print(f"\n📊 Baostock 顺序下载 {len(codes)} 只股票估值数据（{start_date} 起）...")
    df = download_financial_data(codes, start_date=start_date)
    if df is None or df.empty:
        print("   ❌ 完全失败")
        return
    save_financial_data(df)
    fix_financial_data()
    verify_financial_data(df['date'].max())


if __name__ == "__main__":
    download_all_financial()
