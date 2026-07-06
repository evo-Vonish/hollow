# -*- coding: utf-8 -*-
"""编排:search → select → gather-fetch → purify → assemble(docs/design/03 §3)。

不变量(契约):len(items) == fetch.requested == ok+failed+timeout+blocked。
每个被选中的 URL 必有一条占位记录,任何一侧失败都显式可见。
"""
import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from api import config, fetcher, purifier, searx_client
from api.models import (
    EngineFailure,
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


def _assemble_item(candidate: dict, fr: fetcher.FetchResult, req: ResearchRequest) -> ResearchItem:
    content: str | None = None
    purified: bool | None = None
    word_count: int | None = None
    if fr.status == "ok" and fr.body is not None:
        if req.purify:
            content, purified, word_count = purifier.purify(fr.body, fr.url)
        else:
            content, purified, word_count = purifier.raw_html(fr.body)
        # 单条文本量截断(word_count 保留净化全文长度,截断只作用于载荷)
        if content is not None and req.max_content_chars and len(content) > req.max_content_chars:
            content = content[:req.max_content_chars] + "…(truncated)"
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


async def _fetch_and_assemble(
    index: int, candidate: dict, req: ResearchRequest, semaphore: asyncio.Semaphore
) -> tuple[int, ResearchItem, float]:
    """单条:抓取 -> 净化 -> 组装。任何异常都收敛成占位 item(禁止静默丢弃)。
    返回的第三项是抓取完成时刻(perf_counter),供 fetch.took_ms 保持
    "只计抓取段"的旧口径 —— 净化/组装耗时不计入账目(审查确认项)。"""
    fr = await fetcher.fetch_one(
        candidate["url"],
        semaphore=semaphore,
        timeout_s=req.fetch_timeout,
        impersonate=config.IMPERSONATE,
    )
    fetch_done = time.perf_counter()
    try:
        item = await asyncio.to_thread(_assemble_item, candidate, fr, req)
    except Exception as e:  # CancelledError 是 BaseException,不拦,正常传播
        item = ResearchItem(
            url=(_safe_str(candidate.get("url")) or fr.url),
            fetch_status=fr.status,  # type: ignore[arg-type]
            http_status=fr.http_status,
            fetched_at=fr.fetched_at,
            error=f"assemble failed: {type(e).__name__}: {e}",
        )
    return index, item, fetch_done


async def run_research_events(req: ResearchRequest, client: httpx.AsyncClient):
    """事件流版编排:search → 逐条完成即产出 → 汇总。

    产出三种事件(供 /v1 SSE 与非流式共用一条代码路径):
      ("search", SearchMeta, selected_count)
      ("item", index, ResearchItem)   —— 按完成顺序,index 是选取顺位
      ("done", ResearchResponse)
    """
    engines = req.engines or config.DEFAULT_ENGINES
    t_start = time.perf_counter()  # 整单预算从这里起算(含搜索阶段)

    # ① 搜索。整单预算对搜索阶段同样生效:预算内等不到搜索结果就显式收口,
    # 每个请求引擎都记入 engines_failed,绝不静默超支(审查确认项)。
    try:
        outcome = await asyncio.wait_for(
            searx_client.search(
                client,
                q=req.q,
                engines=engines,
                categories=req.categories,
                language=req.language,
                time_range=req.time_range,
                safesearch=req.safesearch,
            ),
            timeout=req.budget,  # None = 不限,搜索自身仍有 SEARCH_TIMEOUT
        )
    except asyncio.TimeoutError:
        outcome = searx_client.SearchOutcome(
            engines_failed=[
                EngineFailure(
                    engine=e,
                    reason=f"total budget {req.budget}s exceeded during search",
                )
                for e in engines
            ],
            took_ms=int((time.perf_counter() - t_start) * 1000),
            q_sanitized=searx_client.sanitize_bang(req.q)[1],
        )

    # ② 选取 top-N
    candidates = select_candidates(outcome.results, req.fetch_top_n)
    search_meta = SearchMeta(
        engines_requested=engines,
        engines_used=outcome.engines_used,
        engines_failed=outcome.engines_failed,
        results_total=len(outcome.results),
        took_ms=outcome.took_ms,
        q_sanitized=outcome.q_sanitized,
    )
    yield ("search", search_meta, len(candidates))

    # ③④⑤ 并行[抓取→净化→组装],谁先完成谁先产出
    semaphore = asyncio.Semaphore(req.concurrency)
    t0 = time.perf_counter()
    # 整单预算:扣掉搜索已耗时后的剩余,给抓取阶段;到点未完成的显式标 timeout
    fetch_deadline: float | None = None
    if req.budget is not None:
        fetch_deadline = max(0.01, req.budget - (t0 - t_start))
    tasks = [
        asyncio.create_task(_fetch_and_assemble(i, c, req, semaphore))
        for i, c in enumerate(candidates)
    ]
    items: list[ResearchItem | None] = [None] * len(candidates)
    last_fetch_done = t0
    try:
        try:
            for fut in asyncio.as_completed(tasks, timeout=fetch_deadline):
                index, item, fetch_done = await fut
                items[index] = item
                last_fetch_done = max(last_fetch_done, fetch_done)
                yield ("item", index, item)
        except asyncio.TimeoutError:
            # 预算耗尽:未完成的每条都补占位并照常产出事件,不变量不破。
            # 抓取段终点计到预算切点,否则全超时会误报 took_ms=0(审查确认项)
            last_fetch_done = time.perf_counter()
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            for i, c in enumerate(candidates):
                if items[i] is None:
                    placeholder = ResearchItem(
                        url=(_safe_str(c.get("url")) or ""),
                        fetch_status="timeout",
                        fetched_at=now_iso,
                        error=f"total budget {req.budget}s exceeded before fetch completed",
                    )
                    items[i] = placeholder
                    yield ("item", i, placeholder)
    finally:
        for t in tasks:  # 预算耗尽/消费方断开时,不留孤儿任务
            t.cancel()
    # 口径与旧版一致:只计抓取段(至最后一条 fetch 完成),净化/组装不计入
    fetch_took_ms = int((last_fetch_done - t0) * 1000)

    final_items = [it for it in items if it is not None]
    counts = {"ok": 0, "failed": 0, "timeout": 0, "blocked": 0}
    for it in final_items:
        counts[it.fetch_status] += 1

    yield (
        "done",
        ResearchResponse(
            query=req.q,
            created_at=datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            items=final_items,
            meta=ResearchMeta(
                search=search_meta,
                fetch=FetchMeta(
                    requested=len(final_items),
                    ok=counts["ok"],
                    failed=counts["failed"],
                    timeout=counts["timeout"],
                    blocked=counts["blocked"],
                    took_ms=fetch_took_ms,
                ),
            ),
        ),
    )


async def run_research(req: ResearchRequest, client: httpx.AsyncClient) -> ResearchResponse:
    """非流式:消费事件流,返回最终 ResearchResponse(/v0 与 /v1 非流式共用)。"""
    final: ResearchResponse | None = None
    async for event in run_research_events(req, client):
        if event[0] == "done":
            final = event[1]
    assert final is not None  # 生成器保证以 done 收尾
    return final
