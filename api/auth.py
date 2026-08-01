# -*- coding: utf-8 -*-
"""Bearer 鉴权与身份解析:共享 key(向后兼容)+ hkv1_ key 池 + 匿名识别。

2026-08-01 双池改造:鉴权语义从"无 key 拒 401"改为"无 key 进匿名池(限速排队,
有 key 进登录池(满速)"。auth_error() 保留原 401 行为仅当显式配置了 HOLLOW_API_KEY
且要求强制鉴权的部署;公网免费档(默认)所有请求放行,由 pools 调度器分池。

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


def client_ip(request: Request) -> str:
    """匿名身份:CF-Connecting-IP > X-Forwarded-For 首跳 > 直连 host。
    边界已知:CGNAT/校园网共享 IP 会同桶——速率曲线按身份数爬升,天然缓解误伤。"""
    cf = request.headers.get("cf-connecting-ip", "").strip()
    if cf:
        return cf
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def resolve_identity(request: Request) -> tuple[bool, str, dict | None]:
    """(authenticated, identity, key_record):
    - hkv1_ 池 key 命中 -> (True, key_id, record)
    - 兼容共享 HOLLOW_API_KEY -> (True, "shared", None)
    - 其他/无 -> (False, client_ip, None) 匿名档
    account.vonish.dev 上线后:hkv1_ 未命中再走 introspect(key_store 预留位)。"""
    from api import key_store  # 延迟导入:auth 被中间件早期引用
    provided = request.headers.get("authorization", "")
    token = provided[7:].strip() if provided.lower().startswith("bearer ") else ""
    if token.startswith("hkv1_"):
        rec = key_store.validate(token)
        if rec:
            return True, rec.get("key_id", "key"), rec
    if config.API_KEY and hmac.compare_digest(provided, f"Bearer {config.API_KEY}"):
        return True, "shared", None
    return False, client_ip(request), None
