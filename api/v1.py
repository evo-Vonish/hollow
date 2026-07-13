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
  GET  /v1/engines     引擎注册表(343 源,支持 status/scene/type/tier 过滤)
  GET  /v1/scenes      场景 -> 默认引擎集

引擎选取:scenes(多选,并集) ∪ engines(自定义点名) ,都不传用网关默认集;
点名拒 L1 removed 与未知名(SearXNG 对无效 engines 会静默回退默认集,必须挡住)。
"""
import json
import time
import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import auth, config, orchestrator, registry, rerank
from api.models import ResearchItem, ResearchRequest, ResearchResponse, SearchMeta
from api.responses import UTF8JSONResponse
from api.searx_client import InvalidQueryError, SearxBadRequestError, SearxUnavailableError
from api import searx_client

router = APIRouter(prefix="/v1")


# ---------- OpenAI 风格的错误与鉴权 ----------

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
    query: str = Field(min_length=1)
    scenes: list[str] | None = Field(default=None, description="场景,可多选,取并集")
    engines: list[str] | None = Field(default=None, description="自定义引擎,并入场景集")
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
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
    client: httpx.AsyncClient = request.app.state.http
    try:
        outcome = await searx_client.search(
            client, q=body.query, engines=engines, language=body.language,
            time_range=body.time_range, safesearch=body.safesearch,
        )
    except InvalidQueryError as e:
        return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
    except SearxBadRequestError as e:
        return _error(400, f"SearXNG rejected a parameter: {e}", "invalid_request_error", "invalid_search_param")
    except SearxUnavailableError as e:
        return _error(502, str(e), "api_error", "upstream_unavailable")

    results = []
    for r in rerank.rerank(body.query, outcome.results):  # 按相关性排序(否则 position 分把空壳排前)
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
            "snippet": orchestrator._safe_str(r.get("content")),
        })
    ledger = SearchMeta(
        engines_requested=engines,
        engines_used=outcome.engines_used,
        engines_failed=outcome.engines_failed,
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
        "results": results,
        "answers": searx_client.instant_answers(outcome),  # infobox/answer 透出(不再丢弃)
        "search": ledger.model_dump(),
    }
    ignored = _ignored(body)
    if ignored:  # 未知/拼错参数如实回报,不静默吞掉(底线②)
        resp["ignored_params"] = ignored
    return resp


# ---------- POST /v1/research(搜索+抓取+净化) ----------

class ResearchCreate(BaseModel):
    query: str = Field(min_length=1, description="研究问题")
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
    stream: bool = False
    model_config = ConfigDict(extra="allow")  # 未知参数收进 model_extra 后回报,不静默吞掉


def _item_dict(item: ResearchItem, with_content: bool = True) -> dict:
    d = item.model_dump()
    if not with_content:
        d["content"] = None
    return {"object": "research.item", **d}


def _research_object(rid: str, created: int, body: ResearchCreate,
                     engines: list[str], resp: ResearchResponse,
                     with_content: bool = True) -> dict:
    obj = {
        "id": rid,
        "object": "research",
        "created": created,
        "query": resp.query,
        "scenes": body.scenes,
        "engines": engines,
        "items": [_item_dict(it, with_content) for it in resp.items],
        "answers": resp.answers,  # infobox/answer 透出(不再丢弃)
        "search": resp.meta.search.model_dump(),
        "fetch": resp.meta.fetch.model_dump(),
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

    req = ResearchRequest(
        q=body.query, engines=engines, language=body.language,
        time_range=body.time_range, safesearch=body.safesearch,
        fetch_top_n=body.top_n, purify=body.purify, fetch_timeout=body.timeout,
        concurrency=body.concurrency, budget=body.budget,
        max_content_chars=body.max_content_chars, escalate=body.escalate,
        mode=body.mode,
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

    def _frame(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def _sse():
        _, search_meta, selected = first
        assert isinstance(search_meta, SearchMeta)
        yield _frame({
            "object": "research.event", "event": "research.search.completed",
            "id": rid, "created": created,
            "search": search_meta.model_dump(), "selected": selected,
        })
        try:
            async for event in events:
                if event[0] == "item":
                    _, index, item = event
                    yield _frame({
                        "object": "research.event", "event": "research.item.completed",
                        "id": rid, "index": index, "item": _item_dict(item),
                    })
                elif event[0] == "done":
                    # 汇总事件不重复携带正文(item 事件已推过),只留账目与占位
                    yield _frame({
                        "object": "research.event", "event": "research.completed",
                        "id": rid,
                        "research": _research_object(rid, created, body, engines,
                                                     event[1], with_content=False),
                    })
            yield "data: [DONE]\n\n"
        finally:
            await events.aclose()  # 客户端断连时收尾,取消未完成的抓取任务

    return StreamingResponse(_sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


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
