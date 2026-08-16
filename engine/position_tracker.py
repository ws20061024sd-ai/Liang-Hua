"""
持仓追踪模块 —— 从 signal_history 推断当前持仓，检查止损触发

用法:
  from engine.position_tracker import get_positions, check_stop_loss

  positions = get_positions()           # 获取当前持仓
  stops = check_stop_loss(positions)    # 检查是否有触发止损的

逻辑:
  最近一次 status='passed' 的 BUY 之后没有 SELL → 持仓中
  当前价 < 持仓期间最高价 × (1 - TRAILING_STOP) → 触发止损
"""
import sqlite3
import pandas as pd
from datetime import datetime
from config import settings

DB = settings.DB_PATH


def get_positions() -> list[dict]:
    """
    从 signal_history 推断当前持仓

    逻辑：对每只股票，找最近一条 status='passed' 的 BUY
          如果之后没有 status='passed' 的 SELL → 持仓中

    返回: [{'code': ..., 'name': ..., 'buy_date': ..., 'buy_price': ..., 'strategy': ...}, ...]
    """
    conn = sqlite3.connect(DB)

    # 移动止损 SELL 确认期：最近 N 个交易日内的"移动止损 SELL"视为待确认执行
    # （用户可能没卖出），不算平仓——持仓保留、止损继续提醒，避免漏掉离场时机。
    # 普通卖出（MA死叉等）仍立即平仓。交易日窗口取自 daily_kline（真实交易日历）。
    confirm_days = settings.STOP_CONFIRM_DAYS
    recent_dates = conn.execute(
        "SELECT DISTINCT date FROM daily_kline ORDER BY date DESC LIMIT ?",
        (confirm_days,)
    ).fetchall()
    if recent_dates:
        dates_in = ','.join('?' * len(recent_dates))
        pending_sell_excl = (
            f" AND NOT (strategy='移动止损' AND action='SELL' "
            f"AND date IN ({dates_in}))"
        )
        date_params = [d[0] for d in recent_dates]
    else:
        pending_sell_excl = ""
        date_params = []

    df = pd.read_sql_query(f"""
        SELECT date, code, name, action, price, strategy, status
        FROM signal_history
        WHERE status = 'passed' AND action IN ('BUY', 'SELL'){pending_sell_excl}
        ORDER BY code, date DESC
    """, conn, params=date_params)
    conn.close()

    if df.empty:
        return []

    positions = []
    for code, group in df.groupby('code'):
        latest = group.iloc[0]
        # 最新一条是BUY → 还没卖 → 持仓
        if latest['action'] == 'BUY':
            positions.append({
                'code': code,
                'name': latest['name'],
                'buy_date': latest['date'],
                'buy_price': latest['price'],
                'strategy': latest['strategy'],
            })

    return positions


def check_stop_loss(positions: list[dict], price_map: dict = None) -> list[dict]:
    """
    检查持仓是否触发移动止损

    移动止损规则：
      - 从买入日起，追踪每日收盘价的最高点
      - 当前收盘价 < 最高点 × (1 - TRAILING_STOP) → 触发止损

    price_map: {code: current_price} 可选，外部传入避免重复查库
    返回: [{'code': ..., 'name': ..., 'buy_price': ..., 'peak': ...,
            'current': ..., 'drop_pct': ..., 'action': 'STOP_LOSS'}, ...]
    """
    if not positions:
        return []

    conn = sqlite3.connect(DB)
    stop_pct = settings.TRAILING_STOP
    codes = [p['code'] for p in positions]

    # 现价：优先用传入的，否则查库
    if price_map is None:
        placeholders = ','.join('?' * len(codes))
        latest_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
        current_prices = pd.read_sql_query(f"""
            SELECT code, close FROM daily_kline
            WHERE date = ? AND code IN ({placeholders})
        """, conn, params=[latest_date] + codes)
        price_map = dict(zip(current_prices['code'], current_prices['close']))

    # 每只持仓的持仓期内最高价
    stops = []
    for p in positions:
        code = p['code']
        current = price_map.get(code)
        if current is None or current <= 0:
            continue

        # 持仓期间最高收盘价
        peak_row = conn.execute("""
            SELECT MAX(close) FROM daily_kline
            WHERE code = ? AND date >= ?
        """, (code, p['buy_date'])).fetchone()
        peak = peak_row[0] if peak_row[0] else current

        # 检查止损
        trail_stop = peak * (1 - stop_pct)
        if current < trail_stop:
            drop_from_peak = (peak - current) / peak * 100
            stops.append({
                'code': code,
                'name': p['name'],
                'buy_date': p['buy_date'],
                'buy_price': p['buy_price'],
                'peak': round(peak, 2),
                'current': round(current, 2),
                'drop_pct': round(drop_from_peak, 1),
                'stop_level': round(trail_stop, 2),
                'action': 'STOP_LOSS',
                'strategy': p['strategy'],
                'reason': (f"移动止损触发：从最高{peak:.2f}回落{drop_from_peak:.1f}% "
                          f"(止损线{trail_stop:.2f}，当前{current:.2f})"),
            })

    conn.close()
    return stops


def print_positions():
    """打印当前持仓（调试用）"""
    positions = get_positions()
    if not positions:
        print("📭 当前无持仓")
        return

    conn = sqlite3.connect(DB)
    latest_date = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]

    print(f"📊 当前持仓 ({latest_date}):")
    print(f"  {'代码':<8} {'名称':<8} {'买入日':<12} {'买入价':>7} {'现价':>7} {'盈亏':>7}")
    print(f"  {'-'*50}")

    codes = [p['code'] for p in positions]
    placeholders = ','.join('?' * len(codes))
    current_prices = pd.read_sql_query(f"""
        SELECT code, close FROM daily_kline
        WHERE date = ? AND code IN ({placeholders})
    """, conn, params=[latest_date] + codes)
    price_map = dict(zip(current_prices['code'], current_prices['close']))
    conn.close()

    for p in positions:
        current = price_map.get(p['code'])
        if current:
            pnl = (current - p['buy_price']) / p['buy_price'] * 100
            print(f"  {p['code']:<8} {p['name']:<8} {p['buy_date']:<12} "
                  f"{p['buy_price']:>7.2f} {current:>7.2f} {pnl:>+6.1f}%")

    stops = check_stop_loss(positions)
    if stops:
        print(f"\n⚠️  止损触发 ({len(stops)}只):")
        for s in stops:
            print(f"  {s['code']} {s['name']}: {s['reason']}")


if __name__ == "__main__":
    print_positions()
