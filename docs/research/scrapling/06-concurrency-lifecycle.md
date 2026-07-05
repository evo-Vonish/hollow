# Scrapling 子系统研究:并发 / 会话 / 进程生命周期

> 版本:Scrapling v0.4.10(`vendor/scrapling/scrapling/__init__.py:2`,未修改快照)
> 研究范围:`fetchers/`、`engines/_browsers/`(async/context 相关)、`spiders/session.py`、`spiders/engine.py`、`spiders/scheduler.py`、`engines/static.py`
> 目标读者:未来维护"网关侧并发治理封装"的自己
> 结论优先级:**源码 > 文档**。源码与 docstring 冲突处已标注。

---

## 0. 职责概述

这一层要回答的是"P2 内存治理"的三个核心问题:能不能并发、浏览器进程什么时候起什么时候死、超时了怎么回收。

一句话总结源码现状:

- Scrapling **自带完整的 async 接口**(静态 / 动态 / 隐身三档都有 async 版本),async 是一等公民,不是同步套线程。
- Scrapling **没有"浏览器实例池"**。它只有"页面池"(`PagePool` = 同一个浏览器 context 里的多个 tab)。一个 Session 对象 = 一个浏览器进程 = 一个持久化 context。想要"复用浏览器"就是"复用同一个 Session 对象反复 `.fetch()`"。
- Scrapling **没有硬超时、没有进程 kill、没有 `__del__`/atexit 兜底**。`timeout` 参数只是喂给 Playwright 的页面级导航超时。整个 `fetch()` 协程没有任何 `asyncio.wait_for` 包裹。资源回收**完全依赖**你显式 `close()` 或用 `async with` 上下文管理器。**这是我们必须自己补的最大缺口。**

因此我们的网关必须自己加三样东西:**asyncio 信号量(限并发)+ `asyncio.wait_for`(硬超时)+ 超时/异常后的会话回收(close 并重建)**。

---

## 1. 关键类与调用链

### 1.1 三档 Fetcher 的入口(都是 classmethod)

| 档位 | 同步 | 异步 | 底层 Session 类 |
|---|---|---|---|
| 静态 HTTP | `Fetcher.get/post/...` | `AsyncFetcher.get/...` | curl_cffi(无浏览器) |
| 动态渲染 | `DynamicFetcher.fetch` | `DynamicFetcher.async_fetch` | `(Async)DynamicSession` |
| 隐身 | `StealthyFetcher.fetch` | `StealthyFetcher.async_fetch` | `(Async)StealthySession` |

- 静态:`fetchers/requests.py:48-65`。`AsyncFetcher.get` 返回 `Awaitable[Response]`(`fetchers/requests.py:52`),底层是 `AsyncCurlSession`(curl_cffi 异步,**真异步、不阻塞事件循环**,见 `engines/static.py:439-474` `await session.request(...)`)。
- 动态:`fetchers/chrome.py:53-94`,`async_fetch` 内部 `async with AsyncDynamicSession(**kwargs) as session: return await session.fetch(url)`(`chrome.py:93-94`)。
- 隐身:`fetchers/stealth_chrome.py:65-115`,`async_fetch` 内部 `async with AsyncStealthySession(**kwargs) as engine: return await engine.fetch(url)`(`stealth_chrome.py:114-115`)。

**关键坑**:这些 classmethod 每次调用都是"起一个全新浏览器 → 抓一个 url → 关掉浏览器"。对小 VPS 来说,每个 url 都付一次 Chromium 冷启动 + 数百 MB 内存峰值的代价,**不适合直接在网关热路径循环调用**。要复用就绕过 classmethod,直接持有 Session 对象。

### 1.2 Session 的继承结构

```
BaseSessionMixin (校验/生成 options)      SyncSession / AsyncSession (生命周期 + PagePool)
        ├─ DynamicSessionMixin                    ├─ start() / close() / __enter__/__aenter__
        └─ StealthySessionMixin                   └─ _page_generator() 每次 fetch 借一个 page

DynamicSession(SyncSession, DynamicSessionMixin)          _controllers.py:22
AsyncDynamicSession(AsyncSession, DynamicSessionMixin)    _controllers.py:215
StealthySession(SyncSession, StealthySessionMixin)        _stealth.py:22
AsyncStealthySession(AsyncSession, StealthySessionMixin)  _stealth.py:303
```

