# -*- coding: utf-8 -*-
"""SearXNG HTTP 客户端:固定浏览器风格头 + 参数映射 + 失败对账。

实测契约(docs/research/searxng/11-live-verification.md +
docs/research/2026-07-06-local-env-verification.md):
- POST /search, form-urlencoded, format=json, 必须带浏览器风格头
- unresponsive_engines 序列化为 [["engine", "message"], ...]
- wikipedia 等引擎的产出走 infoboxes/answers 通道,不在 results 里 ——
  对账时必须算上,否则会被差集误判为失败
- bing 直连出现过"0 条且无报错"的静默失败 → 差集兜底不可省
"""
import asyncio
import time
from dataclasses import dataclass, field

import httpx

from api import config
from api.logging_setup import log
from api.models import EngineFailure

# 上游节流(生产就绪批 #2):同时打向 SearXNG 的搜索数上限。超出的排队等待(背压),
# 避免一拥而上把某引擎打进 SearXNG 的 2min 全局熔断。模块级,进程内所有请求共享。
_SEARX_GATE = asyncio.Semaphore(config.SEARX_MAX_CONCURRENCY)

# 不带这组头,部分引擎(尤其 ddg)行为异常;与两轮实测保持一致
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


class SearxUnavailableError(Exception):
    """SearXNG 进程不可达/返回 5xx/返回非 JSON 响应(真上游故障 → 502)。"""


class SearxBadRequestError(Exception):
    """SearXNG 返回 400(非法 language/time_range 等)——客户端输入问题 → 400,不该误报 502。"""


class InvalidQueryError(Exception):
    """q 经 bang 防护清洗后为空(整条 q 只有 !/:/< 前缀 token)——客户端输入问题。"""


def _s(v) -> str | None:
    return v if isinstance(v, str) and v not in ("", "None") else None


def instant_answers(outcome: "SearchOutcome") -> list[dict]:
    """把 SearXNG 的 infobox / answer 两个通道归一成统一的即时答案列表。

    否则整条通道被丢弃:一个只产出 infobox 的查询(如 wikipedia + "Python")在 API 看来
    会像 results:[] 的彻底失败——审查确认这违反了自家底线②"禁止静默丢弃"。
    """
    out: list[dict] = []
    for ib in outcome.infoboxes or []:
        if not isinstance(ib, dict):
            continue
        urls = ib.get("urls") or []
        url = _s(ib.get("id")) or _s(ib.get("url"))
        if not url and urls and isinstance(urls[0], dict):
            url = _s(urls[0].get("url"))
        out.append({
            "object": "answer", "type": "infobox",
            "title": _s(ib.get("infobox")),
            "content": _s(ib.get("content")),
            "url": url,
            "img_src": _s(ib.get("img_src")),
            "engine": _s(ib.get("engine")),
        })
    for a in outcome.answers or []:
        if isinstance(a, dict):
            out.append({
                "object": "answer", "type": "answer", "title": None,
                "content": _s(a.get("answer")), "url": _s(a.get("url")),
                "img_src": None, "engine": _s(a.get("engine")),
            })
        elif a:  # 旧版 SearXNG:answers 可能是纯字符串
            out.append({"object": "answer", "type": "answer", "title": None,
                        "content": str(a), "url": None, "img_src": None, "engine": None})
    return out


@dataclass
class SearchOutcome:
    results: list[dict] = field(default_factory=list)
    infoboxes: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
    engines_used: list[str] = field(default_factory=list)
    engines_failed: list[EngineFailure] = field(default_factory=list)
    took_ms: int = 0
    q_sanitized: bool = False


def sanitize_bang(q: str) -> tuple[str, bool]:
    """bang/filter 防护。SearXNG 的 RawTextQuery 对 q 的**每个** token 跑前缀解析:
    `!` 引擎/外部 bang(!!g 甚至 302 跳外站,劫持 json —— session-01 实测)、
    `:` 语言覆盖(:de 会架空请求体 language 参数)、`<` 超时覆盖(<3 架空引擎超时)。
    见 docs/research/searxng/01-api-surface.md §前缀 token。统一剥掉 token 开头的
    这三类字符,并在 meta.q_sanitized 标记是否真的剥过(纯空白规整不算)。"""
    tokens = q.split()
    stripped = [t.lstrip("!:<") for t in tokens]
    cleaned = [t for t in stripped if t]  # 纯前缀 token 剥完为空则丢弃
    return (" ".join(cleaned), cleaned != tokens)


