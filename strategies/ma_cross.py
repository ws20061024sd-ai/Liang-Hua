"""
双均线趋势跟踪策略

逻辑：
  MA(fast) 当日线上穿 MA(slow) 当日线 → BUY
  MA(fast) 当日线下穿 MA(slow) 当日线 → SELL
  否则 → HOLD（无信号）

参数：
  fast_period: 快线周期（默认 10）
  slow_period: 慢线周期（默认 30）
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy
from config import settings


class MaCrossStrategy(BaseStrategy):
    """双均线趋势跟踪"""

    name = "双均线趋势跟踪"
    description = "MA快线上穿慢线买入，下穿慢线卖出"
    version = "1.0"
    style = "trend"

    def __init__(self, fast_period: int = None, slow_period: int = None):
        super().__init__()
        self.fast_period = fast_period or settings.MA_FAST
        self.slow_period = slow_period or settings.MA_SLOW

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算双均线及交叉信号

        新增列:
          ma_fast:     快线值
          ma_slow:     慢线值
          cross:       交叉标记 (1=金叉, -1=死叉, 0=无)
          ma_fast_prev: 昨日的快线值
          ma_slow_prev: 昨日的慢线值
        """
        df = df.copy()

        # 计算均线
        df['ma_fast'] = df['close'].rolling(window=self.fast_period).mean()
        df['ma_slow'] = df['close'].rolling(window=self.slow_period).mean()

        # 前一日均线值（用于判断交叉）
        df['ma_fast_prev'] = df['ma_fast'].shift(1)
        df['ma_slow_prev'] = df['ma_slow'].shift(1)

        # 判断交叉
        # 金叉：昨日快线 <= 昨日慢线 且 今日快线 > 今日慢线
        golden_cross = (
            (df['ma_fast_prev'] <= df['ma_slow_prev']) &
            (df['ma_fast'] > df['ma_slow'])
        )
        # 死叉：昨日快线 >= 昨日慢线 且 今日快线 < 今日慢线
        death_cross = (
            (df['ma_fast_prev'] >= df['ma_slow_prev']) &
            (df['ma_fast'] < df['ma_slow'])
        )

        df['cross'] = 0
        df.loc[golden_cross, 'cross'] = 1
        df.loc[death_cross, 'cross'] = -1

        # 计算信号强度（基于均线分离度）
        df['strength'] = np.where(
            df['ma_slow'] > 0,
            (abs(df['ma_fast'] - df['ma_slow']) / df['ma_slow']).round(4),
            0
        )

        return df

    def get_signal(self, stock_code: str, stock_name: str, df: pd.DataFrame) -> dict | None:
        """
        获取最新一根 K 线的交易信号
        """
        if df.empty or len(df) < self.slow_period:
            return None

        latest = df.iloc[-1]

        # 检查均线是否计算成功
        if pd.isna(latest.get('ma_fast')) or pd.isna(latest.get('ma_slow')):
            return None

        # 停牌日跳过
        if latest.get('is_suspended', False):
            return None

        cross = latest['cross']
        strength = latest.get('strength', 0)

        if cross == 1:
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'BUY',
                'strength': round(strength, 3),
                'reason': f'MA{self.fast_period}({latest["ma_fast"]:.2f}) '
                          f'上穿 MA{self.slow_period}({latest["ma_slow"]:.2f})',
                'price': float(latest['close']),
            }
        elif cross == -1:
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'SELL',
                'strength': round(strength, 3),
                'reason': f'MA{self.fast_period}({latest["ma_fast"]:.2f}) '
                          f'下穿 MA{self.slow_period}({latest["ma_slow"]:.2f})',
                'price': float(latest['close']),
            }

        return None
