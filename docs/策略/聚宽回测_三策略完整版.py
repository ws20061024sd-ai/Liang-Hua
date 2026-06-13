"""
聚宽（JoinQuant）三策略回测模板

使用方法：
  1. 打开 joinquant.com → 登录 → 进入「研究」或「策略列表」
  2. 新建策略 → 清空默认代码 → 粘贴本文件全部内容
  3. 设置回测参数 → 点「运行回测」

速度：云端运行，7 年数据通常 30 秒内完成。

和本地回测的对应关系：
  本地 MaCrossStrategy      → 聚宽 ma_cross_signal()
  本地 MomentumBreakoutStrategy → 聚宽 momentum_breakout_signal()
  本地 MeanReversionStrategy    → 聚宽 mean_reversion_signal()
  本地 market_timing.py         → 聚宽 get_market_regime()
  本地 filter_by_regime()       → 聚宽 get_strategy_weight()

三种择时模式（修改 TIMING_MODE 切换）：
  'none'   — 无择时
  'binary' — 二元择时（弱势禁买）
  'full'   — 权重匹配（当前实盘方案）
"""

import numpy as np
import pandas as pd

# ============================================================
# 参数配置（在这里修改）
# ============================================================

# 回测参数
START_DATE = '2019-01-01'
END_DATE   = '2026-06-01'
INITIAL_CAPITAL = 100000
COMMISSION = 0.0008     # 手续费（双向 0.03% 佣金 + 卖出 0.05% 印花税）
SLIPPAGE = 0.001        # 滑点 0.1%

# 策略参数
MA_FAST = 10
MA_SLOW = 30
MOMENTUM_LOOKBACK = 20
MOMENTUM_BUFFER = 0.02
BB_PERIOD = 20
BB_STD_DEV = 2.0

# 仓位管理
MAX_POSITIONS = 10
PER_POSITION_PCT = 0.10
STOP_LOSS = -0.05

# 择时模式: 'none' | 'binary' | 'full'
TIMING_MODE = 'full'


def initialize(context):
    """初始化——回测开始时执行一次"""
    # 沪深300成分股
    g.stock_pool = get_index_stocks('000300.XSHG')
    # 排除 ST、上市不足 60 天的新股（聚宽自动处理大部分）
    set_option('use_real_price', True)  # 使用真实价格（复权）
    set_benchmark('000300.XSHG')
    g.max_positions = MAX_POSITIONS
    g.per_position_pct = PER_POSITION_PCT
    g.stop_loss = STOP_LOSS
    g.timing_mode = TIMING_MODE
    run_daily(trade, time='14:50')  # 收盘前 10 分钟执行


# ============================================================
# 大盘择时（和 engine/market_timing.py 逻辑一致）
# ============================================================

def get_market_regime(context):
    """判断当前大盘状态"""
    # 获取沪深300指数过去 90 个交易日数据
    index_data = attribute_history('000300.XSHG', 90, '1d', ['close'])
    if len(index_data) < 60:
        return {'regime': 'shaky', 'can_buy': True}

    close = index_data['close']
    latest = close[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]

    above_ma20 = latest > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma60 = latest < ma60

    # 连续下跌
    consecutive_down = 0
    for i in range(len(close) - 1, max(0, len(close) - 15), -1):
        if close[i] < close[i - 1]:
            consecutive_down += 1
        else:
            break

    if above_ma20 and ma20_above_ma60:
        return {'regime': 'strong', 'can_buy': True}
    elif above_ma20 and not ma20_above_ma60:
        return {'regime': 'shaky', 'can_buy': True}
    elif below_ma60 and consecutive_down >= 10:
        return {'regime': 'crash', 'can_buy': False}
    else:
        return {'regime': 'weak', 'can_buy': False}


def get_strategy_weight(regime, signal_type):
    """策略权重系数（和 engine/market_timing.py 一致）"""
    if g.timing_mode != 'full':
        return 1.0

    if regime['regime'] == 'shaky':
        if signal_type == 'trend':
            return 0.3   # 震荡市趋势策略降权
        elif signal_type == 'reversion':
            return 1.2   # 震荡市回归策略增强
    elif regime['regime'] == 'strong':
        if signal_type == 'trend':
            return 1.2   # 强势市趋势策略增强
        elif signal_type == 'reversion':
            return 0.5   # 强势市回归策略降权
    return 1.0


# ============================================================
# 三个策略信号（和 strategies/ 目录逻辑一致）
# ============================================================

def ma_cross_signal(stock, hist):
    """双均线 MA10/MA30 交叉信号"""
    close = hist['close']
    ma_fast = close.rolling(MA_FAST).mean()
    ma_slow = close.rolling(MA_SLOW).mean()
    ma_fast_prev = ma_fast.shift(1)
    ma_slow_prev = ma_slow.shift(1)

    # 金叉
    if (ma_fast_prev.iloc[-1] <= ma_slow_prev.iloc[-1] and
        ma_fast.iloc[-1] > ma_slow.iloc[-1]):
        return 'BUY', abs(ma_fast.iloc[-1] - ma_slow.iloc[-1]) / ma_slow.iloc[-1]
    # 死叉
    if (ma_fast_prev.iloc[-1] >= ma_slow_prev.iloc[-1] and
        ma_fast.iloc[-1] < ma_slow.iloc[-1]):
        return 'SELL', abs(ma_fast.iloc[-1] - ma_slow.iloc[-1]) / ma_slow.iloc[-1]
    return None, 0


