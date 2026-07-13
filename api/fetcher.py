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
from urllib.parse import urljoin

from curl_cffi import CurlError
from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from api import config, netguard, purifier

BLOCKED_HTTP = {401, 403, 407, 429, 451}
CURLE_OPERATION_TIMEDOUT = 28
# 触发升级链的状态:被拦 / 失败 / 拿不到正文(空壳/SPA 未渲染 → 升 dynamic 渲染)
_ESCALATE_ON = ("blocked", "failed", "no_content")

# 专用抓取线程池 + 同容量进程级闸:拿到闸位即保证有空闲线程,
# wait_for 的计时不含排队等待;也隔离净化侧 to_thread 的默认共享池。
_FETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=config.FETCH_CONCURRENCY_GLOBAL, thread_name_prefix="hollow-fetch"
)
_GLOBAL_GATE = asyncio.Semaphore(config.FETCH_CONCURRENCY_GLOBAL)

# 浏览器档(dynamic/stealthy)独立小池 + 闸:chromium 重量级,与静态档互不挤占
_BROWSER_EXECUTOR = ThreadPoolExecutor(
    max_workers=config.BROWSER_CONCURRENCY, thread_name_prefix="hollow-browser"
)
_BROWSER_GATE = asyncio.Semaphore(config.BROWSER_CONCURRENCY)


@dataclass
class FetchResult:
    url: str
    status: str  # ok | failed | timeout | blocked | no_content
    http_status: int | None = None
    content: str | None = None       # 净化后正文(内容闸门内前移,ok 才有)
    purified: bool | None = None
    word_count: int | None = None
    fetched_at: str | None = None
    error: str | None = None
    tier: str = "static"  # static | dynamic | stealthy(最终使用的档位)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _classify_http(http_status: int) -> tuple[str, str | None]:
    if http_status in BLOCKED_HTTP:
        return "blocked", f"blocked (HTTP {http_status})"
    if http_status >= 400:
        return "failed", f"HTTP {http_status}"
    return "ok", None  # HTTP 层 ok;内容层由 _gate 再判


def _gate(resp, do_purify: bool, tier: str, fetched_at: str) -> FetchResult:
    """HTTP 分类 + 内容闸门(design/05):HTTP ok 后净化,拿不到正文 → no_content。
    净化前移进抓取层,好让升级链依据"有没有正文"而非仅 HTTP 决定是否升级。"""
    http_status, err = _classify_http(resp.status)
    if http_status != "ok":
        return FetchResult(resp.url if hasattr(resp, "url") else "", http_status,
                           http_status=resp.status, fetched_at=fetched_at, error=err, tier=tier)
    url = str(getattr(resp, "url", ""))
    if not do_purify:  # 用户要 raw HTML,不走内容闸门
        status, content, purified, wc = purifier.raw_html(resp.body)
    else:
        status, content, purified, wc = purifier.extract_gated(
            resp.body, url, config.MIN_CONTENT_CHARS)
    error = None if status == "ok" else (
        f"no usable content (HTTP {resp.status}, extracted "
        f"{wc if wc is not None else 0} chars < {config.MIN_CONTENT_CHARS}); "
        f"likely shell/anti-crawl/unrendered SPA")
    return FetchResult(url, status, http_status=resp.status, content=content,
                       purified=purified, word_count=wc, fetched_at=fetched_at,
                       error=error, tier=tier)


def _too_large(resp) -> bool:
    body = getattr(resp, "body", None)
    return bool(body) and len(body) > config.MAX_FETCH_BYTES