### 1.3 生命周期方法(`engines/_browsers/_base.py`)

- `AsyncSession.__init__(max_pages=1)`:建 `PagePool(max_pages)`,`self._lock = asyncio.Lock()`,`_max_wait_for_page = 60`(`_base.py:224-232`)。
- `start()`(子类实现):`async_playwright().start()` → 按配置 `launch_persistent_context(...)`(默认)/ `launch(...)`(proxy_rotator)/ `connect_over_cdp(...)`(cdp_url)→ 置 `_is_alive=True`(`_controllers.py:261-288`、`_stealth.py:352-380`)。**若已 start 再调 → `RuntimeError("Session has been already started")`**(`_controllers.py:288`)。
- `close()`:`context.close()` → `browser.close()` → `playwright.stop()` → `_is_alive=False`(`_base.py:237-254`)。**幂等**:`if not self._is_alive: return`(`_base.py:239`)。
- `__aenter__/__aexit__` = `start()`/`close()`(`_base.py:256-261`)。

### 1.4 每次 fetch 的页面借还(`_base.py:369-404` `_page_generator`)

标准模式(无 per-request proxy):
1. `_get_page()` 新建 page,受 `self._lock` 保护;若 `pages_count >= max_pages` 则**忙等**,每 50ms 轮询,超过 `_max_wait_for_page`(60s)抛 `TimeoutError`(`_base.py:288-300`)。
2. `yield page_info` 给 `fetch()` 用。
3. `finally: await page_info.page.close()` + 从 pool 移除(`_base.py:402-404`)。**页面每次用完必关**,所以长期复用 Session 不会累积 tab。

---

## 2. 关键参数与默认值(`engines/_browsers/_validators.py`)

`PlaywrightConfig`(动态)与 `StealthConfig`(隐身,继承前者)的默认值,决定我们不传参时的行为:

| 参数 | 默认 | 出处 | 对 P2 的意义 |
|---|---|---|---|
| `max_pages` | **1** | `_validators.py:62`(`PagesCount = ge=1, le=50`,`:54`) | 一个 Session 默认只允许 1 个并发页面!第 2 个并发 fetch 会忙等 |
| `headless` | `True` | `_validators.py:63` | VPS 无头,OK |
| `timeout` | `30000`(ms) | `_validators.py:78` | 仅页面级超时,非整体超时 |
| `network_idle` | `False` | `_validators.py:65` | 默认不等 networkidle |
| `load_dom` | `True` | `_validators.py:66` | 默认等 JS 执行完 |
| `retries` | `3`(ge=1,le=10) | `_validators.py:90,:55` | fetch 内部自带最多 3 次重试,每次都可能重开页面 |
| `retry_delay` | `1`(秒) | `_validators.py:91` | 重试间隔 |
| `disable_resources` | `False` | `_validators.py:64` | 建议我们开 True 省内存/带宽 |
| `solve_cloudflare` | **`False`** | `_validators.py:148`(`StealthConfig`) | **默认不解 Cloudflare**;要过盾必须显式 `solve_cloudflare=True` |
| `allow_webgl` | `True` | `_validators.py:145` | |
| `user_data_dir` | `""`(→ 临时目录) | `_validators.py:80` | 每 Session 一个临时 profile 目录 |

**两条隐藏规则:**
- `solve_cloudflare=True` 时,若 `timeout < 60000` 会被**强制抬到 60000ms**(`_validators.py:150-155`)。即解盾单页最长可占 60s+。
- `block_ads=True` 会把 ~3500 广告域并入 `blocked_domains`(`_validators.py:135-141`)。

---

## 3. 逐问题解答

### 问题 1:Scrapling 自带 async 接口吗?自带浏览器实例池/复用吗?

**async:自带,且是真异步。**
- 三档全有 async:`AsyncFetcher`、`DynamicFetcher.async_fetch`、`StealthyFetcher.async_fetch`(见 §1.1)。
- 静态 async 走 curl_cffi 的 `AsyncCurlSession.request`(`static.py:474`),非阻塞。
- 浏览器 async 走 Playwright `async_api` + patchright `async_playwright`(`_stealth.py:9`、`_controllers.py:8`)。`fetch()` 全程 `await`。

