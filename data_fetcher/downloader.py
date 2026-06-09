"""
数据下载器 —— 从 AKShare 获取行情数据，存入 SQLite
"""
import os
import sqlite3
import time
import pandas as pd
import akshare as ak
from config import settings

# 清除所有代理环境变量（国内数据源直连更快更稳定）
for key in list(os.environ.keys()):
    if 'proxy' in key.lower():
        del os.environ[key]


def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # 提高写入性能
    return conn


def init_database():
    """初始化数据库表结构"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 股票基本信息表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS stock_info (
            code        TEXT PRIMARY KEY,
            name        TEXT,
            market      TEXT,
            listing_date TEXT,
            is_st       INTEGER DEFAULT 0
        )
    """)

    # 日线行情表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_kline (
            code        TEXT,
            date        TEXT,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            amount      REAL,
            pct_change  REAL,
            turnover    REAL,
            PRIMARY KEY (code, date)
        )
    """)

    # 创建索引加速查询
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_kline_code_date
        ON daily_kline(code, date)
    """)

    conn.commit()
    conn.close()
    print("✅ 数据库表结构已就绪")


def fetch_hs300_constituents() -> pd.DataFrame:
    """
    获取沪深300成分股列表
    返回 DataFrame: [code, name]
    """
    print("📡 获取沪深300成分股列表...")
    try:
        # 用中证指数官网接口
        df = ak.index_stock_cons_csindex(symbol="000300")
        result = pd.DataFrame({
            'code': df['成分券代码'].astype(str).str.zfill(6),
            'name': df['成分券名称']
        })
        print(f"   获取到 {len(result)} 只成分股")
        return result
    except Exception as e:
        print(f"   ⚠️ 中证指数接口失败: {e}")
        print("   尝试备用接口...")
        # 备用：用东财接口获取沪深300成分股
        df = ak.index_stock_cons(symbol="000300")
        result = pd.DataFrame({
            'code': df['品种代码'].astype(str).str.zfill(6),
            'name': df['品种名称']
        })
        print(f"   备用接口获取到 {len(result)} 只成分股")
        return result


def save_stock_info(conn, stocks: pd.DataFrame):
    """保存股票基本信息到数据库"""
    for _, row in stocks.iterrows():
        conn.execute("""
            INSERT OR REPLACE INTO stock_info (code, name)
            VALUES (?, ?)
        """, (row['code'], row['name']))
    conn.commit()


def get_last_date(conn, code: str) -> str | None:
    """获取某只股票在数据库中最新的数据日期"""
    cursor = conn.execute(
        "SELECT MAX(date) FROM daily_kline WHERE code = ?", (code,)
    )
    result = cursor.fetchone()
    return result[0] if result else None


def download_stock_history(code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """
    下载单只股票的历史日线数据（前复权）
    优先使用新浪数据源，失败时尝试东方财富
    返回 DataFrame 或 None（失败时）
    """
    df = None

    # 方案一：新浪数据源（稳定）
    try:
        # 根据代码判断市场前缀
        if code.startswith('6'):
            symbol = f'sh{code}'
        elif code.startswith(('0', '3')):
            symbol = f'sz{code}'
        elif code.startswith(('4', '8')):
            symbol = f'bj{code}'
        else:
            symbol = f'sz{code}'  # 默认深圳

        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_date.replace('-', ''),
            end_date=end_date.replace('-', ''),
            adjust='qfq'
        )
        if df is not None and not df.empty:
            # 新浪返回的列名统一
            df = df.rename(columns={
                'date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
                'amount': 'amount',
            })
    except Exception:
        df = None

    # 方案二：东方财富数据源（备用）
    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"  # 前复权
            )
            if df is not None and not df.empty:
                df = df.rename(columns={
                    '日期': 'date',
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                    '成交额': 'amount',
                    '涨跌幅': 'pct_change',
                    '换手率': 'turnover',
                })
        except Exception:
            pass

    if df is None or df.empty:
        return None

    # 统一处理
    df['code'] = code
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

    # 补全缺失的列
    for col in ['pct_change', 'turnover']:
        if col not in df.columns:
            df[col] = None

    # 补全涨跌幅——优先用groupby计算，缺失的后续从DB补
    df['pct_change'] = df['pct_change'].fillna(
        df.groupby('code')['close'].pct_change() * 100
    )

    # 保留需要的列
    columns = ['code', 'date', 'open', 'high', 'low', 'close',
               'volume', 'amount', 'pct_change', 'turnover']
    return df[columns]


