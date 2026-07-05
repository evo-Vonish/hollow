# -*- coding: utf-8 -*-
"""FastAPI app:POST /v0/research + GET /healthz。

启动:.venv-api\\Scripts\\python.exe -m uvicorn api.main:app --port 8080
前置:本地 SearXNG 进程已起(tools/run_local.ps1 一键双起)。
"""
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from api import config, orchestrator
from api.models import ResearchRequest, ResearchResponse
from api.searx_client import InvalidQueryError, SearxUnavailableError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # trust_env=False:对 SearXNG 的本机回环调用不能被系统代理环境变量劫持
    app.state.http = httpx.AsyncClient(trust_env=False)
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(title="hollow", version="0.0.1", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    searx = "unknown"
    try:
        r = await app.state.http.get(f"{config.SEARXNG_URL}/healthz", timeout=3)
        searx = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except httpx.HTTPError:
        searx = "unreachable"
    return {"status": "ok", "searxng": searx}


@app.post("/v0/research", response_model=ResearchResponse)
async def research(req: ResearchRequest) -> ResearchResponse:
    try:
        return await orchestrator.run_research(req, app.state.http)
    except InvalidQueryError as e:  # 客户端输入问题,不是上游故障
        raise HTTPException(status_code=400, detail=str(e)) from e
    except SearxUnavailableError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
