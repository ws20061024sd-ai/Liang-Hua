"""
ROE 数据下载器 —— 从 AKShare 同花顺接口拉取净资产收益率

数据源: ak.stock_financial_abstract_ths() → 净资产收益率
存储: financial_roe(code, date, roe) — 季度数据
用法: python -m data_fetcher.roe_downloader
"""
import sqlite3
import time
import sys
import pandas as pd
from config import settings

DB = settings.DB_PATH


def create_table():
    """创建 financial_roe 表"""
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_roe (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            roe REAL NOT NULL,
            PRIMARY KEY (code, date)
        )
    """)
    conn.commit()
    conn.close()


def download_roe(code: str) -> pd.DataFrame | None:
    """
    下载单只股票的 ROE 历史数据
    返回 DataFrame: [date, roe]，失败返回 None
    """
    import akshare as ak
    try:
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df is None or df.empty:
            return None
        df = df[['报告期', '净资产收益率']].copy()
        df.columns = ['date', 'roe']
        # 清理 ROE: '5.79%' → 5.79
        df['roe'] = df['roe'].astype(str).str.replace('%', '', regex=False)
        df['roe'] = pd.to_numeric(df['roe'], errors='coerce')
        df = df.dropna(subset=['roe'])
        df['code'] = code
        return df[['code', 'date', 'roe']]
    except Exception as e:
        print(f"  ⚠️ {code} 下载失败: {e}", file=sys.stderr)
        return None


def save_roe(conn: sqlite3.Connection, df: pd.DataFrame):
    """批量写入 ROE 数据"""
    for _, row in df.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO financial_roe (code, date, roe) VALUES (?, ?, ?)",
            (row['code'], str(row['date']), float(row['roe']))
        )


def get_csi300_codes() -> list[str]:
    """获取沪深300成分股代码列表"""
    import akshare as ak
    df = ak.index_stock_cons_weight_csindex(symbol='000300')
    return sorted(df['成分券代码'].unique().tolist())


def download_all():
    """下载全部 CSI300 成分股的 ROE 数据"""
    codes = get_csi300_codes()
    print(f"📊 ROE 下载: {len(codes)} 只股票\n")

    create_table()
    conn = sqlite3.connect(DB)

    success = 0
    fail = 0
    rows = 0
    t0 = time.time()

    for i, code in enumerate(codes):
        df = download_roe(code)
        if df is not None and not df.empty:
            save_roe(conn, df)
            success += 1
            rows += len(df)
            conn.commit()
            # 进度条
            pct = (i + 1) / len(codes) * 100
            elapsed = time.time() - t0
            eta = elapsed / (i + 1) * (len(codes) - i - 1) if i > 0 else 0
            print(f"\r[{i+1:3d}/{len(codes)}] {pct:5.1f}% | "
                  f"✅ {success} | ❌ {fail} | "
                  f"预计剩余 {eta:.0f}s", end='', flush=True)
        else:
            fail += 1
            print(f"\r[{i+1:3d}/{len(codes)}] {pct:5.1f}% | "
                  f"✅ {success} | ❌ {fail}", end='', flush=True)
        time.sleep(0.1)  # 限速

    conn.close()
    elapsed = time.time() - t0
    print(f"\n\n✅ 完成: {success}/{len(codes)} 成功 | {rows} 行 | 耗时 {elapsed:.0f}s")

    # 验证
    _verify()


def _verify():
    """验证 ROE 数据覆盖率"""
    conn = sqlite3.connect(DB)
    code_count = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM financial_roe"
    ).fetchone()[0]
    row_count = conn.execute(
        "SELECT COUNT(*) FROM financial_roe"
    ).fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(date), MAX(date) FROM financial_roe"
    ).fetchone()
    # 最近一季度覆盖率
    latest = conn.execute("SELECT MAX(date) FROM financial_roe").fetchone()[0]
    recent_cnt = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM financial_roe WHERE date=?",
        (latest,)
    ).fetchone()[0]
    conn.close()

    print(f"\n📋 验证:")
    print(f"  股票数: {code_count}/300")
    print(f"  总行数: {row_count}")
    print(f"  日期范围: {date_range[0]} ~ {date_range[1]}")
    print(f"  最新季度({latest}): {recent_cnt}/300")


if __name__ == "__main__":
    download_all()
