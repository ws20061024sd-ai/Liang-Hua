#!/usr/bin/env python3
"""
信号效果验证 —— L1 固定窗口预测力 + L2 等权动态账本

用法:
  python scripts/signal_outcome.py           # 每日增量结算
  python scripts/signal_outcome.py --replay  # 首次回放补齐历史

设计见 docs/superpowers/specs/2026-08-16-signal-outcome-design.md
"""
import sys, os, sqlite3, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from config import settings

DB = settings.DB_PATH

def init_outcome_table(conn=None):
    """创建结算结果表（幂等）"""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcome (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, code TEXT, name TEXT,
            strategy TEXT, action TEXT,
            source TEXT, kind TEXT,
            ref_price REAL, exec_price REAL, exit_price REAL,
            pnl REAL, excess REAL, hold_days INTEGER,
            exit_reason TEXT, status TEXT DEFAULT 'done',
            UNIQUE(date, code, strategy, action, kind, source)
        )
    """)
    conn.commit()
    if own:
        conn.close()

def _trading_dates(conn) -> list[str]:
    """daily_kline 去重日期（升序）——真实交易日历"""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_kline ORDER BY date").fetchall()]

def _kline(conn, code: str) -> pd.DataFrame:
    """单股全量日线（date/open/close，升序）"""
    return pd.read_sql_query(
        "SELECT date, open, close FROM daily_kline WHERE code=? ORDER BY date",
        conn, params=(code,))

def _open_price_at(df: pd.DataFrame, date: str) -> float | None:
    """某日期开盘价（成交用）；无该日数据返回 None"""
    row = df[df['date'] == date]
    return float(row.iloc[0]['open']) if not row.empty else None


# ── Task 2: L1 固定窗口结算 ──────────────────────────────────────────────

def _index_df(conn) -> pd.DataFrame:
    """沪深300指数全量（date/close 升序）"""
    return pd.read_sql_query(
        "SELECT date, close FROM index_daily ORDER BY date", conn)

def _nth_trading_day(dates: list[str], sig_date: str, n: int) -> str | None:
    """dates 中信号日之后的第 n 个交易日；不足返回 None"""
    try:
        i = dates.index(sig_date)
    except ValueError:
        return None
    j = i + n
    return dates[j] if j < len(dates) else None

def _pnl_pct(from_price: float, to_price: float) -> float:
    return (to_price / from_price - 1) * 100

def _settle_l1_core(conn, dates: list[str], idx_map: dict, kdf: pd.DataFrame,
                    date: str, code: str, name: str, strategy: str,
                    action: str, source: str, commit: bool = True) -> list[str]:
    """L1 固定窗口结算核心（幂等）

    dates/指数收盘映射/个股K线由调用方一次性预载——settle_l1 每信号每次调用都会
    全表扫日期 + 查指数 + 查个股K线；回放引擎每日期信号可达上百条、累计数万次，
    必须复用预载数据。commit=False 时由调用方统一提交（回放批处理用）。
    语义与 settle_l1 完全一致：
      收益 = 第N交易日收盘 / 信号日收盘 - 1，超额 = 收益 - 同期沪深300收益
    """
    sig_row = kdf[kdf['date'] == date]
    if sig_row.empty:
        return []
    ref_price = float(sig_row.iloc[0]['close'])
    if date not in idx_map:
        return []
    idx_ref = idx_map[date]

    written = []
    for n in settings.OUTCOME_WINDOWS:
        target = _nth_trading_day(dates, date, n)
        if target is None:
            continue  # 窗口未到，等后续每日结算
        row = kdf[kdf['date'] == target]
        idx_row = idx_map.get(target)
        if row.empty or idx_row is None:
            continue  # 该日无数据（停牌/退市）→ 跳过
        pnl = round(_pnl_pct(ref_price, float(row.iloc[0]['close'])), 3)
        excess = round(pnl - _pnl_pct(idx_ref, idx_row), 3)
        conn.execute("""INSERT OR IGNORE INTO signal_outcome
            (date, code, name, strategy, action, source, kind, ref_price, pnl, excess)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (date, code, name, strategy, action, source, f'l1_{n}d', ref_price, pnl, excess))
        written.append(f'l1_{n}d')
    if commit:
        conn.commit()
    return written

