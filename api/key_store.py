# -*- coding: utf-8 -*-
"""API key 存储器:本地签发/吊销/校验,预留 account.vonish.dev 对接位。

key 形态:hkv1_<urlsafe 24B>。落盘只存 sha256 哈希(明文只在签发瞬间返回一次)。
文件:data/api_keys.json(原子写:tmp+rename)。单进程读写,无锁竞争
(与 --workers 1 硬约束一致;多进程化时需迁共享存储)。

account 对接位:config.ACCOUNT_INTROSPECT_URL 非空时,validate() 先查本地,
未命中再调远端 introspect(POST {key} -> {active,tier,key_id}),超时/5xx 按
"未命中=匿名"处理(可用性优先,远端挂不拖垮免费服务;account 上线后切换)。
"""
import hashlib
import json
import os
import secrets
import threading
import time

from api import config

_LOCK = threading.Lock()
_cache: dict[str, dict] | None = None   # sha256(key) -> record


def _path() -> str:
    return config.API_KEYS_FILE


def _load() -> dict[str, dict]:
    global _cache
    with _LOCK:
        if _cache is not None:
            return _cache
        try:
            with open(_path(), encoding="utf-8") as f:
                _cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _cache = {}
        return _cache


def _save(d: dict[str, dict]):
    global _cache
    with _LOCK:
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path())
        _cache = d


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def issue(label: str = "", tier: str = "free") -> tuple[str, dict]:
    """签发:返回 (明文 key(仅此一次), 记录)。"""
    key = "hkv1_" + secrets.token_urlsafe(24)
    now = int(time.time())
    rec = {
        "key_id": key[:12] + "…" + key[-4:],   # 展示用,不可逆推
        "label": label,
        "tier": tier,
        "created": now,
        "revoked": False,
        "last_used": None,
    }
    d = dict(_load())
    d[_hash(key)] = rec
    _save(d)
    return key, rec


def revoke(key_hash_or_id: str) -> bool:
    d = dict(_load())
    for h, rec in d.items():
        if h == key_hash_or_id or rec.get("key_id") == key_hash_or_id:
            rec["revoked"] = True
            _save(d)
            return True
    return False


def list_keys() -> list[dict]:
    return sorted(_load().values(), key=lambda r: r.get("created", 0), reverse=True)


def validate(key: str) -> dict | None:
    """校验:命中且未吊销 -> 记录(顺带 last_used 节流更新);否则 None。"""
    if not key.startswith("hkv1_"):
        return None
    d = _load()
    rec = d.get(_hash(key))
    if not rec or rec.get("revoked"):
        return None
    now = int(time.time())
    if not rec.get("last_used") or now - rec["last_used"] > 300:  # 5min 节流写盘
        rec = dict(rec, last_used=now)
        d2 = dict(d)
        d2[_hash(key)] = rec
        _save(d2)
    return rec


def reset_cache_for_test():
    global _cache
    with _LOCK:
        _cache = None
