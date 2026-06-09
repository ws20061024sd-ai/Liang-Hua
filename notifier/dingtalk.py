"""
钉钉机器人通知模块

发送格式化的交易信号到钉钉群，支持 Markdown 排版
"""
import requests
from config import settings


def format_signals(passed: list[dict], rejected: list[dict],
                   capital: float, tier_label: str, regime: dict = None) -> str:
    """
    将信号格式化为钉钉 Markdown 消息

    返回: Markdown 格式字符串
    """
    from engine.risk_filter import calculate_position
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    buy_signals = [s for s in passed if s['action'] == 'BUY']
    sell_signals = [s for s in passed if s['action'] == 'SELL']

    lines = [
        f"## 📊 量化信号 {today}",
        "",
        "---",
        "",
    ]

    # 大盘择时状态
    if regime:
        lines.append(f"### {regime['label']}")
        lines.append(f"沪深300: {regime['index_close']} | MA20: {regime['ma20']} | MA60: {regime['ma60']}")
        lines.append(f"仓位系数: {regime['position_ratio']} | {regime['detail']}")
        if not regime['can_buy']:
            lines.append("")
            lines.append("> ⚠️ 当前市场禁止开新仓，以下买入信号已自动屏蔽")
        lines.append("")

    lines.append(f"💰 本金：¥{capital:,} | 档位：{tier_label}")
    lines.append("")

    # 买入信号
    if buy_signals:
        lines.append("### 🟢 买入建议")
        lines.append("")
        for sig in buy_signals[:10]:
            pos = calculate_position(sig, capital)
            lines.append(f"- **{sig['stock_name']}**({sig['stock_code']})")
            lines.append(f"  - 信号：{sig['reason']}")
            lines.append(f"  - 强度：{sig['strength']:.3f} | 策略：{sig.get('strategy', '-')}")
            if pos['actionable']:
                lines.append(f"  - 建议：{pos['shares']}股 ¥{pos['amount']:,.0f}（{pos['pct']:.1%}）")
                lines.append(f"  - 🛑 止损：¥{pos['stop_loss']:.2f}（-{pos['stop_loss_pct']:.0%}）")
                if pos.get('warning'):
                    lines.append(f"  - ⚠️ {pos['warning']}")
            else:
                lines.append(f"  - ❌ {pos.get('reason', '资金不足')}")
            lines.append("")
    else:
        lines.append("### 🟢 买入建议")
        lines.append("")
        lines.append("今日无买入信号")
        lines.append("")

    # 卖出信号
    if sell_signals:
        lines.append("### 🔴 卖出建议")
        lines.append("")
        for sig in sell_signals[:10]:
            lines.append(f"- **{sig['stock_name']}**({sig['stock_code']})")
            lines.append(f"  - 信号：{sig['reason']}")
            lines.append(f"  - 强度：{sig['strength']:.3f}")
            lines.append("")
    else:
        lines.append("### 🔴 卖出建议")
        lines.append("")
        lines.append("今日无卖出信号")
        lines.append("")

    # 被过滤的信号（简要）
    if rejected:
        lines.append("---")
        lines.append("")
        lines.append(f"### 📋 今日过滤（{len(rejected)}条）")
        lines.append("")
        for sig in rejected[:10]:
            lines.append(f"- {sig['stock_name']}({sig['stock_code']})：{sig.get('reject_reason', '-')}")
        if len(rejected) > 10:
            lines.append(f"- ...共{len(rejected)}条，仅显示前10")
        lines.append("")

    lines.append("---")
    if capital <= 20000:
        lines.append("💡 小资金提示：优先ETF | 严格止损-3% | 验证策略而非赚钱")
    lines.append(f"[查看详情](http://localhost) | 下次：`python run.py`")

    return "\n".join(lines)


def send(text: str) -> bool:
    """
    发送 Markdown 消息到钉钉群

    参数:
        text: Markdown 格式的消息内容

    返回:
        True 发送成功，False 发送失败
    """
    if not settings.DINGTALK_WEBHOOK:
        print("⚠️ 钉钉 Webhook 未配置，跳过推送")
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "量化交易信号",
            "text": text,
        },
    }

    try:
        resp = requests.post(
            settings.DINGTALK_WEBHOOK,
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            print("✅ 已推送到钉钉")
            return True
        else:
            print(f"⚠️ 钉钉推送失败: {data.get('errmsg', '未知错误')}")
            return False
    except Exception as e:
        print(f"⚠️ 钉钉推送异常: {e}")
        return False
