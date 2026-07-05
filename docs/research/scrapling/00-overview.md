# 00 · Scrapling 综合总览(P2 抓取网关落地设计)

> 版本:Scrapling **v0.4.10**(`vendor/scrapling/`,未修改快照)。
> 本文综合 9 份子系统研读笔记(01–09),为 P2 `POST /v0/fetch` 三档升级链给出**可落地**的架构、判定逻辑、封装骨架、部署清单与风险台账。
> 结论一律**以源码为准**;笔记之间的矛盾已回源码裁决,裁决结果记录在 [§7 笔记勘误与裁决](#7-笔记勘误与裁决)。
> 交叉引用格式:`[NN]` 指第 NN 号笔记;`file:line` 相对 `vendor/scrapling/`。

---

## 目录

1. [整体架构图](#1-整体架构图)
2. [三档升级链落地设计](#2-三档升级链落地设计)
3. [`/v0/fetch` 参数 ↔ Scrapling 参数映射 + Response ↔ 返回字段](#3-v0fetch-参数--scrapling-参数映射)
4. [浏览器池 + 信号量 + 超时回收封装骨架](#4-浏览器池--信号量--超时回收封装骨架)
5. [依赖 / 部署清单](#5-依赖--部署清单)
6. [风险坑点清单 + 待实测问题汇总](#6-风险坑点清单--待实测问题汇总)
7. [笔记勘误与裁决](#7-笔记勘误与裁决)

---

## 1. 整体架构图

Scrapling 分四层。**三档 Fetcher 彼此零耦合,没有任何内置自动升级**——升级链完全由我们的网关编排([01],[06])。

```
                        ┌──────────────────────── 我们的 FastAPI 网关 (P2) ────────────────────────┐
                        │  POST /v0/fetch  →  auto 三档升级链编排 + Semaphore + wait_for + 回收      │
                        └───────────────┬───────────────────┬───────────────────┬──────────────────┘
                                        │ 第1档              │ 第2档              │ 第3档
                                        ▼                    ▼                    ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ ① 门面层  scrapling/fetchers/                                                                    │
   │   Fetcher / AsyncFetcher        DynamicFetcher (别名 PlayWrightFetcher)   StealthyFetcher        │
   │     .get/.post/.put/.delete       .fetch / .async_fetch                    .fetch / .async_fetch │
   │   ── 全部是 @classmethod,免实例化;classmethod 内部「每次新建+销毁」Session(见告警) ──          │
   │   ── 复用要直接持有 Session 类:FetcherSession / (Async)DynamicSession / (Async)StealthySession ─│
   └───────────────┬───────────────────────┬───────────────────────────────┬─────────────────────────┘
                   ▼                        ▼                               ▼
   ┌─────────────────────────┐  ┌──────────────────────────┐  ┌────────────────────────────────────┐
   │ ② 引擎层 engines/static  │  │ engines/_browsers (动态) │  │ engines/_browsers (隐身)             │
   │   底层 = curl_cffi       │  │   底层 = playwright==1.61.0│ │   底层 = patchright==1.61.1          │
   │   TLS/JA3 靠 impersonate │  │   原版 Playwright+Chromium│  │   反检测补丁 Playwright+Chromium     │
   │   （'chrome' 默认）      │  │   launch_persistent_ctx  │  │   +STEALTH_ARGS 60+ flag             │
   │   一次性 session/请求    │  │   PagePool(同ctx多tab)   │  │   +_cloudflare_solver(solve_cf)      │
   │   HTTP4xx/5xx 不抛异常   │  │   DEFAULT_ARGS(无dev-shm)│  │   +block_webrtc/hide_canvas/webgl    │
   │   传输层错→CurlError     │  │   ★无CF检测              │  │   STEALTH_ARGS 带 --disable-dev-shm  │
   └────────────┬────────────┘  └─────────────┬────────────┘  └──────────────────┬───────────────────┘
                │                              │                                   │
                ▼                              ▼                                   ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ ③ 工具带 engines/toolbelt/                                                                        │
   │   convertor.ResponseFactory —— 把 curl_cffi / Playwright(sync/async)三种响应「抹平」成同一 Response│
   │       · 浏览器档 HTML 取「渲染后 page.content()」(content-type含html时);否则 final_response.body()│
   │   fingerprints(生成HTTP头/UA,不校验) · proxy_rotation(ProxyRotator,三引擎都消费)               │
   │   ad_domains(3526条,block_ads,仅浏览器) · navigation(资源/域名拦截 + construct_proxy_dict)     │
   │   custom.StatusText(状态码→短语)                                                                 │
   └───────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                    ▼
   ┌───────────────────────────────────────────────────────────────────────────────────────────────┐
   │ ④ 统一返回  engines/toolbelt/custom.Response  ——  class Response(Selector)  (custom.py:28)        │
   │   既是 HTTP 响应(.status/.headers/.cookies/.reason/.history/.meta)                              │
   │   又是已解析的 lxml 选择器树(.css()/.xpath()/.get_all_text());raw HTML = .body(bytes)          │
   │   解析层 parser.py 纯 CPU、无 IO、不占浏览器池/信号量预算                                          │
   └───────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                    ▼
                                        raw HTML(.body bytes) ── 交给 P3 Purifier(trafilatura)
```

底层依赖速查(源码裁定,纠正了「隐身档=camoufox/firefox」的直觉,[05]):

| 档位 | 门面类 | 底层库 | 浏览器 | timeout 单位 | 反 Cloudflare |
|---|---|---|---|---|---|
| 静态 | `Fetcher`/`AsyncFetcher` | **curl_cffi** | 无 | **秒**(默认 30) | 无(只返回 403/503 挑战页) |
| 动态 | `DynamicFetcher` | **playwright 1.61.0** | Chromium | **毫秒**(默认 30000) | 无 |
| 隐身 | `StealthyFetcher` | **patchright 1.61.1** | Chromium | **毫秒**(默认 30000) | `solve_cloudflare`(默认 False) |

> `pyproject.toml:76-77` 同时精确 pin `playwright==1.61.0` 与 `patchright==1.61.1`([09])。Camoufox 只是 0.3.13 前的遗留,`humanize/geoip/os/firefox_user_prefs` 参数在本版本**已不存在**,传了会被 msgspec 拒绝([05])。

---

## 2. 三档升级链落地设计

### 2.1 状态机总览

```
                     ┌─────────────────────────────────────────────────────────────┐
   URL ──▶ 第1档 静态 Fetcher(curl_cffi)                                            │
          │  · 2xx + content-type含html + 可见正文够长 ──────────────────▶ ok(static)│
          │  · CurlError(传输层)/wait_for 超时 ──────────▶ timeout / failed(static)  │
          │  · status∈{403,429,503} 或 CF 头/标记 ──────┐(blocked,直上第3档)         │
          │  · 2xx 但正文过薄/疑似 SPA ─────────────┐    │                            │
          └────────────────────────────────────────┼────┼────────────────────────────┘
                                                    ▼    │
   第2档 动态 DynamicFetcher(playwright 渲染)       │    │
          │  · 渲染后 2xx + 可见正文够长 ────────────┼────┼──────────────────▶ ok(dynamic)
          │  · 仍正文过薄 / 拿到半成品 ──────────────┘    │(升第3档)                  │
          │  · 判定 blocked(status/CF 特征)─────────────┤(升第3档)                  │
          │  · wait_for 超时 / RuntimeError ─────────────┼──────────▶ timeout/failed(dynamic)
          └──────────────────────────────────────────────┼────────────────────────────┘
                                                          ▼
   第3档 隐身 StealthyFetcher(patchright + solve_cloudflare=True)                     │
          │  · 解盾成功、2xx、无 CF 标记 ────────────────────────────────▶ ok(stealthy)│
          │  · 仍命中 CF 标记 / 403·503 ────────────────────────────────▶ blocked(stealthy)
          │  · wait_for 超时 / 异常 ────────────────────────────────────▶ timeout/failed(stealthy)
          └─────────────────────────────────────────────────────────────────────────┘
```

关键编排原则:

1. **静态命中 blocked → 直接跳到第3档隐身**,不经过第2档([02],[03])。因为静态的 403/CF 头意味着有 WAF,DynamicFetcher(原版 playwright,带可检测 CDP 特征)大概率同样被挡,浪费一次浏览器启动。
2. **静态命中「正文过薄」→ 升第2档动态**(疑似 SPA/JS 渲染)。
3. **每一档返回后都要重新判定**——`wait_selector`/`network_idle` 超时在 Scrapling 内部是**静默吞掉**的([04] `_base.py:322-327`、`_controllers.py:181`),不会抛错,所以拿到半成品页面只能靠我们自测 HTML 长度识别。
4. **失败原样占位**:任一档抛异常或判 blocked,仍产出带 `fetch_status`/`engine_used`/`fetched_at` 的记录,`content` 可为空,禁止静默丢弃(硬约束)。

### 2.2 三个判据的具体信号

#### (a) 静态「成功」判据

```python
def static_ok(resp) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return (200 <= resp.status < 300
            and "html" in ctype
            and not text_too_thin(resp))
```

#### (b)「正文过薄 / 疑似 SPA」判据 —— 用 `get_all_text`,不要用 `len(body)`

源码裁定([08],[02]):`Response` 是 `Selector` 子类,`get_all_text(strip=True)` 默认已忽略 `<script>/<style>` 并跳过纯空白节点(`parser.py:279-329`)。SPA 空壳(`<div id="app"></div>` + 一堆内联 JS)的 `body` 字节可能几十 KB,但**可见文本接近 0**,故必须量文本、不能量字节。

```python
MIN_TEXT_CHARS = 200          # 起点值,必须用真实样本校准(见 §6 待实测)

def visible_text_len(resp) -> int:
    return len(resp.get_all_text(strip=True))   # 默认 ignore_tags=('script','style')

def text_too_thin(resp) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype:          # 非 HTML(PDF/JSON 等)不判薄,直接交下游
        return False
    return visible_text_len(resp) < MIN_TEXT_CHARS
```

> `get_all_text` 会把 nav/footer/cookie 横幅也算进长度,可能漏判「正文短但导航长」的页面([08] 待实测 2);CJK 无空格,阈值需按字符数(而非 `split()` 词数)定,并可能要区分语言。

#### (c)「Cloudflare / 反爬拦截」判据

静态档**没有** CF 检测能力,只会返回 403/503 挑战页([02],[03])。判定信号(任一命中即 blocked):

```python
CF_HEADERS = ("cf-ray", "cf-mitigated")
CF_MARKERS = ("Just a moment...", "Verifying you are human", "Checking your browser",
              "cf-browser-verification", "challenge-platform")
BLOCK_STATUSES = {403, 429, 503}

def looks_blocked(resp) -> bool:
    if resp.status in BLOCK_STATUSES:
        return True
    hdr = {k.lower(): (v or "").lower() for k, v in (resp.headers or {}).items()}
    if "cloudflare" in hdr.get("server", "") or any(h in hdr for h in CF_HEADERS):
        return True
    head = resp.body[:8192].decode(resp.encoding or "utf-8", "replace")
    return any(m in head for m in CF_MARKERS)
```

- 浏览器档还可复用 Scrapling 内部的挑战类型探测:`StealthySessionMixin._detect_cloudflare(html)`(`_base.py:544-577`),它靠 `cType:'non-interactive'|'managed'|'interactive'` 字符串或内嵌 turnstile 脚本判定,返回类型或 `None`。**注意它不看 `<title>`**([02] 勘误);`Just a moment...` 这类 title 文本是 solver 轮询用的([05])。建议在网关侧**复制其判定逻辑**而非直接 import 内部 API([06])。
- 第3档必须显式 `solve_cloudflare=True`(默认 False,只检测不求解,[01][05])。solver 全部标 `# pragma: no cover`,内部超时都「continue」而非抛错,**可能返回一个仍是挑战页的「成功」响应** → 因此隐身档返回后仍要再跑一次 `looks_blocked`([05] Q3)。

#### (d) 异常 → `fetch_status` 映射

| 触发 | fetch_status | 说明 / 出处 |
|---|---|---|
| 各档正常返回 + 判据通过 | `ok` | — |
| `asyncio.TimeoutError`(外层 `wait_for`) | `timeout` | 我们的硬超时,唯一可靠的超时信号 |
| `CurlError` 且 `code==28`(OPERATION_TIMEDOUT) | `timeout` | 静态传输层超时([03];code 值待实测) |
| 其他 `CurlError`(连接/DNS/TLS/代理) | `failed` | 静态传输层错([03] `static.py:260`) |
| Playwright `TimeoutError`(重试耗尽) | `timeout` | 浏览器档([04]) |
| `RuntimeError("Failed to get response…")` | `failed` | goto 无响应([04][05]) |
| 末档仍 `looks_blocked` | `blocked` | 不再升级 |
| 其他异常 | `failed` | 原样占位 |

> `is_proxy_error`([07])**不能**单独用于 timeout 判定——它会把 `connection timed out` 归成代理错误。区分 timeout/failed 以我们自己的 `asyncio.wait_for` 信号为准。

### 2.3 编排伪代码

```python
async def fetch_auto(url: str) -> Result:
    at = now_iso()

    # ── 第1档 静态 ──
    try:
        resp = await static_engine.get(url)          # AsyncFetcher.get + wait_for + Semaphore
    except asyncio.TimeoutError:
        resp = None; static_status = "timeout"
    except CurlError as e:
        resp = None; static_status = "timeout" if getattr(e, "code", None) == 28 else "failed"
    else:
        if static_ok(resp):
            return ok(url, resp.body, "static", resp.status, at)
        if looks_blocked(resp):
            return await stealth_step(url, at)        # 跳过第2档,直上隐身
        # 2xx 但正文过薄 → 落到第2档

    # ── 第2档 动态 ──(静态过薄或静态传输失败都试一次浏览器渲染)
    try:
        d = await dynamic_pool.fetch(url)             # 长驻 AsyncDynamicSession + wait_for
        if not looks_blocked(d) and not text_too_thin(d):
            return ok(url, d.body, "dynamic", d.status, at)
        if looks_blocked(d):
            return await stealth_step(url, at)
        # 渲染后仍过薄 → 升隐身
    except asyncio.TimeoutError:
        return timeout(url, "dynamic", at)            # 会话已被回收(见 §4)
    except Exception:
        pass                                          # 落到隐身兜底

    return await stealth_step(url, at)

async def stealth_step(url, at) -> Result:
    try:
        s = await stealth_pool.fetch(url, solve_cloudflare=True)   # 会话级 timeout≥60000!
    except asyncio.TimeoutError:
        return timeout(url, "stealthy", at)
    except Exception as e:
        return failed(url, "stealthy", str(e), at)
    if looks_blocked(s):
        return blocked(url, s.body, "stealthy", s.status, at)      # 原样占位,不丢弃
    return ok(url, s.body, "stealthy", s.status, at)
```

---

## 3. `/v0/fetch` 参数 ↔ Scrapling 参数映射

### 3.1 请求参数映射表

| `/v0/fetch` 入参 | 语义 | 映射到 Scrapling |
|---|---|---|
| `urls: list[str]`(≤10) | 目标列表 | 每个 URL 独立走 `fetch_auto`,`asyncio.gather` 收集,失败占位 |
| `mode` | `auto`/`static`/`dynamic`/`stealthy` | `auto`=三档链;其余=只跑指定档(仍包 wait_for+回收) |
| `timeout`(秒,单值) | 每 URL 墙钟预算 | **静态**:`AsyncFetcher.get(timeout=秒)`(curl 单位秒);**浏览器**:`fetch(timeout=秒*1000)`(单位毫秒)。**外层 `asyncio.wait_for` 恒用秒**。⚠️ 单位换算是最易踩坑处([01] timeout 陷阱) |
| `format` | `html`/`text`/…(下游 P3 决定) | 本期只取 raw HTML;`format` 不下发给 Scrapling,交 P3 处理。取 HTML 一律用 `resp.body`(bytes) |
| (隐含)`solve_cloudflare` | 仅第3档 | 遇 CF 才置 `True`;**注意会话级 timeout 必须 ≥60000ms**(见 §7 裁决 C) |

固定注入的 Scrapling 参数(所有浏览器档):`headless=True`、`disable_resources=True`、`block_ads=True`、`retries=1`、`extra_flags=["--disable-dev-shm-usage","--disable-gpu"]`(仅 DynamicFetcher 必须补,见 §5)、`max_pages=1`(每会话单 tab,靠信号量控总并发)。

### 3.2 Response 属性 ↔ 返回字段取值方案

| 返回字段 | 取值 | 出处 / 说明 |
|---|---|---|
| `content`(raw HTML,交 P3) | **`resp.body`**(bytes) | [02][08] 裁定。静态=原始响应体;浏览器=渲染后 `page.content()`,已由 ResponseFactory 抹平到同一属性。**不要用 `html_content`**(lxml 重序列化、丢注释/合并空白),更**不要**用 `resp.text`(几乎恒空) |
| 需要 str 时 | `resp.body.decode(resp.encoding or "utf-8", "replace")` | `encoding` 非 UTF-8 站点可靠性待实测([02][08]) |
| `word_count` | `len(resp.get_all_text(strip=True))`(字符数)或 `len(text.split())`(词数,CJK 不适用) | 与「正文过薄」判据同源,顺手复用;CJK 建议按字符数 |
| `engine_used` | `"static"`/`"dynamic"`/`"stealthy"` | 由编排层每步写入 |
| `fetch_status` | `ok/failed/timeout/blocked` | §2.2(d) 映射 |
| `http_status` | `resp.status`(int) | 403/404/503 都是正常属性、非异常([02][03]) |
| `reason` | `resp.reason or StatusText.get(resp.status)` | `StatusText` 兜底短语,`custom.py:303`([07]) |
| `fetched_at` | 网关 `datetime.now(timezone.utc).isoformat()` | 每次调用 try 前取 |
| `final_url` / `history` | `resp.url` / `resp.history` | 重定向链;history 中间跳 body 被置空([02]) |

---

## 4. 浏览器池 + 信号量 + 超时回收封装骨架

综合 [06] 的结论:Scrapling **没有浏览器实例池、没有硬超时、没有 `__del__`/atexit、没有进程 kill**。唯一的池是 `PagePool`(同一浏览器 context 内的 tab)。所以三样东西必须我们自己补:**① `asyncio.Semaphore` 限并发 → ② `asyncio.wait_for` 硬超时 → ③ 超时/异常后 `close()` 并重建 Session**(唯一能真正回收 Chromium 进程的路径)。

设计要点:

- **长驻会话复用,绝不在热路径循环调 classmethod**——classmethod `.fetch()` 每 URL 新建+销毁整个浏览器([01][04][05][06])。进程启动时 `await session.start()` 一次,退出 `await session.close()`。
- **每会话 `max_pages=1` + 多会话 + 外层信号量**,优于「单会话多 tab」——一个 tab 卡死可整会话回收,隔离性更好([06])。总浏览器数(Dynamic+Stealthy 合计)≤ 2~3。
- **`max_pages` 只有 async 生效**,sync 恒为 1([04][05]);且池满是「阻塞轮询 60s 再抛 TimeoutError」([04] `_base.py:288-300`),不利于精确控并发 → 用信号量把排队放到可控处。
- **per-call `fetch()` 支持覆盖参数**(源码已证:`AsyncDynamicSession.fetch(url, **PlaywrightFetchParams)` / `AsyncStealthySession.fetch(url, **StealthFetchParams)`,内部 `_validate` 把 per-call kwargs 合并到会话 config,`_controllers.py:313`/`_stealth.py:482`)。**但** per-call 只传 `solve_cloudflare=True` 而不传 `timeout` 时,timeout **不会**被抬到 60000ms(见 §7 裁决 C)→ 隐身会话必须在**会话级**就设 `timeout≥60000`。

```python
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from scrapling.fetchers import AsyncDynamicSession, AsyncStealthySession

CLOSE_TIMEOUT = 10.0
BROWSER_LIMIT = 2            # 浏览器并发上限(VPS 内存决定,2~3)

class BrowserSessionPool:
    """固定大小的浏览器会话池:每个 Session 长活复用;出错的 Session 被 close 并替换,
    保证 Chromium 进程回收、不泄漏。参考 [06] §4.2。"""

    def __init__(self, session_factory, size: int):
        self._factory = session_factory        # 无参 callable → 未 start 的 AsyncXxxSession
        self._size = size
        self._pool: asyncio.Queue = asyncio.Queue()
        self._sem = asyncio.Semaphore(size)    # 双保险:限总并发

    async def start(self):
        for _ in range(self._size):
            s = self._factory(); await s.start(); await self._pool.put(s)

    async def close(self):
        while not self._pool.empty():
            await self._safe_close(await self._pool.get())

    @staticmethod
    async def _safe_close(session):
        try:
            await asyncio.wait_for(session.close(), timeout=CLOSE_TIMEOUT)
        except Exception:
            pass                               # close 也卡死:放弃对象,靠容器内存上限兜底

    async def _fresh(self):
        s = self._factory(); await s.start(); return s

    @asynccontextmanager
    async def acquire(self):
        async with self._sem:
            session = await self._pool.get()
            broken = False
            try:
                yield session
            except Exception:
                broken = True; raise
            finally:
                if broken:                     # 含超时:杀掉半死浏览器并补新的
                    await self._safe_close(session)
                    try:    session = await self._fresh()
                    except Exception: session = None
                if session is not None:
                    await self._pool.put(session)

    async def fetch(self, url: str, hard_timeout: float, **fetch_kwargs):
        async with self.acquire() as session:
            # wait_for 超时 → TimeoutError → acquire 判 broken → close+重建(回收进程)
            return await asyncio.wait_for(
                session.fetch(url, **fetch_kwargs), timeout=hard_timeout)

# ── 组装 ──
dynamic_pool = BrowserSessionPool(
    lambda: AsyncDynamicSession(
        max_pages=1, headless=True, disable_resources=True, block_ads=True,
        retries=1, timeout=20_000,
        extra_flags=["--disable-dev-shm-usage", "--disable-gpu"],   # DynamicFetcher 默认不带!(§5)
    ),
    size=BROWSER_LIMIT,
)
stealth_pool = BrowserSessionPool(
    lambda: AsyncStealthySession(
        max_pages=1, headless=True, disable_resources=True, block_ads=True,
        allow_webgl=True, block_webrtc=True, retries=1,
        timeout=60_000,          # ★ 会话级必须 ≥60000:per-call solve_cloudflare 不会自动抬(§7-C)
        # STEALTH_ARGS 已含 --disable-dev-shm-usage,无需再补
    ),
    size=BROWSER_LIMIT,
)

# 静态档不吃浏览器内存,单独一条更大的信号量即可
static_sem = asyncio.Semaphore(10)
```

回收保证链:`asyncio.wait_for` 超时 cancel `fetch` 协程 → `acquire` 的 `finally` 判 `broken` → `_safe_close` 真正 `browser.close()`(`_base.py:247`)→ 补一个新会话回池。`close()` 幂等([06])。**若 `close()` 也 hang**,只能放弃对象,靠容器级 cgroup 内存上限 + 按名 kill chromium 孤儿进程兜底(Scrapling/Playwright 不暴露 pid,[06] 待实测 2)。

> ⚠️ **禁用同步 API**:`Fetcher.get`(同步 curl)与 `DynamicFetcher.fetch`(内部 `sync_playwright()`)会阻塞/破坏事件循环([06] Q4)。全链路走 `AsyncFetcher.get` / `AsyncDynamicSession` / `AsyncStealthySession`。不得已才 `asyncio.to_thread`,但它无法真正 cancel、硬超时语义更弱。

---

## 5. 依赖 / 部署清单

来自 [09],要点已回源码核对。

### 5.1 pip 依赖(写死版本)

```
scrapling[fetchers]==0.4.10
# [fetchers] 传递依赖已含 curl_cffi、playwright==1.61.0、patchright==1.61.1、
# browserforge、msgspec、anyio 等 —— 不要自己再单独 pin playwright/patchright
# 不需要 [ai]/[shell](那是 MCP server / IPython shell,P2 用 Python API)
```

- 裸 `pip install scrapling` 只装解析器,`import scrapling.fetchers` 会 `ModuleNotFoundError`。`[fetchers]` 是唯一必须的 extra。
- `playwright==1.61.0` / `patchright==1.61.1` 是**精确等号 pin**:浏览器二进制版本与库版本强绑定。升级 Scrapling 必须重跑浏览器安装。

### 5.2 浏览器二进制(构建期一次装好)

小 VPS 无 GUI,headless 默认开,不需要 xvfb。**必须 Debian 系**(`playwright install-deps` 走 apt、要 root,不支持 Alpine/musl):

```dockerfile
FROM python:3.12-slim-trixie              # Debian;禁用 Alpine
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright   # 固定路径,防多阶段构建/只读根丢二进制
RUN pip install --no-cache-dir -r requirements.txt
# 等价 `scrapling install`,但拆开写、不落哨兵文件、确定性更强:
RUN python -m playwright install-deps chromium && python -m playwright install chromium
# 可选:update_tld_names(Fetcher 用 tld 判域名)
```

- **只装 chromium**(不装 firefox/webkit),与 Scrapling 内部一致。
- **别依赖 `scrapling install` 的哨兵文件** `.scrapling_dependencies_installed`(写在 site-packages,只读根/换层可能误判已装)。构建期直接 `playwright install` 规避。
- **patchright 是否需要单独 `patchright install chromium`** 未定([09] 待实测 4):`scrapling install` 只跑了 `playwright install chromium`。**必须实测隐身档能否直接启动**;若不行,补 `python -m patchright install chromium`。

---

## 6. 风险坑点清单 + 待实测问题汇总

### 6.1 风险坑点(按严重度)

| 严重度 | 坑 | 后果 | 缓解 | 出处 |
|---|---|---|---|---|
| 🔴 致命 | **无硬超时 / 无进程 kill / 无 `__del__`** | 卡死 URL 拖挂协程,半死 Chromium 漏内存,VPS OOM | 外层 `wait_for` + 超时后 `close()` 重建会话 + 容器 cgroup 内存上限兜底 | [06] |
| 🔴 致命 | **DynamicFetcher 默认 `DEFAULT_ARGS` 不含 `--disable-dev-shm-usage`**(只有 StealthyFetcher 的 `STEALTH_ARGS` 带) | 64MB `/dev/shm` 的 VPS 上 Chromium renderer 崩溃 | DynamicFetcher **必须**手动 `extra_flags=["--disable-dev-shm-usage","--disable-gpu"]` | [04] `constants.py:24-37/62` |
| 🔴 致命 | **同步 API 进事件循环**(`Fetcher.get`/`DynamicFetcher.fetch` 内部 `sync_playwright()`) | 在 running loop 里报错/阻塞整个网关 | 全链路 async;不得已才 `asyncio.to_thread` | [06] Q4 |
| 🟠 高 | **classmethod `.fetch()` 每 URL 新建+销毁整个浏览器** | 每 URL 付数百 ms~秒级冷启动 + 内存峰值,10 个 URL 打爆 | 持有长驻 `Async*Session` 复用 | [01][04][05][06] |
| 🟠 高 | **`solve_cloudflare=True` 把 timeout 抬到 60000ms + 默认 `retries=3`** | 单 URL 最坏 ≈3×(60s+1s)≈180s+ | `retries=1` + 会话级 timeout=60000 + 放宽该 URL 硬超时到 90s+ | [05][06] |
| 🟠 高 | **per-call solve_cloudflare 不触发 timeout 自动抬升**(裁决 C) | 会话 timeout<60000 时解盾被过早 wait_for 掐断 | 会话级就设 `timeout≥60000` | §7-C(源码验证) |
| 🟠 高 | **`max_pages` 只有 async 生效**,sync 恒为 1;池满阻塞轮询 60s 才抛错 | sync 误以为能并发;并发排队不可控 | 只用 async;外层信号量控并发,`max_pages` 仅兜底 | [04][05][06] |
| 🟡 中 | **`wait_selector`/`network_idle`/`page_action` 超时被静默吞掉** | 拿到半成品页面却「成功」返回 | 每档返回后自测正文长度,不信任「无异常=完整」 | [04][05] |
| 🟡 中 | **静态档无 CF 检测**,4xx/5xx 不抛异常 | 只 try/except 会漏掉 blocked | 显式读 `resp.status` + 头 + body 标记 | [02][03] |
| 🟡 中 | **`resp.html_content` ≠ 原样 HTML**(lxml 重序列化,丢注释/合并空白);`resp.text` 几乎恒空 | 喂 trafilatura 保真度下降 / 误判正文为空 | 交 P3 一律用 `resp.body`;判薄用 `get_all_text()` | [02][08] |
| 🟡 中 | **`disable_resources=True` 可能让个别站永远加载不完** | 命中硬超时 | 配合硬超时兜底;必要时按站点白/黑名单 | [04][05][07] |
| 🟡 中 | **持久化 context 跨站共享 cookie/缓存,临时 user_data_dir 可能膨胀** | 站点间 cookie 污染 / 磁盘增长 | 定期退役重建会话(每 N 次/M 分钟);或每站换会话 | [06] |
| 🟢 低 | **指纹版本硬编码 chromium/chrome=149** | 升级后可能与实际 Chromium 版本漂移,反成破绽 | 升级 Scrapling 后核对 | [05][07] |
| 🟢 低 | **`block_ads` 后缀链匹配可能误伤目标站 CDN/统计子域** | 正文缺块 | 对实际目标站抽样验证 | [07] |
| 🟢 低 | **`stealthy_headers=True` 给每个静态请求加 Google referer** | 个别站行为差异 | 知晓副作用即可 | [03] |
| 🟢 低 | **`is_proxy_error` 把 `connection timed out` 判成代理错误** | timeout/failed 误分类 | 分类以自己的 wait_for 信号为准 | [07] |

### 6.2 待实测问题汇总(去重,标来源)

**内存 / 进程回收(最高优先)**
1. `max_pages` + `disable_resources=True` 下单 Chromium 在目标 VPS 的**峰值 RSS**;tab close 后 renderer 是否立即回收(源码只保证 `page.close()`,不保证 OS 回收)。— [04][05][06]
2. `asyncio.wait_for` cancel `fetch` 后,内部 `finally: page.close()` 是否一定执行;卡在 `await page.goto` 被 cancel 时 page/进程是否泄漏;紧接 `close()` 能否干净回收。— [01][04][06][09]
3. `browser.close()` 在浏览器真卡死时是否会 hang;无 pid 可 kill,是否需容器 cgroup 内存上限 + 按名 kill chromium 孤儿进程兜底。— [06]
4. 单 Session `max_pages>1` vs 多 Session 各 `max_pages=1` 的峰值内存/稳定性实测对比。— [06]
5. 长驻持久化 context 跨站抓取时 cookie/缓存/临时 user_data_dir 是否膨胀,退役重建周期。— [06]
6. patchright(隐身)与 playwright(动态)两套 async 的超时/关闭进程模型是否一致。— [06]

**升级判据阈值校准**
7. `MIN_TEXT_CHARS`「正文过薄」阈值取值:用真实样本(正常文章/SPA 空壳/软 404)测 `len(get_all_text(strip=True))` 分布;CJK 按字符数、可能要分语言。— [02][03][08]
8. `get_all_text` 会把 nav/footer 算进长度,「正文短导航长」页面是否漏判;是否需先粗剔除或用文本/body 长度比。— [08]
9. 静态档对 React/Vue 首屏的 `<noscript>`/骨架占位文字是否计入,是否要把 `noscript/template` 加进 `ignore_tags`。— [08]
10. 静态档遇 Cloudflare 的确切 status(403 还是 503)、稳定出现的头(`cf-ray`/`cf-mitigated`/`server:cloudflare`)与 body 标记,用真实 CF 站校准 `looks_blocked`。— [01][02][03]

**Cloudflare / 隐身**
11. `solve_cloudflare=True` 对当前(2026)Turnstile 的真实成功率与耗时(solver 全 `# pragma: no cover`);失败时是否稳定返回「仍含 Just a moment 的页面」。— [01][05]
12. `solve_cloudflare` 求解自旋(`attempts>=100` 才 break)叠加 timeout≥60s 的单页最坏墙钟,用以校准隐身档 HARD_TIMEOUT(可能要 90s+)。— [05][06]
13. 强杀后 `close()` 重建对同会话其它并发 tab 的影响,决定「杀单 tab」还是「杀整会话」粒度(必要时 `max_pages=1` 换隔离)。— [05]

**编码 / 内容**
14. 非 UTF-8 站点(GBK/Shift-JIS)`resp.encoding` 是否可靠、trafilatura 直接吃 `resp.body`(bytes)的编码探测是否一致。— [02][08]
15. `disable_resources=True` 砍 CSS/图片后是否影响 trafilatura 正文/图片提取(与 P3 联调)。— [01][04][07]
16. 浏览器档 content-type 非 html 却实为 HTML(如 `text/plain`)的边界站占比,是否需强制 `page.content()`。— [02]

**依赖 / 部署 / curl_cffi**
17. patchright 是否需单独 `patchright install chromium`(`scrapling install` 未跑),隐身档能否直接用 playwright 的 Chromium 启动。— [05][09]
18. curl_cffi 未装于本 env:超时是否抛 `CurlError`、`e.code==28` 语义、是否有独立超时子类;`follow_redirects="safe"` 字符串是否被安装版本接受。— [03]
19. `asyncio.wait_for` 取消一次性 curl session 时 `finally.close()` 能否释放 fd、是否泄漏句柄。— [03]
20. 非 Debian/K8s 无 apt 环境的部署路径(官方强绑 Debian+apt);只读根容器里 `scrapling.cli.install` 触碰哨兵是否报错(用直接 `playwright install` 规避)。— [09]

---

## 7. 笔记勘误与裁决

回源码裁决 9 份笔记间的矛盾:

### A. `Fetcher.async_get` 不存在 —— 静态异步接口是 `AsyncFetcher.get`(修正 [09])

[09] §4.3 骨架第 219 行写 `Fetcher.async_get(url, ...)`。**源码裁定错误**:`requests.py:28-65` 里 `Fetcher` 只有 `get/post/put/delete`(同步、返回 `Response`);异步是**独立的 `AsyncFetcher` 类**,方法名同为 `.get`,返回 `Awaitable[Response]` 需 `await`(`requests.py:48-53`)。没有 `async_get` 这个方法。[01][03][06] 的 `AsyncFetcher.get` 是对的。已在 [09] 加勘误。

### B. 交给 trafilatura 的载荷用 `resp.body`,不是 `resp.html_content`(修正 [09] 骨架取值)

[09] §4.3 骨架用 `resp.html_content` 作为交下游的 HTML,并在骨架里对每个 URL 新建 `Session(max_pages=1)`。[02][08] 两份专题笔记均裁定:trafilatura 载荷用 **`resp.body`**(原始未改动 bytes;`html_content` 是 lxml 重序列化、丢注释/合并空白)。[09] 自己也标注了「骨架 API 名以 fetcher 子系统为准」。**采纳 [02][08]:一律 `resp.body`**;会话应长驻复用(§4),不在热路径 per-URL 新建。已在 [09] 加勘误。

### C. per-call `solve_cloudflare=True` **不会**自动把 timeout 抬到 60000ms(裁定 [05] 待实测 3 / [01] 待确认)

源码验证(`_validators.py:178-215` `validate_fetch`,`:150-155` `StealthConfig.__post_init__`):
`StealthConfig.__post_init__` 里 `if solve_cloudflare and timeout<60000: timeout=60000` 的自动抬升,**只在构造 `StealthConfig` 时触发**。而 per-call `fetch(url, solve_cloudflare=True)` 走 `validate_fetch`:它把 per-call kwargs 收进 `overrides`,`validate(overrides, StealthConfig)` 虽会算出 `timeout=60000`,**但随后只提取 `overrides.keys()` 里的字段**(即只有 `solve_cloudflare`),`timeout` 仍取自会话级 `session._config.timeout`,最终塞进的是普通 `_fetch_params` dataclass(无 `__post_init__`)。
**结论:若只在 per-call 传 `solve_cloudflare=True` 而会话级 timeout<60000,则 timeout 不会被抬到 60000。** → **必须在会话级(构造 `AsyncStealthySession` 时)就设 `timeout≥60000`**(§4 骨架已照此写)。`validate_fetch` 标 `# pragma: no cover`,官方无测试覆盖,更需我们自守此约束。

### D. `_detect_cloudflare` 靠 `cType:` 字符串 + turnstile 脚本判定,**不看 `<title>`**([02] 已自我修正,确认采纳)

`_base.py:544-577`:检测入口是 `cType:'non-interactive'|'managed'|'interactive'` 字面量或内嵌 turnstile 脚本 `script[src*="challenges.cloudflare.com/turnstile/v"]`;`Just a moment...`/`Verifying you are human.` 是 `_stealth.py` solver 轮询循环用来判断挑战页是否还在的条件,不是检测入口。我们网关侧的 `looks_blocked` 可同时用两类信号(status/头 + body 标记),与内部检测互补。

### E. `proxy_rotator` 三个引擎**都**消费(确认 [07] 的重大自我修正,推翻早期「仅浏览器」说法)

`static.py:89/249-250/91-94/264` 证明静态引擎同样读入并每请求 `get_proxy()` 轮换、与 `proxy` 互斥、遇代理错误换代理重试。故三档都支持 `ProxyRotator`。P2 本期不接代理,记录备用。

### F. 其余已被各笔记自我复核修正的点(采纳,不再展开)

- [01]:静态 `Fetcher` 无 `fetch()`;StealthyFetcher classmethod 内部用 `StealthySession`(非 `DynamicSession`);`user_data_dir` 属父类 `PlaywrightConfig` 非隐身档专属。
- [03]:`Response`/`Selector` **无 `.content` 属性**(用会 AttributeError),body 一律经 `resp.body`(bytes)取;别用 `resp.text` 搜 CF 标记。
- [04]:session 级静态 `config.proxy` 走持久化上下文跨 tab 复用,**不**每请求新建 context;只有 per-request proxy / rotator 才新建。
- [08]:`html_content` 实为 **outer HTML**(源码 docstring「inner」是笔误)。
- [09]:`playwright/patchright` pin 在 `pyproject.toml:76-77`;`convert_response` 构造 Response 的字段含 `request_headers`。

---

*(本总览基于 01–09 号笔记与 v0.4.10 源码抽样复核撰写;落地编码前,§6.2 的待实测项应在目标 VPS 逐条验证。)*
