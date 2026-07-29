# -*- coding: utf-8 -*-
"""正文图片内联(embedder):把净化 markdown 里的小图下载转 data URI 直接嵌入正文
(2026-07-29,用户需求"外链资源夹到正文中,比如说小图片")。

输入是 purifier include_images 产出的 markdown(图片已是 ![alt](绝对 URL) 形式)。
对每张候选图:netguard.vet_url SSRF 校验 → curl_cffi 流式下载(超单张上限即弃)
→ Content-Type image/* 校验 → base64 data URI 替换。四重护栏(单张字节/总张数/
总字节/单张超时)全部来自 config.EMBED_*。

铁律(与 extractor 同款):任何一张失败都保留原 URL 引用、如实入账,绝不拖垮正文。
本模块在 fetcher 的 executor 线程里同步运行(与 _gate 同一执行语境),不用 asyncio。
"""
import base64
import re

from curl_cffi import requests as creq

from api import config, netguard

# trafilatura include_images 产出的图片引用;URL 段不允许空白与右括号(markdown 语法边界)
_IMG_REF = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")

_CT_OK = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml",
          "image/avif", "image/x-icon", "image/vnd.microsoft.icon")


def _fetch_small(url: str, max_bytes: int, timeout_s: float,
                 impersonate: str | None) -> tuple[bytes | None, str | None, str | None]:
    """流式下载一张小图。返回 (body, content_type, skip_reason)。
    skip_reason ∈ None | "ssrf" | "too_large" | "bad_ct" | "fetch_failed"。"""
    reason = netguard.vet_url(url)
    if reason:
        return None, None, "ssrf"
    r = None
    try:
        # 注意:curl_cffi 的 Response 不支持 with 协议(2026-07-29 实测 TypeError),
        # 用完在 finally 里 close;stream=True 流式读,超单张上限立即止损。
        r = creq.get(url, timeout=timeout_s, impersonate=impersonate,
                     allow_redirects=True, max_redirects=3, stream=True)
        if r.status_code != 200:
            return None, None, "fetch_failed"
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ct not in _CT_OK:
            return None, None, "bad_ct"
        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            return None, None, "too_large"  # 头即超限:不下载,直接弃
        buf = bytearray()
        for chunk in r.iter_content(8192):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                return None, None, "too_large"  # 流式中段超限:立即止损
        return bytes(buf), ct, None
    except Exception:
        return None, None, "fetch_failed"
    finally:
        if r is not None:
            try:
                r.close()
            except Exception:
                pass


def embed_markdown_images(content: str, impersonate: str | None = None) -> tuple[str, dict]:
    """把 markdown 里的小图换成 data URI。返回 (新正文, 账目)。
    账目: candidates/embedded/skipped_* 五项计数,供响应如实携带(底线②)。"""
    stats = {"candidates": 0, "embedded": 0, "skipped_too_large": 0,
             "skipped_fetch_failed": 0, "skipped_ssrf": 0, "skipped_bad_ct": 0,
             "skipped_quota": 0}
    budget = {"left_imgs": config.EMBED_IMAGES_MAX, "left_bytes": config.EMBED_TOTAL_BYTES}

    def _sub(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2)
        stats["candidates"] += 1
        if budget["left_imgs"] <= 0 or budget["left_bytes"] <= 0:
            stats["skipped_quota"] += 1
            return m.group(0)  # 配额耗尽:保留原引用
        body, ct, why = _fetch_small(url, config.EMBED_IMAGE_BYTES,
                                     config.EMBED_TIMEOUT_S, impersonate)
        if body is None:
            stats[f"skipped_{why}"] += 1
            return m.group(0)  # 失败:保留原 URL 引用,不丢信息
        b64 = base64.b64encode(body).decode("ascii")
        budget["left_imgs"] -= 1
        budget["left_bytes"] -= len(b64)
        stats["embedded"] += 1
        return f"![{alt}](data:{ct};base64,{b64})"

    return _IMG_REF.sub(_sub, content), stats
