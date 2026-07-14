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
from starlette.exceptions import HTTPException as StarletteHTTPException

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
# 尾斜杠一致化(OpenAPI/契约收尾):不做 307 跳转;/v1/search/ 直接 404(走错误封套),
# 一个规范 URL,少一次让部分客户端丢 body 的重定向惊喜。
app.router.redirect_slashes = False


def _custom_openapi():
    """在自动 schema 上补声明 Bearer 鉴权(鉴权是手写的 auth_error,FastAPI 不会自动标注)。
    /docs 出现 Authorize 按钮,/v1/* 标注需要 bearerAuth——HOLLOW_API_KEY 未设时其实开放,
    但契约层如实声明鉴权方式。"""
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(
        title=app.title, version=app.version, routes=app.routes,
        description="hollow —— AI 研究浏览器搜索 API(参考 OpenAI 的信封/错误/SSE,自家域模型)。"
                    "设置 HOLLOW_API_KEY 后 /v1/* 需 `Authorization: Bearer <key>`。",
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
        "type": "http", "scheme": "bearer",
        "description": "HOLLOW_API_KEY 设置时启用;未设置则 /v1 开放。/v0、/healthz 为内部面。",
    }
    for path, item in schema.get("paths", {}).items():
        if path.startswith("/v1/"):
            for method, op in item.items():
                if isinstance(op, dict) and method in ("get", "post", "put", "delete", "patch"):
                    op.setdefault("security", [{"bearerAuth": []}])
    app.openapi_schema = schema
    return schema


class _InflightLimiter:
    """在飞重端点计数闸。单线程 asyncio:check→自增之间无 await,故无需锁。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.current = 0

    def try_acquire(self) -> bool:
        if self.current >= self.limit:
            return False
        self.current += 1
        return True

    def release(self) -> None:
        if self.current > 0:
            self.current -= 1


# 抓取密集的重端点(search 只召回不算;其上游压力由 SEARX_GATE 单独管)
_HEAVY_PATHS = frozenset({"/v1/research", "/v1/fetch", "/v0/research"})
_inflight = _InflightLimiter(config.MAX_INFLIGHT_HEAVY)


@app.middleware("http")
async def _inflight_guard(request: Request, call_next):
    """重端点在飞上限:超限直接 429 shed load(审查 #4:不让 12 并发把所有人尾延迟拉大 10 倍)。
    轻端点(healthz/search/engines/scenes)不受限。定义在 _access_log 之前 → 后者仍是最外层、能记到 429。"""
    heavy = request.method == "POST" and request.url.path in _HEAVY_PATHS
    if not heavy:
        return await call_next(request)
    if not _inflight.try_acquire():
        log.warning("inflight cap %d reached -> 429 %s", config.MAX_INFLIGHT_HEAVY, request.url.path)
        return UTF8JSONResponse(
            status_code=429,
            content={"error": {
                "message": f"Server at capacity ({config.MAX_INFLIGHT_HEAVY} concurrent heavy "
                           f"requests in flight). Retry shortly.",
                "type": "rate_limit_error", "param": None, "code": "too_many_requests"}},
            headers={"Retry-After": "1"},
        )
    try:
        return await call_next(request)
    finally:
        _inflight.release()


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
app.openapi = _custom_openapi  # 路由已挂,openapi schema 惰性生成时能拿到全部 /v1 路径


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


# 状态码 → OpenAI 风格 code(契约健壮批 #5:404/405/500 也走统一封套,不再裸 {"detail":...})
_STATUS_CODE = {
    400: "invalid_request", 401: "invalid_api_key", 403: "forbidden",
    404: "not_found", 405: "method_not_allowed", 429: "too_many_requests",
    502: "upstream_unavailable", 503: "service_unavailable",
}


def _err_type(status: int) -> str:
    if status == 429:
        return "rate_limit_error"
    if status >= 500:
        return "api_error"
    return "invalid_request_error"


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    """所有 HTTPException(含 Starlette 路由的 404/405 与 /v0 抛的 400/502)统一转错误封套。
    FastAPI 的 HTTPException 是其子类,一并覆盖。"""
    return UTF8JSONResponse(
        status_code=exc.status_code,
        content={"error": {
            "message": str(exc.detail),
            "type": _err_type(exc.status_code),
            "param": None,
            "code": _STATUS_CODE.get(exc.status_code, "http_error"),
        }},
        headers=getattr(exc, "headers", None) or None,
    )


@app.exception_handler(Exception)
async def _unhandled_exc(request: Request, exc: Exception):
    """兜底 500:不泄漏内部堆栈给客户端(只回通用消息);详情已由 access-log 中间件记进服务端日志。"""
    return UTF8JSONResponse(
        status_code=500,
        content={"error": {
            "message": "Internal server error.",
            "type": "api_error", "param": None, "code": "internal_error",
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
