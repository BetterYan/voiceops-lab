"""统一日志配置：模块名 + 级别 + 时间，级别由环境变量 VOICEOPS_LOG_LEVEL 控制。

用法：
    from voiceops.logging_setup import setup_logging
    setup_logging()                       # 默认 INFO
    setup_logging("DEBUG")                # 显式覆盖
或设置环境变量 VOICEOPS_LOG_LEVEL=DEBUG（uvicorn --factory 路径同样生效）。
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    """初始化根日志（幂等）；uvicorn 自身日志保持原格式不劫持。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    level = (level or os.environ.get("VOICEOPS_LOG_LEVEL", "INFO")).upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # 第三方库降噪：WARNING 以下不输出
    for noisy in ("websockets", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
