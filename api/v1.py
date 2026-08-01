# -*- coding: utf-8 -*-
"""/v1 —— hollow 的正式 API 面,参考 OpenAI 的格式与调用方式(不仿制其端点)。

借鉴的惯例:
- 对象封套:响应带 id("res_.."/"srch_..")/object/created;列表统一 {"object":"list","data":[...]}
- 错误统一 {"error": {message, type, param, code}} + 正确的 HTTP 状态码
- Bearer 鉴权(HOLLOW_API_KEY 设置时启用,只管 /v1/*)
- stream: true 走 SSE;事件是**语义化**的(search.completed / item.completed /
  completed),每条来源抓完净化完立即推送,不等全齐

端点(2026-07-06 拍板:搜索与研究拆两个端点,不做 fetch 开关):
  POST /v1/search      纯搜索召回:秒级返回 URL/标题/摘要 + 搜索账目,不抓全文
  POST /v1/research    搜索 + 并行抓取 + 净化全套(stream 可选)
  POST /v1/fetch       按 URL 直取:抓取+净化,不经搜索(2026-07-14,审计催生 + vonish 集成)
  GET  /v1/engines     引擎注册表(343 源,支持 status/scene/type/tier 过滤)
  GET  /v1/scenes      场景 -> 默认引擎集

引擎选取:scenes(多选,并集) ∪ engines(自定义点名) ,都不传用网关默认集;
点名拒 L1 removed 与未知名(SearXNG 对无效 engines 会静默回退默认集,必须挡住)。
"""
import asyncio
import json
import time
import uuid
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import auth, config, engine_health, fetcher, filters, orchestrator, registry, rerank
from api.models import ResearchItem, ResearchRequest, ResearchResponse, SearchMeta
from api.responses import UTF8JSONResponse
from api.searx_client import InvalidQueryError, SearxBadRequestError, SearxUnavailableError
from api import searx_client

router = APIRouter(prefix="/v1")


# ---------- OpenAI 风格的错误与鉴权 ----------

def _sse_frame(payload: dict) -> str:
    """语义化 SSE 帧:原生 event: 行 + data 行(payload 内含同名 event 字段)双轨。
    标准客户端(EventSource)按事件名路由;手写 data 解析器(本仓前端/旧客户端)不受影响。
    [DONE] 终结帧按惯例为纯 data 行(见 create_research._sse 尾帧)。"""
    return f"event: {payload['event']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error(status: int, message: str, err_type: str, code: str,
           param: str | None = None) -> JSONResponse:
    return UTF8JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type,
                           "param": param, "code": code}},
    )


def _check_auth(request: Request) -> JSONResponse | None:
    return auth.auth_error(request)  # 共享 + 常量时间比较(api/auth.py)


# ---------- 引擎选取(search 与 research 共用) ----------

def _resolve_engines(scenes: list[str] | None,
                     named: list[str] | None) -> list[str] | JSONResponse:
    resolved: list[str] = []
    if scenes:
        for s in scenes:
            eng = registry.SCENES.get(s)
            if eng is None:
                return _error(400,
                              f"Unknown scene '{s}'. Valid: {sorted(registry.SCENES)}",
                              "invalid_request_error", "unknown_scene", "scenes")
            resolved.extend(eng)
    if named:
        for name in named:
            if name in registry.REMOVED:
                return _error(400,
                              f"Engine '{name}' is removed ({registry.REMOVED[name]}), "
                              f"see data/engine_registry.yaml.",
                              "invalid_request_error", "engine_removed", "engines")
            if name not in registry.ALL_NAMES:
                return _error(400, f"Unknown engine '{name}'.",
                              "invalid_request_error", "unknown_engine", "engines")
        resolved.extend(named)
    if not resolved:
        return list(config.DEFAULT_ENGINES)
    return list(dict.fromkeys(resolved))  # 并集去重,保序


# ---------- POST /v1/search(纯召回) ----------

