"""双池调度器(api/pools.py)单元测试:速率曲线、公平轮转、队列上限、账目、登录池不限速。"""
import asyncio
import time

import pytest

from api import config, pools


def _mkpool(name="anon", slots=2, rate_fn=None, q_per_id=3, q_global=8,
            idle_s=60.0, wait_max_s=5.0):
    return pools._FairPool(name, slots=slots, rate_fn=rate_fn, q_per_id=q_per_id,
                           q_global=q_global, idle_s=idle_s, wait_max_s=wait_max_s)


# ---------- 速率曲线 ----------

def test_rate_curve_monotonic(monkeypatch):
    monkeypatch.setattr(config, "ANON_RATE_SLOW", 0.2)
    monkeypatch.setattr(config, "ANON_RATE_FULL", 1.6)
    monkeypatch.setattr(config, "ANON_K_FULL", 4)
    assert pools.anon_rate_rps(1) == pytest.approx(0.2)          # 单人独占钉死
    assert pools.anon_rate_rps(4) == pytest.approx(1.6)          # 满速
    assert pools.anon_rate_rps(10) == pytest.approx(1.6)         # 超 K_FULL 仍满速
    mid = pools.anon_rate_rps(2)
    assert 0.2 < mid < 1.6                                        # 中间线性爬升
    r = [pools.anon_rate_rps(k) for k in range(1, 6)]
    assert all(b >= a for a, b in zip(r, r[1:]))                  # 单调不减


# ---------- 调度行为 ----------

def test_single_identity_throttled():
    """k=1 独占:放行间隔 >= 1/R(慢速钉死,池照转)。"""
    async def go():
        rate = 20.0  # 测试用高速率,间隔 50ms
        pool = _mkpool(rate_fn=lambda k: rate, slots=2)
        pool.start()
        try:
            t0 = time.monotonic()
            tickets = []
            for _ in range(3):                      # acquire/release 交替,不积压
                tickets.append(await pool.acquire("1.1.1.1"))
                pool.release("1.1.1.1")
            dt = time.monotonic() - t0
            assert dt >= 2 / rate * 0.9  # 3 次放行至少 2 个间隔(容忍 10%)
            assert all(t.pool == "anon" and t.rate_limit_rps == rate for t in tickets)
        finally:
            await pool.stop()
    asyncio.run(go())


def test_two_identities_fair_rotation():
    """两身份各压 4 请求:轮转放行,前 2 次放行必见双方(攻击者挤不掉别人)。"""
    async def go():
        pool = _mkpool(rate_fn=lambda k: 100.0, slots=4, q_per_id=8, q_global=16)
        pool.start()
        done = []

        async def wait_and_record(ident):
            await pool.acquire(ident)
            done.append(ident)
            pool.release(ident)                       # 立刻释放,后续请求才能获槽

        try:
            await asyncio.gather(*[wait_and_record("a") for _ in range(4)],
                                 *[wait_and_record("b") for _ in range(4)])
            assert "a" in done[:2] and "b" in done[:2]     # 轮转:前两名就有双方
            assert done.count("a") == 4 and done.count("b") == 4
        finally:
            await pool.stop()
    asyncio.run(go())


def test_identity_queue_full_rejects():
    async def go():
        pool = _mkpool(rate_fn=lambda k: 100.0, q_per_id=2, slots=1)
        pool.start()
        try:
            first = await pool.acquire("9.9.9.9")      # 占槽,不释放
            t2 = asyncio.ensure_future(pool.acquire("9.9.9.9"))   # 在队 1
            t3 = asyncio.ensure_future(pool.acquire("9.9.9.9"))   # 在队 2 = 满
            await asyncio.sleep(0.05)
            with pytest.raises(pools.PoolFullError) as ei:
                await pool.acquire("9.9.9.9")          # 第 3 条排队请求 → 拒
            assert ei.value.reason == "identity_queue_full"
            assert ei.value.retry_after_s >= 1
            t2.cancel(); t3.cancel(); pool.release("9.9.9.9")
        finally:
            await pool.stop()
    asyncio.run(go())


def test_global_queue_full_rejects():
    async def go():
        pool = _mkpool(rate_fn=lambda k: 100.0, q_per_id=3, q_global=2, slots=1)
        pool.start()
        try:
            await pool.acquire("ip0")                       # 占槽,不释放
            ts = [asyncio.ensure_future(pool.acquire("ip1")),   # 在队 1
                  asyncio.ensure_future(pool.acquire("ip2"))]   # 在队 2 = 全局满
            await asyncio.sleep(0.05)
            with pytest.raises(pools.PoolFullError) as ei:
                await pool.acquire("ip3")                 # depth=2 >= q_global=2 → 拒
            assert ei.value.reason == "pool_queue_full"
            for t in ts:
                t.cancel()
            pool.release("ip0")
        finally:
            await pool.stop()
    asyncio.run(go())


def test_queue_wait_timeout():
    async def go():
        pool = _mkpool(rate_fn=lambda k: 0.001, q_per_id=5, slots=1, wait_max_s=0.15)
        pool.start()
        try:
            blocker = asyncio.ensure_future(pool.acquire("slow"))  # 获槽后永不释放(占住唯一槽)
            await asyncio.sleep(0.05)
            with pytest.raises(pools.PoolFullError) as ei:
                await pool.acquire("victim")
            assert ei.value.reason in ("queue_timeout",)
            blocker.cancel()
        finally:
            await pool.stop()
    asyncio.run(go())


def test_auth_pool_no_rate_limit():
    """登录池:rate_fn=None,仅槽约束——单 key 独占满速(已认证不惩罚独占)。"""
    async def go():
        pool = _mkpool(name="auth", slots=2, rate_fn=None)
        pool.start()
        try:
            t0 = time.monotonic()
            for _ in range(4):
                await pool.acquire("key_x")
                pool.release("key_x")
            assert time.monotonic() - t0 < 0.5   # 无速率整形,毫秒级连续放行
        finally:
            await pool.stop()
    asyncio.run(go())


def test_ticket_ledger_fields():
    async def go():
        pool = _mkpool(rate_fn=lambda k: 50.0, slots=1)
        pool.start()
        try:
            t = await pool.acquire("8.8.8.8")
            pool.release("8.8.8.8")
            assert t.pool == "anon" and t.identity == "8.8.8.8"
            assert t.queue_wait_ms >= 0 and t.active_identities >= 1
            assert t.rate_limit_rps == 50.0
        finally:
            await pool.stop()
    asyncio.run(go())


def test_inflight_counts_active_identity():
    """在途请求计入活跃身份——攻击者请求在途时 k 不塌缩。"""
    async def go():
        pool = _mkpool(rate_fn=lambda k: 100.0, slots=2)
        pool.start()
        try:
            await pool.acquire("attacker")
            now = time.monotonic()
            assert pool._active_identities(now) >= 1
            pool.release("attacker")
        finally:
            await pool.stop()
    asyncio.run(go())
