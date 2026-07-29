# -*- coding: utf-8 -*-
"""Scrapling 静态档抓取:信号量限并发 + 硬超时 + 失败占位(docs/design/03 §6)。

- 静态档 timeout 单位是「秒」(浏览器档才是毫秒,别搞混 —— session-01 实测)
- curl_cffi CurlError code 28 → timeout;其余传输错误 → failed
- HTTP 401/403/407/429/451 → blocked(静态档被拦,二版走浏览器升级链)
- asyncio.wait_for 只是兜底(cancel 不了里面的线程),真正的超时靠 curl 自身
"""
import asyncio
import json
import os
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from curl_cffi import CurlError
from curl_cffi import requests as _cffi
from curl_cffi.const import CurlOpt
from scrapling.fetchers import DynamicFetcher, StealthyFetcher

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
    # 终端判定:升级链到此为止(二进制内容/超大响应——浏览器档必然同果或更糟)。
    # 2026-07-29 PDF 穿甲修复:替换原 "HEAD probe" 字符串匹配的脆弱短路。
    no_escalate: bool = False


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


class _StaticResp:
    """curl_cffi Response 适配到 _gate/_too_large 期望的 .status/.url/.body/.headers。"""
    __slots__ = ("status", "url", "body", "headers")

    def __init__(self, r) -> None:
        self.status = r.status_code
        self.url = str(getattr(r, "url", "") or "")
        self.body = r.content
        self.headers = r.headers


def _static_get(url: str, pin: str | None, timeout_s: float, impersonate: str | None):
    """静态档单跳:**直调 curl_cffi**(绕开 Scrapling —— 它不转发 curl_options,审查确认)。
    pin 存在时用 CURLOPT_RESOLVE 把主机名钉到 netguard 刚校验过的 IP,关掉 vet→连接之间的
    DNS-rebind 窗口(审查 #2);SNI/Host/证书仍按原主机名走(--resolve 语义)。
    follow_redirects(allow_redirects)=False:逐跳交外层 netguard 校验。无内置重试(预算可控)。"""
    session = _cffi.Session(curl_options={CurlOpt.RESOLVE: [pin]}) if pin else _cffi.Session()
    try:
        return session.request(
            "GET", url, impersonate=impersonate, timeout=timeout_s,
            allow_redirects=False, verify=True, stream=False,
        )
    finally:
        session.close()


# ---------- ① fetch LRU 缓存(2026-07-28 延迟治理) ----------
_FETCH_CACHE: "dict[tuple, tuple[float, FetchResult]]" = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: tuple) -> FetchResult | None:
    with _CACHE_LOCK:
        hit = _FETCH_CACHE.get(key)
        if not hit:
            return None
        exp, fr = hit
        if time.monotonic() > exp:
            _FETCH_CACHE.pop(key, None)
            return None
        return fr