**浏览器实例池:不自带。** 源码里只有 `PagePool`(`engines/_browsers/_page.py:41`),它管的是**同一个浏览器 context 内的 page/tab**,不是多浏览器进程池。证据:
- `PagePool.__init__(max_pages=5)` 只持有 `self.pages: List[PageInfo]`(`_page.py:46-49`),`add_page` 超过 `max_pages` 抛 `RuntimeError`(`_page.py:60-61`)。
- 一个 Session 只启动一个 browser/context(`start()` 里只有一次 `launch_persistent_context`,`_controllers.py:276`)。
- 没有任何"多个 Session/browser 的管理器"。`spiders/session.py` 的 `SessionManager` 是"按 id 存放**用户自己建的** Session 实例"的字典(`session.py:12-33`),不负责按需扩缩容,也不做进程池。

**复用方式:自己持有一个已 start 的 Session,反复 `.fetch()`。** 因为每次 fetch 用完即关 page(§1.4),Session 可长期存活复用;`max_pages>1` 时同一浏览器可并发多 tab。

> docs vs 源码:docstring 反复写 "with page pooling / persistent browser Context",容易让人以为有"浏览器池"。实际只有"页面池",且默认 `max_pages=1`(动态/隐身 Session 构造时把 `self._config.max_pages` 传进去,`_controllers.py:259`、`_stealth.py:350`)。以源码为准:**没有浏览器进程池,复用粒度是 Session 对象。**

### 问题 2:自己用 asyncio 信号量 + 实例上限 2-3 包装,如何正确复用/关闭避免泄漏?

要点:
1. **复用 Session 对象,而不是每 url 调 classmethod。** 预先建好 N 个(N=2~3)`AsyncStealthySession` / `AsyncDynamicSession`,各自 `await session.start()`(或 `await session.__aenter__()`)一次,放进一个 `asyncio.Queue` 当池子;信号量隐含在"池子里只有 N 个对象"里,或额外用 `asyncio.Semaphore(N)`。
2. **一个 Session 想同时跑几个 tab,就在构造时传 `max_pages`。** 公共入参名是 `max_pages`(TypedDict `PlaywrightSession.max_pages`,`_types.py:65`;`AsyncDynamicSession.__init__` 把它透传给 `super().__init__(max_pages=self._config.max_pages)`,`_controllers.py:259`)。VPS 内存紧,建议**每 Session `max_pages=1`,靠"多 Session + 信号量"控总并发**,比"单 Session 多 tab"更好回收(一个 tab 卡死可整会话重建)。
3. **关闭必须显式。** 没有 `__del__` 兜底(见问题 3)。用完/退出时对池里每个 Session `await session.close()`。`close()` 幂等(`_base.py:239`),重复调安全。
4. **cookie/状态会串。** 持久化 context 跨 fetch 共享 cookie/localStorage(`launch_persistent_context`)。研究型抓取如果不希望站点间 cookie 污染,要么每站换 Session,要么调用时用 per-request `proxy`(会走 `_page_generator` 的"新建独立 context 再关"分支,`_base.py:379-396`,天然隔离但更慢)。

### 问题 3:每 URL 硬超时后如何确保浏览器进程被杀死回收?有 close/context manager/__del__ 吗?

**现状(必须记牢):**
- **有** `close()` 和 `async with`(`__aenter__/__aexit__`)(`_base.py:237-261`)。
- **没有** `__del__`、**没有** `atexit`、**没有** 任何 `SIGKILL/SIGTERM/.kill()/terminate`。全仓库 grep 只有 `core/storage.py:154` 有个与浏览器无关的 `__del__`。
- **没有整体硬超时。** `fetch()` 里的 `timeout` 只通过 `page.set_default_navigation_timeout/​set_default_timeout`(`_base.py:303-304`)作用于单个 Playwright 操作(`goto`、`wait_for_selector`)。像 `network_idle` 等待还会**吞掉超时异常**(`_base.py:322-327` `except (PlaywrightError, Exception): pass`),`solve_cloudflare` 里有自旋循环(`_stealth.py:382-457`)。所以**单个 `await session.fetch()` 的墙钟时间可能远超 `timeout`**。
- Playwright 单步超时抛异常后,会被 `fetch()` 的 `try/except` 捕获 → `page_info.mark_error()` → 按 `retries` 重试(`_controllers.py:196-210`),**浏览器进程不动、不重启**。