def settle_l1(conn, date: str, code: str, name: str, strategy: str,
              action: str, source: str) -> list[str]:
    """结算单条信号的 L1 固定窗口（5/10/20 交易日），幂等

    收益 = 第N交易日收盘 / 信号日收盘 - 1（信号日收盘 = signal_history.price，
    由调用方传入 date/code 后此处从 daily_kline 取该日收盘，与 price 同源）
    超额 = 收益 - 同期沪深300收益
    """
    dates = _trading_dates(conn)
    kdf = _kline(conn, code)
    idx = _index_df(conn)
    if kdf.empty or idx.empty:
        return []
    return _settle_l1_core(conn, dates, dict(zip(idx['date'], idx['close'])),
                           kdf, date, code, name, strategy, action, source)

def summary_l1(conn, source: str | None = None) -> list[dict]:
    """分策略 L1 汇总（胜率按 BUY 超额>0、SELL 超额<0 计命中）

    返回 [{'strategy', 'total', 'win_rate', 'avg_excess'}] 按 10 日窗口
    """
    q = """SELECT strategy, action, kind, COUNT(*) as n,
        AVG(excess) as avg_ex, SUM(CASE WHEN
            (action='BUY' AND excess>0) OR (action='SELL' AND excess<0)
            THEN 1 ELSE 0 END) as hit
        FROM signal_outcome WHERE kind='l1_10d'"""
    params = []
    if source:
        q += " AND source=?"
        params.append(source)
    q += " GROUP BY strategy, action"
    rows = conn.execute(q, params).fetchall()
    out = {}
    for strategy, action, kind, n, avg_ex, hit in rows:
        d = out.setdefault(strategy, {'strategy': strategy, 'total': 0,
                                      'win_rate': None, 'avg_excess': None})
        d['total'] += n
        d['hit_count'] = d.get('hit_count', 0) + (hit or 0)
        d['ex_sum'] = d.get('ex_sum', 0.0) + (avg_ex or 0) * n
    result = []
    for s, d in out.items():
        result.append({'strategy': s, 'total': d['total'],
            'win_rate': round(d['hit_count'] / d['total'] * 100, 1) if d['total'] else None,
            'avg_excess': round(d['ex_sum'] / d['total'], 2) if d['total'] else None})
    return sorted(result, key=lambda r: -(r['avg_excess'] or 0))

# ── Task 3: L2 等权账本撮合 ──────────────────────────────────────────────