def fix_pct_change():
    """
    修复数据库中 NULL 的 pct_change（增量下载时单日数据算不出涨跌幅）
    用前一天收盘价补算
    """
    conn = get_db_connection()
    # 找到所有 pct_change 为 NULL 的行，用前一天收盘价计算
    conn.execute("""
        UPDATE daily_kline SET pct_change = (
            SELECT (d1.close - d2.close) / d2.close * 100
            FROM daily_kline AS d1
            JOIN daily_kline AS d2 ON d1.code = d2.code
            WHERE d1.code = daily_kline.code
              AND d1.date = daily_kline.date
              AND d2.date = (
                  SELECT MAX(date) FROM daily_kline AS d3
                  WHERE d3.code = d1.code AND d3.date < d1.date
              )
            LIMIT 1
        )
        WHERE pct_change IS NULL
    """)
    fixed = conn.total_changes
    conn.commit()
    conn.close()
    if fixed > 0:
        print(f"   🔧 修复了 {fixed} 条 pct_change 空值")


def save_kline(conn, df: pd.DataFrame):
    """保存日线数据到数据库（批量插入）"""
    rows = df[['code', 'date', 'open', 'high', 'low', 'close',
               'volume', 'amount', 'pct_change', 'turnover']].values.tolist()

    conn.executemany("""
        INSERT OR REPLACE INTO daily_kline
        (code, date, open, high, low, close, volume, amount, pct_change, turnover)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()


def download_all(force_update: bool = False):
    """
    下载所有沪深300成分股的历史数据（增量更新）

    参数:
        force_update: 是否强制重新下载所有数据
    """
    conn = get_db_connection()

    # 获取成分股列表
    stocks = fetch_hs300_constituents()
    save_stock_info(conn, stocks)

    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    start_default = pd.Timestamp.now() - pd.DateOffset(years=settings.YEARS_OF_DATA)
    start_default = start_default.strftime('%Y-%m-%d')

    total = len(stocks)
    new_data_count = 0
    skip_count = 0
    fail_count = 0
    failed_codes = []

    print(f"\n📥 开始下载/更新 {total} 只股票的日线数据...")
    print(f"   数据范围: {start_default} ~ {today}\n")

    for i, (_, row) in enumerate(stocks.iterrows()):
        code = row['code']
        name = row['name']

        # 检查是否需要更新
        last_date = get_last_date(conn, code)

        if not force_update and last_date:
            # 增量模式：从上一次的最后日期开始
            last_dt = pd.Timestamp(last_date)
            if last_dt >= pd.Timestamp(today):
                skip_count += 1
                if skip_count <= 3:
                    print(f"   [{i+1}/{total}] {code} {name} ✓ 已是最新")
                continue
            # 从最新日期后一天开始
            start_date = (last_dt + pd.DateOffset(days=1)).strftime('%Y-%m-%d')
        else:
            start_date = start_default

        # 下载数据（最多重试3次）
        df = None
        for retry in range(3):
            df = download_stock_history(code, start_date, today)
            if df is not None and not df.empty:
                break
            if retry < 2:
                time.sleep(0.5 * (retry + 1))  # 退避：0.5s, 1.0s

        if df is not None and not df.empty:
            save_kline(conn, df)
            new_data_count += 1
            rows = len(df)
            date_range = f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}"
            print(f"   [{i+1}/{total}] {code} {name} +{rows}条 ({date_range})")
        elif pd.Timestamp(start_date) <= pd.Timestamp(today):
            skip_count += 1
        else:
            fail_count += 1
            failed_codes.append(code)
            if fail_count <= 3:
                print(f"   ⚠️ {code} {name} 下载失败")

        time.sleep(0.15)

    # 第二轮：重试失败的股票（更长退避）
    if failed_codes:
        print(f"\n🔄 第二轮重试 {len(failed_codes)} 只失败股票...")
        for code in failed_codes.copy():
            time.sleep(1.0)
            df = download_stock_history(code, start_default, today)
            if df is not None and not df.empty:
                save_kline(conn, df)
                failed_codes.remove(code)
                new_data_count += 1
                print(f"   {code} ✅ 第二轮成功 ({len(df)}条)")
        if failed_codes:
            print(f"   ⚠️ {len(failed_codes)} 只仍失败: {failed_codes[:5]}")

    conn.close()

    print(f"\n{'='*50}")
    print(f"📊 下载完成统计:")
    print(f"   成分股总数: {total}")
    print(f"   本次更新:   {new_data_count} 只")
    print(f"   已是最新:   {skip_count} 只")
    print(f"   下载失败:   {fail_count} 只 → 第二轮后剩余 {len(failed_codes)} 只")
    print(f"{'='*50}")

    show_db_stats()


def show_db_stats():
    """显示数据库统计信息"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(DISTINCT code) FROM daily_kline")
    stock_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM daily_kline")
    row_count = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(date), MAX(date) FROM daily_kline")
    date_min, date_max = cursor.fetchone()

    conn.close()

    print(f"\n📋 数据库概况:")
    print(f"   股票数量: {stock_count} 只")
    print(f"   日线记录: {row_count:,} 条")
    print(f"   日期范围: {date_min} ~ {date_max}")


