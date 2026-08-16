#!/usr/bin/env python3
"""
沪深300指数数据下载器 + 全市场成交额聚合
用法: python data_fetcher/index_downloader.py          # 下载全量
      python data_fetcher/index_downloader.py --update  # 仅增量
"""
import sqlite3, sys, os
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

DB = settings.DB_PATH

def download_csi300():
    """从 AKShare 下载沪深300指数日线（全量历史）"""
    try:
        import akshare as ak
    except ImportError:
        print("❌ pip install akshare")
        return None

    print("下载沪深300指数 (sh000300)...")
    df = ak.stock_zh_index_daily(symbol="sh000300")
    # 列: date, open, close, high, low, volume
    df = df.rename(columns={'date': 'date', 'open': 'open', 'close': 'close',
                            'high': 'high', 'low': 'low', 'volume': 'volume'})
    # 日期统一为 YYYY-MM-DD
    df['date'] = df['date'].astype(str)
    df['amount'] = 0  # AKShare 指数日线不含成交额，另算
    print(f"  ✅ {len(df)} 条 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")
    return df


def update_index():
    """增量更新指数数据（run.py 每日调用）"""
    conn = sqlite3.connect(DB)
    # 确保表存在（全新安装时 run.py 直接调用本函数，不会先走 store_index_data）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            date TEXT PRIMARY KEY,
            open REAL, close REAL, high REAL, low REAL,
            volume REAL, amount REAL
        )
    """)
    last_date = conn.execute("SELECT MAX(date) FROM index_daily").fetchone()[0]
    conn.close()

    today = pd.Timestamp.now()
    if last_date:
        # 按日期比较（last_date 是 'YYYY-MM-DD'，与 now 的时刻比较会永不命中"已最新"）
        if last_date >= today.strftime('%Y-%m-%d'):
            print("   ✅ 指数数据已是最新")
            return
        lag_days = (today - pd.Timestamp(last_date)).days
        # 只差几天的话直接全量重下（指数只有一条时间序列，很快）
        if lag_days <= 7:
            print(f"   📡 指数数据落后 {lag_days} 天，增量下载...")
        else:
            print(f"   📡 指数数据落后 {lag_days} 天，全量刷新...")

    import akshare as ak
    df = ak.stock_zh_index_daily(symbol="sh000300")
    df = df.rename(columns={'date': 'date', 'open': 'open', 'close': 'close',
                            'high': 'high', 'low': 'low', 'volume': 'volume'})
    df['date'] = df['date'].astype(str)

    # 只保留新数据
    if last_date:
        df = df[df['date'] > last_date]

    if df.empty:
        print("   ℹ️ 无新指数数据")
        return

    rows = df[['date', 'open', 'close', 'high', 'low', 'volume']].values.tolist()
    conn = sqlite3.connect(DB)
    conn.executemany("""
        INSERT OR REPLACE INTO index_daily (date, open, close, high, low, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, rows)
    conn.commit()
    conn.close()
    print(f"   ✅ 指数 +{len(rows)} 条 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")

    # 新行 amount=0，回填当日全市场成交额（只回填缺失行，历史行不动）
    backfill_amount()

def store_index_data(df):
    """存入 index_daily 表"""
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_daily (
            date TEXT PRIMARY KEY,
            open REAL, close REAL, high REAL, low REAL,
            volume REAL, amount REAL
        )
    """)

    # 批量 upsert
    count = 0
    for _, row in df.iterrows():
        try:
            conn.execute("""
                INSERT OR REPLACE INTO index_daily (date, open, close, high, low, volume, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (str(row['date']), float(row['open']), float(row['close']),
                  float(row['high']), float(row['low']), float(row['volume']), float(row.get('amount', 0))))
            count += 1
        except Exception as e:
            print(f"   ⚠️ [指数] 行 {str(row.get('date', '?'))} 写入失败: {e}")

    conn.commit()
    print(f"  ✅ 写入 {count} 条到 index_daily")
    conn.close()

def compute_index_amount():
    """
    沪深300成交量(股) → 成交额(元)
    AKShare 返回的 volume 是手数，没有成交额字段。
    我们用成分股 SUM(amount) 来近似（更准确反映全市场情况）。
    """
    conn = sqlite3.connect(DB)
    df = conn.execute("""
        SELECT date, SUM(amount) as total_amount
        FROM daily_kline
        GROUP BY date
        ORDER BY date
    """).fetchall()
    conn.close()
    return {d: a for d, a in df if a}

def backfill_amount():
    """将全市场成交额写入 index_daily.amount（只回填 amount=0/IS NULL 的行）"""
    conn = sqlite3.connect(DB)
    amt_map = compute_index_amount()
    # 只处理缺失成交额的行（新插入的行 amount=0）；历史已有成交额的行不动
    zero_rows = conn.execute(
        "SELECT date FROM index_daily WHERE amount IS NULL OR amount = 0"
    ).fetchall()
    updated = 0
    for (date,) in zero_rows:
        amt = amt_map.get(date)
        if amt:
            conn.execute("UPDATE index_daily SET amount = ? WHERE date = ?", (amt, date))
            updated += 1
    conn.commit()
    conn.close()
    print(f"  ✅ 回填 {updated} 天成交额")

def verify():
    conn = sqlite3.connect(DB)
    cnt = conn.execute("SELECT COUNT(*) FROM index_daily").fetchone()[0]
    latest = conn.execute("SELECT MAX(date) FROM index_daily").fetchone()[0]
    earliest = conn.execute("SELECT MIN(date) FROM index_daily").fetchone()[0]
    # 最新一天的数据
    row = conn.execute(
        "SELECT date, close, amount FROM index_daily ORDER BY date DESC LIMIT 1"
    ).fetchone()
    # 成交额覆盖
    amt_ok = conn.execute(
        "SELECT COUNT(*) FROM index_daily WHERE amount > 0"
    ).fetchone()[0]
    conn.close()
    print(f"  验证: {cnt}条 · {earliest}~{latest} · 最新 {row[0]}: "
          f"收盘{row[1]:.0f}点 · 成交{row[2]/1e8:.0f}亿 · 成交额覆盖{amt_ok}天")

if __name__ == "__main__":
    df = download_csi300()
    if df is not None:
        store_index_data(df)
        backfill_amount()
        verify()
    print("✅ 完成")