**结论:P2 的"每 URL 硬超时 + 超时杀进程回收"Scrapling 不提供,必须我们做:**
1. 外层用 `asyncio.wait_for(session.fetch(url, ...), timeout=HARD_TIMEOUT)` 包硬超时。
2. `wait_for` 超时会 cancel 掉 fetch 协程,但**被 cancel 的浏览器可能处于半死状态,内存不会自动释放**。因此超时/异常后要**把这个 Session 判死并重建**:`await session.close()` 再 `new + start` 一个替换它。这是唯一能保证进程被回收的路径(`browser.close()` 会真正关掉 Chromium 进程,`_base.py:247`)。
3. `close()` 自身也可能因浏览器卡死而 hang → 给 close 再套一层 `asyncio.wait_for(session.close(), timeout=CLOSE_TIMEOUT)`;若 close 也超时,记录并放弃该对象(极端情况才需要 OS 级 kill,Scrapling/Playwright 没暴露 pid,得靠容器级内存上限/重启兜底 —— 见 open_questions)。

### 问题 4:同步 API 在 asyncio 事件循环里怎么安全调用?

- **首选:根本别用同步 API,用 `async_fetch` / `AsyncFetcher`。** 同步档位内部用 `sync_playwright()`(`_controllers.py:75`)/同步 curl,**在正在运行的事件循环线程里直接调会报错或阻塞**(sync Playwright 不能在有 running loop 的线程里跑)。
- **若不得不用同步 API**(例如某第三方只给同步),用线程池:`await asyncio.to_thread(StealthyFetcher.fetch, url, **kw)` 或 `loop.run_in_executor(...)`。`to_thread` 把它丢到**没有 running loop 的 worker 线程**,`sync_playwright` 在那里能正常起。注意:①线程池大小要和浏览器上限一致,否则又会超并发;②`asyncio.to_thread` 无法真正 cancel 底层同步调用,硬超时语义更弱。**所以对浏览器档,坚决走 async 版本。**
- 静态档:`Fetcher.get`(同步 curl)会阻塞事件循环;并发场景用 `AsyncFetcher.get`(非阻塞,`static.py:474`)。

### 问题 5:Scrapling 自己是怎么控并发的?(可借鉴)

`spiders/engine.py`(爬虫编排层)的做法值得抄:
- 用 **anyio `CapacityLimiter`** 做全局并发闸:`self._global_limiter = CapacityLimiter(spider.concurrent_requests)`(`engine.py:63`),还支持 per-domain 限流(`engine.py:125-130`)。
- 每个请求在 `async with self._rate_limiter(domain):` 内执行(`engine.py:196`)。
- `SessionManager` 持有**长生命周期、复用**的 async Session,请求按 `sid` 路由到已 start 的 Session,`await session.fetch(...)`(`spiders/session.py:103-134`)。
- `Scheduler` 是带去重的优先级队列(`scheduler.py`),与内存治理无直接关系,可忽略。

这正是我们要复刻的形态:**限流器(信号量/CapacityLimiter)+ 复用的长活 Session + 每请求包裹**。差别是我们要额外加**硬超时 + 超时后会话回收**(Scrapling spider 层也没做整体硬超时)。

---

## 4. 对 P2 的影响与行动建议

### 4.1 结论清单