def _collect_used_engines(payload: dict) -> set[str]:
    """从 results/infoboxes/answers 三个通道收集实际产出结果的引擎名。"""
    used: set[str] = set()
    for r in payload.get("results", []) or []:
        for e in r.get("engines") or []:
            used.add(e)
        if r.get("engine"):
            used.add(r["engine"])
    for channel in ("infoboxes", "answers"):
        for r in payload.get(channel, []) or []:
            if r.get("engine"):
                used.add(r["engine"])
            for e in r.get("engines") or []:
                used.add(e)
    return used


def reconcile(requested: list[str], payload: dict) -> tuple[list[str], list[EngineFailure]]:
    """失败对账:unresponsive_engines + 「请求集 − 结果集 − 失败集」差集兜底。

    差集兜底抓的是"引擎既没产出也没报错"的静默失败(bing 直连实测出现过),
    以及 engines= 点名无效被 SearXNG 悄悄回退默认集的情况。
    """
    used = _collect_used_engines(payload)
    failed: dict[str, str] = {}
    for entry in payload.get("unresponsive_engines", []) or []:
        # 序列化格式 [engine, message]
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            failed[str(entry[0])] = str(entry[1])
        elif isinstance(entry, str):
            failed[entry] = "unresponsive"
    for engine in requested:
        if engine not in used and engine not in failed:
            failed[engine] = "no results and no error reported (silent failure)"
    failures = [EngineFailure(engine=e, reason=r) for e, r in sorted(failed.items())]
    return sorted(used), failures


async def search(
    client: httpx.AsyncClient,
    *,
    q: str,
    engines: list[str],
    categories: str | None = None,
    language: str = "auto",
    time_range: str | None = None,
    safesearch: int = 0,
) -> SearchOutcome:
    safe_q, sanitized = sanitize_bang(q)
    if not safe_q:
        # 空 q 发给 SearXNG 会回 400,若透传会被误映射成 502(上游没坏)
        raise InvalidQueryError(
            "q is empty after bang/filter sanitization (only !/:/< prefixed tokens)"
        )
    data = {
        "q": safe_q,
        "format": "json",
        "language": language,
        "safesearch": str(safesearch),
        "pageno": "1",
        "engines": ",".join(engines),
    }
    if categories:
        data["categories"] = categories
    if time_range:
        data["time_range"] = time_range

    t0 = time.perf_counter()
    try:
        async with _SEARX_GATE:  # 上游并发闸:排队等待即背压,不一拥而上打熔断上游引擎
            resp = await client.post(
                f"{config.SEARXNG_URL}/search",
                data=data,
                headers=BROWSER_HEADERS,
                timeout=config.SEARCH_TIMEOUT,
            )
    except httpx.HTTPError as e:
        log.warning("searxng unreachable (q=%r engines=%s): %r", safe_q[:80], engines, e)
        raise SearxUnavailableError(f"SearXNG unreachable: {e!r}") from e
    took_ms = int((time.perf_counter() - t0) * 1000)

    if resp.status_code == 400:
        # SearXNG 判定客户端参数非法(如 language/time_range 取值错)——转 400,别误报 502。
        detail = resp.text[:200]
        try:
            detail = resp.json().get("error", detail)
        except ValueError:
            pass
        raise SearxBadRequestError(str(detail))
    if resp.status_code != 200:
        raise SearxUnavailableError(
            f"SearXNG /search returned HTTP {resp.status_code}: {resp.text[:200]}"
        )
    try:
        payload = resp.json()
    except ValueError as e:  # 200 但 body 非 JSON(截断/反代错误页) → 归一为上游不可用
        raise SearxUnavailableError(
            f"SearXNG /search returned non-JSON body: {resp.text[:200]!r}"
        ) from e

    used, failures = reconcile(engines, payload)
    if failures:  # 引擎失败入日志(诊断自伤 DoS 熔断、静默失败;底线③运维溯源)
        log.info("searxng engine failures (q=%r): %s", safe_q[:80],
                 ", ".join(f"{f.engine}={f.reason[:40]}" for f in failures))
    return SearchOutcome(
        results=payload.get("results", []) or [],
        infoboxes=payload.get("infoboxes", []) or [],
        answers=payload.get("answers", []) or [],
        engines_used=used,
        engines_failed=failures,
        took_ms=took_ms,
        q_sanitized=sanitized,
    )