def _cache_put(key: tuple, fr: FetchResult) -> None:
    # 只缓存确定性结果;瞬时失败(failed/timeout/blocked)不缓存,避免把抖动固化
    if fr.status not in ("ok", "no_content"):
        return
    with _CACHE_LOCK:
        if len(_FETCH_CACHE) >= config.FETCH_CACHE_MAX:
            for k in list(_FETCH_CACHE)[: config.FETCH_CACHE_MAX // 4]:  # 懒驱逐最旧 1/4
                _FETCH_CACHE.pop(k, None)
        _FETCH_CACHE[key] = (time.monotonic() + config.FETCH_CACHE_TTL, fr)


# ---------- ③ static 空壳域自适应跳过 ----------
_STATIC_FAILS: "dict[str, int]" = {}
_FAILS_LOCK = threading.Lock()


def _static_fail_count(host: str) -> int:
    with _FAILS_LOCK:
        return _STATIC_FAILS.get(host, 0)


def _static_note_result(host: str, status: str) -> None:
    if not host:
        return
    with _FAILS_LOCK:
        if status == "ok":
            _STATIC_FAILS.pop(host, None)  # static 成功即清零
        elif status in ("no_content", "blocked"):
            _STATIC_FAILS[host] = min(_STATIC_FAILS.get(host, 0) + 1, 99)
        # failed/timeout 是网络抖动,不计入"空壳"判定


# ---------- ② 浏览器暖池(CDP 常驻 chromium) ----------
_WARM_PROC: "subprocess.Popen | None" = None
_WARM_WS: str | None = None
_WARM_LOCK = threading.Lock()


def _warm_browser_ws() -> str | None:
    """取/起常驻 chromium 的 CDP websocket。启动失败返回 None(回退冷启动)。
    懒启动 + 健康检查 + 死亡重启;代理/绕行镜像 fetch 的 env 语义(国际经代理、国内直连)。"""
    global _WARM_PROC, _WARM_WS
    if not config.BROWSER_WARM:
        return None
    with _WARM_LOCK:
        if _WARM_WS:
            try:  # 健康检查(100ms,本地回环)
                urllib.request.urlopen(
                    f"http://127.0.0.1:{config.BROWSER_WARM_PORT}/json/version", timeout=0.5)
                return _WARM_WS
            except Exception:
                _warm_kill_locked()  # 死了重启
        shell = os.environ.get(
            "HOLLOW_BROWSER_SHELL",
            os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1228/"
                               "chrome-headless-shell-linux64/chrome-headless-shell"))
        if not os.path.exists(shell):
            return None
        proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        bypass = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
        argv = [shell, f"--remote-debugging-port={config.BROWSER_WARM_PORT}",
                "--no-sandbox", "--disable-gpu"]
        if proxy:
            argv.append(f"--proxy-server={proxy}")
            argv.append(f"--proxy-bypass-list={bypass.replace(',', ';')}")
        try:
            _WARM_PROC = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):  # 最多等 15s
                try:
                    v = json.loads(urllib.request.urlopen(
                        f"http://127.0.0.1:{config.BROWSER_WARM_PORT}/json/version", timeout=1).read())
                    _WARM_WS = v["webSocketDebuggerUrl"]
                    return _WARM_WS
                except Exception:
                    time.sleep(0.3)
        except Exception:
            pass
        _warm_kill_locked()
        return None


def _warm_kill_locked() -> None:
    global _WARM_PROC, _WARM_WS
    _WARM_WS = None
    if _WARM_PROC is not None:
        try:
            _WARM_PROC.terminate()
            _WARM_PROC.wait(timeout=3)
        except Exception:
            try:
                _WARM_PROC.kill()
            except Exception:
                pass
    _WARM_PROC = None


def _host_in_skip_domains(url: str) -> bool:
    """域名(含子域)是否命中升档黑名单(config.ESCALATE_SKIP_DOMAINS)——与 filters 的域过滤同语义。"""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in config.ESCALATE_SKIP_DOMAINS)


# 二进制内容族 Content-Type(净化管线只吃 HTML,这些拿到也必无正文)——2026-07-29
_BINARY_CT_PREFIXES = (
    "application/pdf", "application/zip", "application/x-zip", "application/gzip",
    "application/x-tar", "application/x-7z", "application/vnd.", "application/msword",
    "application/epub", "application/x-mobipocket", "application/x-rar",
    "image/", "audio/", "video/",
)


def _is_binary_ct(ct: str, body: bytes | None) -> bool:
    """响应是否二进制内容(净化必无正文)。octet-stream 单独按 %PDF- 魔数嗅探:
    个别站点把 HTML 错标成 octet-stream(尚有救),真 PDF/压缩包又常发 octet-stream。"""
    ct = (ct or "").split(";", 1)[0].strip().lower()
    if any(ct.startswith(p) for p in _BINARY_CT_PREFIXES):
        return True
    if ct == "application/octet-stream" and body is not None:
        return body[:5] == b"%PDF-" or body[:4] in (b"PK\x03\x04", b"Rar!") or body[:2] == b"\x1f\x8b"
    return False


