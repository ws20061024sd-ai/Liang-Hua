# ============================================================
# 聚宽回测：双均线趋势跟踪（沪深300成分股）
#
# 用法：
#   1. 注册 joinquant.com
#   2. 导航栏 → 研究 → 新建策略
#   3. 全选删除，粘贴本文全部内容
#   4. 点击「运行回测」
#   5. 看报告
# ============================================================

def initialize(context):
    """
    初始化——设置策略参数、基准、手续费
    """
    # 策略参数（和本地脚本保持一致）
    g.fast_period = 10   # 快线周期
    g.slow_period = 30   # 慢线周期

    # 筛选参数
    g.max_stocks = 10    # 最多持仓数
    g.stop_loss = 0.05   # 止损线 -5%

    # 基准：沪深300
    set_benchmark('000300.XSHG')

    # 手续费：0.03%买卖 + 0.05%印花税卖出
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0.0005,
        open_commission=0.0003,
        close_commission=0.0003,
        close_today_commission=0,
        min_commission=5
    ), type='stock')

    # 滑点：0.1%
    set_slippage(FixedSlippage(0.001))

    # 股票池：沪深300成分股
    g.stock_pool = get_index_stocks('000300.XSHG')

    # 记录统计
    g.trade_count = 0
    g.win_count = 0


def handle_data(context, data):
    """
    每天运行一次——检查信号、执行交易
    """
    # 排除停牌、ST、涨跌停
    current_universe = []
    for stock in g.stock_pool:
        if (not data.is_suspended(stock) and
            not is_st(stock) and
            not is_limit_up(stock, data) and
            not is_limit_down(stock, data)):
            current_universe.append(stock)

    # 对每只股票检查信号
    buy_list = []
    sell_list = []

    for stock in current_universe:
        # 获取历史数据（足够计算MA30）
        hist = attribute_history(stock, g.slow_period + 1, '1d', ['close'])
        if hist is None or len(hist) < g.slow_period:
            continue

        # 计算均线
        ma_fast = hist['close'].rolling(g.fast_period).mean()
        ma_slow = hist['close'].rolling(g.slow_period).mean()

        # 昨日的均线值
        ma_fast_yesterday = ma_fast.iloc[-2]  # T-1
        ma_slow_yesterday = ma_slow.iloc[-2]  # T-1
        ma_fast_today = ma_fast.iloc[-1]      # T
        ma_slow_today = ma_slow.iloc[-1]      # T

        # 判断交叉
        golden_cross = (ma_fast_yesterday <= ma_slow_yesterday) and (ma_fast_today > ma_slow_today)
        death_cross = (ma_fast_yesterday >= ma_slow_yesterday) and (ma_fast_today < ma_slow_today)

        if golden_cross:
            buy_list.append(stock)
        elif death_cross:
            sell_list.append(stock)

    # 执行卖出（先卖后买，释放资金）
    positions = context.portfolio.positions
    for stock in positions:
        if stock in sell_list or positions[stock].value == 0:
            order_target(stock, 0)

    # 检查止损
    for stock in positions:
        if positions[stock].value > 0:
            cost = positions[stock].avg_cost
            current = data.current(stock, 'close')
            if (current - cost) / cost <= -g.stop_loss:
                order_target(stock, 0)
                log.info(f'止损卖出 {stock}')

    # 执行买入
    if len(buy_list) > 0:
        cash_per_stock = context.portfolio.available_cash / min(len(buy_list), g.max_stocks)
        for stock in buy_list[:g.max_stocks]:
            order_value(stock, cash_per_stock)


# ============================================================
# 辅助函数
# ============================================================

def is_st(stock):
    """判断是否ST"""
    try:
        name = get_security_info(stock).display_name
        return 'ST' in name or '*ST' in name
    except:
        return False


def is_limit_up(stock, data):
    """判断是否涨停（买不到）"""
    try:
        current = data.current(stock, 'close')
        prev_close = data.previous(stock, 'close')
        if prev_close > 0:
            return (current - prev_close) / prev_close >= 0.098
    except:
        pass
    return False


def is_limit_down(stock, data):
    """判断是否跌停（卖不掉）"""
    try:
        current = data.current(stock, 'close')
        prev_close = data.previous(stock, 'close')
        if prev_close > 0:
            return (current - prev_close) / prev_close <= -0.098
    except:
        pass
    return False


# ============================================================
# 回测参数设置（在聚宽界面上配置，不在代码里）
# ============================================================
#
# 回测区间：2020-01-01 到 2026-06-01
# 初始资金：100000（10万，方便看百分比）
# 频率：日
#
# 运行后观察以下指标：
#   年化收益率  →  _____%
#   最大回撤    →  _____%
#   夏普比率    →  _____
#   胜率       →  _____%
#   盈亏比     →  _____
#
# ============================================================