1. 网关走 **async 全链路**:静态 `AsyncFetcher.get`,动态 `AsyncDynamicSession.fetch`,隐身 `AsyncStealthySession.fetch`。不碰同步 API。
2. **浏览器 Session 池要自己建**,Scrapling 不提供。池大小 2~3,每 Session `max_pages=1`,用 `asyncio.Semaphore` 兜总并发。
3. **每 URL 硬超时靠 `asyncio.wait_for`**,Scrapling 的 `timeout` 只是页面级、且会被部分等待吞掉,不可依赖。
4. **超时/异常后必须回收 Session(close + 重建)**,否则半死的 Chromium 会漏内存;没有 `__del__` 兜底。
5. 静态档单独一条信号量(可给更大并发,如 10),因为它不吃浏览器内存。
6. 建议默认开 `disable_resources=True`、`block_ads=True` 省内存;`solve_cloudflare` 默认 False,只在检测到盾时才对该 url 单独用 `solve_cloudflare=True` 重试(注意它会把 timeout 抬到 60s+)。

### 4.2 推荐封装骨架(最小可运行思路)

> 说明:这是"网关侧并发治理"的骨架,聚焦信号量 + 硬超时 + 资源回收。三档升级链(auto 模式)的判定逻辑由 P2 的 fetch 编排层套在最外面;这里只给"安全地跑一次浏览器 fetch 并保证回收"的原语。

```python
import asyncio
import time
from contextlib import asynccontextmanager
from scrapling.fetchers import (
    AsyncFetcher, AsyncDynamicSession, AsyncStealthySession,
)

HARD_TIMEOUT = 45.0     # 每 url 墙钟硬超时(秒),浏览器档
CLOSE_TIMEOUT = 10.0    # 关会话的超时
BROWSER_LIMIT = 2       # 浏览器并发上限(VPS 内存决定,2~3)
STATIC_LIMIT = 10       # 静态 HTTP 并发上限


class BrowserSessionPool:
    """一个固定大小的浏览器会话池。池里每个 Session 长活复用;
    出问题的 Session 被 close 并替换,保证进程回收、不泄漏。"""

    def __init__(self, session_factory, size: int):
        # session_factory: 无参 callable,返回一个「未 start」的 AsyncXxxSession
        self._factory = session_factory
        self._size = size
        self._pool: asyncio.Queue = asyncio.Queue()
        self._sem = asyncio.Semaphore(size)  # 双保险:限总并发

    async def start(self):
        for _ in range(self._size):
            s = self._factory()
            await s.start()               # 显式启动浏览器
            await self._pool.put(s)

    async def close(self):
        while not self._pool.empty():
            s = await self._pool.get()
            await self._safe_close(s)

    @staticmethod
    async def _safe_close(session):
        try:
            await asyncio.wait_for(session.close(), timeout=CLOSE_TIMEOUT)
        except Exception:
            # close 都卡死:放弃该对象,靠容器内存上限兜底(见 open_questions)
            pass

    async def _fresh(self):
        s = self._factory()
        await s.start()
        return s

    @asynccontextmanager
    async def acquire(self):
        """借一个 Session;归还时若期间出过错则替换成新的。"""
        async with self._sem:
            session = await self._pool.get()
            broken = False
            try:
                yield session
            except Exception:
                broken = True
                raise
            finally:
                if broken:
                    await self._safe_close(session)        # 杀掉可能半死的浏览器
                    try:
                        session = await self._fresh()      # 补一个新的回池
                    except Exception:
                        session = None
                if session is not None:
                    await self._pool.put(session)


async def browser_fetch(pool: BrowserSessionPool, url: str, **fetch_kwargs):
    """跑一次浏览器 fetch,带硬超时;超时会触发会话回收(靠 acquire 的 broken 分支)。"""
    async with pool.acquire() as session:
        # wait_for 超时 → 抛 TimeoutError → acquire 判 broken → close+重建
        return await asyncio.wait_for(
            session.fetch(url, timeout=int(HARD_TIMEOUT * 1000), **fetch_kwargs),
            timeout=HARD_TIMEOUT,
        )


# ---- 组装 ----
# 隐身池:每 Session max_pages=1,便于整会话回收
stealth_pool = BrowserSessionPool(
    lambda: AsyncStealthySession(max_pages=1, headless=True,
                                 disable_resources=True, block_ads=True),
    size=BROWSER_LIMIT,
)
dynamic_pool = BrowserSessionPool(
    lambda: AsyncDynamicSession(max_pages=1, headless=True,
                                disable_resources=True, block_ads=True),
    size=BROWSER_LIMIT,
)
static_sem = asyncio.Semaphore(STATIC_LIMIT)


async def static_fetch(url: str, **kw):
    async with static_sem:
        return await asyncio.wait_for(AsyncFetcher.get(url, timeout=20, **kw),
                                      timeout=25)
```

