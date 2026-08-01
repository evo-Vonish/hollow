# -*- coding: utf-8 -*-
"""_ResourceAndAccessMiddleware 单元(网络无关,CI 可跑)。

直接驱动真中间件走一条流式响应,证审查 bug 已修:槽必须**持到整条 body 发完**才释放
(旧 BaseHTTPMiddleware 在响应头就释放,流式 body 阶段不受约束)。
2026-08-01 双池改造后:观测点从旧计数器换为 pools.scheduler 两池在途合计;
新增 429 路径测试——新模型下拒绝仅发生在队列满(pool_queue_full,如实 Retry-After)。
用 httpx.ASGITransport 本进程驱动完整 ASGI 周期,不联网。
"""
import asyncio

import httpx
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

import pytest

import api.main as main
from api import pools


@pytest.fixture
def fresh_scheduler(monkeypatch):
    """每用例新建调度器并替换单例——单例的 Semaphore/Event 绑首个使用它的 loop,
    跨 asyncio.run 复用必炸(RuntimeError: attached to a different loop);
    生产单 loop 无此问题,测试每用例新 loop 必须换新实例。"""
    s = pools.PoolScheduler()
    monkeypatch.setattr(pools, "scheduler", s)
    return s


def _inflight_now() -> int:
    return (sum(pools.scheduler.anon._inflight.values())
            + sum(pools.scheduler.auth._inflight.values()))


def _make_app(observed: list):
    async def research(request):
        async def gen():
            for i in range(3):
                observed.append(_inflight_now())  # 每个 chunk 时的两池在途合计
                yield b"data: x\n\n"
                await asyncio.sleep(0)
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/v1/research", research, methods=["POST"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)
    return app


def _drive(app, path="/v1/research"):
    async def run():
        pools.scheduler.start()  # 中间件 acquire 需要调度协程(本 loop)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                async with c.stream("POST", path) as r:
                    status = r.status_code
                    async for _ in r.aiter_bytes():
                        pass  # 消费整条流
            return status, _inflight_now()
        finally:
            await pools.scheduler.stop()
    return asyncio.run(run())


def test_slot_held_through_streaming_body(fresh_scheduler):
    observed: list = []
    status, final = _drive(_make_app(observed))
    assert status == 200
    # 关键:流式 body 的每个 chunk 时槽都仍被持有(>0);此前 bug 会观测到 0
    assert observed and all(c >= 1 for c in observed), observed
    # 整条 body 发完后归还
    assert final == 0


def test_slot_released_even_if_body_raises(fresh_scheduler):
    # body 中途异常也必须释放槽(finally 保证),否则计数泄漏、慢性耗尽
    async def research(request):
        async def gen():
            yield b"data: x\n\n"
            raise RuntimeError("boom mid-stream")
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/v1/research", research, methods=["POST"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)

    async def run():
        pools.scheduler.start()
        try:
            transport = httpx.ASGITransport(app=app)
            try:
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                    async with c.stream("POST", "/v1/research") as r:
                        async for _ in r.aiter_bytes():
                            pass
            except Exception:
                pass
            return _inflight_now()
        finally:
            await pools.scheduler.stop()

    assert asyncio.run(run()) == 0  # 不泄漏


def test_light_path_not_gated(fresh_scheduler):
    # 轻端点不占池槽
    async def health(request):
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/healthz", health, methods=["GET"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)

    async def run():
        pools.scheduler.start()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.get("/healthz")
            return r.status_code, _inflight_now()
        finally:
            await pools.scheduler.stop()
    status, final = asyncio.run(run())
    assert status == 200 and final == 0


def test_pool_queue_full_returns_429(fresh_scheduler):
    # 新模型唯一拒绝路径:槽 + 队列全满 → 429 pool_queue_full + Retry-After(如实)
    async def slow(request):
        await asyncio.sleep(30)  # 占槽不放
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/v1/research", slow, methods=["POST"])])
    app.add_middleware(main._ResourceAndAccessMiddleware)

    async def run():
        # 测试特权:缩队列上限,1 槽(配置) + 1 队 → 第 3 个必拒
        pools.scheduler.anon._q_per_id = 1
        pools.scheduler.anon._q_global = 1
        pools.scheduler.start()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                t1 = asyncio.ensure_future(c.post("/v1/research"))  # 获槽
                await asyncio.sleep(0.3)
                t2 = asyncio.ensure_future(c.post("/v1/research"))  # 在队(=全局满)
                await asyncio.sleep(0.3)
                r3 = await c.post("/v1/research")                    # → 429
                assert r3.status_code == 429
                body = r3.json()
                assert body["error"]["code"] == "pool_queue_full"
                assert int(r3.headers["Retry-After"]) >= 1
                t1.cancel(); t2.cancel()
                for t in (t1, t2):
                    try:
                        await t
                    except BaseException:  # CancelledError 是 BaseException(3.8+)
                        pass
        finally:
            pools.scheduler.anon._q_per_id = 16   # 还原,防串测试
            pools.scheduler.anon._q_global = 64
            await pools.scheduler.stop()

    asyncio.run(run())