class Ledger:
    """L2 等权账本撮合器

    语义：信号在 t 日收盘后产生 → t+1 开盘价成交。
    事件（pending）在 process_pending 用当日开盘价统一成交。
    止损/到期在 check_stop_loss/check_timeout 用当日收盘判定 → 次日开盘成交。
    同 code 多笔持仓 FIFO 配对。
    """

    def __init__(self):
        self.positions: dict[str, list[dict]] = {}  # code → [open 记录]
        self.pending: list[dict] = []               # 待次日开盘成交事件

    def _position(self, code: str, name: str, strategy: str, source: str,
                  date: str, exec_price: float) -> dict:
        return {'code': code, 'name': name, 'strategy': strategy,
                'source': source, 'buy_date': date, 'exec_price': exec_price,
                'hold_days': 0, 'signal_date': date}

    def on_signal(self, conn, date: str, code: str, name: str, strategy: str,
                  action: str, source: str) -> list[dict]:
        """登记一条当日信号：BUY → 排队次日开盘开仓；SELL 无持仓则忽略，
        有持仓则 FIFO 出队并排队次日开盘平仓（先出队再待成交，符合 FIFO 测试语义）"""
        if action == 'SELL':
            opens = self.positions.get(code) or []
            if not opens:
                return []
            pos = opens.pop(0)  # FIFO：平最早开仓的那笔
            self.pending.append({'kind': 'l2_close', 'date': date, 'code': code,
                'name': name, 'strategy': strategy, 'source': source,
                'pos': pos, 'exit_reason': 'sell'})
        elif action == 'BUY':
            self._queue_open(date, code, name, strategy, source)
        return []

    def _queue_open(self, date, code, name, strategy, source):
        self.pending.append({'kind': 'l2_open', 'date': date, 'code': code,
            'name': name, 'strategy': strategy, 'source': source,
            'exit_reason': None})

    def buy(self, conn, date: str, code: str, name: str, strategy: str,
            source: str) -> None:
        """BUY 信号 → 排队次日开盘开仓（等价 on_signal 的 BUY 分支）"""
        self._queue_open(date, code, name, strategy, source)

    def check_stop_loss(self, conn, date: str) -> list[dict]:
        """移动止损：当日收盘 < 持仓期峰值×(1-TRAILING_STOP) → 排队次日开盘平仓

        峰值维护：首个检查日用 [buy_date, 当日] 全窗口 close.max() 计算（日期上界，
        杜绝把 DB 里未来日期更高价当成峰值）并缓存进 pos['peak_close']；之后每日
        max(缓存, 当日收盘) 递增，与"自买日起到当日最高收盘"严格等价——回放期
        持仓可达数千笔，若每日每笔都重扫全窗口会退化到小时级。
        """
        triggered = []
        kdf_cache = {}
        cur_close = {}  # 当日收盘缓存（同 code 多笔持仓只查一次 K 线）
        for code, opens in list(self.positions.items()):
            kdf = kdf_cache.get(code)
            if kdf is None:
                kdf = _kline(conn, code)
                if kdf.empty:
                    continue
                kdf_cache[code] = kdf
            row = kdf[kdf['date'] == date]
            if row.empty:
                continue  # 当日无行情（停牌）→ 跳过
            cur = cur_close.get(code)
            if cur is None:
                cur = float(row.iloc[0]['close'])
                cur_close[code] = cur
            for i, pos in enumerate(opens):
                peak = pos.get('peak_close')
                if peak is None:
                    peak_rows = kdf[(kdf['date'] >= pos['buy_date']) & (kdf['date'] <= date)]
                    if peak_rows.empty:
                        continue
                    peak = float(peak_rows['close'].max())
                else:
                    peak = max(peak, cur)
                pos['peak_close'] = peak
                if cur < peak * (1 - settings.TRAILING_STOP):
                    pos = opens.pop(i)
                    self.pending.append({'kind': 'l2_close', 'date': date,
                        'code': code, 'name': pos['name'],
                        'strategy': pos['strategy'], 'source': pos['source'],
                        'pos': pos, 'exit_reason': 'stop_loss'})
                    triggered.append({'exit_reason': 'stop_loss', 'code': code})
                    break  # 该 code 当日只触发一笔，其余下次检查
        return triggered

    def check_timeout(self, conn, date: str) -> list[dict]:
        """持仓交易日计数 ≥ OUTCOME_MAX_HOLD_DAYS → 排队次日开盘平仓"""
        out = []
        for code, opens in list(self.positions.items()):
            for i, pos in enumerate(opens):
                pos['hold_days'] += 1
                if pos['hold_days'] >= settings.OUTCOME_MAX_HOLD_DAYS:
                    pos = opens.pop(i)
                    self.pending.append({'kind': 'l2_close', 'date': date,
                        'code': code, 'name': pos['name'],
                        'strategy': pos['strategy'], 'source': pos['source'],
                        'pos': pos, 'exit_reason': 'timeout'})
                    out.append({'exit_reason': 'timeout', 'code': code})
                    break
        return out

    def process_pending(self, conn, date: str) -> list[dict]:
        """用当日开盘价成交所有待处理事件（开仓/平仓），返回成交事件"""
        events = []
        kdf_cache = {}
        for ev in list(self.pending):
            kdf = kdf_cache.get(ev['code'])
            if kdf is None:
                kdf = _kline(conn, ev['code'])
                kdf_cache[ev['code']] = kdf
            op = _open_price_at(kdf, date)
            if op is None:
                continue  # 当日无开盘（停牌/数据缺）→ 保留待下次
            self.pending.remove(ev)
            if ev['kind'] == 'l2_open':
                pos = self._position(ev['code'], ev['name'], ev['strategy'],
                                     ev['source'], ev['date'], op)
                self.positions.setdefault(ev['code'], []).append(pos)
                events.append({'kind': 'l2_open', 'date': ev['date'],
                    'code': ev['code'], 'name': ev['name'],
                    'strategy': ev['strategy'], 'source': ev['source'],
                    'exec_price': op, 'signal_date': ev['date']})
            else:
                pos = ev['pos']
                pnl = _pnl_pct(pos['exec_price'], op)
                events.append({'kind': 'l2_close', 'date': ev['date'],
                    'code': ev['code'], 'name': ev['name'],
                    'strategy': pos['strategy'], 'source': ev['source'],
                    'exec_price': op, 'pnl': round(pnl, 3),
                    'hold_days': pos['hold_days'], 'exit_reason': ev['exit_reason'],
                    'signal_date': ev['date']})
        return events

    def write_events(self, conn, events: list[dict]) -> None:
        """成交事件写库（幂等：UNIQUE(date, code, strategy, action, kind, source)）"""
        for ev in events:
            conn.execute("""INSERT OR IGNORE INTO signal_outcome
                (date, code, name, strategy, action, source, kind,
                 exec_price, pnl, hold_days, exit_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ev['date'], ev['code'], ev['name'], ev['strategy'],
                 'BUY' if ev['kind'] == 'l2_open' else 'SELL',
                 ev['source'], ev['kind'], ev.get('exec_price'),
                 ev.get('pnl'), ev.get('hold_days'), ev.get('exit_reason')))
        conn.commit()

    def save_state(self, conn, date: str) -> None:
        """每日模式状态快照（覆盖写）：当前持仓+排队事件全量 → kind='l2_state' 单行

        字段映射（表结构在 Task 1 已定、不可改列，借用现有列表达、不破坏
        UNIQUE 与既有行语义——详见代码内注释）：
          date        = 状态日期（该快照覆盖到的交易日；run_daily 幂等键）
          source      = 'real'（只对真实信号账本落快照；replay 纯内存不落）
          kind        = 'l2_state'（与 l1_*/l2_open/l2_close 并列的新结算类型）
          code/name/strategy/action = NULL（聚合行，不表达单笔交易——
            避免与 UNIQUE(date, code, strategy, action, kind, source) 撞键：
            同 code 同 strategy 可同时持多笔（FIFO 堆叠），按笔分行必撞）
          exit_reason = JSON 负载，含 positions（code→开仓序列表）与
            pending（排队事件）。此列对 l2_state 行无语义占用（仅 l2_close
            用它表达平仓原因），其余列（exec_price/hold_days/...）因单行
            聚合放不下多笔，全部 NULL。
        replay 与每日模式互不读写对方持仓（replay 全内存账本），互不相干。
        账本为空（无持仓无排队）时不落行并清掉残留状态行——空快照会让
        source='real' 的统计/测试把"无事发生的一天"误计为一条记录。
        """
        conn.execute("DELETE FROM signal_outcome WHERE kind='l2_state'")
        if not self.positions and not self.pending:
            conn.commit()
            return
        payload = json.dumps({'positions': self.positions,
                              'pending': self.pending}, ensure_ascii=False)
        conn.execute("""INSERT INTO signal_outcome
            (date, code, name, strategy, action, source, kind, exit_reason)
            VALUES (?, NULL, NULL, NULL, NULL, 'real', 'l2_state', ?)""",
            (date, payload))
        conn.commit()

    def load_state(self, conn) -> str | None:
        """读最近 l2_state 快照重建 positions/pending；返回状态日期（无则 None）

        重建语义与 replay 的账本完全一致（positions 按 code→列表、FIFO 即
        列表序；pending 原样恢复排队事件），因此每日模式可直接复用
        process_pending/check_stop_loss/check_timeout 继续推进。
        """
        row = conn.execute(
            "SELECT date, exit_reason FROM signal_outcome WHERE kind='l2_state'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        state_date, payload = row
        try:
            data = json.loads(payload or '{}')
        except (ValueError, TypeError):
            print(f"⚠️ l2_state 快照 JSON 解析失败(date={state_date})，按空账本继续",
                  file=sys.stderr)
            data = {}
        self.positions = {code: list(opens) for code, opens in
                          (data.get('positions') or {}).items()}
        self.pending = list(data.get('pending') or [])
        return state_date

# ── Task 4: 回放引擎 ────────────────────────────────────────────────────

def _daily_snapshot(conn, date: str, codes: list[str]) -> pd.DataFrame:
    """当日全股票快照（供 filter_signals）：code/close/pct_change/volume/amount/is_st"""
    rows = []
    for code in codes:
        row = conn.execute("""SELECT d.close, d.pct_change, d.volume, d.amount, s.is_st
            FROM daily_kline d LEFT JOIN stock_info s ON d.code = s.code
            WHERE d.code=? AND d.date=?""", (code, date)).fetchone()
        if row:
            rows.append({'code': code, 'close': row[0], 'pct_change': row[1],
                         'volume': row[2], 'amount': row[3], 'is_st': row[4] or 0})
    return pd.DataFrame(rows)

def _daily_signals(conn, date: str, stocks: pd.DataFrame,
                   kline_all: dict, strategies: list) -> list[dict]:
    """对单日跑所有策略（数据截至 date 截断，不偷看未来），返回原始信号

    - kline_all 由 cleaner 的清洗管道预加载（datetime 索引 + is_suspended 标记），
      与生产 run_strategies 喂给策略的 DataFrame 同构
    - 截断后只留最近 STRATEGY_DATA_DAYS 天：与生产"每天看近 200 日"视角一致，
      且指标是因果的（只依赖最近 ≤MA60 行），等价但大幅缩短逐日重算量
    """
    out = []
    t = pd.to_datetime(date)
    for _, st_row in stocks.iterrows():
        code = st_row['code']
        df = kline_all.get(code)
        if df is None or df.empty:
            continue
        df_t = df[df['date'] <= t]
        if len(df_t) < settings.REPLAY_MIN_HISTORY:
            continue  # 历史不足（MA60 慢线无交叉可言）
        if len(df_t) > settings.STRATEGY_DATA_DAYS:
            df_t = df_t.tail(settings.STRATEGY_DATA_DAYS)
        df_t = df_t.reset_index(drop=True)
        for st in strategies:
            try:
                sig = st.run(code, st_row['name'], df_t)
                if sig:
                    out.append({**sig, 'strategy': st.name})
            except Exception as e:
                print(f"⚠️ [回放] {code} {st.name} 异常: {e}")
    return out

def replay(conn) -> dict:
    """回放引擎：逐交易日 跑策略→风控→L1 结算→Ledger 排队/成交，补齐历史样本

    主循环与 Task 3 语义一致（t 日收盘后出信号 → t+1 开盘成交）：
      1) 今日开盘先 process_pending（成交昨日信号/止损单）并写库
      2) 今日收盘后 check_stop_loss / check_timeout（判定 → 排队明日开盘）
      3) 今日收盘后生成新信号 → 基础风控 → Ledger 登记
      4) 循环结束统一 L1 窗口结算（库内数据已全量在库，无需逐日等窗口；
         _settle_l1_core 与 settle_l1 同核心，仅复用预载数据避免数万次全表扫描）

    已知简化（与生产差异，已确认可接受）：
      - 不含大盘择时降权：v3 择时只按 regime 调 strength 权重，等权验证不受影响
      - 成分股为当前沪深 300 名单（历史成分漂移为已知局限，与现有回测同源）
      - 回放范围 = 最近 REPLAY_YEARS 年（约 250 交易日/年，设计文档定）；更早不回放：
        qfq 前复权除权失真随时间增大，且样本时效性有限
      - 末日残留 pending 不撮合：信号/止损在收盘后产生、需次日开盘成交，而回放窗口
        止于库内最后一日 → 尾部 pending 与窗口末尾 ≤OUTCOME_MAX_HOLD_DAYS 日内开仓
        的持仓本次运行无平仓样本，属批处理固有边界。收敛路径为滚动窗口周期性重跑
        回放——窗口向前延伸时，尾部持仓会在重跑中继续撮合/到期（当前没有"每日增量
        任务接管 replay 尾日 pending"的机制）
    返回统计 dict {signals, l1, l2_close}；重复调用幂等（UNIQUE 去重 + 重算一致）。
    """
    from engine.runner import get_enabled_strategies
    from data_fetcher.cleaner import get_all_stocks, _get_stock_data_from_conn
    from engine.risk_filter import filter_signals

    init_outcome_table(conn)
    stocks = get_all_stocks()
    if stocks.empty:
        print("❌ 股票池为空，无法回放")
        return {'signals': 0, 'l1': 0, 'l2_close': 0}
    codes = stocks['code'].tolist()

    # 与生产 run_strategies 同口径：按 settings.ENABLED_STRATEGIES 过滤（而非全注册表）
    strategies = get_enabled_strategies()
    if not strategies:
        print("❌ 回放无启用的策略（settings.ENABLED_STRATEGIES 为空或均不在注册表），退出")
        return {'signals': 0, 'l1': 0, 'l2_close': 0}

    # 预加载全量 K 线（复用生产清洗管道），主循环按日截断——杜绝偷看未来
    kline_all = {}
    for code in codes:
        kdf = _get_stock_data_from_conn(conn, code, days=100000)  # 上限远超 7 年数据
        if kdf is not None and not kdf.empty:
            kline_all[code] = kdf

    dates = _trading_dates(conn)
    # 回放窗口：最近 REPLAY_YEARS 年（按 250 交易日/年估算，见设计文档"约 250 交易日"）
    n_days = min(len(dates), settings.REPLAY_YEARS * 250)
    replay_dates = dates[-n_days:] if n_days else []
    # L1 结算预载：数万条信号若各自全表扫日期/指数会退化到小时级，这里一次性算好
    idx_map = dict(conn.execute("SELECT date, close FROM index_daily").fetchall())

    ledger = Ledger()
    sigs = []  # (date, code, name, strategy, action) 风控通过信号，L1 尾批统一结算
    n_sig = 0
    for date in replay_dates:
        # 1) 今日开盘：成交昨日排队事件（t 日信号 → t+1 开盘价）
        events = ledger.process_pending(conn, date)
        if events:
            ledger.write_events(conn, events)
        # 2) 今日收盘：止损/到期判定 → 排队明日开盘平仓
        ledger.check_stop_loss(conn, date)
        ledger.check_timeout(conn, date)
        # 3) 今日收盘后：生成信号 → 基础风控 → L2 排队（L1 尾批统一结算，见下）
        snapshot = _daily_snapshot(conn, date, codes)
        if snapshot.empty:
            continue
        raw = _daily_signals(conn, date, stocks, kline_all, strategies)
        if not raw:
            continue
        passed, _rejected = filter_signals(raw, snapshot)
        for sig in passed:
            n_sig += 1
            code = sig['stock_code']
            name = sig.get('stock_name', '')
            strat = sig['strategy']
            action = sig['action']
            sigs.append((date, code, name, strat, action))
            if action == 'BUY':
                ledger.buy(conn, date, code, name, strat, 'replay')
            elif action == 'SELL':
                ledger.on_signal(conn, date, code, name, strat, 'SELL', 'replay')
            else:
                # 防御：策略基类合法 action 含 HOLD（base_strategy.py），未知 action
                # 不得静默按 SELL 处理误平仓——只记 L1 窗口样本，跳过 L2 撮合
                print(f"⚠️ [回放] 未知 action {action}，跳过（{code} {name}）")

    # 4) L1 统一结算（同 settle_l1 同核心，仅复用预载数据），末日 pending 不撮合：
    #    信号收盘后产生需次日开盘成交，本次窗口止于库内最后一日 → 尾部持仓无平仓
    #    样本属批处理边界，收敛靠滚动窗口周期性重跑回放（窗口延伸时继续撮合/到期）
    for date, code, name, strat, action in sigs:
        kdf = kline_all.get(code)
        if kdf is not None:
            _settle_l1_core(conn, dates, idx_map, kdf, date, code, name,
                            strat, action, 'replay', commit=False)
    conn.commit()

    l1 = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE kind LIKE 'l1_%' AND source='replay'"
    ).fetchone()[0]
    l2 = conn.execute(
        "SELECT COUNT(*) FROM signal_outcome WHERE kind='l2_close' AND source='replay'"
    ).fetchone()[0]
    print(f"✅ 回放完成: 信号 {n_sig} 条 | L1 结算 {l1} | L2 平仓 {l2}")
    return {'signals': n_sig, 'l1': l1, 'l2_close': l2}

# ── Task 5: 每日增量结算入口 ─────────────────────────────────────────────

def _real_signals(conn) -> list[dict]:
    """signal_history 中全部 passed 真实信号（L1 登记源，L2 按日期过滤登记）"""
    rows = conn.execute("""SELECT date, code, name, strategy, action
        FROM signal_history WHERE status='passed' AND action IN ('BUY','SELL')
        ORDER BY date""").fetchall()
    return [{'date': r[0], 'code': r[1], 'name': r[2], 'strategy': r[3],
             'action': r[4]} for r in rows]

def run_daily(conn) -> dict:
    """每日增量结算（无参运行入口 / cron 21:02 调用）

    ① L1：对 signal_history 全部 passed 真实 BUY/SELL 全量尝试 settle_l1
       ——"未结算判定"不依赖 l1 行存在与否（窗口未到会自己跳过），INSERT OR
       IGNORE 幂等兜底，重复运行不重复写。
    ② L2：从 l2_state 快照恢复账本 → 对 (state_date, today] 每个交易日镜像
       replay 的日循环（与回放逐日语义完全一致）：
         1) 今日开盘 process_pending —— 成交此前排队事件（昨日信号/止损单）
         2) 今日收盘 check_stop_loss / check_timeout —— 判定并排队次日开盘
         3) 登记当日新真实信号（收盘后产生 → 排队次日开盘成交）
       → save_state 回写快照。
    状态日期 == 今日（同日重复运行/行情未更新）→ L2 跳过，天然幂等。
    已知简化：漏跑 cron 期间的信号只补 L1 不补 L2 登记（真实 L2 样本自部署
    日起逐日积累，历史样本由 --replay 补齐——两账本互不相干，见 save_state）。
    """
    init_outcome_table(conn)
    sigs = _real_signals(conn)
    n_l1 = 0
    for s in sigs:
        kinds = settle_l1(conn, s['date'], s['code'], s['name'],
                          s['strategy'], s['action'], 'real')
        n_l1 += len(kinds)

    dates = _trading_dates(conn)
    if not dates:
        print(f"✅ 每日结算完成（无行情数据）: L1 +{n_l1}")
        return {'l1': n_l1, 'l2_open': 0, 'l2_close': 0, 'positions': 0}
    today = dates[-1]

    ledger = Ledger()
    state_date = ledger.load_state(conn)
    n_open = n_close = 0
    if state_date != today:
        # 逐日推进窗口：从状态日期之后到今日（含）；无状态/状态日期失效 = 仅今日
        # （与 replay 的逐日循环同构：缺跑的日子会被顺延补齐，成交价取窗口内
        #  首个可用开盘价——与回放"逐日推进、当日无数据顺延"行为一致）
        loop = ([d for d in dates if d > state_date] if state_date in dates
                else [today])
        for d in loop:
            # 1) 今日开盘：成交此前排队事件（昨日信号/止损单）
            events = ledger.process_pending(conn, d)
            if events:
                ledger.write_events(conn, events)
            # 2) 今日收盘：止损/到期判定 → 排队次日开盘
            ledger.check_stop_loss(conn, d)
            ledger.check_timeout(conn, d)
            # 3) 今日收盘后：登记当日新真实信号 → 排队次日开盘
            for s in sigs:
                if s['date'] != d:
                    continue
                if s['action'] == 'BUY':
                    ledger.buy(conn, d, s['code'], s['name'], s['strategy'], 'real')
                elif s['action'] == 'SELL':
                    ledger.on_signal(conn, d, s['code'], s['name'],
                                     s['strategy'], 'SELL', 'real')
            # 每日落一次状态：崩溃后重跑只会从未推进的那天继续，不重复登记
            ledger.save_state(conn, d)
            n_open += sum(1 for e in events if e['kind'] == 'l2_open')
            n_close += sum(1 for e in events if e['kind'] == 'l2_close')

    n_pos = sum(len(v) for v in ledger.positions.values())
    print(f"✅ 每日结算完成: L1 +{n_l1} | L2 开仓 {n_open} 平仓 {n_close}"
          f" | 当前持仓 {n_pos}")
    return {'l1': n_l1, 'l2_open': n_open, 'l2_close': n_close, 'positions': n_pos}

if __name__ == '__main__':
    conn = sqlite3.connect(DB)
    try:
        if '--replay' in sys.argv:
            replay(conn)
        else:
            run_daily(conn)
    finally:
        conn.close()
