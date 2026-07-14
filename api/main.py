# -*- coding: utf-8 -*-
"""FastAPI app:POST /v0/research + GET /healthz。

启动:.venv-api\\Scripts\\python.exe -m uvicorn api.main:app --port 8080
前置:本地 SearXNG 进程已起(tools/run_local.ps1 一键双起)。
"""
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from api import auth, config, fetcher, orchestrator
from api.logging_setup import log, setup_logging
from api.models import ResearchRequest, ResearchResponse
from api.responses import UTF8JSONResponse
from api.searx_client import InvalidQueryError, SearxBadRequestError, SearxUnavailableError


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("hollow gateway starting (searxng=%s, log_level=%s)",
             config.SEARXNG_URL, config.LOG_LEVEL)
    # trust_env=False:对 SearXNG 的本机回环调用不能被系统代理环境变量劫持
    app.state.http = httpx.AsyncClient(trust_env=False)
    try:
        yield
    finally:
        await app.state.http.aclose()
        fetcher.shutdown_executors()  # 取消排队抓取,不 join 在跑的浏览器线程
        log.info("hollow gateway stopped")


app = FastAPI(title="hollow", version="0.0.1", lifespan=lifespan,
              default_response_class=UTF8JSONResponse)


@app.middleware("http")
async def _access_log(request: Request, call_next):
    """请求访问日志(底线③运维溯源):方法/路径/状态/耗时。异常先记再抛给 500 handler。"""
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        dt = (time.perf_counter() - t0) * 1000
        log.exception("%s %s -> unhandled (%.0fms)", request.method, request.url.path, dt)
        raise
    dt = (time.perf_counter() - t0) * 1000
    lvl = log.warning if response.status_code >= 500 else log.info
    lvl("%s %s -> %d (%.0fms)", request.method, request.url.path, response.status_code, dt)
    return response

# /v1 正式 API 面(OpenAI 风格的封套/错误/SSE,自家域模型;见 api/v1.py)
from api.v1 import router as _v1_router  # noqa: E402

app.include_router(_v1_router)


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc: RequestValidationError):
    """pydantic 校验失败统一转 OpenAI 风格错误封套(400),不走 FastAPI 默认
    422 {"detail":[...]} —— 否则按封套编码的客户端解析不了(审查确认项)。"""
    errors = exc.errors()
    first = errors[0] if errors else {}
    loc = [str(x) for x in first.get("loc", []) if x != "body"]
    param = ".".join(loc) or None
    return UTF8JSONResponse(
        status_code=400,
        content={"error": {
            "message": f"Invalid request: {param or 'body'}: {first.get('msg', 'validation error')}",
            "type": "invalid_request_error",
            "param": param,
            "code": "invalid_parameter",
        }},
    )


@app.get("/healthz")
async def healthz(deep: bool = False):
    """浅探(默认):网关活着 + SearXNG 可达。深探(?deep=1):接 SearXNG /stats/errors,
    汇总当前有报错的引擎数——线上排障用(底线③)。深探失败不拖垮 healthz,字段给 null。"""
    searx = "unknown"
    try:
        r = await app.state.http.get(f"{config.SEARXNG_URL}/healthz", timeout=3)
        searx = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except httpx.HTTPError:
        searx = "unreachable"
    body = {"status": "ok" if searx == "ok" else "degraded", "searxng": searx}
    if deep:
        body["searxng_error_engines"] = await _searx_error_engines()
    return body


async def _searx_error_engines() -> int | None:
    """SearXNG /stats/errors 里当前有报错的引擎数;拿不到(端点缺失/超时)返回 null。"""
    try:
        r = await app.state.http.get(f"{config.SEARXNG_URL}/stats/errors", timeout=3)
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if isinstance(data, dict):  # 形如 {engine: [errors...]}
        return sum(1 for v in data.values() if v)
    if isinstance(data, list):  # 形如 [{engine,...}, ...]
        return len(data)
    return None


@app.post("/v0/research", response_model=ResearchResponse)
async def research(req: ResearchRequest, request: Request):
    denied = auth.auth_error(request)  # 安全批:/v0 也受 HOLLOW_API_KEY 保护(此前完全敞开)
    if denied:
        return denied
    try:
        return await orchestrator.run_research(req, app.state.http)
    except (InvalidQueryError, SearxBadRequestError) as e:  # 客户端输入问题,不是上游故障
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SearxUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
