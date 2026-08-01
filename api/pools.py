# -*- coding: utf-8 -*-
"""双池调度器:登录池/匿名池对称隔离 + 匿名池自适应公平慢速(AFSD)。

动机(2026-08-01):免费 API 公网开放,匿名流量需要防恶意刷取,同时保证登录用户体验。
算法:
  池划分   登录池与匿名池容量对称(各 POOL_*_SLOTS 并发槽)。
  匿名身份 i = 客户端 IP(CF-Connecting-IP > X-Forwarded-For 首跳 > client.host)。
  活跃身份 A = 队列非空 ∨ 在途>0 ∨ 近 ANON_ID_IDLE_S 秒有入队;k = |A|。

  匿名池服务速率(核心):
    R(k) = R_SLOW + (R_FULL - R_SLOW) * min(1, (k-1)/(K_FULL-1))
    k=1   -> R_SLOW:单人独占必为刷取嫌疑,钉死低速(池照转,只是慢)。
    k>=K_FULL -> R_FULL:真实公共流量,池满速。
    work-conserving:队列非空则槽不空转("始终保持满");不掉请求,仅排队。

  身份公平  轮转(RR)调度各身份队列,有效速率 r_i = R(k)/k——攻击者挤不掉别人。
  排队上限  每身份 ANON_Q_PER_ID + 全局 ANON_Q_GLOBAL;满才拒(PoolFullError,
    携带 retry_after_s 如实告知)。排队超 QUEUE_WAIT_MAX_S 同样拒绝(queue_timeout)。
  防绕    多 IP 轮换攻击由池级速率兜底:匿名池总吞吐 <= R(k),IP 再多只是把饼切薄;
    登录池容量物理隔离,匿名流量任何形态都无法侵占。
  登录池  per-key 轮转公平(多 key 均分);单 key 独占满速——已认证不惩罚独占。
  账目    Ticket 携带 queue_wait_ms / pool / active_identities / rate_limit_rps,
    全部进响应 meta(底线②③:可溯源,不静默)。
"""
import asyncio
import time
from collections import deque
from dataclasses import dataclass

from api import config


class PoolFullError(Exception):
    """队列满或排队超时——唯一的拒绝路径,retry_after_s 如实告知。"""

    def __init__(self, reason: str, retry_after_s: int):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


@dataclass
class Ticket:
    """获槽凭证:携账目,由调用方写入响应 meta。"""

    pool: str                  # "auth" | "anon"
    identity: str              # key_id 或 IP(日志/meta 用)
    queue_wait_ms: int
    pool_depth: int            # 获槽瞬间池内仍在排队总数
    active_identities: int
    rate_limit_rps: float      # 匿名池当前生效速率;登录池为 0(不限)


@dataclass
class _Waiter:
    identity: str
    enqueued_at: float
    future: asyncio.Future
    cancelled: bool = False


