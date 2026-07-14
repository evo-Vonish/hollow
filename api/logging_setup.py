# -*- coding: utf-8 -*-
"""应用日志(2026-07-15,生产就绪批 #1)。

底线③ 的"运维溯源"维度:线上炸了得有服务端记录可查。此前应用零日志、run_local 又用
-WindowStyle Hidden 把唯一诊断输出吞进不可见窗口——本模块 + run_local 落盘一起补上。

- 统一 `hollow` logger:输出到 stdout(uvicorn/run_local 再落成文件),格式含 UTC 时间戳。
- 不劫持 root logger,不与 uvicorn 的 access log 打架;只保证我们自己的诊断有去处。
"""
import logging
import sys

from api import config

_CONFIGURED = False


def setup_logging() -> logging.Logger:
    """幂等配置 `hollow` logger,返回它。多次调用(reload)不重复挂 handler。"""
    global _CONFIGURED
    logger = logging.getLogger("hollow")
    if _CONFIGURED:
        return logger
    level = getattr(logging, str(config.LOG_LEVEL).upper(), logging.INFO)
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    # 用 UTC(与 fetched_at 等时间戳口径一致,便于跨日志对齐)
    handler.formatter.converter = __import__("time").gmtime
    logger.addHandler(handler)
    logger.propagate = False  # 不再冒泡到 root,避免与 uvicorn 重复打印
    _CONFIGURED = True
    return logger


log = logging.getLogger("hollow")
