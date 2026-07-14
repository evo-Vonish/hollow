# -*- coding: utf-8 -*-
"""_ResourceAndAccessMiddleware 单元(网络无关,CI 可跑)。

直接驱动真中间件走一条流式响应,证审查 bug 已修:在飞槽必须**持到整条 body 发完**才释放
(旧 BaseHTTPMiddleware 在响应头就释放,流式 body 阶段不受在飞上限约束)。用 httpx.ASGITransport
本进程驱动完整 ASGI 周期,不联网。
"""
import asyncio

import httpx
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

import api.main as main


def _make_app(observed: list):
    async def research(request):
        async def gen():
            for i in range(3):
                observed.append(main._inflight.current)  # 每个 chunk 时的在飞计数
                yield b"data: x\n\n"
                await asyncio.sleep(0)
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/v1/research", research, methods=["POST"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)
    return app


def _drive(app, path="/v1/research"):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            async with c.stream("POST", path) as r:
                status = r.status_code
                async for _ in r.aiter_bytes():
                    pass  # 消费整条流
        return status, main._inflight.current
    return asyncio.run(run())


def setup_function(_):
    main._inflight.current = 0


def teardown_function(_):
    main._inflight.current = 0


def test_slot_held_through_streaming_body():
    observed: list = []
    status, final = _drive(_make_app(observed))
    assert status == 200
    # 关键:流式 body 的每个 chunk 时槽都仍被持有(>0);此前 bug 会观测到 0
    assert observed and all(c >= 1 for c in observed), observed
    # 整条 body 发完后归还
    assert final == 0


def test_slot_released_even_if_body_raises():
    # body 中途异常也必须释放槽(finally 保证),否则计数泄漏、慢性耗尽
    async def research(request):
        async def gen():
            yield b"data: x\n\n"
            raise RuntimeError("boom mid-stream")
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/v1/research", research, methods=["POST"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)

    async def run():
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                async with c.stream("POST", "/v1/research") as r:
                    async for _ in r.aiter_bytes():
                        pass
        except Exception:
            pass
        return main._inflight.current

    assert asyncio.run(run()) == 0  # 不泄漏


def test_light_path_not_gated():
    # 轻端点不占在飞槽
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/healthz", health, methods=["GET"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/healthz")
        return r.status_code, main._inflight.current
    status, final = asyncio.run(run())
    assert status == 200 and final == 0
