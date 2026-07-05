# Scrapling 静态引擎(Fetcher 底层)研读笔记

> 研读版本:vendor/scrapling v0.4.10(未修改快照)
> 主要源码:`scrapling/engines/static.py`、`scrapling/fetchers/requests.py`
> 关联源码:`scrapling/engines/toolbelt/convertor.py`(Response 工厂)、`scrapling/engines/toolbelt/custom.py`(Response 类)、`scrapling/engines/_browsers/_types.py`(参数 TypedDict)、`scrapling/engines/toolbelt/proxy_rotation.py`
> 写给以后维护 P2 抓取网关封装的自己。**结论均标注出处;源码与文档冲突以源码为准。**

---

## 1. 职责概述

`Fetcher` / `AsyncFetcher`(`fetchers/requests.py`)是对外的静态 HTTP 抓取入口,底层完全基于 **`curl_cffi`**(不是 httpx / requests)。调用链:

```
Fetcher.get(url, **kw)                         # requests.py:32  classmethod
  -> __FetcherClientInstance__.get(...)        # requests.py:13  模块级单例 FetcherClient()
    -> _SyncSessionLogic.get(...)              # static.py:280
      -> _SyncSessionLogic._make_request("GET", ...)   # static.py:224
        -> curl_cffi Session.request(method, **args)   # static.py:256
        -> ResponseFactory.from_http_request(resp,...) # static.py:258 -> convertor.py:301
          -> Response(...)                     # custom.py:28 (Selector 子类)
```

关键点:
- `Fetcher` 是**类方法**接口,内部用一个**模块级全局单例** `FetcherClient()`(`requests.py:13`)。`FetcherClient` 继承 `_SyncSessionLogic`,但把 `__enter__/__exit__` 置 `None`、`_curl_session` 置哨兵 `_NO_SESSION`(`static.py:769-776`),因此每次请求是**一次性 session**(见 §3)。
- `AsyncFetcher` 对应 `AsyncFetcherClient`(`static.py:779`),`get/post/...` 返回 `Awaitable[Response]`,需要 `await`。**这是我们 P2 该用的接口**(要塞进 asyncio 信号量)。
- 若要长连接复用,另有上下文管理器 `FetcherSession`(`static.py:627`),`with`/`async with` 持有一个持久 curl session(见 §3)。

---

## 2. 关键 API / 参数详解

### 2.1 底层库与 TLS 指纹模拟(impersonate)

- 底层是 `curl_cffi`。`static.py:6-12` 直接 `from curl_cffi.requests import Session, AsyncSession`,请求由 `session.request(method, **args)` 发出(`static.py:256` / `:474`)。
- **TLS/JA3 指纹模拟靠 `impersonate` 参数**,由 curl_cffi 内建实现(curl-impersonate 的 BoringSSL 补丁)。scrapling 只是把 `impersonate` 透传给 `session.request`(`static.py:129` 放进 `final_args["impersonate"]`)。
- 默认值:`impersonate="chrome"`(`FetcherSession.__init__` 默认参数 `static.py:661`;`_ConfigurationLogic.__init__` 兜底 `kwargs.get("impersonate", "chrome")` `static.py:73`)。`"chrome"` 是 curl_cffi 的"最新可用 Chrome"别名。
- `impersonate` 可传**单个字符串**或**字符串列表**;传列表时每次请求随机选一个(`_select_random_browser` `static.py:36-47`,`random.choice`)。
- 当 `impersonate` 为真值时,**curl_cffi 会自动生成匹配该浏览器的 HTTP 头**(注释 `static.py:114`;`_headers_job` 里 `impersonate_enabled=True` 时不再叠加自造头 `static.py:181-186`)。
- `stealthy_headers`(默认 `True`,`static.py:74`/`:663`):
  - 无论如何会补一个 `referer: https://www.google.com/`(`static.py:178-179`)——**注意这会给每个请求都带 Google referer**。
  - 仅当**没有** impersonate 时,才用 `generate_headers()` 造一套真实浏览器头(`static.py:181-186`)。默认既有 impersonate 又有 stealth,所以实际主要生效的是 referer。
- `http3`(默认 `False`,`static.py:86`):开启则设 `http_version=CurlHttpVersion.V3ONLY`(`static.py:159-160`),源码明确警告与 `impersonate` 混用可能报 curl 错(`static.py:162-164`)。**P2 保持默认关闭。**

