"""
数据下载器 —— 从 AKShare 获取行情数据，存入 SQLite
"""
import os
import sqlite3
import time
import pandas as pd
from data_fetcher.proxy_cleanup import cleanup_proxy
cleanup_proxy()  # 必须在 import akshare 之前清除代理

import akshare as ak  # noqa: E402
from config import settings


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
            is_st       INTEGER DEFAULT 0,
            updated_at  TEXT
        )
    """)

    # 日线行情表（股票成分）
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


def _cache_stale(max_updated: str | None, force: bool, refresh_days: int) -> bool:
    """成分股缓存是否过期：强制刷新、无更新时间记录（旧库）、或超期未刷新"""
    if force:
        return True
    if not max_updated:
        return True  # 旧库无 updated_at 记录 → 触发一次刷新补写
    last = pd.Timestamp(max_updated)
    return (pd.Timestamp.now() - last).days > refresh_days


def fetch_hs300_constituents(force_refresh: bool = False) -> pd.DataFrame:
    """
    获取沪深300成分股列表（优先本地缓存，超 CONSTITUENT_REFRESH_DAYS 天自动刷新）

    沪深300成分股每半年调整一次（6月/12月），没必要每次运行都调API，
    但也不能永久冻结——缓存超期后自动调 API 刷新（2026-08-16 审查问题3）。
    首次运行调用API成功后存入 stock_info 表，后续直接从表里读。

    API调用加了30秒超时保护，超时或失败时自动降级到本地缓存。
    """
    # ── 优先读本地缓存（stock_info 表），超期则刷新 ──
    conn = get_db_connection()
    # 旧库兼容：补 updated_at 列（幂等，已存在时忽略）
    try:
        conn.execute("ALTER TABLE stock_info ADD COLUMN updated_at TEXT")
        conn.commit()
    except Exception as e:
        if 'duplicate column' not in str(e).lower():
            print(f"⚠️ [成分股] 加列 updated_at 失败: {e}")
    cached_count = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    max_updated = conn.execute("SELECT MAX(updated_at) FROM stock_info").fetchone()[0]
    conn.close()

    refresh_days = getattr(settings, 'CONSTITUENT_REFRESH_DAYS', 30)
    if cached_count >= 200 and not _cache_stale(max_updated, force_refresh, refresh_days):
        conn = get_db_connection()
        df = pd.read_sql_query("SELECT code, name FROM stock_info ORDER BY code", conn)
        conn.close()
        print(f"📋 成分股列表：{len(df)} 只（本地缓存，跳过API）")
        return df

    # ── 缓存为空或强制刷新 → 调API ──
    print("📡 获取沪深300成分股列表（API）...")

    def _call_api():
        """实际API调用逻辑"""
        try:
            df = ak.index_stock_cons_csindex(symbol="000300")
            return pd.DataFrame({
                'code': df['成分券代码'].astype(str).str.zfill(6),
                'name': df['成分券名称']
            })
        except Exception as e:
            print(f"   ⚠️ 中证指数源失败，切换东财备用: {e}")
        # 备用：东财接口
        df = ak.index_stock_cons(symbol="000300")
        return pd.DataFrame({
            'code': df['品种代码'].astype(str).str.zfill(6),
            'name': df['品种名称']
        })

    # 用 signal.alarm 做超时保护（Unix only，服务器和Mac都支持）
    result = None
    try:
        import signal

        def _on_timeout(signum, frame):
            raise TimeoutError("API 调用超时（30秒）")

        old = signal.signal(signal.SIGALRM, _on_timeout)
        signal.alarm(30)
        try:
            result = _call_api()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    except (TimeoutError, Exception) as e:
        print(f"   ⚠️ API 调用失败: {e}")

    # ── API成功 → 返回 + 更新缓存 ──
    if result is not None and not result.empty:
        print(f"   获取到 {len(result)} 只成分股")
        return result

    # ── API失败 → 降级到本地缓存 ──
    conn = get_db_connection()
    cached_count = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
    if cached_count >= 200:
        df = pd.read_sql_query("SELECT code, name FROM stock_info ORDER BY code", conn)
        conn.close()
        print(f"   📋 降级使用本地缓存（{len(df)} 只）")
        return df
    conn.close()

    raise RuntimeError(
        "❌ 无法获取沪深300成分股：API 不可达且本地无缓存。\n"
        "   请检查服务器网络（ping eastmoney.com），或在本地 Mac 运行一次 python run.py 生成缓存后同步 DB 到服务器。"
    )


def save_stock_info(conn, stocks: pd.DataFrame):
    """保存股票基本信息到数据库（记录更新时间，供缓存刷新判断）

    同步模式：删除不在新成分列表中的旧行——已调出沪深300的股票
    必须离开股票池，否则会继续产生买卖信号（审查问题3 + 补修）。
    注意：降级路径传入的 stocks 是本地缓存（= 当前池），NOT IN 为空，
    不会误删；仅 API 刷新成功时列表变化，才会删调出股。
    """
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    codes = [str(r) for r in stocks['code'].tolist()]
    if codes:
        placeholders = ','.join('?' * len(codes))
        conn.execute(
            f"DELETE FROM stock_info WHERE code NOT IN ({placeholders})", codes)
    for _, row in stocks.iterrows():
        conn.execute("""
            INSERT OR REPLACE INTO stock_info (code, name, updated_at)
            VALUES (?, ?, ?)
        """, (str(row['code']), row['name'], today))
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
        except Exception as e:
            print(f"   ⚠️ {code} 数据源均失败: {e}")  # 由上层重试逻辑统一处理

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
    try:
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
    finally:
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
    下载所有沪深300成分股的历史数据（增量更新 + 多线程并发）

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
    if hasattr(settings, 'DAILY_START_DATE') and settings.DAILY_START_DATE:
        start_default = min(start_default, settings.DAILY_START_DATE)

    total = len(stocks)

    # ── 批量查询所有股票的最新日期（1次SQL替代300次）──
    last_dates = {}
    first_dates = {}
    rows = conn.execute("SELECT code, MIN(date), MAX(date) FROM daily_kline GROUP BY code").fetchall()
    for code, first_d, last_d in rows:
        last_dates[code] = last_d
        first_dates[code] = first_d

    print(f"\n📥 下载/更新 {total} 只股票（并发模式）...")
    print(f"   数据范围: {start_default} ~ {today}")
    print(f"   已有数据: {len(last_dates)} 只\n")

    # ── 构建下载任务列表 ──
    tasks = []
    skip_count = 0
    for _, row in stocks.iterrows():
        code = row['code']
        name = row['name']
        last_date = last_dates.get(code)

        if not force_update and last_date:
            last_dt = pd.Timestamp(last_date)
            if last_dt >= pd.Timestamp(today):
                skip_count += 1
                continue
            start_date = (last_dt + pd.DateOffset(days=1)).strftime('%Y-%m-%d')
        else:
            start_date = start_default

        # 检查前向回填
        first_date = first_dates.get(code)
        gap_start = None
        gap_end = None
        if first_date and first_date > start_default and not force_update:
            gap_start = start_default
            gap_end = (pd.Timestamp(first_date) - pd.DateOffset(days=1)).strftime('%Y-%m-%d')

        tasks.append({'code': code, 'name': name, 'start': start_date,
                       'gap_start': gap_start, 'gap_end': gap_end})

    print(f"   已最新: {skip_count} 只 | 需更新: {len(tasks)} 只\n")

    # ── 串行下载（无sleep, AKShare不支持多线程）──
    new_data_count = 0
    fail_count = 0
    failed_codes = []

    for i, task in enumerate(tasks):
        code = task['code']
        name = task['name']
        start = task['start']

        # 前向回填
        if task['gap_start']:
            gap_df = download_stock_history(code, task['gap_start'], task['gap_end'])
            if gap_df is not None and not gap_df.empty:
                save_kline(conn, gap_df)

        # 增量下载（3次重试）
        df = None
        for retry in range(3):
            df = download_stock_history(code, start, today)
            if df is not None and not df.empty:
                break
            if retry < 2:
                time.sleep(0.3 * (retry + 1))

        if df is not None and not df.empty:
            save_kline(conn, df)
            new_data_count += 1
            rows = len(df)
            date_range = f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}"
            if i % 20 == 0 or i <= 2:
                print(f"   [{i+1}/{len(tasks)}] {code} {name} +{rows}条 ({date_range})")
        elif pd.Timestamp(start) > pd.Timestamp(today):
            pass
        else:
            fail_count += 1
            failed_codes.append(code)
            if fail_count <= 3:
                print(f"   ⚠️ {code} {name} 下载失败")

    # ── 第二轮：串行重试失败列表（保持现有逻辑）──
    if failed_codes:
        print(f"\n🔄 第二轮串行重试 {len(failed_codes)} 只失败股票...")
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
    print(f"   下载失败:   {len(failed_codes)} 只")
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
        from data_fetcher.trading_calendar import is_trading_day
        if not is_trading_day():
            print(f"📅 今日非交易日，最新数据 {max_date}，正常")
        else:
            issues.append(f"数据未更新到今日（最新:{max_date}，今日:{today}）")

    # 2. 检查当日股票数量
    cnt = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date=?", (max_date,)).fetchone()[0]
    if cnt < settings.MIN_STOCK_COUNT:
        issues.append(f"数据覆盖率不足（{cnt}/300只）")

    # 3. 检查 pct_change 是否全部非空
    nulls = conn.execute(
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND pct_change IS NULL", (max_date,)
    ).fetchone()[0]
    if nulls > 0:
        issues.append(f"涨跌幅缺失 {nulls} 条")

    # 4. 检查行业数据（表可能还不存在）
    try:
        sector_count = conn.execute(
            "SELECT COUNT(DISTINCT name) FROM sector_history WHERE date=?", (max_date,)
        ).fetchone()[0]
    except:
        sector_count = 0
    if sector_count < settings.MIN_SECTOR_COUNT and sector_count > 0:
        issues.append(f"行业数据不足（{sector_count}/90个行业）")

    # 5. 检查涨跌幅极值
    extreme = conn.execute(
        "SELECT COUNT(*) FROM daily_kline WHERE date=? AND abs(pct_change) > ?",
        (max_date, settings.MAX_EXTREME_PCT)
    ).fetchone()[0]
    if extreme > settings.MAX_EXTREME_COUNT:
        issues.append(f"涨跌幅异常值 {extreme} 条（|pct|>{settings.MAX_EXTREME_PCT}%）")

    # 6. 检查估值数据覆盖度（financial_data 表可能还不存在）
    try:
        fin_stocks = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM financial_data WHERE date=?",
            (max_date,)
        ).fetchone()[0]
        if fin_stocks > 0 and fin_stocks < settings.FINANCIAL_MIN_STOCKS:
            issues.append(f"估值数据覆盖不足（{fin_stocks}/{settings.FINANCIAL_MIN_STOCKS}只）")
    except Exception as e:
        print(f"   ⚠️ [质量检查] 估值覆盖检查跳过: {e}")

    # 7. 检查 PE 异常值残留
    try:
        pe_bad = conn.execute(
            f"SELECT COUNT(*) FROM financial_data WHERE date=? AND pe IS NOT NULL AND (pe < {settings.PE_MIN_VALID} OR pe > {settings.PE_MAX_VALID})",
            (max_date,)
        ).fetchone()[0]
        if pe_bad > 0:
            issues.append(f"PE 异常值残留 {pe_bad} 条（需运行 fix_financial_data）")
    except Exception as e:
        print(f"   ⚠️ [质量检查] PE 异常值检查跳过: {e}")

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