def _fetch_sync(url: str, timeout_s: float, impersonate: str | None, do_purify: bool) -> FetchResult:
    """静态档:手动逐跳跟随重定向,每一跳 netguard 校验目的地(SSRF 防护,安全批)。
    curl_cffi 经环境代理连接,follow_redirects=False,由我们自己看 Location 决定下一跳——
    既能过代理、又能对**原始 URL 和每个重定向目标**都做内网拒绝(curl 的 safe 模式做不到)。"""
    fetched_at = _now_iso()
    blocked = netguard.vet_url(url)
    if blocked:
        return FetchResult(url, "blocked", fetched_at=fetched_at, error=f"SSRF guard: {blocked}")
    current = url
    for _hop in range(config.MAX_REDIRECTS + 1):
        try:
            # retries=1:关掉 Scrapling 内置 3 连试(否则线程内最坏 3×timeout,外层 wait_for 兜不住)
            resp = Fetcher.get(current, timeout=timeout_s, impersonate=impersonate,
                               retries=1, follow_redirects=False)
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
        if 300 <= resp.status < 400:  # 重定向:取 Location,校验后进下一跳
            loc = None
            try:
                loc = resp.headers.get("location") or resp.headers.get("Location")
            except Exception:
                pass
            if not loc:
                return FetchResult(url, "failed", http_status=resp.status, fetched_at=fetched_at,
                                   error=f"HTTP {resp.status} redirect without Location")
            nxt = urljoin(current, loc)
            blocked = netguard.vet_url(nxt)
            if blocked:
                return FetchResult(url, "blocked", http_status=resp.status, fetched_at=fetched_at,
                                   error=f"SSRF guard (redirect→{nxt[:80]}): {blocked}")
            current = nxt
            continue
        if _too_large(resp):
            return FetchResult(url, "failed", http_status=resp.status, fetched_at=fetched_at,
                               error=f"response too large ({len(resp.body)} bytes > {config.MAX_FETCH_BYTES})")
        fr = _gate(resp, do_purify, "static", fetched_at)
        fr.url = fr.url or url  # resp.url 缺失时回落传入 url
        return fr
    return FetchResult(url, "failed", fetched_at=fetched_at,
                       error=f"too many redirects (> {config.MAX_REDIRECTS})")


def _fetch_browser_sync(url: str, timeout_s: float, tier: str, do_purify: bool) -> FetchResult:
    """浏览器档(dynamic=playwright chromium / stealthy=patchright 反检测)。
    Scrapling 浏览器 API 的 timeout 单位是毫秒(静态档才是秒)。retries=1 预算可控。
    渲染完的 HTML 走同一内容闸门:SPA 渲出正文 → ok;仍空 → no_content。

    **本版不开 solve_cloudflare**:其 vendor 实现(_stealth.py)是不受 timeout
    约束、不可中断的无上限循环,run_in_executor 又杀不掉线程 → 线程泄漏(审查确认的
    high 缺陷根因)。关掉后,goto 等操作全部受 playwright 的 timeout 硬约束,线程必在
    timeout 内返回。碰到 Cloudflare 挑战墙 → 返回 blocked,如实上报(不静默、不卡死)。
    (主动解 CF 需可中断执行——独立进程 + kill——才能安全启用,记入待办。)"""
    fetched_at = _now_iso()
    blocked = netguard.vet_url(url)  # 初始 URL 校验(浏览器内部自跟随重定向,跳内网属残留向量,见待办)
    if blocked:
        return FetchResult(url, "blocked", fetched_at=fetched_at, tier=tier, error=f"SSRF guard: {blocked}")
    try:
        fetcher_cls = StealthyFetcher if tier == "stealthy" else DynamicFetcher
        resp = fetcher_cls.fetch(
            url, headless=True, timeout=int(timeout_s * 1000), retries=1,
        )
    except Exception as e:
        first_line = str(e).split("\n")[0][:200]  # playwright 错误带多行 Call log,只留首行
        return FetchResult(url, "failed", fetched_at=fetched_at, tier=tier,
                           error=f"{type(e).__name__}: {first_line}")
    if _too_large(resp):
        return FetchResult(url, "failed", http_status=resp.status, fetched_at=fetched_at, tier=tier,
                           error=f"response too large ({len(resp.body)} bytes > {config.MAX_FETCH_BYTES})")
    # 审查 #1:Chromium 内部自跟随重定向,初始 vet 挡不住跳内网(如 302→169.254.169.254)。
    # 抓完复校**最终 URL + 每一跳重定向历史**(Scrapling 的 resp.url/resp.history 均暴露),
    # 命中内网即丢正文、返回 blocked——闭合"读到云元数据/内网正文"的外泄路径。
    # (残留:页面内 XHR/meta-refresh 到内网、盲 SSRF 请求发出本身,需 page.route 抢先拦截,见待办。)
    landed = [str(getattr(resp, "url", "") or url)]
    for _h in (getattr(resp, "history", None) or []):
        _hu = str(getattr(_h, "url", "") or "")
        if _hu:
            landed.append(_hu)
    for _lu in landed:
        bad = netguard.vet_url(_lu)
        if bad:
            return FetchResult(url, "blocked", http_status=getattr(resp, "status", None),
                               fetched_at=fetched_at, tier=tier,
                               error=f"SSRF guard (browser redirected to internal {_lu[:80]}): {bad}")
    fr = _gate(resp, do_purify, tier, fetched_at)
    fr.url = fr.url or url
    return fr


