"""
均值回归策略（布林带）

逻辑：
  收盘价 < 布林带下轨 → BUY（超卖，预期回归中轨）
  收盘价 > 布林带上轨 → SELL（超买）
  收盘价回到中轨附近 → 平仓参考

趋势过滤：
  MA60 向下时不做多（防止在下跌趋势中反复抄底被套）

参数：
  period: 布林带周期（默认 20）
  std_dev: 标准差倍数（默认 2.0）

适合环境：🟡 震荡市（趋势策略在震荡市假信号多，均值回归正好互补）
"""

import pandas as pd
import numpy as np
from strategies.base_strategy import BaseStrategy
from config import settings


class MeanReversionStrategy(BaseStrategy):
    """均值回归（布林带）"""

    name = "均值回归"
    description = "布林带下轨超卖买入，上轨超买卖出"
    version = "1.0"

    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__()
        self.period = period
        self.std_dev = std_dev

    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算布林带及回归信号

        新增列:
          ma_mid:    中轨（MA20）
          bb_upper:  上轨（中轨 + 2σ）
          bb_lower:  下轨（中轨 - 2σ）
          bb_width:  带宽（上-下）/中轨 × 100
          ma60:      60日均线（趋势过滤）
          signal_buy: 超卖买入信号
          signal_sell: 超买卖出信号
          strength:  信号强度（偏离程度）
        """
        df = df.copy()

        # 布林带
        df['ma_mid'] = df['close'].rolling(window=self.period).mean()
        df['bb_std'] = df['close'].rolling(window=self.period).std()
        df['bb_upper'] = df['ma_mid'] + self.std_dev * df['bb_std']
        df['bb_lower'] = df['ma_mid'] - self.std_dev * df['bb_std']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['ma_mid'] * 100

        # 趋势过滤：MA60
        df['ma60'] = df['close'].rolling(window=60).mean()
        df['ma60_rising'] = df['ma60'] > df['ma60'].shift(5)

        # 信号判断
        # 买入：收盘 < 下轨 且 不是在停牌日 且 MA60上升（趋势过滤）
        df['below_lower'] = df['close'] < df['bb_lower']
        df['above_upper'] = df['close'] > df['bb_upper']

        # 首次触及下轨（上一个交易日不在下轨下方）
        df['prev_below_lower'] = df['below_lower'].shift(1).fillna(False)
        df['signal_buy'] = df['below_lower'] & df['ma60_rising']

        # 卖出：收盘 > 上轨
        df['signal_sell'] = df['above_upper']

        # 强度：偏离中轨的程度
        df['strength'] = np.where(
            (df['ma_mid'] > 0) & (df['below_lower'] | df['above_upper']),
            (abs(df['close'] - df['ma_mid']) / df['ma_mid']).round(4),
            0
        )

        return df

    def get_signal(self, stock_code: str, stock_name: str, df: pd.DataFrame) -> dict | None:
        """获取最新 K 线的均值回归信号"""
        if df.empty or len(df) < max(self.period, 60) + 1:
            return None

        latest = df.iloc[-1]

        if latest.get('is_suspended', False):
            return None

        if pd.isna(latest.get('bb_lower')) or pd.isna(latest.get('bb_upper')):
            return None

        # 买入信号
        if latest.get('signal_buy', False):
            dev_pct = (latest['close'] - latest['ma_mid']) / latest['ma_mid'] * 100
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'BUY',
                'strength': round(latest.get('strength', 0.01), 3),
                'reason': (f'超卖回归：收盘{latest["close"]:.2f} '
                           f'< 下轨{latest["bb_lower"]:.2f} '
                           f'(偏离中轨{dev_pct:.1f}%)'),
                'price': float(latest['close']),
            }

        # 卖出信号
        if latest.get('signal_sell', False):
            dev_pct = (latest['close'] - latest['ma_mid']) / latest['ma_mid'] * 100
            return {
                'stock_code': stock_code,
                'stock_name': stock_name,
                'action': 'SELL',
                'strength': round(latest.get('strength', 0.01), 3),
                'reason': (f'超买回归：收盘{latest["close"]:.2f} '
                           f'> 上轨{latest["bb_upper"]:.2f} '
                           f'(偏离中轨{dev_pct:.1f}%)'),
                'price': float(latest['close']),
            }

        return None
