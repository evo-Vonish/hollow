# -*- coding: utf-8 -*-
"""编排:search → select → gather-fetch → purify → assemble(docs/design/03 §3)。

不变量(契约):len(items) == fetch.requested == ok+failed+timeout+blocked。
每个被选中的 URL 必有一条占位记录,任何一侧失败都显式可见。
"""
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from api import config, fetcher, purifier, searx_client
from api.models import (
    FetchMeta,
    ResearchItem,
    ResearchMeta,
    ResearchRequest,
    ResearchResponse,
    SearchMeta,
)

# 静态档抓不了/净化不了的明显非网页后缀(pdf 二版考虑单独通道)
_SKIP_EXTENSIONS = (
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mkv", ".exe", ".dmg",
)


def select_candidates(results: list[dict], top_n: int) -> list[dict]:
    """取 top-N:按 SearXNG 融合排序顺序,host+path 去重,跳过明显非网页。

    results 是跨进程边界的非受信 JSON(引擎解析器五花八门),逐条防御:
    非 dict 条目、非字符串 url 直接跳过,不能让单条坏结果 500 掉整个请求。
    """
    seen: set[tuple[str, str]] = set()
    picked: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        if not isinstance(url, str) or not url:
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme not in ("http", "https"):
            continue
        path = parts.path.rstrip("/")
        if path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        key = (parts.netloc.lower(), path)
        if key in seen:
            continue
        seen.add(key)
        picked.append(r)
        if len(picked) >= top_n:
            break
    return picked


def _safe_str(v) -> str | None:
    return v if isinstance(v, str) else None


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _result_engine(r: dict) -> str | None:
    """可溯源引擎名:多来源用 engines 数组,单来源用 engine。"""
    engines = r.get("engines")
    if isinstance(engines, list) and engines:
        return ",".join(str(e) for e in engines)
    return _safe_str(r.get("engine"))


def _assemble_item(candidate: dict, fr: fetcher.FetchResult, do_purify: bool) -> ResearchItem:
    content: str | None = None
    purified: bool | None = None
    word_count: int | None = None
    if fr.status == "ok" and fr.body is not None:
        if do_purify:
            content, purified, word_count = purifier.purify(fr.body, fr.url)
        else:
            content, purified, word_count = purifier.raw_html(fr.body)
    return ResearchItem(
        url=_safe_str(candidate.get("url")) or fr.url,
        title=_safe_str(candidate.get("title")),
        engine=_result_engine(candidate),
        score=_safe_float(candidate.get("score")),
        fetched_at=fr.fetched_at,
        fetch_status=fr.status,  # type: ignore[arg-type]
        engine_used="static",
        http_status=fr.http_status,
        word_count=word_count,
        purified=purified,
        content=content,
        error=fr.error,
    )


async def run_research(req: ResearchRequest, client: httpx.AsyncClient) -> ResearchResponse:
    engines = req.engines or config.DEFAULT_ENGINES

    # ① 搜索
    outcome = await searx_client.search(
        client,
        q=req.q,
        engines=engines,
        categories=req.categories,
        language=req.language,
        time_range=req.time_range,
        safesearch=req.safesearch,
    )

    # ② 选取 top-N
    candidates = select_candidates(outcome.results, req.fetch_top_n)

    # ③ 并行抓取(信号量 + 硬超时 + 占位)
    urls = [c["url"] for c in candidates]
    fetch_results, fetch_took_ms = await fetcher.fetch_all(
        urls,
        concurrency=config.FETCH_CONCURRENCY,
        timeout_s=req.fetch_timeout,
        impersonate=config.IMPERSONATE,
    )

    # ④+⑤ 净化(CPU 型,丢线程池)+ 组装
    # return_exceptions=True + 占位:与抓取侧同款兜底,单条组装异常
    # 只影响自己那条 item,不变量 len(items)==requested 在异常路径也成立
    gathered = await asyncio.gather(
        *(
            asyncio.to_thread(_assemble_item, c, fr, req.purify)
            for c, fr in zip(candidates, fetch_results)
        ),
        return_exceptions=True,
    )
    items: list[ResearchItem] = []
    for c, fr, res in zip(candidates, fetch_results, gathered):
        if isinstance(res, BaseException):
            items.append(
                ResearchItem(
                    url=(_safe_str(c.get("url")) or fr.url),
                    fetch_status=fr.status,  # type: ignore[arg-type]
                    http_status=fr.http_status,
                    fetched_at=fr.fetched_at,
                    error=f"assemble failed: {type(res).__name__}: {res}",
                )
            )
        else:
            items.append(res)

    counts = {"ok": 0, "failed": 0, "timeout": 0, "blocked": 0}
    for it in items:
        counts[it.fetch_status] += 1

    return ResearchResponse(
        query=req.q,
        created_at=datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        items=items,
        meta=ResearchMeta(
            search=SearchMeta(
                engines_requested=engines,
                engines_used=outcome.engines_used,
                engines_failed=outcome.engines_failed,
                results_total=len(outcome.results),
                took_ms=outcome.took_ms,
                q_sanitized=outcome.q_sanitized,
            ),
            fetch=FetchMeta(
                requested=len(items),
                ok=counts["ok"],
                failed=counts["failed"],
                timeout=counts["timeout"],
                blocked=counts["blocked"],
                took_ms=fetch_took_ms,
            ),
        ),
    )
