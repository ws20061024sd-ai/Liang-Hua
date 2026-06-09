"""
钉钉机器人通知模块

发送格式化的交易信号到钉钉群，支持 Markdown 排版
"""
import requests
from config import settings


def format_signals(aggregated: list[dict], rejected: list[dict],
                   capital: float, tier_label: str, regime: dict = None) -> str:
    """
    将汇总后的信号格式化为钉钉 Markdown 消息

    参数:
        aggregated: signal_aggregator.aggregate() 的输出
        rejected: 被风控过滤的信号
    """
    from engine.risk_filter import calculate_position
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    buy_agg = [s for s in aggregated if s['action'] == 'BUY']
    sell_agg = [s for s in aggregated if s['action'] == 'SELL']

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

    # 策略状态
    strat_count = len(set(s['name'] for s in aggregated for s in s.get('strategies', [])))
    if strat_count > 1:
        lines.append(f"💰 本金 ¥{capital:,} | {strat_count}策略并行 | ⭐⭐=多策略确认")
    else:
        lines.append(f"💰 本金 ¥{capital:,} | 档位 {tier_label}")
    lines.append("")

    # 买入信号
    if buy_agg:
        lines.append("### 🟢 买入建议")
        lines.append("")
        for sig in buy_agg[:10]:
            pos = calculate_position(sig, capital)
            stars = "⭐⭐" if sig['confirm'] >= 2 else "⭐"
            conflict = " ⚠️冲突" if sig.get('conflict') else ""

            lines.append(f"- {stars} **{sig['stock_name']}**({sig['stock_code']}){conflict}")
            for s in sig['strategies']:
                lines.append(f"  - [{s['name']}] {s['reason']}")
            if pos['actionable']:
                lines.append(f"  - 建议：{pos['shares']}股 ¥{pos['amount']:,.0f}（{pos['pct']:.1%}）")
                lines.append(f"  - 🛑 止损 ¥{pos['stop_loss']:.2f}（-{pos['stop_loss_pct']:.0%}）")
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
    if sell_agg:
        lines.append("### 🔴 卖出建议")
        lines.append("")
        total_sell = len(sell_agg)
        for sig in sell_agg[:5]:
            stars = "⭐⭐" if sig['confirm'] >= 2 else "⭐"
            lines.append(f"- {stars} **{sig['stock_name']}**({sig['stock_code']})")
            for s in sig['strategies']:
                lines.append(f"  - [{s['name']}] {s['reason']}")
            lines.append("")
        if total_sell > 5:
            lines.append(f"...共{total_sell}条，仅显示前5")
            lines.append("")
    else:
        lines.append("### 🔴 卖出建议")
        lines.append("")
        lines.append("今日无卖出信号")
        lines.append("")

    # 过滤
    if rejected:
        lines.append("---")
        lines.append("")
        lines.append(f"### 📋 今日过滤（{len(rejected)}条）")
        lines.append("")
        for sig in rejected[:10]:
            lines.append(f"- {sig['stock_name']}({sig['stock_code']})：{sig.get('reject_reason', '-')}")
        if len(rejected) > 10:
            lines.append(f"- ...共{len(rejected)}条")
        lines.append("")

    lines.append("---")
    if capital <= 20000:
        lines.append("💡 小资金提示：优先ETF | 严格止损-3%")
    if strat_count > 1:
        lines.append("💡 ⭐⭐双策略确认信号优先关注")
    lines.append(f"下次：`python run.py`")

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
