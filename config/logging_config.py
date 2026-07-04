"""
日志配置 —— 统一替换 print()，支持时间戳、级别过滤、文件轮转

用法：
    from config.logging_config import setup_logging
    logger = setup_logging("run")    # 入口模块名

    logger.info("数据下载完成")
    logger.warning("覆盖率不足")
    logger.error("推送失败", exc_info=True)
"""
import logging
import sys
import os
from logging.handlers import RotatingFileHandler

_loggers = {}


def setup_logging(name: str = "quant", log_dir: str = "logs") -> logging.Logger:
    """
    创建并配置 logger

    - 控制台：INFO 级别，格式带时间戳+模块名
    - 文件：DEBUG 级别，自动轮转（10MB × 7 天备份）
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # 避免重复添加 handler
    if logger.handlers:
        _loggers[name] = logger
        return logger

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )

    # 控制台 handler（INFO 及以上）
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # 文件 handler（DEBUG 及以上，带轮转）
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{name}.log")
    fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _loggers[name] = logger
    return logger