def _pdf_probe(current: str, pin: str | None, impersonate: str | None) -> bool:
    """二进制前置判定(QA 2026-07-22:慢速主机的 PDF 会把整段 curl 超时烧在下载上,且 timeout
    不触发升档 → 零挽救)。对 .pdf//pdf/ 形态的 URL 先发 5s HEAD 探 Content-Type,确认是
    二进制族就直接判 no_content 快速返回;HEAD 失败/拿不到类型则回退到正常 GET 路径
    (行为不变,由 GET 响应上的二进制闸门兜底)。
    2026-07-29 修复:HEAD 改跟重定向——arxiv 等站 .pdf 先 301 剥后缀(首响 CT=text/html),
    不跟跳永远探不到 application/pdf,导致漏网下载整个二进制。"""
    session = _cffi.Session(curl_options={CurlOpt.RESOLVE: [pin]}) if pin else _cffi.Session()
    try:
        r = session.request("HEAD", current, impersonate=impersonate, timeout=5,
                            allow_redirects=True, max_redirects=3, verify=True)
        # 跟跳后的落点也要过 SSRF 校验(只拿 CT 判型,但请求发出本身要受控)
        landed = str(getattr(r, "url", "") or current)
        if landed != current and netguard.vet_url(landed):
            return False  # 落点可疑:回退正常 GET 路径(那里逐跳 netguard,会如实 blocked)
        ct = (r.headers.get("content-type") or r.headers.get("Content-Type") or "").lower()
        return _is_binary_ct(ct, None)
    except Exception:
        return False
    finally:
        session.close()