def momentum_breakout_signal(stock, hist):
    """动量突破 20 日信号"""
    close = hist['close']
    highest_N = close.rolling(MOMENTUM_LOOKBACK).max().shift(1).iloc[-1]
    lowest_N = close.rolling(MOMENTUM_LOOKBACK).min().shift(1).iloc[-1]
    current = close.iloc[-1]

    if pd.isna(highest_N) or pd.isna(lowest_N):
        return None, 0

    threshold = highest_N * (1 + MOMENTUM_BUFFER)
    if current > threshold:
        return 'BUY', (current - threshold) / (highest_N - lowest_N + 0.01)
    if current < lowest_N:
        return 'SELL', 0.5
    return None, 0


def mean_reversion_signal(stock, hist):
    """均值回归布林带信号"""
    close = hist['close']
    ma_mid = close.rolling(BB_PERIOD).mean()
    bb_std = close.rolling(BB_PERIOD).std()
    bb_upper = ma_mid + BB_STD_DEV * bb_std
    bb_lower = ma_mid - BB_STD_DEV * bb_std
    ma60 = close.rolling(60).mean()
    ma60_rising = ma60.iloc[-1] > ma60.iloc[-6]  # MA60 上升趋势

    current = close.iloc[-1]
    if pd.isna(bb_lower.iloc[-1]):
        return None, 0

    if current < bb_lower.iloc[-1] and ma60_rising:
        strength = (ma_mid.iloc[-1] - current) / ma_mid.iloc[-1]
        return 'BUY', strength
    if current > bb_upper.iloc[-1]:
        strength = (current - ma_mid.iloc[-1]) / ma_mid.iloc[-1]
        return 'SELL', strength
    return None, 0


# ============================================================
# 每日交易逻辑
# ============================================================

def trade(context):
    """每天收盘前执行"""
    regime = get_market_regime(context)
    cur_data = get_current_data()

    # ---- 止损检查 ----
    for stock in list(context.portfolio.positions.keys()):
        position = context.portfolio.positions[stock]
        if position.total_amount == 0:
            continue
        cost_basis = position.avg_cost
        current_price = cur_data[stock].close
        pnl_pct = (current_price - cost_basis) / cost_basis
        if pnl_pct <= g.stop_loss:
            order_target_value(stock, 0)
            log.info('止损 %s: %.1f%%' % (stock, pnl_pct * 100))

    # ---- 生成信号 ----
    buy_candidates = []
    sell_candidates = []

    for stock in g.stock_pool:
        # 跳过停牌
        if cur_data[stock].paused:
            continue

        hist = attribute_history(stock, 80, '1d', ['close'])

        # 双均线
        action, strength = ma_cross_signal(stock, hist)
        if action:
            weight = get_strategy_weight(regime, 'trend')
            if action == 'BUY':
                buy_candidates.append((stock, strength * weight, '均线'))
            else:
                sell_candidates.append(stock)

        # 动量突破
        action, strength = momentum_breakout_signal(stock, hist)
        if action:
            weight = get_strategy_weight(regime, 'trend')
            if action == 'BUY':
                buy_candidates.append((stock, strength * weight, '动量'))
            else:
                sell_candidates.append(stock)

        # 均值回归
        action, strength = mean_reversion_signal(stock, hist)
        if action:
            weight = get_strategy_weight(regime, 'reversion')
            if action == 'BUY':
                buy_candidates.append((stock, strength * weight, '回归'))
            else:
                sell_candidates.append(stock)

    # ---- 执行卖出 ----
    for stock in sell_candidates:
        if stock in context.portfolio.positions:
            order_target_value(stock, 0)

    # ---- 执行买入 ----
    if regime['can_buy'] or g.timing_mode == 'none':
        # 按信号强度排序
        buy_candidates.sort(key=lambda x: x[1], reverse=True)
        slots = g.max_positions - len(context.portfolio.positions)

        for stock, strength, source in buy_candidates[:slots]:
            target_value = context.portfolio.total_value * g.per_position_pct
            order_target_value(stock, target_value)


# ============================================================
# 使用说明
# ============================================================
"""
复现步骤：

1. 打开 https://www.joinquant.com
2. 注册 → 进入「我的策略」→「新建策略」
3. 清空默认代码 → 粘贴本文件
4. 修改顶部参数（TIMING_MODE 切换三种方案）
5. 点击「运行回测」→ 设置回测区间 2019-01-01 ~ 2026-06-01
6. 查看回测报告（收益率、回撤、夏普、胜率等）

本地 vs 聚宽对比：
  本地回测：20-30 分钟 | 完全可控 | 和实盘同一代码库
  聚宽回测：30 秒     | 云端运行 | 需翻译策略代码

建议用法：
  聚宽 → 快速验证新想法（几秒钟看方向对不对）
  本地 → 精确回测（确定最终参数用本地）
"""