### 2.2 HTTP 方法 / 重定向 / 超时 / 代理 / 重试

支持的方法(`SUPPORTED_HTTP_METHODS`,`core/_types.py:38`):**仅 `GET / POST / PUT / DELETE`**。每个方法在 `_SyncSessionLogic` 和 `_ASyncSessionLogic` 上都有独立方法(`static.py:280/310/342/374` 与 `:498/528/560/592`)。**没有 HEAD**。P2 只需 GET。

参数默认值(`_ConfigurationLogic.__init__` `static.py:72-89`,`FetcherSession.__init__` `static.py:659-696`):

| 参数 | 默认 | 说明 / 出处 |
|---|---|---|
| `timeout` | `30`(秒) | `static.py:78`。透传 curl_cffi `timeout`(`static.py:124`)。 |
| `retries` | `3` | `static.py:80`。见下方重试逻辑。 |
| `retry_delay` | `1`(秒) | `static.py:81`。 |
| `follow_redirects` | `"safe"` | `static.py:82`。映射到 curl_cffi 的 `allow_redirects`(`static.py:125`)。`FollowRedirects = Union[bool, Literal["safe","all","obeycode","firstonly"]]`(`core/_types.py:43`)。docstring:`"safe"` = 跟随重定向但**拒绝跳到内网/私有 IP(SSRF 防护)**;传 `True` 则无限制跟随(`static.py:690`)。 |
| `max_redirects` | `30` | `static.py:83`。`-1` = 无限(docstring `static.py:293`)。 |
| `proxy` | `None` | 单代理,格式 `http://user:pass@host:port`(`static.py:76`)。 |
| `proxies` | `{}` | dict 形式(`static.py:75`)。 |
| `proxy_auth` | `None` | `(user, pass)` 元组(`static.py:77`)。 |
| `proxy_rotator` | `None` | `ProxyRotator` 实例;**与 `proxy`/`proxies` 互斥**,同时给会 `raise ValueError`(`static.py:91-95`)。 |
| `verify` | `True` | HTTPS 证书校验(`static.py:84`)。 |
| `cert` | `None` | 客户端证书 `(cert,key)`(`static.py:85`)。 |
| `headers` | `{}` | 会话级默认头(`static.py:79`)。 |
| `selector_config` | `{}` | 传给最终 `Selector`(解析层)的参数(`static.py:87`)。 |

**重试逻辑(重要,决定异常语义)** `_make_request` `static.py:246-278`:
- `for attempt in range(max_retries)` 循环。**只 catch `curl_cffi.curl.CurlError`**(`static.py:260`),即**传输层错误**(连接失败/超时/DNS/SSL 等)。
- 非最后一次:log warning + `sleep(retry_delay)` 重试;最后一次:log error + **`raise` 原样抛出 `CurlError`**(`static.py:271-273`)。
- **HTTP 4xx/5xx 状态码不在 catch 范围,不触发重试**,直接作为正常 `Response` 返回(见 §4)。
- 每次重试会重新取代理(rotator 场景 `static.py:249-252`)。异步版同构(`static.py:463-491`,用 `asyncio.sleep`)。

请求级覆盖:GET/POST 等的 `**kwargs` 可逐请求覆盖上述任意默认(`_get_param` 逐键判断 `static.py:98-100`)。额外透传参数:`params`、`cookies`、`auth`、`data`、`json`(`GetRequestParams`/`DataRequestParams`,`_browsers/_types.py:49-58`)。注意 `stealthy_headers` 在方法层被 `pop` 出来当作 `stealth` 传入(`static.py:307-308`)。

### 2.3 会话 / 连接复用机制(§3 展开见下)

---

## 3. 会话 / 连接复用机制

三种使用形态:

1. **`Fetcher.get(...)` / `AsyncFetcher.get(...)`(P2 默认用法)**
   - 走全局单例 `FetcherClient` / `AsyncFetcherClient`,其 `_curl_session` 是哨兵 `_NO_SESSION`(`static.py:776`/`:786`)。
   - `_make_request` 里判断 `session is _NO_SESSION and self.__enter__ is None` → **每次请求新建一个 `CurlSession()` / `AsyncCurlSession()`,请求完在 `finally` 里 `close()`**(同步 `static.py:237-241, 274-276`;异步 `static.py:452-458, 492-494`)。
   - 源码注释解释原因(`static.py:453-456`):① curl_cffi 会**缓存 impersonation 状态**,复用同一 session 切换 impersonate 会串味;② curl_cffi 不支持无 session 的异步请求;③ **同一 async session 并发多请求在 curl_cffi 下表现不稳**。
   - **结论:`AsyncFetcher.get` 是"每请求一个一次性 session",天然适合并发,无跨请求连接池复用,也不会共享 cookie。**

2. **`FetcherSession()` 上下文管理器**(`static.py:627`)
   - `with FetcherSession(...) as s:` / `async with ... as s:` 创建一个**持久** `CurlSession`(`static.py:206`/`:421`),块内多次 `s.get()` **复用同一 TCP 连接 + cookie jar**。
   - 同一实例**不可重入**:已激活再进入会 `RuntimeError`(`static.py:203-204`/`:418-419`/`:732`/`:758`)。
   - **若 P2 后续要"同域批量抓取 + 保持 cookie"**,应改用 `AsyncFetcherSession`(通过 `FetcherSession().__aenter__()`)而非全局 `AsyncFetcher`。本期 URL 列表可能跨域,单例一次性 session 更简单安全。

3. `FetcherClient` / `AsyncFetcherClient`:内部实现类,不建议直接用,`Fetcher` 已封装。

---

## 4. 遇到 403 / 429 / Cloudflare 的表现(**升级判定的核心**)

**结论:静态引擎对 HTTP 错误状态码"不抛异常",而是返回带该状态码的正常 `Response`,`content` 就是挑战页/拦截页的 HTML。**

依据:
- `_make_request` 只捕获 `CurlError`(传输层),**从不调用 `raise_for_status`**;拿到 curl 响应后直接 `ResponseFactory.from_http_request(response, ...)`(`static.py:256-258`)。
- `from_http_request`(`convertor.py:301-325`)无条件把 `response.status_code` 写进 `Response.status`、`response.content` 写进 `Response.content`,**不判断状态码**。curl_cffi 默认也不会因 4xx/5xx 抛错。
- 因此 403(Forbidden)、429(Too Many Requests)、503(Cloudflare "Just a moment..." 挑战)统统作为 `Response(status=403/429/503, content=<挑战页HTML>)` 正常返回。

**什么时候会抛异常(而非返回 Response)** —— 仅传输层失败,类型是 `curl_cffi.curl.CurlError`(`static.py:6` import,`:260` catch):
- 连接被拒 / 重置 / 无法连接、DNS 解析失败、TLS 握手失败、**超时**(curl code 28)、代理错误等。
- 这些错误经 `retries` 次重试后仍失败会**原样 `raise`**(`static.py:272-273`)。**我们的网关必须 try/except 兜住 `CurlError` 才能把它归类为 `failed`/`timeout`。**

**判定"需要升级到 dynamic / stealthy"的信号取法(在 `Response` 对象上):**
- `response.status`(int):`403`/`429`/`503` 是最强信号。
- `response.headers`(dict,`custom.py:65`):Cloudflare 常见 `server: cloudflare`、`cf-mitigated: challenge`、`cf-ray: ...`。可用于把"普通 403"与"Cloudflare 挑战"区分开(→ 前者可能 DynamicFetcher 够,后者直上 StealthyFetcher)。
- `response.body`(bytes,`custom.py:84`)/ `response.text`:挑战页 body 里通常含 `Just a moment...`、`cf-browser-verification`、`__cf_chl`、`challenge-platform` 等标记。
- 注意:Cloudflare 挑战页 HTTP 状态可能是 **403 或 503**,不一定是 429。

---

## 5. 判定"正文过薄疑似 SPA"能拿到的信号

`Response` 是 `Selector` 子类(`custom.py:28`),可用信号:

| 信号 | 取法 | 出处 |
|---|---|---|
| HTTP 状态 | `response.status` | `custom.py:61` |
| 原始 HTML 字节 | `response.body`(bytes) / `len(response.body)` | `custom.py:83-86`(Response 覆写,返回 bytes) |
| 原始 HTML 长度 | `len(response.content)` 等价 | `content` 在 `__init__` 编码为 bytes(`custom.py:57-58`) |
| 提取正文纯文本 | `response.get_all_text(strip=True, ignore_tags=("script","style"))` → `TextHandler` | `parser.py:279`(默认已忽略 script/style) |
| 元素文本 | `response.text` | `parser.py:269` |
| 内部 HTML | `response.html_content` | `parser.py:345` |
| 响应头(判 content-type) | `response.headers.get("content-type")` | `custom.py:65` |
| 编码 | `response.encoding` | `from_http_request` 用 `response.encoding or "utf-8"`(`convertor.py:316`) |

**SPA 判定建议**(纯静态引擎侧,交给下游 trafilatura 之前的粗筛):
- `status==200` 但 `get_all_text` 后的可见文本长度很小(如 < 某阈值,几百字符)→ 疑似壳页面(JS 渲染)。用 `get_all_text` 而非 `len(body)`,因为 SPA 的 HTML 字节可能不小(全是内联 JS/框架),但**可见文本极少**。
- 可加信号:body 里有 `<div id="root">` / `<div id="app">` 且几乎为空、`<noscript>` 提示需要 JS、大量 `<script src=...>` 但 `<body>` 文本稀薄。
- **注意** `get_all_text` 内部要真正解析 lxml 树,对超大页面有成本;可先用 `len(response.body)` 做便宜的第一道闸,再对可疑者算文本长度。

---

## 6. 对 P2 的影响与行动建议

### 6.1 三档升级链的判定策略(静态档产出)

对每个 URL,用 `AsyncFetcher.get` 拿 `Response` 后:

```python
import asyncio
from datetime import datetime, timezone
from curl_cffi.curl import CurlError            # 传输层异常类型,static.py:6
from scrapling.fetchers import AsyncFetcher

MIN_TEXT_LEN = 500          # 可见正文阈值,需实测校准
BLOCK_STATUSES = {403, 429, 503}

def _looks_blocked(resp) -> bool:
    if resp.status in BLOCK_STATUSES:
        return True
    server = (resp.headers.get("server") or "").lower()
    if "cloudflare" in server or resp.headers.get("cf-mitigated"):
        return True
    return False

def _text_too_thin(resp) -> bool:
    # 只对 2xx 的 HTML 判薄;非 HTML 直接交给下游
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" not in ctype:
        return False
    txt = resp.get_all_text(strip=True, ignore_tags=("script", "style"))
    return len(txt) < MIN_TEXT_LEN

async def fetch_static(url: str, sem: asyncio.Semaphore, timeout: float = 20):
    async with sem:                              # 全局并发闸
        started = datetime.now(timezone.utc)
        try:
            # AsyncFetcher 内部每请求一个一次性 curl session(static.py:452-458)
            resp = await asyncio.wait_for(       # 外层再包一层硬超时(见 6.2)
                AsyncFetcher.get(
                    url,
                    timeout=timeout,             # curl 自身超时,static.py:124
                    retries=1,                   # 关掉多次重试,升级链自己控制,见下
                    stealthy_headers=True,
                    impersonate="chrome",
                ),
                timeout=timeout + 5,
            )
        except asyncio.TimeoutError:
            return {"fetch_status": "timeout", "engine_used": "fetcher", ...}
        except CurlError as e:
            # 传输层失败:连接/DNS/TLS/curl 超时(code 28)等
            status = "timeout" if getattr(e, "code", None) == 28 else "failed"
            return {"fetch_status": status, "engine_used": "fetcher", ...}

        if _looks_blocked(resp):
            return {"decision": "escalate_stealthy", "engine_used": "fetcher", "status": resp.status}
        if _text_too_thin(resp):
            return {"decision": "escalate_dynamic", "engine_used": "fetcher"}
        return {
            "fetch_status": "ok",
            "engine_used": "fetcher",
            "html": resp.body,                   # bytes,交给 P3 trafilatura
            "status": resp.status,
            "fetched_at": started.isoformat(),
        }
```

