"""
策略基类 —— 所有策略必须继承此类，实现统一接口

添加新策略只需 3 步：
1. 在 strategies/ 下新建 .py 文件
2. 继承 BaseStrategy，实现 calculate() 和 get_signal()
3. 在 config/settings.py 的 ENABLED_STRATEGIES 中注册
"""

import pandas as pd
from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """策略基类"""

    # 子类必须定义这些属性
    name: str = "BaseStrategy"
    description: str = "策略基类"
    version: str = "1.0"
    style: str = ""  # 'trend'（趋势）| 'reversion'（回归），供 market_timing 策略匹配

    def __init__(self):
        pass

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        核心计算逻辑 —— 在历史数据上计算指标

        输入: 一只股票的日线 DataFrame
              (列: date, open, high, low, close, volume, ...)
        输出: 添加了指标列的 DataFrame
              (新增列如: ma_fast, ma_slow, signal, ...)
        """
        ...

    def get_signal(self, stock_code: str, stock_name: str, df: pd.DataFrame) -> dict | None:
        """
        从计算结果中提取今日的交易信号

        输入:
            stock_code: 股票代码
            stock_name: 股票名称
            df: calculate() 处理后的 DataFrame（包含指标列）

        返回:
            dict: {
                'stock_code': '600519',
                'stock_name': '贵州茅台',
                'action': 'BUY',           # BUY / SELL / HOLD
                'strength': 0.85,          # 信号强度 0~1
                'reason': 'MA10上穿MA30',
                'price': 1850.00,          # 信号触发时的收盘价
            }
            或 None（无信号时）
        """
        return None

    def run(self, stock_code: str, stock_name: str, df: pd.DataFrame) -> dict | None:
        """
        完整运行流程：计算 → 提取信号
        """
        df = self.calculate(df)
        return self.get_signal(stock_code, stock_name, df)