def verify_data_quality() -> dict:
    """
    每日数据质量检查——保证数据新鲜完整

    返回: {'ok': bool, 'issues': [str]}
    """
    from datetime import datetime
    conn = get_db_connection()
    issues = []
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. 检查最新数据日期
    max_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    if max_date != today:
        weekday = datetime.now().weekday()
        if weekday >= 5:
            print(f"📅 今日周末，最新数据 {max_date}，正常")
        else:
            issues.append(f"数据未更新到今日（最新:{max_date}，今日:{today}）")

    # 2. 检查当日股票数量
    cnt = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date=?", (max_date,)).fetchone()[0]
    if cnt < 280:
        issues.append(f"数据覆盖率不足（{cnt}/300只）")

    # 3. 检查 pct_change 是否全部非空
    nulls = conn.execute(
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL", (max_date,)
    ).fetchone()[0]
    if nulls > 0:
        issues.append(f"涨跌幅缺失 {nulls} 条")

    # 4. 检查行业数据
    sector_count = conn.execute(
        "SELECT COUNT(DISTINCT name) FROM sector_history WHERE date=?", (max_date,)
    ).fetchone()[0]
    if sector_count < 80:
        issues.append(f"行业数据不足（{sector_count}/90个行业）")

    # 5. 检查涨跌幅极值
    extreme = conn.execute(
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND abs(pct_change) > 15", (max_date,)
    ).fetchone()[0]
    if extreme > 5:
        issues.append(f"涨跌幅异常值 {extreme} 条（|pct|>15%）")

    conn.close()

    if issues:
        print(f"\n⚠️ 数据质量检查发现问题:")
        for i in issues:
            print(f"  - {i}")
        return {'ok': False, 'issues': issues}
    else:
        print(f"✅ 数据质量检查通过（{max_date}，{cnt}只，涨跌幅正常）")
        return {'ok': True, 'issues': []}


if __name__ == "__main__":
    init_database()
    download_all()
