# -*- coding: utf-8 -*-
"""API key 管理端点(签发/吊销/列表):X-Admin-Key 保护;ADMIN_KEY 未配置 → 404(关闭)。

account.vonish.dev 上线前的本地签发器;上线后签发迁移 account 侧,本端点保留
只读/list 或整体下线。密钥脱敏:响应只含 key_id/label/tier/created,明文仅签发瞬间。
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api import config, key_store
from api.responses import UTF8JSONResponse

router = APIRouter()


def _admin_guard(request: Request) -> UTF8JSONResponse | None:
    """管理面保护:未配置 ADMIN_KEY 视为功能关闭(404 不暴露存在性);配了则常量时间比对。"""
    if not config.ADMIN_KEY:
        return UTF8JSONResponse(status_code=404, content={"error": {
            "message": "Not found.", "type": "invalid_request_error",
            "param": None, "code": "not_found"}})
    import hmac
    provided = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(provided, config.ADMIN_KEY):
        return UTF8JSONResponse(status_code=401, content={"error": {
            "message": "Invalid admin key.", "type": "invalid_request_error",
            "param": None, "code": "invalid_admin_key"}})
    return None


class KeyIssue(BaseModel):
    label: str = Field(default="", max_length=80)
    tier: str = Field(default="free", pattern="^(free|pro)$")


@router.post("/v1/admin/keys")
async def issue_key(body: KeyIssue, request: Request):
    if (err := _admin_guard(request)) is not None:
        return err
    key, rec = key_store.issue(label=body.label, tier=body.tier)
    return {"object": "api_key", "key": key,  # 明文仅此一次返回
            "key_id": rec["key_id"], "label": rec["label"], "tier": rec["tier"],
            "created": rec["created"],
            "note": "Store this key now; it is shown only once."}


class KeyRevoke(BaseModel):
    key_id: str = Field(min_length=4, max_length=64)


@router.post("/v1/admin/keys/revoke")
async def revoke_key(body: KeyRevoke, request: Request):
    if (err := _admin_guard(request)) is not None:
        return err
    ok = key_store.revoke(body.key_id)
    if not ok:
        return UTF8JSONResponse(status_code=404, content={"error": {
            "message": f"Key {body.key_id} not found.", "type": "invalid_request_error",
            "param": "key_id", "code": "key_not_found"}})
    return {"object": "api_key", "key_id": body.key_id, "revoked": True}


@router.get("/v1/admin/keys")
async def list_keys(request: Request):
    if (err := _admin_guard(request)) is not None:
        return err
    return {"object": "list", "data": key_store.list_keys()}
