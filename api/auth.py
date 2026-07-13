# -*- coding: utf-8 -*-
"""Bearer 鉴权(共享):HOLLOW_API_KEY 设置时校验,常量时间比较。

/v1 与 /v0 复用同一函数。安全批(2026-07-14):
- 原先只有 /v1 校验,/v0 在同端口完全敞开(审查确认)——现在 /v0 也走这里。
- 原比较用 != 有时序侧信道(CWE-208)——改用 hmac.compare_digest。
"""
import hmac

from fastapi import Request

from api import config
from api.responses import UTF8JSONResponse


def auth_error(request: Request) -> UTF8JSONResponse | None:
    """未设 key → 放行(None)。设了但 Bearer 不匹配 → 返回 401 错误封套。"""
    if not config.API_KEY:
        return None
    provided = request.headers.get("authorization", "")
    expected = f"Bearer {config.API_KEY}"
    if not hmac.compare_digest(provided, expected):
        return UTF8JSONResponse(
            status_code=401,
            content={"error": {"message": "Incorrect API key provided.",
                               "type": "invalid_request_error", "param": None,
                               "code": "invalid_api_key"}},
        )
    return None
