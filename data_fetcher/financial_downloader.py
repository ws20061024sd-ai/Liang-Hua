"""
财务数据下载器 —— 从 AKShare（Sina 源）获取 ROE/PB/利润率等因子数据

数据存储：SQLite → financial_data 表
更新策略：季度更新（和财报发布频率一致）
来源：AKShare stock_financial_analysis_indicator（Sina，稳定可用）

我们的多因子模型：
  - 价值因子：PB（市净率）= close ÷ 每股净资产
  - 质量因子：ROE（净资产收益率）
  - 动量因子：收盘价 12月涨幅（剔除近1月）—— 从已有日线数据计算
"""
import sqlite3
import time
import pandas as pd
import numpy as np
from config import settings


def init_financial_table():
    """创建财务数据表"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS financial_data (
            code        TEXT,
            date        TEXT,      -- 报告期（如 2025-12-31）
            roe         REAL,      -- ROE 净资产收益率（%）
            roa         REAL,      -- ROA 总资产净利润率（%）
            bvps        REAL,      -- 每股净资产（元）
            eps         REAL,      -- 每股收益（元）
            net_margin  REAL,      -- 销售净利率（%）
            gross_margin REAL,     -- 主营业务利润率（%）
            profit_yoy  REAL,      -- 净利润同比增长（%）
            revenue_yoy REAL,      -- 营收同比增长（%）
            PRIMARY KEY (code, date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_financial_code_date
        ON financial_data(code, date)
    """)
    conn.commit()
    conn.close()


def _to_code(code: str) -> str:
    """补齐6位股票代码"""
    return str(code).zfill(6)


def download_financial_data(codes: list, years: list = None) -> pd.DataFrame | None:
    """
    从 AKShare 下载财务指标数据（最近一年，增量）

    策略：只下载最近年份（2026），历史数据后续增量补充。
    每只股票约 2 秒，300 只约 10 分钟。
    """
    try:
        import akshare as ak
    except ImportError:
        print("   ❌ AKShare 未安装")
        return None

    if years is None:
        years = [2025, 2026]  # 只取最近两年

    all_rows = []
    total = len(codes)
    fail_count = 0

    print(f"   📡 AKShare 下载财务数据（{total} 只，{years[0]}-{years[-1]}年）...")

    for i, code in enumerate(codes):
        try:
            df = ak.stock_financial_analysis_indicator(
                symbol=code,
                start_year=str(years[0])
            )
            if df is None or df.empty:
                fail_count += 1
                continue

            col_map = {
                '日期': 'date',
                '净资产收益率(%)': 'roe',
                '总资产净利润率(%)': 'roa',
                '每股净资产_调整前(元)': 'bvps',
                '摊薄每股收益(元)': 'eps',
                '销售净利率(%)': 'net_margin',
                '主营业务利润率(%)': 'gross_margin',
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            keep_cols = ['date', 'roe', 'roa', 'bvps', 'eps', 'net_margin', 'gross_margin']
            df = df[[c for c in keep_cols if c in df.columns]]
            df['code'] = code
            df['profit_yoy'] = None
            df['revenue_yoy'] = None
            all_rows.append(df)

        except Exception:
            fail_count += 1

        if (i + 1) % 100 == 0:
            print(f"      [{i+1}/{total}] 完成（失败 {fail_count}）")

        time.sleep(0.05)

    if not all_rows:
        print(f"   ❌ AKShare 全部失败（{fail_count}/{total}）")
        return None

    result = pd.concat(all_rows, ignore_index=True)
    print(f"   ✅ 下载成功：{len(all_rows)} 只，{len(result)} 条季度记录（失败 {fail_count}）")
    return result


def save_financial_data(df: pd.DataFrame):
    """保存财务数据到 SQLite"""
    conn = sqlite3.connect(settings.DB_PATH)

    cols = ['code', 'date', 'roe', 'roa', 'bvps', 'eps',
            'net_margin', 'gross_margin', 'profit_yoy', 'revenue_yoy']
    available = [c for c in cols if c in df.columns]
    rows = df[available].values.tolist()

    conn.executemany(f"""
        INSERT OR REPLACE INTO financial_data
        ({', '.join(available)})
        VALUES ({', '.join(['?'] * len(available))})
    """, rows)

    conn.commit()
    conn.close()
    print(f"   💾 保存 {len(rows)} 条季度财务数据")


def verify_financial_data() -> dict:
    """验证财务数据质量"""
    conn = sqlite3.connect(settings.DB_PATH)
    issues = []

    cnt = conn.execute("SELECT COUNT(*) FROM financial_data").fetchone()[0]
    stock_cnt = conn.execute(
        "SELECT COUNT(DISTINCT code) FROM financial_data"
    ).fetchone()[0]

    # 检查 ROE 异常值
    roe_bad = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE roe IS NOT NULL AND (roe < -100 OR roe > 100)"
    ).fetchone()[0]

    # 检查每股净资产
    bvps_null = conn.execute(
        "SELECT COUNT(*) FROM financial_data WHERE bvps IS NULL"
    ).fetchone()[0]

    conn.close()

    if cnt < 500:
        issues.append(f"数据量偏少（{cnt}条季度记录）")
    if stock_cnt < 200:
        issues.append(f"覆盖率不足（{stock_cnt}/300只）")
    if roe_bad > 20:
        issues.append(f"ROE 异常值 {roe_bad} 条")
    if bvps_null > cnt * 0.3:
        issues.append(f"每股净资产缺失 {bvps_null} 条")

    if issues:
        print(f"\n⚠️ 财务数据质量：")
        for i in issues:
            print(f"  - {i}")
        return {'ok': False, 'issues': issues}
    else:
        print(f"✅ 财务数据正常（{cnt}条季度记录，{stock_cnt}只股票）")
        return {'ok': True, 'issues': []}


# ============================================================
# 估值计算辅助函数（供因子模块使用）
# ============================================================

def compute_pb(db_path: str = None) -> pd.DataFrame:
    """
    从现有数据计算 PB（市净率）

    PB = 收盘价 ÷ 每股净资产
    使用最新的股价和最近季度的每股净资产
    """
    if db_path is None:
        db_path = settings.DB_PATH

    conn = sqlite3.connect(db_path)

    # 获取每只股票最新收盘价
    df_price = pd.read_sql_query("""
        SELECT d.code, d.close, MAX(d.date) as price_date
        FROM daily_kline d
        GROUP BY d.code
    """, conn)

    # 获取每只股票最近季度的每股净资产
    df_bvps = pd.read_sql_query("""
        SELECT f.code, f.bvps, f.roe, MAX(f.date) as fin_date
        FROM financial_data f
        WHERE f.bvps IS NOT NULL
        GROUP BY f.code
    """, conn)

    conn.close()

    if df_bvps.empty:
        return pd.DataFrame()

    merged = df_price.merge(df_bvps, on='code', how='inner')
    merged['pb'] = (merged['close'] / merged['bvps']).round(2)
    merged = merged[merged['pb'] > 0]  # 过滤异常
    return merged


# ============================================================
# 主入口
# ============================================================

def download_all_financial(force: bool = False):
    """
    下载全部沪深300成分股的财务数据
    """
    init_financial_table()

    conn = sqlite3.connect(settings.DB_PATH)
    codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()
    conn.close()

    print(f"\n📊 下载 {len(codes)} 只股票的财务数据...")

    df = download_financial_data(codes)

    if df is None or df.empty:
        print("   ❌ 财务数据下载完全失败")
        return

    save_financial_data(df)
    verify_financial_data()


if __name__ == "__main__":
    download_all_financial()