class SearchCreate(BaseModel):
    query: str = Field(min_length=1, max_length=config.QUERY_MAX_LEN)
    scenes: list[str] | None = Field(default=None, description="场景,可多选,取并集")
    engines: list[str] | None = Field(default=None, description="自定义引擎,并入场景集")
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
    # 产品差距批:分页 + 域过滤(Tavily/Exa 风格,hollow 侧后置过滤,过滤在召回之后)
    page: int = Field(default=1, ge=1, le=20, description="分页(SearXNG pageno)")
    include_domains: list[str] | None = Field(default=None, description="只保留这些域(含子域)")
    exclude_domains: list[str] | None = Field(default=None, description="剔除这些域(含子域)")
    # extra="allow"(而非 ignore):未知/拼错参数不静默吞掉,收进 model_extra 后在响应的
    # ignored_params 里如实回报(底线②:丢的不该是客户端的意图)——见 _ignored()。
    model_config = ConfigDict(extra="allow")


def _ignored(body) -> list[str]:
    """请求里未被识别的字段名(拼错的 engine、别家产品的 exclude_domains 等)。"""
    return sorted((body.model_extra or {}).keys())


@router.post("/search")
async def create_search(body: SearchCreate, request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    engines = _resolve_engines(body.scenes, body.engines)
    if isinstance(engines, JSONResponse):
        return engines
    # 引擎健康熔断(2026-07-30):默认集/场景集剔除退避中的引擎;engines= 显式点名
    # 豁免(用户意图优先,失败如实入账)。账目进 meta.engines_degraded(底线②)。
    degraded: list[dict] = []
    if not body.engines:
        engines, degraded = engine_health.filter_engines(engines)
    client: httpx.AsyncClient = request.app.state.http
    try:
        outcome = await searx_client.search(
            client, q=body.query, engines=engines, language=body.language,
            time_range=body.time_range, safesearch=body.safesearch, page=body.page,
        )
    except InvalidQueryError as e:
        return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
    except SearxBadRequestError as e:
        return _error(400, f"SearXNG rejected a parameter: {e}", "invalid_request_error", "invalid_search_param")
    except SearxUnavailableError as e:
        return _error(502, str(e), "api_error", "upstream_unavailable")

    results = []
    ranked = filters.filter_by_domain(  # 域过滤在重排之后、取结果之前(过滤在召回之后)
        rerank.rerank(body.query, outcome.results), body.include_domains, body.exclude_domains)
    for r in ranked:  # 按相关性排序(否则 position 分把空壳排前)
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        if not isinstance(url, str) or not url:
            continue
        results.append({
            "object": "search.result",
            "url": url,
            "title": orchestrator._safe_str(r.get("title")),
            "engine": orchestrator._result_engine(r),
            "score": orchestrator._safe_float(r.get("score")),   # SearXNG 原分(可溯源)
            "relevance": round(r.get("_rel", 0.0), 4),           # 词汇重排分(排序依据)
            # 媒体模式前置(2026-07-30):images 类引擎的图直链/缩略图透传,缺省 null
            "img_src": orchestrator._safe_str(r.get("img_src")),
            "thumbnail": orchestrator._safe_str(r.get("thumbnail_src") or r.get("thumbnail")),
            "snippet": orchestrator._safe_str(r.get("content")),
            "published_date": orchestrator._published(r),        # SearXNG 透传(缺 → null)
        })
    ledger = SearchMeta(
        engines_requested=engines,
        engines_used=outcome.engines_used,
        engines_failed=outcome.engines_failed,
        engines_no_results=outcome.engines_no_results,
        engines_degraded=degraded,
        results_total=len(outcome.results),
        took_ms=outcome.took_ms,
        q_sanitized=outcome.q_sanitized,
    )
    resp = {
        "id": f"srch_{uuid.uuid4().hex}",
        "object": "search",
        "created": int(time.time()),
        "query": body.query,
        "scenes": body.scenes,
        "engines": engines,
        "page": body.page,
        "results": results,
        "answers": searx_client.instant_answers(outcome),  # infobox/answer 透出(不再丢弃)
        "search": ledger.model_dump(),
        # usage:OpenAI 风格用量账目(配额强制需存储,暂只暴露用量;详细账目见 search)
        "usage": {"searches": 1, "engines_queried": len(engines),
                  "results_returned": len(results)},
    }
    ignored = _ignored(body)
    if ignored:  # 未知/拼错参数如实回报,不静默吞掉(底线②)
        resp["ignored_params"] = ignored
    return resp


# ---------- POST /v1/research(搜索+抓取+净化) ----------

class ResearchCreate(BaseModel):
    query: str = Field(min_length=1, max_length=config.QUERY_MAX_LEN, description="研究问题")
    scenes: list[str] | None = Field(default=None, description="场景,可多选,取并集")
    engines: list[str] | None = Field(default=None, description="自定义引擎,并入场景集")
    top_n: int = Field(default=config.FETCH_TOP_N_DEFAULT, ge=1,
                       le=config.FETCH_TOP_N_MAX, description="想要的成功正文条数(凑够即停)")
    mode: Literal["fast", "balanced", "thorough"] = Field(
        default=config.DEFAULT_MODE,
        description="一根旋钮:fast 广度/速度(超召回+凑够即停+不升级) | "
                    "balanced 默认 | thorough 质量/难度(死磕每条+升级全开)")
    budget: float | None = Field(default=None, gt=0, le=300,
                                 description="整单时间预算(秒);到点即返回,未完成的计入 cancelled")
    concurrency: int = Field(default=config.FETCH_CONCURRENCY, ge=1,
                             le=config.REQUEST_CONCURRENCY_MAX, description="并行抓取数")
    max_content_chars: int | None = Field(default=None, ge=100,
                                          description="单条净化正文截断上限(字符)")
    # timeout/escalate 缺省跟随 mode 预设;显式传值则覆盖
    timeout: float | None = Field(default=None, gt=0, le=60,
                                  description="单 URL 抓取超时(秒);缺省跟随 mode")
    escalate: bool | None = Field(default=None,
                                  description="三档升级链;缺省跟随 mode(fast 关/其余开)")
    purify: bool = True
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
    page: int = Field(default=1, ge=1, le=20, description="分页(SearXNG pageno)")
    include_domains: list[str] | None = Field(default=None, description="只保留这些域(含子域)")
    exclude_domains: list[str] | None = Field(default=None, description="剔除这些域(含子域)")
    # 页面资产抽取(2026-07-29):外链/媒体结构化清单;默认关,不给载荷灌水
    include_links: bool = Field(default=False,
                                description="抽取每条来源的外链清单 [{url,text,internal}]")
    include_media: bool = Field(default=False,
                                description="抽取每条来源的媒体清单 [{url,type,source,alt?}]")
    # 正文图片(2026-07-29):include_images 正文保留 ![alt](url);embed_images 小图内联 data URI
    include_images: bool = Field(default=False,
                                 description="净化正文保留图片引用(![alt](url),夹在原位置)")
    embed_images: bool = Field(default=False,
                               description="小图转 data URI 内联正文(隐含 include_images)")
    stream: bool = False
    model_config = ConfigDict(extra="allow")  # 未知参数收进 model_extra 后回报,不静默吞掉


def _item_dict(item: ResearchItem, with_content: bool = True) -> dict:
    d = item.model_dump()
    if not with_content:
        d["content"] = None
        d["links"] = None   # 汇总帧不带内容载荷(与 content 同等待遇;2026-07-29)
        d["media"] = None
        d["embed"] = None
    return {"object": "research.item", **d}


def _research_object(rid: str, created: int, body: ResearchCreate,
                     engines: list[str], resp: ResearchResponse,
                     with_content: bool = True) -> dict:
    fetch = resp.meta.fetch
    obj = {
        "id": rid,
        "object": "research",
        "created": created,
        "query": resp.query,
        "scenes": body.scenes,
        "engines": engines,
        "page": body.page,
        "items": [_item_dict(it, with_content) for it in resp.items],
        "answers": resp.answers,  # infobox/answer 透出(不再丢弃)
        "search": resp.meta.search.model_dump(),
        "fetch": fetch.model_dump(),
        # usage:用量账目(配额强制需存储,暂只暴露用量;细账见 search/fetch)
        "usage": {"searches": 1, "engines_queried": len(engines),
                  "fetches": fetch.requested, "results_ok": fetch.ok},
    }
    ignored = _ignored(body)
    if ignored:  # 未知/拼错参数如实回报(底线②)
        obj["ignored_params"] = ignored
    return obj


@router.post("/research")
async def create_research(body: ResearchCreate, request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    engines = _resolve_engines(body.scenes, body.engines)
    if isinstance(engines, JSONResponse):
        return engines
    # 引擎健康熔断(同 search 端点;显式点名豁免)
    degraded: list[dict] = []
    if not body.engines:
        engines, degraded = engine_health.filter_engines(engines)

    req = ResearchRequest(
        q=body.query, engines=engines, language=body.language,
        engines_degraded=degraded,
        time_range=body.time_range, safesearch=body.safesearch,
        fetch_top_n=body.top_n, purify=body.purify, fetch_timeout=body.timeout,
        concurrency=body.concurrency, budget=body.budget,
        max_content_chars=body.max_content_chars, escalate=body.escalate,
        mode=body.mode, page=body.page,
        include_domains=body.include_domains, exclude_domains=body.exclude_domains,
        include_links=body.include_links, include_media=body.include_media,
        include_images=body.include_images, embed_images=body.embed_images,
    )
    client: httpx.AsyncClient = request.app.state.http
    rid = f"res_{uuid.uuid4().hex}"
    created = int(time.time())

    if not body.stream:
        try:
            resp = await orchestrator.run_research(req, client)
        except InvalidQueryError as e:
            return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
        except SearxBadRequestError as e:
            return _error(400, f"SearXNG rejected a parameter: {e}", "invalid_request_error", "invalid_search_param")
        except SearxUnavailableError as e:
            return _error(502, str(e), "api_error", "upstream_unavailable")
        return _research_object(rid, created, body, engines, resp)

    # ---- SSE:语义化事件,每条来源完成即推送 ----
    # 搜索先行完成再开流,搜索类错误仍走标准 HTTP 错误(客户端好处理);
    # 开流后每条抓完即 item 事件;够了/预算到的候选不产 item,计入 completed 的 cancelled。
    events = orchestrator.run_research_events(req, client)
    try:
        first = await events.__anext__()  # ("search", SearchMeta, selected)
    except InvalidQueryError as e:
        return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
    except SearxBadRequestError as e:
        return _error(400, f"SearXNG rejected a parameter: {e}", "invalid_request_error", "invalid_search_param")
    except SearxUnavailableError as e:
        return _error(502, str(e), "api_error", "upstream_unavailable")

    async def _sse():
        _, search_meta, selected = first
        assert isinstance(search_meta, SearchMeta)
        yield _sse_frame({
            "object": "research.event", "event": "research.search.completed",
            "id": rid, "created": created,
            "search": search_meta.model_dump(), "selected": selected,
        })
        # 心跳:抓取慢时事件之间可能空闲数十秒,反代会掐断空闲连接(审查 #6)。这里用一个
        # 常驻的 __anext__ 任务,每 SSE_HEARTBEAT_SECS 秒没等到新事件就发一帧注释心跳——
        # **不 cancel** 该任务(wait 超时只是没就绪),故不会破坏事件生成器或误取消底层抓取。
        agen = events.__aiter__()
        nxt: asyncio.Task | None = None
        try:
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(agen.__anext__())
                done, _pending = await asyncio.wait({nxt}, timeout=config.SSE_HEARTBEAT_SECS)
                if not done:
                    yield ": heartbeat\n\n"  # SSE 注释行:客户端忽略,但让连接保持活跃
                    continue
                try:
                    event = nxt.result()
                except StopAsyncIteration:
                    break
                finally:
                    nxt = None
                if event[0] == "item":
                    _, index, item = event
                    yield _sse_frame({
                        "object": "research.event", "event": "research.item.completed",
                        "id": rid, "index": index, "item": _item_dict(item),
                    })
                elif event[0] == "done":
                    # 汇总事件不重复携带正文(item 事件已推过),只留账目与占位
                    yield _sse_frame({
                        "object": "research.event", "event": "research.completed",
                        "id": rid,
                        "research": _research_object(rid, created, body, engines,
                                                     event[1], with_content=False),
                    })
            yield "data: [DONE]\n\n"
        finally:
            # 断连/收尾:先取消并等挂起的 __anext__ 结束,否则对"仍在运行"的生成器 aclose 会
            # 抛 RuntimeError(async generator already running)。等它 unwind 完再 aclose。
            if nxt is not None:
                nxt.cancel()
                try:
                    await nxt
                except (asyncio.CancelledError, StopAsyncIteration):
                    pass
                except Exception:  # 收尾阶段吞掉底层异常,不掩盖真正的断连原因
                    pass
            await events.aclose()  # 客户端断连时收尾,取消未完成的抓取任务

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------- POST /v1/fetch(按 URL 直取:抓取+净化,不经搜索) ----------
# 2026-07-14:审计"最高投入产出比"项 + vonish 集成需要(search 拿 URL → fetch 取正文)。
# 复用 fetcher.fetch_one 全套:三档升级链、内容闸门、netguard SSRF 防护、大小上限。

class FetchCreate(BaseModel):
    # 借 OpenAI embeddings 的 input 惯例:单个字符串或数组均可(≤ FETCH_URLS_MAX)
    urls: str | list[str] = Field(description="要抓取的 URL,单个或数组")
    mode: Literal["fast", "balanced", "thorough"] = Field(
        default=config.DEFAULT_MODE,
        description="只取 mode 的 escalate/timeout 预设(直取无候选池概念)")
    timeout: float | None = Field(default=None, gt=0, le=60,
                                  description="单 URL 抓取超时(秒);缺省跟随 mode")
    escalate: bool | None = Field(default=None,
                                  description="三档升级链;缺省跟随 mode(fast 关/其余开)")
    purify: bool = True
    max_content_chars: int | None = Field(default=None, ge=100)
    concurrency: int = Field(default=config.FETCH_CONCURRENCY, ge=1,
                             le=config.REQUEST_CONCURRENCY_MAX)
    budget: float | None = Field(default=None, gt=0, le=300,
                                 description="整单时间预算(秒);到点即收口,未完成的 URL 记 timeout "
                                             "并注明是预算切断——防单请求垄断浏览器槽(审查 #4)")
    # 页面资产抽取(2026-07-29):外链/媒体结构化清单;默认关,不给载荷灌水
    include_links: bool = Field(default=False,
                                description="抽取页面外链清单 [{url,text,internal}]")
    include_media: bool = Field(default=False,
                                description="抽取页面媒体清单 [{url,type,source,alt?}]")
    # 正文图片(2026-07-29):include_images 在净化正文原位置保留 ![alt](url);
    # embed_images 再把小图(≤EMBED_IMAGE_BYTES)下载转 data URI 直接内联,正文自包含。
    include_images: bool = Field(default=False,
                                 description="净化正文保留图片引用(![alt](url),夹在原位置)")
    embed_images: bool = Field(default=False,
                               description="小图转 data URI 内联正文(隐含 include_images;"
                                           "四重护栏见 config.EMBED_*,账目进条目 embed 字段)")
    # 外链自动展开(2026-07-29):抓完按 links 清单自动跟进,展到一定程度即止。
    # 内部隐式开启父页 links 抽取(不输出,除非 include_links 显式开);子孙页不抽 media。
    # 四重封顶:每页扇出 EXPAND_LINKS_MAX / 层数 EXPAND_DEPTH_MAX /
    #           整树总量 EXPAND_TOTAL_MAX / 时间预算 EXPAND_BUDGET_S。
    expand_links: int = Field(default=0, ge=0, le=config.EXPAND_LINKS_MAX,
                              description="每个页面自动跟进的外链数(0=关);子条目进 children")
    expand_depth: int = Field(default=1, ge=1, le=config.EXPAND_DEPTH_MAX,
                              description="展开递归层数:1=只跟进一层")
    expand_scope: Literal["internal", "all"] = Field(
        default="internal",
        description="展开范围:internal 只跟站内(同 host/子域);all 任意外链")
    model_config = ConfigDict(extra="allow")  # 未知参数收进 model_extra 后回报,不静默吞掉


def _fetch_item_dict(u: str, fr: "fetcher.FetchResult", body: FetchCreate,
                     show_links: bool, show_media: bool) -> dict:
    """fetch 条目序列化(父条目与 expand 子孙条目同构)。u=客户端点名的原始 URL。"""
    content = fr.content
    if content is not None and body.max_content_chars and len(content) > body.max_content_chars:
        content = content[:body.max_content_chars] + "…(truncated)"
    item = {
        "object": "fetch.item",
        "url": u,
        "fetch_status": fr.status,
        "engine_used": fr.tier,
        "http_status": fr.http_status,
        "word_count": fr.word_count,
        "purified": fr.purified,
        "content": content,
        "error": fr.error,
        "fetched_at": fr.fetched_at,
    }
    if fr.url and fr.url != u:
        item["final_url"] = fr.url  # 重定向后的最终落点(可溯源,底线③)
    if show_links:
        item["links"] = fr.links if fr.links is not None else []
    if show_media:
        item["media"] = fr.media if fr.media is not None else []
    if body.embed_images and fr.embed_stats is not None:
        item["embed"] = fr.embed_stats  # 内联账目如实携带(底线②)
    return item


# ---------- 外链自动展开(expand_*;四重封顶见 FetchCreate 注释) ----------

def _pick_children(links: list[dict] | None, scope: str, n: int,
                   visited: set[str]) -> list[str]:
    """从父页 links 清单挑跟进对象:scope 过滤 + visited 防循环(整树共享)+ 保序取前 n。"""
    out: list[str] = []
    for l in links or []:
        u = l.get("url")
        if not u or u in visited:
            continue
        if scope == "internal" and not l.get("internal"):
            continue
        visited.add(u)
        out.append(u)
        if len(out) >= n:
            break
    return out


async def _expand_child(u: str, depth: int, body: FetchCreate, ctx: dict,
                        visited: set[str], budget: list[int]) -> dict | None:
    """抓一个子孙页并按需递归。budget=[剩余名额] 整树共享,耗尽即停止下钻(返回 None)。"""
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    want_links = depth < body.expand_depth  # 还要下钻才抽子页 links
    try:
        fr = await fetcher.fetch_one(
            u, semaphore=ctx["semaphore"], timeout_s=ctx["timeout_s"],
            impersonate=config.IMPERSONATE, escalate=ctx["escalate"],
            purify=body.purify, browser_semaphore=ctx["browser_semaphore"],
            include_links=want_links, include_media=False,
            include_images=body.include_images, embed_images=body.embed_images)
    except BaseException as ex:  # fetch_one 契约上不抛;防御收敛,单条坏不炸整树
        fr = fetcher.FetchResult(u, "failed", error=f"{type(ex).__name__}: {ex}")
    item = _fetch_item_dict(u, fr, body, show_links=False, show_media=False)
    item["depth"] = depth  # 子孙条目带层号(父条目隐式 depth 0)
    if want_links and fr.status == "ok" and fr.links:
        kids = _pick_children(fr.links, body.expand_scope, body.expand_links, visited)
        if kids:
            sub = await asyncio.gather(
                *[_expand_child(k, depth + 1, body, ctx, visited, budget) for k in kids])
            children = [c for c in sub if c]
            if children:
                item["children"] = children
    return item


async def _expand_item(item: dict, fr: "fetcher.FetchResult", body: FetchCreate,
                       ctx: dict) -> None:
    """对单个父条目做整树展开,原地写 item["children"]。父页失败/无 links 不展开。"""
    if fr.status != "ok" or not fr.links:
        return
    visited = {item["url"]}
    if fr.url:
        visited.add(fr.url)  # 最终落点也算祖先,防 A→redirect→B→A 循环
    budget = [config.EXPAND_TOTAL_MAX]
    kids = _pick_children(fr.links, body.expand_scope, body.expand_links, visited)
    if not kids:
        return
    sub = await asyncio.gather(
        *[_expand_child(k, 1, body, ctx, visited, budget) for k in kids])
    children = [c for c in sub if c]
    if children:
        item["children"] = children


@router.post("/fetch")
async def create_fetch(body: FetchCreate, request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    submitted = [body.urls] if isinstance(body.urls, str) else list(body.urls)
    if not submitted:
        return _error(400, "urls must contain at least one URL.",
                      "invalid_request_error", "invalid_parameter", "urls")
    if len(submitted) > config.FETCH_URLS_MAX:
        return _error(400, f"Too many URLs: {len(submitted)} > {config.FETCH_URLS_MAX}.",
                      "invalid_request_error", "invalid_parameter", "urls")
    # 结构性错误(非 http/https 绝对 URL)= 客户端 bug → 400 指明哪条;
    # 运行时拦截(netguard 内网/抓取失败)→ item 显式状态(与 research 一致,底线②)
    for i, u in enumerate(submitted):
        try:
            parts = urlsplit(u)
            _ = parts.port  # 强制求值:端口越界/非数字时 urlsplit 惰性抛 ValueError(审查 LOW)
        except ValueError:
            parts = None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            return _error(400, f"urls[{i}] is not an absolute http(s) URL: {u[:200]!r}",
                          "invalid_request_error", "invalid_url", "urls")
    # 去重保序;移除的重复显式入账(deduped),不静默消失
    unique = list(dict.fromkeys(submitted))
    deduped = len(submitted) - len(unique)

    escalate, timeout_s = orchestrator.resolve_mode_fetch(
        body.mode, body.timeout, body.escalate)
    semaphore = asyncio.Semaphore(body.concurrency)
    browser_semaphore = asyncio.Semaphore(config.REQUEST_BROWSER_CONCURRENCY)
    # 展开需要父页 links:隐式开启抽取(不输出,除非 include_links 显式开)
    need_links = body.include_links or body.expand_links > 0
    t0 = time.perf_counter()
    tasks = [asyncio.create_task(
        fetcher.fetch_one(u, semaphore=semaphore, timeout_s=timeout_s,
                          impersonate=config.IMPERSONATE, escalate=escalate,
                          purify=body.purify, browser_semaphore=browser_semaphore,
                          include_links=need_links,
                          include_media=body.include_media,
                          include_images=body.include_images,
                          embed_images=body.embed_images))
        for u in unique]
    # 整单预算:到点即收口(审查 #4:防一个全 blocked 的请求死磕升级链、垄断 2 个全局浏览器槽)。
    # 无 budget 时 timeout=None 等价于等全部完成。budget_cut 计数供账目透明(禁止静默丢弃)。
    await asyncio.wait(tasks, timeout=body.budget)

    counts = {"ok": 0, "failed": 0, "timeout": 0, "blocked": 0, "no_content": 0}
    budget_cut = 0
    items: list[dict] = []
    frs: list[fetcher.FetchResult] = []  # 与 items 同序,展开阶段要用 FetchResult.links
    for u, t in zip(unique, tasks):
        if not t.done():  # 预算到点仍未完成:取消并如实出条目(timeout,注明是预算切断)
            t.cancel()
            budget_cut += 1
            fr = fetcher.FetchResult(u, "timeout",
                                     error=f"whole-request budget {body.budget}s exceeded "
                                           f"before this URL completed (not a per-URL timeout)")
        else:
            try:
                fr = t.result()
            except BaseException as ex:  # fetch_one 契约上不抛;防御收敛,单条坏不 500 整单
                fr = fetcher.FetchResult(u, "failed", error=f"{type(ex).__name__}: {ex}")
        # 条目顺序 == 输入顺序(对位客户端意图)
        item = _fetch_item_dict(u, fr, body,
                                show_links=body.include_links,
                                show_media=body.include_media)
        counts[fr.status] = counts.get(fr.status, 0) + 1
        items.append(item)
        frs.append(fr)

    if budget_cut:  # reap 掉被取消的抓取任务,避免 "Task was destroyed but pending" 告警
        await asyncio.gather(*tasks, return_exceptions=True)

    # 外链自动展开:父条目抓完后按 links 清单跟进,展到四重封顶即止。
    # 时间预算:body.budget 有剩余用剩余,否则 EXPAND_BUDGET_S;到点取消,已抓的如实保留。
    expanded = 0
    if body.expand_links > 0:
        ctx = {"semaphore": semaphore, "browser_semaphore": browser_semaphore,
               "timeout_s": timeout_s, "escalate": escalate}
        wait_s = (max(1.0, body.budget - (time.perf_counter() - t0))
                  if body.budget else config.EXPAND_BUDGET_S)
        expand_tasks = [asyncio.create_task(_expand_item(it, fr, body, ctx))
                        for it, fr in zip(items, frs)]
        await asyncio.wait(expand_tasks, timeout=wait_s)
        pending = [t for t in expand_tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:  # reap,同 budget_cut 的处理
            await asyncio.gather(*expand_tasks, return_exceptions=True)

        def _count_children(it: dict) -> int:
            return sum(1 + _count_children(c) for c in it.get("children", []))

        expanded = sum(_count_children(it) for it in items)
    took_ms = int((time.perf_counter() - t0) * 1000)  # 含展开阶段耗时

    # 不变量:requested == len(items) == ok+failed+timeout+blocked+no_content;
    #         submitted == requested + deduped。budget_cut ⊆ timeout(其中因预算切断的条数)。
    resp = {
        "id": f"ftch_{uuid.uuid4().hex}",
        "object": "fetch",
        "created": int(time.time()),
        "items": items,
        "fetch": {"submitted": len(submitted), "requested": len(unique),
                  "deduped": deduped, **counts, "budget_cut": budget_cut,
                  "expanded": expanded, "took_ms": took_ms},
    }
    ignored = _ignored(body)
    if ignored:  # 未知/拼错参数如实回报(底线②)
        resp["ignored_params"] = ignored
    return resp


# ---------- GET /v1/engines / GET /v1/scenes ----------

@router.get("/engines")
async def list_engines(request: Request, status: str | None = None,
                       scene: str | None = None, type: str | None = None,
                       tier: str | None = None):
    denied = _check_auth(request)
    if denied:
        return denied
    data = []
    for e in registry.ENGINES:
        if status and e.get("status") != status:
            continue
        if type and e.get("type") != type:
            continue
        if tier and e.get("tier") != tier:
            continue
        if scene and scene not in (e.get("scenes") or []):
            continue
        entry = {"id": e["name"], "object": "engine", "tier": e["tier"],
                 "type": e["type"], "status": e["status"]}
        for opt in ("removed_reason", "scenes", "note"):
            if e.get(opt) is not None:
                entry[opt] = e[opt]
        data.append(entry)
    return {"object": "list", "data": data}


@router.get("/scenes")
async def list_scenes(request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    return {
        "object": "list",
        "data": [{"id": name, "object": "scene", "engines": engines}
                 for name, engines in registry.SCENES.items()],
    }
