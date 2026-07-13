# -*- coding: utf-8 -*-
"""编排:search → select 候选池 → 并发抓取(凑够即停)→ purify → assemble。

抓取模式(mode,docs/design/05):fast 超召回、先到先得凑够 top_n 条 ok 即砍其余;
balanced/thorough 爬完候选池每条。

内容闸门(design/05):fetch_status=ok 表示**真拿到正文**;HTTP 2xx 但没净化出正文
(空壳/反爬/SPA)= no_content,不算成功、不计入 target,fast 会继续拉池子补位。

不变量(契约):
  fetch.requested == len(items) == ok+failed+timeout+blocked+no_content  (每条 item 有状态)
  fetch.pool      == fetch.requested + fetch.cancelled                   (候选无一静默丢弃)
  fetch.ok        <= fetch.target                                        (够了就停)
被丢弃的候选显式计入 cancelled + stopped_reason,不静默消失。
"""
import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from api import config, fetcher, searx_client
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
    # 净化 + 内容闸门已在抓取层完成(fr.content/purified/word_count);这里只做载荷截断
    content = fr.content
    if content is not None and req.max_content_chars and len(content) > req.max_content_chars:
        content = content[:req.max_content_chars] + "…(truncated)"
    return ResearchItem(
        url=_safe_str(candidate.get("url")) or fr.url,
        title=_safe_str(candidate.get("title")),
        engine=_result_engine(candidate),
        score=_safe_float(candidate.get("score")),
        fetched_at=fr.fetched_at,
        fetch_status=fr.status,  # type: ignore[arg-type]
        engine_used=fr.tier,
        http_status=fr.http_status,
        word_count=fr.word_count,
        purified=fr.purified,
        content=content,
        error=fr.error,
    )


def resolve_effective(req: ResearchRequest) -> tuple[bool, float, int]:
    """按 mode 预设 + 显式覆盖,解出 (escalate, per_url_timeout, pool_size)。
    escalate/fetch_timeout 为 None 时取 mode 预设,显式传值则覆盖。"""
    preset = config.MODE_PRESETS.get(req.mode, config.MODE_PRESETS[config.DEFAULT_MODE])
    escalate = preset["escalate"] if req.escalate is None else req.escalate
    timeout_s = preset["timeout"] if req.fetch_timeout is None else req.fetch_timeout
    pool_size = min(req.fetch_top_n * preset["pool_factor"], config.POOL_MAX)
    return escalate, timeout_s, pool_size


