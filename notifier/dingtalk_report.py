"""
钉钉日报格式化 —— 将分析结果转为 Markdown 并推送
"""
import requests
from config import settings
from datetime import datetime


def format_report(macro: dict, sector: dict, stock: dict, data_date: str = None) -> str:
    """将三层分析结果格式化为钉钉 Markdown"""
    now = datetime.now().strftime("%Y-%m-%d")
    display_date = data_date or now
    stale_note = ""
    if data_date and data_date != now:
        stale_note = f"> ⚠️ 数据日期: {data_date}（非今日 {now}）"
    regime = macro.get('regime', {})

    lines = [
        f"## 📈 量化市场日报 {now}",
        "",
    ]
    if stale_note:
        lines.append(stale_note)
        lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 宏观 ----
    lines.append("### 🌤 大盘状态")
    lines.append("")
    if regime.get('index_close'):
        lines.append(f"{regime['label']} | 沪深300 {regime['index_close']}")
        lines.append(f"MA20: {regime['ma20']} | MA60: {regime['ma60']}")
        lines.append(f"仓位系数: {regime['position_ratio']}")
    if regime.get('detail'):
        lines.append(f"> {regime['detail']}")
    lines.append("")

    # 四大指数表格
    indices = macro.get('indices', [])
    if indices:
        lines.append("### 📊 主要指数")
        lines.append("")
        lines.append("| 指数 | 收盘 | 日涨跌 | 5日涨跌 |")
        lines.append("|------|------|--------|---------|")
        for idx in indices:
            pct = f"{idx['pct_change']:+.2f}%"
            pct5 = f"{idx['pct_5d']:+.2f}%" if idx.get('pct_5d') is not None else "-"
            lines.append(f"| {idx['name']} | {idx['close']} | {pct} | {pct5} |")
        lines.append("")

    # 市场广度
    breadth = macro.get('breadth', {})
    if breadth:
        lines.append("### 📈 市场广度")
        lines.append("")
        if breadth.get('data_date'):
            lines.append(f"数据日期：{breadth['data_date']}")
        if breadth.get('data_error'):
            lines.append(f"> ⚠️ {breadth['data_error']}")
        else:
            n_null = breadth.get('null_count', 0)
            null_note = f"（{n_null}只无涨跌幅）" if n_null > 0 else ""
            lines.append(f"- 上涨: **{breadth.get('up', '?')}** | "
                         f"下跌: **{breadth.get('down', '?')}** | "
                         f"涨跌比: {breadth.get('up_ratio', '?')}% {null_note}")
            lines.append(f"- 平均涨跌: {breadth.get('avg_pct', '?')}% | "
                         f"中位数: {breadth.get('med_pct', '?')}%")
        lines.append(f"- 总成交额: {breadth.get('total_amount_yi', '?')}亿")
        lines.append("")

    # ---- 中观 ----
    board = sector.get('board', [])
    price_tier = sector.get('price_tier', [])
    style = sector.get('style', '')

    if board:
        lines.append("---")
        lines.append("")
        lines.append("### 🏭 板块表现")
        lines.append("")
        lines.append("| 板块 | 只数 | 涨/跌 | 平均涨跌 |")
        lines.append("|------|------|-------|----------|")
        for b in board:
            lines.append(f"| {b['name']} | {b['count']} | {b['up']}/{b['down']} | {b['avg_pct']:+.2f}% |")
        lines.append("")

    if price_tier:
        lines.append("### 💰 股价分层")
        lines.append("")
        bits = []
        for pt in price_tier:
            bits.append(f"{pt['name']}: {pt['avg_pct']:+.2f}%")
        lines.append(" | ".join(bits))
        lines.append("")

    if style:
        lines.append(f"**风格判断**：{style}")
        lines.append("")

    # ---- 微观 ----
    top_gainers = stock.get('top_gainers', [])
    top_losers = stock.get('top_losers', [])

    if top_gainers or top_losers:
        lines.append("---")
        lines.append("")
        lines.append("### 🔥 今日异动")
        lines.append("")

    if top_gainers:
        lines.append("**涨幅前5**：")
        lines.append("")
        for s in top_gainers:
            lines.append(f"- {s['name']}({s['code']}) {s['close']} | **{s['pct']:+.2f}%**")
        lines.append("")

    if top_losers:
        lines.append("**跌幅前5**：")
        lines.append("")
        for s in top_losers:
            lines.append(f"- {s['name']}({s['code']}) {s['close']} | {s['pct']:+.2f}%")
        lines.append("")

    # 信号复盘
    signal_review = stock.get('signal_review', [])
    if signal_review:
        lines.append("### 🎯 昨日信号复盘")
        lines.append("")
        hit_count = sum(1 for s in signal_review if s['hit'])
        total = len(signal_review)
        lines.append(f"命中率: {hit_count}/{total}")
        lines.append("")
        for s in signal_review[:5]:
            icon = "✅" if s['hit'] else "❌"
            lines.append(f"- {icon} **{s['name']}**({s['code']}) {s['action']}")
            lines.append(f"  信号价 {s['prev_close']} → 今日 {s['latest_close']}（{s['change']:+.2f}%）")
        lines.append("")

    lines.append("---")
    lines.append(f"> 数据截止 {display_date} | 自动生成")

    return "\n".join(lines)


def send_report(markdown: str) -> bool:
    """发送日报到钉钉"""
    if not settings.DINGTALK_WEBHOOK:
        print("⚠️ 钉钉未配置，跳过推送")
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "量化市场日报",
            "text": markdown,
        },
    }
    try:
        resp = requests.post(settings.DINGTALK_WEBHOOK, json=payload, timeout=10)
        if resp.json().get("errcode") == 0:
            print("✅ 日报已推送到钉钉")
            return True
        print(f"⚠️ 推送失败: {resp.json().get('errmsg')}")
        return False
    except Exception as e:
        print(f"⚠️ 推送异常: {e}")
        return False
