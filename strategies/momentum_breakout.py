"""
动量突破策略

逻辑：
  今日收盘 > 过去 N 日最高收盘价 × (1 + buffer) → BUY
  今日收盘 < 过去 M 日最低收盘价 → SELL

参数：
  lookback: 回顾天数（默认 20）
  buffer: 突破确认缓冲区（默认 0.02 = 2%）
  exit_period: 卖出参考周期（默认 10）

适合环境：🟢 强势趋势市场
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy
from config import settings


class MomentumBreakoutStrategy(BaseStrategy):
    """动量突破"""

    name = "动量突破"
    description = "价格突破N日最高价买入，跌破M日最低价卖出"
    version = "1.0"

    def __init__(self, lookback: int = None, buffer: float = None, exit_period: int = None):
        super().__init__()
        self.lookback = lookback or settings.MOMENTUM_LOOKBACK
        self.buffer = buffer or settings.MOMENTUM_BUFFER
        self.exit_period = exit_period or settings.MOMENTUM_EXIT_PERIOD

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算突破信号

        新增列:
          highest_N:    过去 N 日最高收盘价
          lowest_N:     过去 N 日最低收盘价
          breakout_up:  向上突破（收盘 > 最高价 × (1+buffer)）
          breakout_down: 向下突破（收盘 < 最低价）
          lowest_exit:  过去 M 日最低价（卖出用）
          strength:     信号强度（突破幅度）
        """
        df = df.copy()

        df['highest_N'] = df['close'].rolling(window=self.lookback).max().shift(1)
        df['lowest_N'] = df['close'].rolling(window=self.lookback).min().shift(1)
        df['lowest_exit'] = df['close'].rolling(window=self.exit_period).min().shift(1)

        # 突破阈值 = 最高价 × (1+buffer)
        df['breakout_threshold'] = df['highest_N'] * (1 + self.buffer)

        # 突破判断
        df['breakout_up'] = df['close'] > df['breakout_threshold']
        df['breakout_down'] = df['close'] < df['lowest_exit']

        # 信号强度：突破幅度 / 过去波动范围
        price_range = df['highest_N'] - df['lowest_N']
        df['strength'] = np.where(
            df['breakout_up'] & (price_range > 0),
            ((df['close'] - df['breakout_threshold']) / price_range).round(4),
            0
        )

        return df

    def get_signal(self, stock_code: str, stock_name: str, df: pd.DataFrame) -> dict | None:
        """
        获取最新一根 K 线的突破信号
        """
        if df.empty or len(df) < self.lookback + 1:
            return None

        latest = df.iloc[-1]

        # 停牌日跳过
        if latest.get('is_suspended', False):
            return None

        # 检查均线数据计算成功
        if pd.isna(latest.get('highest_N')) or pd.isna(latest.get('lowest_N')):
            return None

        if latest.get('breakout_up', False):
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'BUY',
                'strength': round(latest.get('strength', 0.01), 3),
                'reason': (f'突破{self.lookback}日最高价 '
                           f'(收盘{latest["close"]:.2f} > '
                           f'阈值{latest["breakout_threshold"]:.2f})'),
                'price': float(latest['close']),
            }
        elif latest.get('breakout_down', False):
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'SELL',
                'strength': 0.5,
                'reason': (f'跌破{self.exit_period}日最低价 '
                           f'(收盘{latest["close"]:.2f} < '
                           f'最低{latest["lowest_exit"]:.2f})'),
                'price': float(latest['close']),
            }

        return None