def _attempt_summary(fr: FetchResult) -> str:
    return f"{fr.tier}: {fr.error or f'HTTP {fr.http_status}'}"


async def _run_browser_tier(
    url: str, tier: str, browser_semaphore: asyncio.Semaphore, do_purify: bool
) -> FetchResult:
    """跑一档浏览器抓取。两道闸:
    - browser_semaphore(每请求):限单请求同时占用的浏览器升级数,防跨请求垄断(审查 medium)
    - _BROWSER_GATE(进程级):**绑定线程真实生命周期**——acquire 后只有 _fetch_browser_sync
      线程真正结束(done_callback)才 release,故"持闸==持线程槽"。排队发生在 acquire 处、
      不计入下方超时窗口,等价于静态档的"闸位与线程一一对应"不变量(审查确认的 high 缺陷修复)。
    下方 wait_for 只是逃生舱:线程已有 playwright 硬超时兜底,shield 保证逃生不取消线程
    (线程仍需跑完才归还槽,gate 不会被提前释放而脱钩)。"""
    timeout_s = config.BROWSER_TIMEOUT
    loop = asyncio.get_running_loop()
    async with browser_semaphore:
        await _BROWSER_GATE.acquire()
        fut = loop.run_in_executor(_BROWSER_EXECUTOR, _fetch_browser_sync,
                                   url, timeout_s, tier, do_purify)
        fut.add_done_callback(lambda _f: _BROWSER_GATE.release())
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout_s + 15)
        except asyncio.TimeoutError:
            return FetchResult(url, "timeout", fetched_at=_now_iso(), tier=tier,
                               error=f"browser escape-hatch timeout after {timeout_s + 15}s")


def shutdown_executors() -> None:
    """进程关闭时取消排队中的抓取,不 join 在跑的线程(uvicorn lifespan 收尾调用)。
    在跑的浏览器线程因已关 solve_cloudflare 而有界(≤timeout),不会无限阻塞退出。"""
    _FETCH_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _BROWSER_EXECUTOR.shutdown(wait=False, cancel_futures=True)


async def fetch_one(
    url: str,
    *,
    semaphore: asyncio.Semaphore,
    timeout_s: float,
    impersonate: str | None,
    escalate: bool = True,
    purify: bool = True,
    browser_semaphore: asyncio.Semaphore | None = None,
) -> FetchResult:
    """三档升级链 + 内容闸门:static → (blocked|failed|no_content 时) dynamic →
    (同上) stealthy。2026-07-07 拍板:blocked+failed 触发升级;新增 no_content(HTTP ok
    但没净化出正文,如 SPA 空壳)也触发——升 dynamic 用 chromium 渲染。timeout 不升级。
    任一档拿到正文(ok)即返回;走完仍无正文时 error 保留完整升级历史(可溯源)。
    browser_semaphore 为空时新建一个(独立调用/测试用);编排器会传共享的每请求闸。"""
    if browser_semaphore is None:
        browser_semaphore = asyncio.Semaphore(config.REQUEST_BROWSER_CONCURRENCY)
    async with semaphore, _GLOBAL_GATE:
        loop = asyncio.get_running_loop()
        try:
            # 外层兜底超时 = curl 超时 + 5s 余量;正常情况 curl 先到点
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    _FETCH_EXECUTOR, _fetch_sync, url, timeout_s, impersonate, purify
                ),
                timeout=timeout_s + 5,
            )
        except asyncio.TimeoutError:
            result = FetchResult(url, "timeout", fetched_at=_now_iso(),
                                 error=f"hard timeout after {timeout_s + 5}s (outer wait_for)")

    if not escalate or result.status not in _ESCALATE_ON:
        return result

    history = [_attempt_summary(result)]
    for tier in ("dynamic", "stealthy"):
        result = await _run_browser_tier(url, tier, browser_semaphore, purify)
        if result.status == "ok":
            return result
        history.append(_attempt_summary(result))
        if result.status == "timeout":  # 超时不再往上升
            break
    result.error = "escalation exhausted: " + " -> ".join(history)
    return result


