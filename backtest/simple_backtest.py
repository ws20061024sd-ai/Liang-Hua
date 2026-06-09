"""
简易回测脚本 —— 用本地 SQLite 数据 + 和实盘完全一致的双均线策略
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import numpy as np
from config import settings
from strategies.ma_cross import MaCrossStrategy


def run():
    print("📊 双均线策略回测 (MA10/MA30)")
    print("=" * 60)

    # 加载数据
    conn = sqlite3.connect(settings.DB_PATH)
    codes = pd.read_sql_query("SELECT code FROM stock_info", conn)['code'].tolist()
    print(f"股票池: {len(codes)} 只（沪深300）")

    # 批量加载
    print("加载数据...")
    from data_fetcher.cleaner import get_batch_stock_data
    all_data = get_batch_stock_data(codes, days=9999)  # 全部数据

    strategy = MaCrossStrategy()

    # 回测参数
    capital = 100000  # 初始资金 10万
    max_positions = 10
    per_position_pct = 0.10
    commission = 0.0008  # 手续费+印花税往返

    trades = []
    daily_values = []
    cash = capital

    # 按日期逐日模拟
    all_dates = set()
    for code, df in all_data.items():
        all_dates.update(df['date'].tolist())
    all_dates = sorted(all_dates)
    all_dates = [d for d in all_dates if d >= pd.Timestamp('2020-01-01')]

    holdings = {}  # {code: {'shares': int, 'cost': float}}

    print(f"回测区间: {all_dates[0].strftime('%Y-%m-%d')} ~ {all_dates[-1].strftime('%Y-%m-%d')}")
    print(f"交易天数: {len(all_dates)}")
    print()

    prev_value = capital
    max_value = capital
    max_dd = 0
    win_trades = 0
    total_trades = 0
    total_pnl = 0

    for i, date in enumerate(all_dates):
        # ---- 检查止损 ----
        for code in list(holdings.keys()):
            if code in all_data:
                df = all_data[code]
                row = df[df['date'] == date]
                if row.empty:
                    continue
                current_price = float(row.iloc[0]['close'])
                cost = holdings[code]['cost']
                pnl_pct = (current_price - cost) / cost
                if pnl_pct <= -0.05:  # -5% 止损
                    shares = holdings[code]['shares']
                    cash += shares * current_price * (1 - commission)
                    trades.append({
                        'date': date, 'code': code, 'action': '止损',
                        'price': current_price, 'pnl_pct': round(pnl_pct * 100, 1)
                    })
                    total_trades += 1
                    total_pnl += pnl_pct
                    if pnl_pct < 0:
                        pass  # 止损肯定是亏的
                    del holdings[code]

        # ---- 计算信号 ----
        buy_signals = []
        sell_signals = []

        for code, df in all_data.items():
            row = df[df['date'] == date]
            if row.empty:
                continue
            # 截取到当前日期
            hist = df[df['date'] <= date].copy()
            if len(hist) < strategy.slow_period:
                continue

            sig = strategy.run(code, '', hist)
            if sig:
                if sig['action'] == 'BUY' and code not in holdings:
                    buy_signals.append((code, float(row.iloc[0]['close']), sig['strength']))
                elif sig['action'] == 'SELL' and code in holdings:
                    sell_signals.append((code, float(row.iloc[0]['close'])))

        # ---- 执行卖出 ----
        for code, price in sell_signals:
            if code in holdings:
                shares = holdings[code]['shares']
                cost = holdings[code]['cost']
                pnl_pct = (price - cost) / cost
                cash += shares * price * (1 - commission)
                trades.append({
                    'date': date, 'code': code, 'action': '卖出',
                    'price': price, 'pnl_pct': round(pnl_pct * 100, 1)
                })
                total_trades += 1
                total_pnl += pnl_pct
                if pnl_pct > 0:
                    win_trades += 1
                del holdings[code]

        # ---- 执行买入 ----
        buy_signals.sort(key=lambda x: x[2], reverse=True)
        slots = max_positions - len(holdings)
        for code, price, strength in buy_signals[:slots]:
            amount = capital * per_position_pct
            shares = int(amount / price / 100) * 100
            if shares >= 100 and cash >= shares * price * (1 + commission):
                cash -= shares * price * (1 + commission)
                holdings[code] = {'shares': shares, 'cost': price}

        # ---- 计算当日权益 ----
        equity_value = cash
        for code, h in holdings.items():
            if code in all_data:
                row = all_data[code][all_data[code]['date'] == date]
                if not row.empty:
                    equity_value += h['shares'] * float(row.iloc[0]['close'])

        daily_values.append(equity_value)
        max_value = max(max_value, equity_value)
        dd = (max_value - equity_value) / max_value
        max_dd = max(max_dd, dd)

    # ---- 最终清仓 ----
    final_date = all_dates[-1]
    for code in list(holdings.keys()):
        row = all_data[code][all_data[code]['date'] == final_date]
        if not row.empty:
            price = float(row.iloc[0]['close'])
            shares = holdings[code]['shares']
            cost = holdings[code]['cost']
            pnl_pct = (price - cost) / cost
            cash += shares * price * (1 - commission)
            trades.append({
                'date': final_date, 'code': code, 'action': '清仓',
                'price': price, 'pnl_pct': round(pnl_pct * 100, 1)
            })
            total_trades += 1
            total_pnl += pnl_pct
            if pnl_pct > 0:
                win_trades += 1
            del holdings[code]

    final_value = cash

    # ---- 报告 ----
    total_return = (final_value - capital) / capital * 100
    years = len(all_dates) / 252
    annual_return = ((final_value / capital) ** (1 / years) - 1) * 100

    daily_returns = pd.Series(daily_values).pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() > 0 else 0

    win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0

    print("=" * 60)
    print("📈 回测结果")
    print("=" * 60)
    print(f"累计收益率:   {total_return:+.1f}%")
    print(f"年化收益率:   {annual_return:+.1f}%")
    print(f"最大回撤:     {max_dd*100:.1f}%")
    print(f"夏普比率:     {sharpe:.2f}")
    print(f"胜率:         {win_rate:.1f}% ({win_trades}/{total_trades})")
    print(f"交易次数:     {total_trades}")
    print(f"最终资金:     ¥{final_value:,.0f}")

    if trades:
        pnl_list = [t['pnl_pct'] for t in trades]
        avg_win = np.mean([p for p in pnl_list if p > 0]) if any(p > 0 for p in pnl_list) else 0
        avg_loss = abs(np.mean([p for p in pnl_list if p < 0])) if any(p < 0 for p in pnl_list) else 0
        profit_factor = avg_win / avg_loss if avg_loss > 0 else float('inf')
        print(f"平均盈利:     {avg_win:+.1f}%")
        print(f"平均亏损:     {-avg_loss:.1f}%")
        print(f"盈亏比:       {profit_factor:.2f}")

    print()
    print("⚠️ 注意：")
    print("  - 未包含大盘择时（防线一），实盘会更好")
    print("  - 未包含滑点（0.1%），实盘略差")
    print("  - 幸存者偏差：回测用的是当前成分股（历史成分股可能不同）")


if __name__ == "__main__":
    run()