async def _fetch_and_assemble(
    index: int, candidate: dict, req: ResearchRequest,
    semaphore: asyncio.Semaphore, browser_semaphore: asyncio.Semaphore,
    timeout_s: float, escalate: bool,
) -> tuple[int, ResearchItem, float]:
    """单条:抓取(含内容闸门净化)-> 组装占位。任何异常都收敛成占位 item(禁止静默丢弃)。
    返回的第三项是 fetch_one 完成时刻(perf_counter)。内容闸门后,净化已前移进抓取层
    (它是判定"抓没抓到正文/是否升级"的一部分),故 fetch.took_ms 现在= 抓取+抽取的耗时;
    _assemble_item 的截断/组装(极轻)仍不计入(口径较内容闸门前变化,见 design/05)。"""
    fr = await fetcher.fetch_one(
        candidate["url"],
        semaphore=semaphore,
        timeout_s=timeout_s,
        impersonate=config.IMPERSONATE,
        escalate=escalate,
        purify=req.purify,
        browser_semaphore=browser_semaphore,
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

    # ② 按 mode 解出有效参数,选候选池(fast 会超召回)
    escalate, timeout_s, pool_size = resolve_effective(req)
    target_ok = req.fetch_top_n
    candidates = select_candidates(outcome.results, pool_size)
    search_meta = SearchMeta(
        engines_requested=engines,
        engines_used=outcome.engines_used,
        engines_failed=outcome.engines_failed,
        results_total=len(outcome.results),
        took_ms=outcome.took_ms,
        q_sanitized=outcome.q_sanitized,
    )
    yield ("search", search_meta, len(candidates))

    # ③④⑤ 并发[抓取→净化→组装],谁先完成谁先产出;凑够 target_ok 条 ok 即砍其余
    semaphore = asyncio.Semaphore(req.concurrency)
    # 每请求浏览器闸:限本请求同时占用的浏览器升级数,防跨请求垄断全局槽(审查 medium)
    browser_semaphore = asyncio.Semaphore(config.REQUEST_BROWSER_CONCURRENCY)
    t0 = time.perf_counter()
    fetch_deadline: float | None = None  # 整单预算扣掉搜索已耗时后的剩余
    if req.budget is not None:
        fetch_deadline = max(0.01, req.budget - (t0 - t_start))
    tasks = [
        asyncio.create_task(
            _fetch_and_assemble(i, c, req, semaphore, browser_semaphore, timeout_s, escalate)
        )
        for i, c in enumerate(candidates)
    ]

    items: list[ResearchItem] = []
    counts = {"ok": 0, "failed": 0, "timeout": 0, "blocked": 0, "no_content": 0}
    cancelled = 0
    last_fetch_done = t0
    stopped_reason = "pool_exhausted"
    pending: set = set(tasks)

    def _record(fut) -> tuple[int, ResearchItem] | None:
        """消费一个已完成任务:真实结果记入 items/counts 并返回 (index,item);
        被取消的返回 None(计入 cancelled)。_fetch_and_assemble 契约上不抛非取消异常。"""
        nonlocal cancelled, last_fetch_done
        if fut.cancelled():
            cancelled += 1
            return None
        try:
            index, item, fetch_done = fut.result()
        except asyncio.CancelledError:
            cancelled += 1
            return None
        items.append(item)
        counts[item.fetch_status] += 1
        last_fetch_done = max(last_fetch_done, fetch_done)
        return (index, item)

    try:
        while pending:
            remaining: float | None = None
            if fetch_deadline is not None:
                remaining = fetch_deadline - (time.perf_counter() - t0)
                if remaining <= 0:
                    stopped_reason = "budget"
                    break
            done, pending = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:  # 预算到点,这一轮无新完成
                stopped_reason = "budget"
                break
            # asyncio.wait 一次可返回多条已完成:逐条消费,每加一条 ok 即判 target,
            # 达标即停(ok 恒 == target,不越界)——该批剩余显式计 cancelled(审查确认)
            done_list = list(done)
            hit_target = False
            for i, fut in enumerate(done_list):
                rec = _record(fut)
                if rec is not None:
                    yield ("item", rec[0], rec[1])
                if counts["ok"] >= target_ok:
                    stopped_reason = "target_reached"
                    for surplus in done_list[i + 1:]:
                        surplus.cancel()
                        cancelled += 1
                    hit_target = True
                    break
            if hit_target:
                break

        # 收尾(非断开):按停止原因处理剩余 pending —— 禁止静默丢弃
        if stopped_reason == "target_reached":
            for t in pending:  # 已够,剩余候选显式计 cancelled
                t.cancel()
            cancelled += len(pending)
        else:
            # budget / pool_exhausted:pending 里"挂起期间已抓完"的排空进 items
            # (不丢真实成功结果,审查 high);仍在跑的才 cancel;排空同样封顶 target
            for t in list(pending):
                if t.done() and counts["ok"] < target_ok:
                    rec = _record(t)
                    if rec is not None:
                        yield ("item", rec[0], rec[1])
                else:
                    t.cancel()
                    cancelled += 1
            if stopped_reason == "budget":
                # 抓取段跑满预算窗口:终点计到预算切点(否则零完成会误报 took_ms=0,审查回归)
                last_fetch_done = time.perf_counter()
    except (GeneratorExit, asyncio.CancelledError):
        for t in pending:  # 消费方断开:全部取消,不计不产出
            t.cancel()
        raise
    # took_ms = 抓取+抽取阶段(至最后一条 fetch_one 完成);组装截断极轻不计入。
    # 内容闸门后抽取前移进抓取层,故此口径含净化(design/05,审查确认的口径变化)
    fetch_took_ms = int((last_fetch_done - t0) * 1000)

    yield (
        "done",
        ResearchResponse(
            query=req.q,
            created_at=datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            items=items,
            answers=searx_client.instant_answers(outcome),  # infobox/answer 透出(不再丢弃)
            meta=ResearchMeta(
                search=search_meta,
                fetch=FetchMeta(
                    target=target_ok,
                    pool=len(candidates),
                    requested=len(items),
                    ok=counts["ok"],
                    failed=counts["failed"],
                    timeout=counts["timeout"],
                    blocked=counts["blocked"],
                    no_content=counts["no_content"],
                    cancelled=cancelled,
                    stopped_reason=stopped_reason,
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
