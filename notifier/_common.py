"""
钉钉推送公共逻辑 —— dingtalk.py 和 dingtalk_report.py 共享
"""
import requests
from config import settings


def send_markdown(text: str, title: str, label: str = "消息") -> bool:
    """
    发送 Markdown 消息到钉钉群

    参数:
        text:  Markdown 格式的消息内容
        title: 消息标题（钉钉通知栏显示）
        label: 日志标签（如"交易信号"/"市场日报"）

    返回:
        True 发送成功，False 发送失败
    """
    if not settings.DINGTALK_WEBHOOK:
        print(f"⚠️ 钉钉 Webhook 未配置，跳过{label}推送")
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
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
            print(f"✅ {label}已推送到钉钉")
            return True
        else:
            print(f"⚠️ {label}推送失败: {data.get('errmsg', '未知错误')}")
            return False
    except Exception as e:
        print(f"⚠️ {label}推送异常: {e}")
        return False