要点回顾:
- **信号量**:`Semaphore(BROWSER_LIMIT)` + 池容量,双重限住浏览器并发。
- **硬超时**:`asyncio.wait_for` 包 `session.fetch`,不信任 Scrapling 内部 timeout。
- **资源回收**:`acquire()` 的 `finally` 里,只要出过异常(含超时)就 `close()` 掉旧 Session 再补新的 —— 这是保证 Chromium 进程被杀、内存被还的关键。
- **fetch_status 映射**(供 P2 编排层参考):正常返回 → `ok`;`asyncio.TimeoutError` → `timeout`;`fetch()` 抛异常且内容/状态提示反爬 → `blocked`(隐身档 `_detect_cloudflare` 可辅助,`_base.py:544-577`);其他异常 → `failed`。全部保留占位,禁止静默丢弃。

### 4.3 auto 升级链落点提示
- 静态 → 动态判据("正文过薄/疑似 SPA")在 `AsyncFetcher.get` 返回后由 P2/P3 判定,与本子系统无关。
- 动态 → 隐身判据("Cloudflare/反爬")可复用 `StealthySessionMixin._detect_cloudflare(page_content)`(静态方法,输入 HTML 字符串返回挑战类型或 None,`_base.py:544-577`);但它是内部 API,建议在网关侧复制其判定逻辑而非直接 import,避免耦合内部实现。

---

## 5. 待实测确认的问题(open_questions)

1. **`asyncio.wait_for` 取消 `session.fetch` 后,浏览器/context 的真实状态**:被 cancel 的 fetch 是否会残留孤儿 page 或让 context 进入不可用态?需实测 cancel 后紧接着 `close()` 能否干净回收(预期需要,故骨架里做了)。
2. **`close()` 本身在浏览器已卡死时会不会 hang**:`browser.close()` 依赖与 Chromium 的 CDP 通信,进程真卡死时可能不返回。骨架给了 `CLOSE_TIMEOUT`,但超时后**没有 pid 可 kill**(Scrapling/Playwright 未暴露浏览器 pid)。需确认:是否要在容器层加内存 cgroup 限制 + 进程级 watchdog(如按名 kill `chrome`/`chromium` 孤儿进程)兜底。
3. **单 Session `max_pages>1` vs 多 Session 各 `max_pages=1` 的内存实测对比**:前者省一个 Chromium 冷启动,但一个 tab 卡死难以只回收该 tab;需实测哪种在小 VPS 上更稳、峰值内存更低。
4. **持久化 context 跨 fetch 的 cookie/缓存增长**:长活 Session 反复抓不同站,`user_data_dir`(临时目录)与内存里的 cookie/cache 是否随时间膨胀?是否需要定期"退役重建"Session(如每抓 N 次或每 M 分钟)。
5. **patchright vs playwright 的进程模型差异**:隐身档用 `patchright.async_api`(`_stealth.py:9`),动态档用 `playwright.async_api`(`_controllers.py:8`)。两者在超时/关闭行为上是否一致,需分别验证。
6. **`solve_cloudflare=True` 时的墙钟上限**:`_cloudflare_solver` 有多层自旋(如 `attempts >= 100` 才 break,`_stealth.py:441`),叠加 `timeout` 被抬到 60s,单页最坏耗时需实测,以此校准 `HARD_TIMEOUT`(解盾 url 可能要单独放宽到 90s+)。
7. **`max_pages` 公共入参是否对隐身 async 生效**:源码显示 `AsyncStealthySession.__init__ → super().__init__(max_pages=self._config.max_pages)`(`_stealth.py:350`),而 `StealthConfig` 继承 `PlaywrightConfig` 有 `max_pages` 字段,理论上生效;但 `StealthSession` TypedDict 是否显式列出 `max_pages` 未逐字确认(`PlaywrightSession` 列了,`_types.py:65`),建议实测 `AsyncStealthySession(max_pages=2)` 是否真能并发 2 tab。
