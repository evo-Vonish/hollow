# -*- coding: utf-8 -*-
"""/v1 —— hollow 的正式 API 面,参考 OpenAI 的格式与调用方式(不仿制其端点)。

借鉴的惯例:
- 对象封套:响应带 id("res_..")/object/created;列表统一 {"object":"list","data":[...]}
- 错误统一 {"error": {message, type, param, code}} + 正确的 HTTP 状态码
- Bearer 鉴权(HOLLOW_API_KEY 设置时启用,只管 /v1/*)
- stream: true 走 SSE;事件是**语义化**的(search.completed / item.completed /
  completed),每条来源抓完净化完立即推送,不等全齐 —— 这是研究 API 该有的流式
域模型仍是我们自己的:query/scene/engines/items/search/fetch,
search+fetch 双侧账目就是本服务的 "usage"。

端点:
  POST /v1/research    创建一次研究(stream 可选)
  GET  /v1/engines     引擎注册表(343 源,支持 status/scene/type/tier 过滤)
  GET  /v1/scenes      场景 -> 默认引擎集
"""
import json
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import config, orchestrator, registry
from api.models import ResearchItem, ResearchRequest, ResearchResponse, SearchMeta
from api.searx_client import InvalidQueryError, SearxUnavailableError

router = APIRouter(prefix="/v1")


# ---------- OpenAI 风格的错误与鉴权 ----------

def _error(status: int, message: str, err_type: str, code: str,
           param: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": err_type,
                           "param": param, "code": code}},
    )


def _check_auth(request: Request) -> JSONResponse | None:
    if not config.API_KEY:
        return None
    if request.headers.get("authorization", "") != f"Bearer {config.API_KEY}":
        return _error(401, "Incorrect API key provided.",
                      "invalid_request_error", "invalid_api_key")
    return None


# ---------- POST /v1/research ----------

class ResearchCreate(BaseModel):
    query: str = Field(min_length=1, description="研究问题")
    scene: str | None = Field(default=None, description="场景默认集(注册表 meta.scenes)")
    engines: list[str] | None = Field(default=None, description="点名引擎,优先于 scene")
    top_n: int = Field(default=config.FETCH_TOP_N_DEFAULT, ge=1,
                       le=config.FETCH_TOP_N_MAX, description="抓取前 N 条")
    purify: bool = True
    timeout: float = Field(default=config.FETCH_TIMEOUT, gt=0, le=60,
                           description="每 URL 抓取超时(秒)")
    language: str = "auto"
    time_range: str | None = None
    safesearch: int = Field(default=0, ge=0, le=2)
    stream: bool = False
    model_config = ConfigDict(extra="ignore")


def _resolve_engines(body: ResearchCreate) -> list[str] | JSONResponse:
    """engines > scene > 网关默认集。点名时拒 L1 removed 与未知名——
    SearXNG 对无效 engines 会静默回退默认集(实测),必须在这里挡住。"""
    if body.engines:
        for name in body.engines:
            if name in registry.REMOVED:
                return _error(400,
                              f"Engine '{name}' is removed ({registry.REMOVED[name]}), "
                              f"see data/engine_registry.yaml.",
                              "invalid_request_error", "engine_removed", "engines")
            if name not in registry.ALL_NAMES:
                return _error(400, f"Unknown engine '{name}'.",
                              "invalid_request_error", "unknown_engine", "engines")
        return body.engines
    if body.scene:
        engines = registry.SCENES.get(body.scene)
        if engines is None:
            return _error(400,
                          f"Unknown scene '{body.scene}'. Valid: {sorted(registry.SCENES)}",
                          "invalid_request_error", "unknown_scene", "scene")
        return engines
    return config.DEFAULT_ENGINES


def _item_dict(item: ResearchItem, with_content: bool = True) -> dict:
    d = item.model_dump()
    if not with_content:
        d["content"] = None
    return {"object": "research.item", **d}


def _research_object(rid: str, created: int, body: ResearchCreate,
                     engines: list[str], resp: ResearchResponse,
                     with_content: bool = True) -> dict:
    return {
        "id": rid,
        "object": "research",
        "created": created,
        "query": resp.query,
        "scene": body.scene if not body.engines else None,
        "engines": engines,
        "items": [_item_dict(it, with_content) for it in resp.items],
        "search": resp.meta.search.model_dump(),
        "fetch": resp.meta.fetch.model_dump(),
    }


@router.post("/research")
async def create_research(body: ResearchCreate, request: Request):
    denied = _check_auth(request)
    if denied:
        return denied
    engines = _resolve_engines(body)
    if isinstance(engines, JSONResponse):
        return engines

    req = ResearchRequest(
        q=body.query, engines=engines, language=body.language,
        time_range=body.time_range, safesearch=body.safesearch,
        fetch_top_n=body.top_n, purify=body.purify, fetch_timeout=body.timeout,
    )
    client: httpx.AsyncClient = request.app.state.http
    rid = f"res_{uuid.uuid4().hex}"
    created = int(time.time())

    if not body.stream:
        try:
            resp = await orchestrator.run_research(req, client)
        except InvalidQueryError as e:
            return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
        except SearxUnavailableError as e:
            return _error(502, str(e), "api_error", "upstream_unavailable")
        return _research_object(rid, created, body, engines, resp)

    # ---- SSE:语义化事件,每条来源完成即推送 ----
    # 搜索先行完成再开流,搜索类错误仍走标准 HTTP 错误(客户端好处理);
    # 开流之后的一切失败都以占位 item 事件呈现,不断流。
    events = orchestrator.run_research_events(req, client)
    try:
        first = await events.__anext__()  # ("search", SearchMeta, selected)
    except InvalidQueryError as e:
        return _error(400, str(e), "invalid_request_error", "invalid_query", "query")
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
