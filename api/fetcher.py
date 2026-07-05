# -*- coding: utf-8 -*-
"""Scrapling 静态档抓取:信号量限并发 + 硬超时 + 失败占位(docs/design/03 §6)。

- 静态档 timeout 单位是「秒」(浏览器档才是毫秒,别搞混 —— session-01 实测)
- curl_cffi CurlError code 28 → timeout;其余传输错误 → failed
- HTTP 401/403/407/429/451 → blocked(静态档被拦,二版走浏览器升级链)
- asyncio.wait_for 只是兜底(cancel 不了里面的线程),真正的超时靠 curl 自身
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from curl_cffi import CurlError
from scrapling.fetchers import Fetcher

from api import config

BLOCKED_HTTP = {401, 403, 407, 429, 451}
CURLE_OPERATION_TIMEDOUT = 28

# 专用抓取线程池 + 同容量进程级闸:拿到闸位即保证有空闲线程,
# wait_for 的计时不含排队等待;也隔离净化侧 to_thread 的默认共享池。
_FETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=config.FETCH_CONCURRENCY_GLOBAL, thread_name_prefix="hollow-fetch"
)
_GLOBAL_GATE = asyncio.Semaphore(config.FETCH_CONCURRENCY_GLOBAL)


@dataclass
class FetchResult:
    url: str
    status: str  # ok | failed | timeout | blocked
    http_status: int | None = None
    body: bytes | None = None
    fetched_at: str | None = None
    error: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _classify(http_status: int) -> tuple[str, str | None]:
    if http_status in BLOCKED_HTTP:
        return "blocked", (
            f"static tier blocked ({http_status}); browser upgrade deferred to v2"
        )
    if http_status >= 400:
        return "failed", f"HTTP {http_status}"
    return "ok", None


def _fetch_sync(url: str, timeout_s: float, impersonate: str | None) -> FetchResult:
    fetched_at = _now_iso()
    try:
        # retries=1:关掉 Scrapling 内置 3 连试(否则线程内最坏 3×timeout,
        # 外层 wait_for 兜不住);失败重试语义留给调用方/二版升级链。
        resp = Fetcher.get(
            url,
            timeout=timeout_s,
            impersonate=impersonate,
            retries=1,
            follow_redirects=config.FOLLOW_REDIRECTS,
        )
    except CurlError as e:
        code = getattr(e, "code", None)
        code_val = getattr(code, "value", code)  # CurlECode 枚举或裸 int
        if code_val == CURLE_OPERATION_TIMEDOUT:
            return FetchResult(url, "timeout", fetched_at=fetched_at,
                               error=f"curl timeout after {timeout_s}s")
        return FetchResult(url, "failed", fetched_at=fetched_at,
                           error=f"CurlError({code_val}): {e}")
    except Exception as e:  # 单 URL 任何异常都只影响自己这条占位
        return FetchResult(url, "failed", fetched_at=fetched_at,
                           error=f"{type(e).__name__}: {e}")

    status_label, error = _classify(resp.status)
    body = resp.body if status_label == "ok" else None
    return FetchResult(url, status_label, http_status=resp.status, body=body,
                       fetched_at=fetched_at, error=error)


async def fetch_one(
    url: str,
    *,
    semaphore: asyncio.Semaphore,
    timeout_s: float,
    impersonate: str | None,
) -> FetchResult:
    async with semaphore, _GLOBAL_GATE:
        loop = asyncio.get_running_loop()
        try:
            # 外层兜底超时 = curl 超时 + 5s 余量;正常情况 curl 先到点
            return await asyncio.wait_for(
                loop.run_in_executor(
                    _FETCH_EXECUTOR, _fetch_sync, url, timeout_s, impersonate
                ),
                timeout=timeout_s + 5,
            )
        except asyncio.TimeoutError:
            return FetchResult(url, "timeout", fetched_at=_now_iso(),
                               error=f"hard timeout after {timeout_s + 5}s (outer wait_for)")


