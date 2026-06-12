"""
大盘择时模块 —— 防线一

判断市场状态，调节仓位系数：

状态判断依据：沪深300指数与 MA20、MA60 的关系

四档状态：
  🟢 强势: 指数>MA20 且 MA20>MA60  → 仓位系数 1.0
  🟡 震荡: 指数>MA20 但 MA20<MA60  → 仓位系数 0.3
           或 指数在 MA20 附近反复
  🟠 弱势: 指数<MA20 且 MA20<MA60  → 仓位系数 0.0（禁买）
  🔴 极弱: 指数<MA60 且连续下跌10日  → 仓位系数 0.0（建议清仓）
"""

import pandas as pd
import numpy as np
from data_fetcher.cleaner import get_stock_data


def _fetch_index_data(days: int = 90) -> pd.DataFrame | None:
    """从 AKShare 实时拉取沪深300指数日线数据"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol='sh000300')
        if df is not None and not df.empty:
            df['close'] = df['close'].astype(float)
            df['date'] = pd.to_datetime(df['date'])
            return df.tail(days).reset_index(drop=True)
    except Exception:
        pass
    return None


def get_market_regime() -> dict:
    """
    判断当前大盘状态

    返回:
        {
            'regime': 'strong' | 'shaky' | 'weak' | 'crash',
            'label': '🟢 强势' | '🟡 震荡' | '🟠 弱势' | '🔴 极弱',
            'position_ratio': 1.0 | 0.3 | 0.0,
            'can_buy': True | False,
            'index_name': '沪深300',
            'index_close': 4713.64,
            'ma20': 4880.14,
            'ma60': ...,
            'detail': '...',
        }
    """
    df = get_stock_data("000300", days=90)

    # 本地数据库可能没存指数数据，fallback 到 AKShare 实时拉取
    if df is None or len(df) < 60:
        df = _fetch_index_data(days=90)

    if df is None or len(df) < 60:
        # 数据不足，默认保守
        return {
            'regime': 'shaky',
            'label': '🟡 数据不足，偏保守',
            'position_ratio': 0.3,
            'can_buy': True,
            'index_name': '沪深300',
            'index_close': None,
            'ma20': None,
            'ma60': None,
            'deviation_from_ma20': None,
            'ma20_slope_5d': None,
            'consecutive_down': 0,
            'detail': '数据不足60根K线，默认偏保守',
        }

    close = df['close']
    latest = close.iloc[-1]
    latest_date = df['date'].iloc[-1].strftime('%Y-%m-%d')

    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]

    above_ma20 = latest > ma20
    ma20_above_ma60 = ma20 > ma60
    below_ma60 = latest < ma60

    # 连续下跌天数
    consecutive_down = 0
    for i in range(len(close) - 1, max(0, len(close) - 15), -1):
        if close.iloc[i] < close.iloc[i - 1]:
            consecutive_down += 1
        else:
            break

    # 计算偏离 MA20 的幅度
    dev_pct = (latest - ma20) / ma20 * 100

    # 均线斜率（MA20 近5日变化趋势）
    ma20_5d_ago = close.rolling(20).mean().iloc[-6]
    ma20_slope = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100 if ma20_5d_ago > 0 else 0

    # 判断状态
    if above_ma20 and ma20_above_ma60:
        regime = 'strong'
        label = '🟢 强势'
        position_ratio = 1.0
        detail = f'指数{latest:.0f}在MA20({ma20:.0f})上方，均线多头排列'
    elif above_ma20 and not ma20_above_ma60:
        regime = 'shaky'
        label = '🟡 震荡'
        position_ratio = 0.3
        detail = f'指数在MA20上方但均线未形成多头排列'
    elif not above_ma20 and not ma20_above_ma60 and not below_ma60:
        regime = 'shaky'
        label = '🟡 震荡（偏弱）'
        position_ratio = 0.3
        detail = f'指数在MA20({ma20:.0f})附近，但MA20在MA60下方'
    elif below_ma60 and consecutive_down >= 10:
        regime = 'crash'
        label = '🔴 极弱'
        position_ratio = 0.0
        detail = f'指数{latest:.0f}跌破MA60({ma60:.0f})且连续{consecutive_down}日下跌'
    else:
        regime = 'weak'
        label = '🟠 弱势'
        position_ratio = 0.0
        detail = f'指数{latest:.0f}在MA20({ma20:.0f})下方，趋势偏空'

    return {
        'regime': regime,
        'label': label,
        'position_ratio': position_ratio,
        'can_buy': position_ratio > 0,
        'index_name': '沪深300',
        'index_code': '000300',
        'index_close': round(latest, 2),
        'ma20': round(ma20, 2),
        'ma60': round(ma60, 2),
        'deviation_from_ma20': round(dev_pct, 2),
        'ma20_slope_5d': round(ma20_slope, 2),
        'consecutive_down': consecutive_down,
        'latest_date': latest_date,
        'detail': detail,
    }


def _get_strategy_style_map() -> dict[str, str]:
    """构建策略名→风格映射（从策略注册表读取 class.style）"""
    try:
        from engine.runner import STRATEGY_REGISTRY
        return {cls.name: cls.style for cls in STRATEGY_REGISTRY.values()}
    except Exception:
        return {}


def filter_by_regime(signals: list[dict], regime: dict) -> tuple[list[dict], list[dict]]:
    """
    根据大盘状态过滤信号 + 策略权重调节

    策略-市场匹配（基于策略 style 属性）：
      🟢 强势 → trend 策略增强 +20%，reversion 降权至 50%
      🟡 震荡 → reversion 策略增强 +20%，trend 降权至 30%
      🟠 弱势 → 所有买入信号拦截
      🔴 极弱 → 所有买入拦截 + 卖出建议增强
    """
    style_map = _get_strategy_style_map()
    passed = []
    blocked = []

    for sig in signals:
        if sig['action'] == 'BUY' and not regime['can_buy']:
            sig['block_reason'] = f'大盘择时拦截: {regime["label"]}'
            blocked.append(sig)
        else:
            # 根据市场状态 + 策略类型 调节信号强度
            if sig['action'] == 'BUY':
                strategy_name = sig.get('strategy', '')
                style = style_map.get(strategy_name, '')
                if regime['regime'] == 'shaky':
                    if style == 'trend':
                        sig['strength'] = round(sig['strength'] * 0.3, 3)
                        sig['regime_note'] = '震荡市趋势策略降权'
                    elif style == 'reversion':
                        sig['strength'] = round(min(sig['strength'] * 1.2, 1.0), 3)
                        sig['regime_note'] = '震荡市均值回归增强'
                elif regime['regime'] == 'strong':
                    if style == 'trend':
                        sig['strength'] = round(min(sig['strength'] * 1.2, 1.0), 3)
                        sig['regime_note'] = '强势市趋势策略增强'
                    elif style == 'reversion':
                        sig['strength'] = round(sig['strength'] * 0.5, 3)
                        sig['regime_note'] = '强势市回归策略降权'
            passed.append(sig)

    return passed, blocked


def get_strategy_advice(regime: dict, enabled_strategies: list) -> str:
    """根据大盘状态给出策略使用建议"""
    advice = {
        'strong':  '🟢 强势市：趋势策略（双均线/动量突破）优先，均值回归降权',
        'shaky':   '🟡 震荡市：均值回归策略优先，趋势策略降权（假信号多）',
        'weak':    '🟠 弱势市：所有买入信号已屏蔽，耐心等待趋势好转',
        'crash':   '🔴 极弱市：建议观望，不建议任何做多操作',
    }
    return advice.get(regime['regime'], '')