def _fetch_sync(url: str, timeout_s: float, impersonate: str | None, do_purify: bool) -> FetchResult:
    """静态档:手动逐跳跟随重定向,每一跳 netguard 校验目的地 + DNS-pin(SSRF 防护,安全批 + 审查 #2)。
    对**原始 URL 和每个重定向目标**都做内网拒绝并钉 IP(curl 的 safe 模式做不到)。"""
    fetched_at = _now_iso()
    current = url
    for _hop in range(config.MAX_REDIRECTS + 1):
        reason, pin = netguard.check_and_resolve(current)
        if reason:
            tag = "" if current == url else f" (redirect→{current[:80]})"
            return FetchResult(url, "blocked", fetched_at=fetched_at,
                               error=f"SSRF guard{tag}: {reason}")
        _path = current.split("?", 1)[0].lower()
        if (_path.endswith(".pdf") or "/pdf/" in _path) and _pdf_probe(current, pin, impersonate):
            return FetchResult(url, "no_content", fetched_at=fetched_at, no_escalate=True,
                               error="binary content (extraction not supported yet); "
                                     "identified via HEAD probe, full download skipped")
        try:
            raw = _static_get(current, pin, timeout_s, impersonate)
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
        resp = _StaticResp(raw)
        if 300 <= resp.status < 400:  # 重定向:取 Location,下一跳由循环顶部 check_and_resolve 校验+钉
            loc = None
            try:
                loc = resp.headers.get("location") or resp.headers.get("Location")
            except Exception:
                pass
            if not loc:
                return FetchResult(url, "failed", http_status=resp.status, fetched_at=fetched_at,
                                   error=f"HTTP {resp.status} redirect without Location")
            current = urljoin(current, loc)
            continue
        if _too_large(resp):
            # 超大响应:浏览器档要重新下载同样会爆——终端判定不升级(2026-07-29)
            return FetchResult(url, "failed", http_status=resp.status, fetched_at=fetched_at,
                               error=f"response too large ({len(resp.body)} bytes > {config.MAX_FETCH_BYTES})",
                               no_escalate=True)
        # 二进制内容闸门(GET 已拿到字节):URL 形态漏网的 PDF/压缩包/图片等在这里兜底,
        # 净化必无正文、浏览器档必"Download is starting"——终端判定不升级(2026-07-29 PDF 穿甲修复)
        _ct = ""
        try:
            _ct = (resp.headers.get("content-type") or resp.headers.get("Content-Type") or "")
        except Exception:
            pass
        if _is_binary_ct(_ct, resp.body):
            return FetchResult(url, "no_content", http_status=resp.status, fetched_at=fetched_at,
                               error=f"binary content ({_ct.split(';')[0].strip() or 'unknown'}), "
                                     "extraction not supported; detected at GET response",
                               no_escalate=True)
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
        warm_ws = _warm_browser_ws() if tier == "dynamic" else None
        if warm_ws:
            # 暖池:CDP 连常驻 chromium,省 2-4s 冷启动(2026-07-28 延迟治理②)
            resp = DynamicFetcher.fetch(
                url, cdp_url=warm_ws, headless=True, timeout=int(timeout_s * 1000), retries=1,
            )
        else:
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
    with _WARM_LOCK:
        _warm_kill_locked()  # 暖池 chromium 一并收尾(2026-07-28 延迟治理②)


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

    # ① LRU 缓存:命中直接返回,不占任何闸(缓存键含影响产出的全部参数)
    cache_key = (url, timeout_s, impersonate, escalate, purify) if config.FETCH_CACHE else None
    if cache_key is not None:
        hit = _cache_get(cache_key)
        if hit is not None:
            return hit

    # ③ 空壳域自适应跳过:同域连续 N 次 static 无正文/被拦,升档开着就直上浏览器
    host = (urlsplit(url).hostname or "").lower()
    skip_static = bool(
        escalate and config.STATIC_ADAPTIVE_SKIP
        and _static_fail_count(host) >= config.STATIC_ADAPTIVE_SKIP
    )
    if skip_static:
        result = FetchResult(url, "no_content", fetched_at=_now_iso(),
                             error=f"static skipped (adaptive: {host} 连续空壳/被拦,直上浏览器)")
    else:
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
        _static_note_result(host, result.status)

    if not escalate or result.status not in _ESCALATE_ON:
        if cache_key is not None:
            _cache_put(cache_key, result)
        return result

    # SSRF 命中的 blocked:浏览器档 vet 同样会拦,升级纯属浪费槽位与时间(QA 2026-07-22:
    # 还会在浏览器闸排队后把 blocked 误报成 timeout)——短路返回,保留原始原因。
    if result.status == "blocked" and (result.error or "").startswith("SSRF guard"):
        if cache_key is not None:
            _cache_put(cache_key, result)
        return result

    # 终端判定(二进制内容/超大响应,no_escalate):升级必然同果或更糟——短路。
    # (替换原 "HEAD probe" 字符串匹配,2026-07-29;SSRF blocked 仍走下方独立分支,语义不同)
    if result.no_escalate:
        if cache_key is not None:
            _cache_put(cache_key, result)
        return result

    # 已知强反爬域(机房 IP 信誉层拦截):浏览器指纹伪装救不回,跳过升级链(config.ESCALATE_SKIP_DOMAINS)。
    if result.status in _ESCALATE_ON and _host_in_skip_domains(url):
        result.error = (result.error or "") + (
            f" [escalation skipped: known hard anti-bot domain, see ESCALATE_SKIP_DOMAINS]")
        return result

    history = [_attempt_summary(result)]
    # 对端 5xx:服务器侧错误,反检测指纹(stealthy)救不了,最多升一档 dynamic 兜底
    # (QA 2026-07-22:三档逐级重试 5xx 会把整单预算烧光)。
    tiers = ("dynamic",) if (result.http_status and 500 <= result.http_status < 600) \
        else ("dynamic", "stealthy")
    for tier in tiers:
        result = await _run_browser_tier(url, tier, browser_semaphore, purify)
        if result.status == "ok":
            if cache_key is not None:
                _cache_put(cache_key, result)
            return result
        history.append(_attempt_summary(result))
        if result.status == "timeout":  # 超时不再往上升
            break
    result.error = "escalation exhausted: " + " -> ".join(history)
    if cache_key is not None:
        _cache_put(cache_key, result)
    return result


