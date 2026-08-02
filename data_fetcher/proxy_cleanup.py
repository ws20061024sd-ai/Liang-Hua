"""
代理环境清理 —— 必须在 import akshare 之前调用

国内金融数据源（新浪、东财等）直连更快更稳定，不需要走代理。
清除系统代理环境变量，防止 Clash/V2Ray 等代理拦截 API 请求。
"""
import os

# 需要添加直连的域名
_NO_PROXY_DOMAINS = (
    "eastmoney.com,sina.com.cn,qq.com,10jqka.com.cn,"
    "csindex.com.cn,tushare.pro,baostock.com"
)

# 需要清除的代理变量
_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy",
)


def cleanup_proxy():
    """清除代理环境变量（幂等，可重复调用）"""
    os.environ["NO_PROXY"] = _NO_PROXY_DOMAINS
    os.environ["no_proxy"] = _NO_PROXY_DOMAINS
    for key in _PROXY_VARS:
        os.environ.pop(key, None)

    # 同时清除其他含 proxy 的环境变量（npm_proxy, docker_proxy 等）
    # 注意排除 no_proxy/NO_PROXY —— "no_proxy" 包含子串 "proxy"，误删会导致刚设置的直连名单失效
    for key in list(os.environ.keys()):
        if "proxy" in key.lower() and "no_proxy" not in key.lower():
            del os.environ[key]