要点:
1. **HTTP 403/429/503 不会抛异常**——必须显式读 `resp.status` 判定,别指望 try/except 兜住(§4)。
2. **建议把 scrapling 的 `retries` 调成 1**(默认 3,`static.py:80`)。scrapling 的重试是"同参重试传输错误",而我们的升级链是"换引擎重试",两者叠加会放大延迟(3 × retry_delay + 30s timeout,单 URL 最坏近 90s+)。让升级链主导重试。
3. **超时要双保险**:`AsyncFetcher.get(timeout=...)` 是 curl 层超时;但 curl 超时后 scrapling 仍会按 `retries` 重试。用外层 `asyncio.wait_for` 兜一层硬墙,超时即放弃、归 `timeout`,符合硬约束"每 URL 强制超时"。(静态档纯 HTTP,无子进程可杀;真正"超时杀进程回收"针对 DynamicFetcher/StealthyFetcher 的浏览器实例。)
4. **timeout 分类**:`CurlError.code == 28` 是 curl 的 `OPERATION_TIMEDOUT`。可据此把 `CurlError` 细分成 `timeout` vs `failed`。⚠ code 值需实测确认(见 §7)。
5. `resp.body` 返回 bytes(`custom.py:84`),`resp.encoding` 给编码,直接喂 P3。

### 6.2 与硬约束的对应

- 静态档**不吃浏览器内存**,不占浏览器实例池;但仍应过**同一个 asyncio 信号量**做全局并发闸(避免一次 10 个 URL 同时打满出口带宽/被限速)。可给静态档一个较大的并发额度,浏览器档单独一个 2-3 的小信号量。
- "超时杀进程回收"只对浏览器引擎有意义;静态档一次性 session 会在 `finally` 里 `close()`(`static.py:274-276`),`asyncio.wait_for` 超时后该协程被取消,curl session 由 `finally` 收尾。**需实测确认取消时 finally 能否干净关闭 curl 句柄(见 §7)。**

### 6.3 显式状态映射(禁止静默丢弃)

| 场景 | fetch_status | engine_used | 来源 |
|---|---|---|---|
| 2xx + 正文够长 | `ok` | `fetcher` | 正常 Response |
| 2xx + 正文过薄 | (内部)升级 dynamic | — | `_text_too_thin` |
| 403/429/503 或 CF 头 | (内部)升级 stealthy | — | `_looks_blocked` |
| CurlError code 28 / wait_for 超时 | `timeout` | `fetcher` | §4 异常路径 |
| 其他 CurlError(连接/DNS/TLS/代理) | `failed` | `fetcher` | §4 异常路径 |
| 升级链全部失败 | `blocked`/`failed` | 末档引擎 | 由编排层定 |

每 URL 无论成功失败都返回一条记录(含 `fetched_at`),失败原样占位。

---

## 7. 待实测确认的问题

1. **curl_cffi 版本与 `CurlError.code` 语义**:本 env 未安装 curl_cffi(仅 vendored 源码),无法确认 `code==28` 就是超时、以及超时到底抛 `CurlError` 还是子类。需在部署环境 `pip show curl_cffi` 并写小脚本触发超时/DNS 失败,打印 `type(e)`、`e.code`、`str(e)`。
2. **`follow_redirects="safe"` 是否被当前 curl_cffi 版本接受**:scrapling 把字符串 `"safe"` 直接透传给 curl_cffi 的 `allow_redirects`(`static.py:125`)。老版本 curl_cffi 的 `allow_redirects` 只吃 bool。需确认安装版本支持该字符串,否则默认请求就会报错。
3. **`asyncio.wait_for` 取消协程时,一次性 curl session 的 `finally.close()` 能否真正释放句柄**(`static.py:492-494`),会不会泄漏 fd。需压测观察 fd/内存。
4. **thin-body / SPA 阈值** `MIN_TEXT_LEN` 的具体数值,需拿真实新闻页 / SPA 壳页样本校准;不同语言(中文 vs 英文字符数)阈值不同。
5. **Cloudflare 挑战页的稳定判别特征**:实际抓几个 CF 站点,确认状态码是 403 还是 503、response.headers 里稳定出现哪些字段(`cf-mitigated`/`cf-ray`/`server: cloudflare`)、body 关键标记,再固化到 `_looks_blocked`。
6. **`Fetcher` 全局单例并发安全性**:`AsyncFetcher` 单例每请求新建 session(理论上安全),但单例对象本身的属性读取在高并发下是否有隐患,需并发压测确认(源码注释暗示 curl_cffi 有 impersonation 状态缓存问题,单例一次性 session 正是为规避它 `static.py:453-456`)。
