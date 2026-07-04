"""
项目常量 —— 信号动作、市场状态、信号状态等枚举

用法:
    from config.constants import Action, Regime, SignalStatus
    if sig['action'] == Action.BUY:
        ...
"""
from enum import StrEnum


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class Regime(StrEnum):
    STRONG = "strong"
    SHAKY = "shaky"
    WEAK = "weak"
    CRASH = "crash"


class SignalStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"


class StrategyStyle(StrEnum):
    TREND = "trend"
    REVERSION = "reversion"


# 大盘状态标签映射
REGIME_LABELS = {
    Regime.STRONG: "🟢 强势",
    Regime.SHAKY: "🟡 震荡",
    Regime.WEAK: "🟠 弱势",
    Regime.CRASH: "🔴 极弱",
}