class _FairPool:
    """单池:per-identity FIFO 队列 + 轮转调度 + 并发槽 + 可选动态速率。

    rate_fn(k) -> req/s;None 表示不限速(登录池,仅槽约束)。
    槽在调度协程 acquire、在请求协程 release(asyncio.Semaphore 不绑定 owner)。
    """

    def __init__(self, name: str, slots: int, rate_fn, q_per_id: int, q_global: int,
                 idle_s: float, wait_max_s: float):
        self.name = name
        self._slots = asyncio.Semaphore(slots)
        self._rate_fn = rate_fn
        self._q_per_id = q_per_id
        self._q_global = q_global
        self._idle_s = idle_s
        self._wait_max_s = wait_max_s
        self._queues: dict[str, deque[_Waiter]] = {}
        self._rr_order: deque[str] = deque()      # 轮转序:有非空队列的身份
        self._inflight: dict[str, int] = {}        # identity -> 在途数
        self._last_seen: dict[str, float] = {}     # identity -> 最近入队时刻
        self._new_work = asyncio.Event()
        self._last_dispense = 0.0                  # 上次放行时刻(速率整形)
        self._scheduler_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()                # 队列结构互斥(单事件循环内仍防交错)

    # ---------- 生命周期 ----------

    def start(self):
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.ensure_future(self._schedule_loop())

    async def stop(self):
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

    # ---------- 观测 ----------

    def _active_identities(self, now: float) -> int:
        n = 0
        for ident in set(list(self._queues) + list(self._inflight) + list(self._last_seen)):
            if (self._queues.get(ident) and len(self._queues[ident]) > 0) \
                    or self._inflight.get(ident, 0) > 0 \
                    or now - self._last_seen.get(ident, 0.0) <= self._idle_s:
                n += 1
        return max(n, 1)

    def depth(self) -> int:
        return sum(len(q) for q in self._queues.values())

    # ---------- 请求侧 ----------

    async def acquire(self, identity: str) -> Ticket:
        """排队等待获槽。PoolFullError:队列满/排队超时(仅两条拒绝路径)。"""
        now = time.monotonic()
        async with self._lock:
            q = self._queues.setdefault(identity, deque())
            if len(q) >= self._q_per_id:
                raise PoolFullError("identity_queue_full", self._retry_after(now))
            if self.depth() >= self._q_global:
                raise PoolFullError("pool_queue_full", self._retry_after(now))
            if len(q) == 0 and identity not in self._rr_order:
                self._rr_order.append(identity)
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            w = _Waiter(identity=identity, enqueued_at=now, future=fut)
            q.append(w)
            self._last_seen[identity] = now
            self._new_work.set()
        try:
            await asyncio.wait_for(fut, timeout=self._wait_max_s)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            w.cancelled = True
            async with self._lock:
                try:
                    q.remove(w)
                except ValueError:
                    pass
            raise PoolFullError("queue_timeout", self._retry_after(time.monotonic()))
        return fut.result()

    def release(self, identity: str):
        """请求完成:释放在途计数与并发槽。必须与 acquire 成功配对。"""
        self._inflight[identity] = max(0, self._inflight.get(identity, 1) - 1)
        self._slots.release()

    def _retry_after(self, now: float) -> int:
        k = self._active_identities(now)
        rps = self._rate_fn(k) if self._rate_fn else 1.0
        waiters = max(1, min(self._q_per_id, 4))
        return max(1, int(waiters / max(rps, 0.05)) + 1)

    # ---------- 调度协程 ----------

    async def _schedule_loop(self):
        while True:
            # 无工作则挂起
            if self.depth() == 0:
                self._new_work.clear()
                await self._new_work.wait()
                continue
            now = time.monotonic()
            k = self._active_identities(now)
            # 速率整形:两次放行最小间隔 1/R(k)(登录池 rate_fn=None 不限)
            if self._rate_fn:
                min_gap = 1.0 / self._rate_fn(k)
                gap = now - self._last_dispense
                if gap < min_gap:
                    await asyncio.sleep(min_gap - gap)
                    continue
            # 并发槽:满则等(任一请求 release 即唤醒——轮询节拍足够小)
            if self._slots.locked():
                await asyncio.sleep(0.05)
                continue
            # 轮转选一个非空队列
            picked: _Waiter | None = None
            async with self._lock:
                for _ in range(len(self._rr_order)):
                    ident = self._rr_order[0]
                    self._rr_order.rotate(-1)
                    q = self._queues.get(ident)
                    while q and q[0].cancelled:
                        q.popleft()
                    if q:
                        picked = q.popleft()
                        if not q:
                            try:
                                self._rr_order.remove(ident)
                            except ValueError:
                                pass
                        else:
                            # 队尾重排,保证身份间轮转公平
                            try:
                                self._rr_order.remove(ident)
                            except ValueError:
                                pass
                            self._rr_order.append(ident)
                        break
            if picked is None:
                continue
            await self._slots.acquire()
            self._inflight[picked.identity] = self._inflight.get(picked.identity, 0) + 1
            self._last_dispense = time.monotonic()
            ticket = Ticket(
                pool=self.name,
                identity=picked.identity,
                queue_wait_ms=int((self._last_dispense - picked.enqueued_at) * 1000),
                pool_depth=self.depth(),
                active_identities=k,
                rate_limit_rps=self._rate_fn(k) if self._rate_fn else 0.0,
            )
            if not picked.future.done():
                picked.future.set_result(ticket)


# ---------- 速率函数 ----------

def anon_rate_rps(k: int) -> float:
    """匿名池核心曲线:k=1 钉死 R_SLOW;k>=K_FULL 满速;中间线性爬升。"""
    k = max(1, k)
    if k >= config.ANON_K_FULL:
        return config.ANON_RATE_FULL
    span = max(1, config.ANON_K_FULL - 1)
    return config.ANON_RATE_SLOW + (config.ANON_RATE_FULL - config.ANON_RATE_SLOW) * ((k - 1) / span)


class PoolScheduler:
    """双池组合:auth(登录)与 anon(匿名)。单例进程内持有(单进程硬约束一致)。"""

    def __init__(self):
        self.auth = _FairPool(
            "auth", slots=config.POOL_AUTH_SLOTS, rate_fn=None,
            q_per_id=config.AUTH_Q_PER_KEY, q_global=config.AUTH_Q_GLOBAL,
            idle_s=config.ANON_ID_IDLE_S, wait_max_s=config.QUEUE_WAIT_MAX_S)
        self.anon = _FairPool(
            "anon", slots=config.POOL_ANON_SLOTS, rate_fn=anon_rate_rps,
            q_per_id=config.ANON_Q_PER_ID, q_global=config.ANON_Q_GLOBAL,
            idle_s=config.ANON_ID_IDLE_S, wait_max_s=config.QUEUE_WAIT_MAX_S)

    def start(self):
        self.auth.start()
        self.anon.start()

    async def stop(self):
        await self.auth.stop()
        await self.anon.stop()

    def pool_for(self, authenticated: bool) -> _FairPool:
        return self.auth if authenticated else self.anon


scheduler = PoolScheduler()
