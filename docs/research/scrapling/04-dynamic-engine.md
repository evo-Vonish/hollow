# Scrapling 动态引擎(DynamicFetcher / 浏览器控制)研读笔记

> 版本:Scrapling v0.4.10(vendor 快照,未改源码)
> 研读范围:`scrapling/engines/_browsers/{_controllers,_base,_page,_config_tools,_validators}.py`、`scrapling/fetchers/chrome.py`、`scrapling/engines/toolbelt/navigation.py`、`scrapling/engines/constants.py`、`docs/fetching/dynamic.md`
> 面向对象:以后维护 P2 抓取网关封装的自己。所有结论标注了源码出处;源码与文档冲突处已单独指出。

---

## 一、职责概述

`DynamicFetcher`(旧名 `PlayWrightFetcher`,`chrome.py:97` 有别名)是 Scrapling 的「浏览器渲染」档位,用于抓取需要执行 JS 的 SPA / 动态页面。它对应我们三档升级链里的**第二档**(Fetcher 静态失败/正文过薄 → DynamicFetcher 浏览器渲染)。

调用层次(自上而下):

- `DynamicFetcher.fetch / async_fetch`(`chrome.py:10 / 53`)—— 每次调用 `with DynamicSession(**kwargs) as session: return session.fetch(url)`(`chrome.py:50-51` / `93-94`)。**注意:classmethod 每次都新建并销毁一个 Session(即一整个浏览器),不复用。**
- `DynamicSession`(同步)/ `AsyncDynamicSession`(异步)—— 真正的会话/浏览器管理器,在 `_controllers.py:22 / 215`。
- `SyncSession` / `AsyncSession` 基类 —— 页面池、页面获取、等待逻辑,在 `_base.py:48 / 218`。
- `DynamicSessionMixin`(`_base.py:499`)—— 用 `PlaywrightConfig` 做参数校验。
- `PagePool` / `PageInfo`(`_page.py:41 / 14`)—— 标签页(tab)池与状态跟踪。

底层驱动见第二节。

---

## 二、底层是什么、驱动哪种浏览器

**结论:DynamicFetcher 用的是「原版 Playwright」(不是 patchright),驱动 Chromium。**

- `_controllers.py:4-11` 直接 `from playwright.sync_api import ...` / `from playwright.async_api import ...`。启动时 `sync_playwright().start()`(`_controllers.py:75`)/ `async_playwright().start()`(`:264`)。
- 浏览器固定走 chromium 引擎:`self.playwright.chromium.launch_persistent_context(...)`(`_controllers.py:88 / 276`)、`chromium.launch(...)`(代理轮换模式,`:83 / 271`)、`chromium.connect_over_cdp(...)`(CDP 模式,`:79 / 267`)。
- channel 由 `real_chrome` 决定:`"chrome" if config.real_chrome else "chromium"`(`_base.py:469`)。即默认用 Playwright 自带的 Chromium;`real_chrome=True` 时用系统装的 Google Chrome。
- **对照:StealthyFetcher(第三档)用的是 `patchright`**(`_stealth.py:8-9` `from patchright.sync_api import sync_playwright`),这是一个反检测补丁版 Playwright。两者是**两套不同的浏览器驱动**。`pyproject.toml:76-77` 同时依赖 `playwright==1.61.0` 和 `patchright==1.61.1`。

> 差异提示:docs/dynamic.md:36 说「就是纯 PlayWright API」,与源码一致。文档没提 patchright,因为 patchright 只用于 StealthyFetcher。

三种运行形态(`docs/dynamic.md:28-53`,源码 `_controllers.py:77-88`):
1. 默认:启动本地 Chromium 的 **持久化上下文**(persistent context)。
2. `real_chrome=True`:同上但用系统 Chrome。
3. `cdp_url=...`:不启动本地浏览器,连远程 CDP。

---

## 三、关键参数语义与默认值

默认值全部来自 `PlaywrightConfig`(`_validators.py:59-94`),这是 msgspec Struct。下表「默认」列即该结构体字段默认值。

| 参数 | 默认 | 语义 / 源码位置 |
|---|---|---|
| `headless` | `True` | 无头模式。`_validators.py:63`。传给 `browser_options["headless"]`(`_base.py:468`)。 |
| `network_idle` | `False` | 等待「≥500ms 无网络连接」。`_validators.py:65`。实现 `_wait_for_networkidle` 调 `page.wait_for_load_state("networkidle")`(`_base.py:135-140 / 322-327`)。**关键:超时被 try/except 吞掉,永不抛错**,只是等到 `timeout` 后继续。 |
| `load_dom` | `True` | 「等 JS 全部加载执行」。`_validators.py:66`。实现:先 `wait_for_load_state("load")`,若 `load_dom` 再等 `"domcontentloaded"`(`_base.py:142-147 / 329-334`)。 |
| `wait_selector` | `None` | 等某 CSS 选择器达到某状态。`_validators.py:67`。实现 `_controllers.py:176-182 / 365-371`:`page.locator(sel).first.wait_for(state=...)`。**关键:整段包在 try/except 里,选择器等超时只 `log.error` 不抛错**(`:181 / 370`),页面照常返回。 |
| `wait_selector_state` | `"attached"` | 等待状态。`_validators.py:68`。取值 `attached`/`detached`/`visible`/`hidden`(`docs/dynamic.md:277-280`)。默认 `attached` = 只要 DOM 里出现即可,不保证可见。 |
| `timeout` | `30000`(ms) | **所有页面操作与等待的统一超时**。`_validators.py:78`。在 `_get_page` 里 `page.set_default_navigation_timeout(timeout)` + `page.set_default_timeout(timeout)`(`_base.py:115-116 / 303-304`)。 |
| `wait` | `0`(ms) | 一切结束后、关页返回前的额外静置。`_validators.py:71`。实现 `page.wait_for_timeout(params.wait)`(`_controllers.py:184 / 373`)。 |
| `page_action` | `None` | 导航**之后**运行的自定义交互函数,签名 `f(page)`。`_validators.py:73`。执行点 `_controllers.py:170-174 / 359-363`,**异常被吞**(只 log)。异步版必须传 async 函数。 |
| `page_setup` | `None` | 导航**之前**运行的函数(注册监听/路由)。`_validators.py:74`。执行点 `_controllers.py:157-161 / 346-350`,异常也被吞。 |
| `disable_resources` | `False` | 拦掉一批资源类型省速度/内存。`_validators.py:64`。详见第五节。 |
| `blocked_domains` | `None` | 按域名(含子域)拦请求。`_validators.py:88`。 |
| `block_ads` | `False` | 拦 ~3500 个广告/追踪域;开了会并入 `blocked_domains`(`_validators.py:135-141`)。 |
| `google_search` | `True` | 设 `Referer: https://www.google.com/`。`_validators.py:70`;逻辑 `_controllers.py:130-132`。 |
| `useragent` | `None` | 自定义 UA;不传且 headless 时自动生成真实 UA(`_base.py:446-451`)。 |
| `real_chrome` | `False` | 用系统 Chrome(`_base.py:469`)。 |
| `cdp_url` | `None` | 连远程浏览器(`_controllers.py:78-79 / 266-267`)。 |
| `proxy` / `proxy_rotator` | `None` | 静态代理 / 轮换器,二者互斥(`_validators.py:102-106`)。**注意:session 级静态 `proxy` 会被烘进持久化上下文的 `_context_options["proxy"]`(`_base.py:439`)、跨 tab 复用,并不会每请求新建 context;只有 `proxy_rotator` 或每请求 `fetch(url, proxy=...)` 覆盖(`_controllers.py:123` pop 出的 `static_proxy`)才会走每请求新建 context 分支**(见第四节)。 |
| `max_pages` | `1` | 标签页池上限,`ge=1, le=50`(`_validators.py:54, 62`)。**只有 async 生效**(见第四节)。 |
| `retries` | `3` | 失败重试次数,`ge=1, le=10`(`_validators.py:55, 90`)。见第六节告警。 |
| `retry_delay` | `1`(秒) | 重试间隔(`_validators.py:91`)。 |
| `capture_xhr` | `None` | 传正则则捕获匹配的 XHR/fetch 响应(`_validators.py:92`)。 |
| `extra_flags` | `None` | 追加浏览器启动 flag(`_validators.py:87`,合并逻辑 `_base.py:455-456`)。**内存调优的主要抓手**。 |
| `additional_args` | `{}` | 直接透传给 Playwright context,优先级最高(`_base.py:479-480`)。 |
| `executable_path` | `None` | 自定义浏览器可执行文件(`_validators.py:93`,`_base.py:472-473`)。 |
| `user_data_dir` | `""` | 用户数据目录;默认建临时目录。**仅 session 有效**(`docs/dynamic.md:87`)。 |
| `init_script` | `None` | 页面创建时注入的 JS 文件(绝对路径,`_base.py:94-95`)。 |
| `dns_over_https` | `False` | 走 Cloudflare DoH 防 DNS 泄漏(`_base.py:458-463`)。 |

> `selector_config` 是给下游 Selector/Response 解析用的,不影响抓取行为。

### solve_cloudflare(仅 StealthyFetcher,便于三档链交接)
- `solve_cloudflare` **不属于** `PlaywrightConfig`,只在 `StealthConfig`,**默认 `False`**(`_validators.py:148`)。
- 开启后若 `timeout < 60000` 会被强制抬到 `60000`(`_validators.py:154-155`)。
- DynamicFetcher 完全没有反 Cloudflare 能力;遇到 CF 拦截应升级到 StealthyFetcher。CF 挑战类型检测函数 `_detect_cloudflare`(`_base.py:544-577`),它检测 `cType: 'non-interactive'|'managed'|'interactive'` 及内嵌 turnstile。

---

## 四、浏览器 / 上下文 / 页面的生命周期(每次 fetch 新开还是复用?)

这是对 P2 池化最要命的一节。

### 4.1 Session 内:浏览器/上下文长期存活,页面(tab)每请求新建新销毁
- `start()`(`_controllers.py:72 / 261`):启动 playwright,建 **一个持久化上下文**(`launch_persistent_context`,`:88 / 276`)。这个浏览器+上下文在整个 session 生命周期内**只建一次**。
- `fetch()` 每次通过 `_page_generator`(`_base.py:182 / 369`)拿一个新 tab:
  - 标准模式:`_get_page` → `ctx.new_page()`(`_base.py:114 / 302`),`finally` 里 `page.close()` 并从池移除(`_base.py:213-215 / 402-404`)。
  - **即:每个 URL = 新开一个 tab,用完关掉;浏览器进程本身复用。**
- 文档明确说明旧版曾复用 tab,但因「无法防止上一次配置污染」已改为**每请求新建 tab**(`docs/dynamic.md:159`)。

### 4.2 `DynamicFetcher.fetch()` classmethod:每次调用新建整个浏览器!
- `chrome.py:50` `with DynamicSession(**kwargs) as session:` —— 每调一次 `DynamicFetcher.fetch()` 就 **launch 一个浏览器 → 抓一个 URL → close**。**完全不复用浏览器进程**,冷启动成本高(数百 ms~秒级,内存峰值高)。
- **P2 结论:绝对不要在循环里调 `DynamicFetcher.async_fetch()`。要显式持有一个长期 `AsyncDynamicSession`,对多个 URL 复用。**

### 4.3 max_pages 只有 async 生效(易踩坑)
- MRO:`DynamicSession(SyncSession, DynamicSessionMixin)`,其 `__init__` 调 `super().__init__()`**不传参**(`_controllers.py:70`)→ `SyncSession.__init__(max_pages=1)`(`_base.py:54`)。**同步 session 的 max_pages 恒为 1,配置里写了也没用。**
- `AsyncDynamicSession.__init__` 调 `super().__init__(max_pages=self._config.max_pages)`(`_controllers.py:259`)→ async 才真正用上 `PagePool(max_pages)`。
- async 的池限流在 `_get_page`(`_base.py:288-300`):加锁,若 `pages_count >= max_pages` 就每 50ms 轮询等位,最多等 `_max_wait_for_page=60` 秒,超时抛 `TimeoutError`(`:298-299`)。
- **P2 结论:并发抓取必须用 async 路径(`AsyncDynamicSession` / `async_fetch`),并用 `max_pages` 控制同时打开的 tab 数。**

### 4.4 代理轮换模式:每请求新建 context(更贵)
- 当 `fetch` 里解析出的**每请求 `proxy` 变量**非空时(来自 `proxy_rotator.get_proxy()` 或每请求 `fetch(url, proxy=...)` 覆盖,**不含** session 级静态 `config.proxy`——后者走持久化上下文,见第三节表 `proxy` 行),`_page_generator` 走另一分支:`self.browser.new_context(...)` 建**独立上下文**,`finally` 里 `context.close()`(`_base.py:192-207 / 379-396`)。因为浏览器不能按 tab 设代理(`docs/dynamic.md:357`)。这条路更耗资源;VPS 上尽量避免每 URL 带静态代理覆盖或用轮换器。

---

## 五、资源拦截(禁图/禁 CSS 省内存)怎么配

**抓手一:`disable_resources=True`**
- 生效点:`_get_page` 里 `if disable_resources or blocked_domains: page.route("**/*", create_intercept_handler(...))`(`_base.py:120-121 / 308-309`)。
- 拦截的资源类型集合 `EXTRA_RESOURCES`(`constants.py:2-13`):`font, image, media, beacon, object, imageset, texttrack, websocket, csp_report, stylesheet`。命中即 `route.abort()`(`navigation.py:54-56 / 81-83`)。
- 效果:图片/CSS/字体/媒体全不下载。文档称对某些站快约 25%,但**警告可能导致某些站永远加载不完**(`docs/dynamic.md:104`)。对我们只要 raw HTML(下游 trafilatura 提取正文,不需要 CSS/图片)非常合适。

**抓手二:`blocked_domains` / `block_ads`**
- 按域名(含子域,后缀链匹配 `_is_domain_blocked`,`navigation.py:22-40`)拦请求。`block_ads=True` 并入 ~3500 广告域(`_validators.py:135-141`)。可与 `disable_resources` 叠加。

> 注意:拦截 handler 是**按 page(tab)注册**的(`page.route`),不是 context 级,所以每个新 tab 都会重新挂;开销可忽略,但意味着它随 tab 生命周期走。

---

## 六、对 P2 的影响与行动建议

### 6.1 用长期 async session 池,别用 classmethod
每次 `DynamicFetcher.fetch()` 都会 launch+close 整个浏览器(4.2)。P2 应在网关进程启动时建 1 个(至多与 StealthyFetcher 合计 2-3 个)长期 `AsyncDynamicSession`,对所有 URL 复用。最小骨架:

```python
import asyncio
from scrapling.fetchers import AsyncDynamicSession

class DynamicEngine:
    def __init__(self, max_pages: int = 3):
        # 浏览器实例池上限 => 这里就是「一个浏览器 + 至多 max_pages 个 tab」
        self._session = AsyncDynamicSession(
            headless=True,
            disable_resources=True,      # 只要 HTML,禁图/CSS/字体/媒体省内存(第五节)
            network_idle=False,          # 默认;SPA 可按需 True,但注意它超时只是静默等满 timeout
            load_dom=True,               # 默认;等 domcontentloaded
            timeout=20000,               # 每操作 20s;配合我们外层硬超时
            max_pages=max_pages,         # 只有 async 生效(4.3)
            retries=1,                   # 关键:默认 3 会让超时 ×3(见 6.3)
            extra_flags=[                # VPS 省内存/防 /dev/shm 爆(见 6.2)
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        self._sem = asyncio.Semaphore(max_pages)  # 与 max_pages 对齐的信号量

    async def start(self):
        await self._session.start()      # 只 launch 一次浏览器

    async def close(self):
        await self._session.close()

    async def fetch_one(self, url: str, timeout_s: float = 30.0):
        async with self._sem:            # 双保险:信号量 + 内部 PagePool 都限流
            # 外层硬超时:内部 Playwright 超时只作用于单步操作,
            # 极端卡死时用 asyncio.wait_for 兜底
            return await asyncio.wait_for(self._session.fetch(url), timeout=timeout_s)
```

- `AsyncDynamicSession.fetch()` 返回 Scrapling `Response`(`_controllers.py:375`)。`response.status` 是 Playwright HTTP 状态,`response.body` 是 bytes,`.html_content`/`.css()` 可用——raw HTML 直接交给 P3 Purifier。
- 关闭要点:`session.close()` 会依次关 context/browser/playwright(`_base.py:237-254`),进程退出前务必调,否则残留 chromium。

### 6.2 VPS 内存:DynamicFetcher 默认启动参数**不含** `--disable-dev-shm-usage`
- `DEFAULT_ARGS`(`constants.py:24-37`)里**没有** `--disable-dev-shm-usage`;该 flag 只出现在 `STEALTH_ARGS`(`constants.py:62`),即只有 StealthyFetcher 默认带。
- 小 VPS 的 `/dev/shm` 通常只有 64MB,Chromium 会因共享内存不足崩渲染进程。**P2 必须给 DynamicFetcher 显式加 `extra_flags=["--disable-dev-shm-usage"]`**(建议再加 `--disable-gpu`)。合并逻辑见 `_base.py:453-471`,`extra_flags` 会并进 `browser_options["args"]`。
- 省内存组合:`disable_resources=True` + 小 `max_pages`(2-3) + 上述 flags。每个 tab 会起一个 renderer 进程,`max_pages` 直接决定 renderer 上限。

### 6.3 retries 默认 3 会放大超时预算(重要坑)
- `fetch()` 的重试循环 `for attempt in range(self._config.retries)`(`_controllers.py:323`),`timeout` 触发的 `TimeoutError` 会被当普通异常捕获重试(`:385-399`)。默认 `retries=3` + `retry_delay=1s` 意味着一个卡死 URL 最坏 ≈ `3×timeout + 2×1s`。
- **P2 建议设 `retries=1`**(结构体允许最小 1,`_validators.py:55`),把重试策略上移到我们自己的三档升级链里控制,避免超时预算被内部悄悄放大。

### 6.4 fetch_status 语义映射(禁止静默丢弃)
根据源码行为给出映射建议:
- **ok**:`session.fetch()` 正常返回 Response 且 `response.status` 合理、正文够长。
- **timeout**:`asyncio.wait_for` 抛 `TimeoutError`,或内部最终抛 Playwright `TimeoutError`(重试耗尽,`_controllers.py:397-399`)。→ 标 `timeout`,占位保留,交升级链决定是否升 StealthyFetcher。
- **failed**:`goto` 拿不到响应会 `raise RuntimeError(f"Failed to get response ...")`(`_controllers.py:167-168 / 356-357`);其他异常重试耗尽后 re-raise(`:398-399`)。
- **blocked**:DynamicFetcher **自身不判 Cloudflare**。需要我们拿到 HTML 后自己判(可复用 `_detect_cloudflare` 的思路,或看 `response.status` in {403,429,503} + CF 特征),判定为 blocked → 升级 StealthyFetcher。
- **坑:`wait_selector` / `network_idle` 超时是静默的**(第三节),不会体现为异常。所以「正文过薄 = 疑似 SPA」的判定要靠我们对返回 HTML 长度/结构自行检测,不能指望 Scrapling 抛错。

### 6.5 「静态薄 → 动态」判定放在我们这层
Fetcher(静态)结果正文过薄时才升 DynamicFetcher。DynamicFetcher 返回后同样要再判一次正文长度(因为 `wait_selector` 静默失败可能拿到半成品),不够再升 StealthyFetcher。这条链的每一步都要写 `engine_used` + `fetch_status` + `fetched_at`。

### 6.6 信号量与内部池的关系
`max_pages` 内部 PagePool 已有限流(4.3),但它超额时是**阻塞等待 60s 再抛 TimeoutError**,不利于我们精确控并发。**建议外层再套一个 `asyncio.Semaphore(max_pages)`**(值与 `max_pages` 对齐或略小),让排队发生在我们可控的地方,内部池只作兜底。跨引擎(Dynamic + Stealthy 合计 2-3 浏览器)的总内存上限,则用一个全局信号量或独立的两套 session 各自限流来保证。

---

## 七、待实测确认的问题(open questions)

1. **单浏览器多 tab 的真实内存曲线**:`max_pages=3` + `disable_resources=True` 下,一个 Chromium 峰值内存到底多少?renderer 进程是否随 tab 关闭立即回收?需在目标 VPS 实测(源码只保证 `page.close()`,不保证 OS 立即回收内存)。
2. **`asyncio.wait_for` 超时后 Playwright tab/进程是否泄漏**:外层 `wait_for` 取消协程时,内部 `_page_generator` 的 `finally: page.close()` 是否一定执行?若协程在 `await page.goto` 处被 cancel,需确认 page 会被正确关闭,否则要我们兜底 kill。源码未覆盖此路径。
3. **`network_idle=True` 在长轮询/WebSocket 站点上的行为**:实现里超时被吞(`_base.py:322-327`),会不会实际每次都等满 `timeout` 再返回?需实测决定是否默认关闭 network_idle。
4. **`headless=True` 下自动生成 UA 的真实值**:`__default_useragent__` 由 `generate_headers(browser_mode=True)`(`_config_tools.py:3`)在**导入时**生成,是否每进程固定?是否与 Chromium 实际版本一致?可能影响被识别为 bot。
5. **`retries=1` 是否会丢失「代理失败自动换下一个」的能力**:重试循环里才会 `proxy_rotator.get_proxy()` 换代理(`_controllers.py:325-326`)。若我们不用代理轮换,`retries=1` 无副作用;若将来用,需重新评估。
6. **sync `DynamicSession` 的 max_pages=1 是否为有意设计**:4.3 的 MRO 结论需用一次 sync session 实跑确认(理论上锁死单 tab 串行)。我们 P2 用 async,影响不大,但值得记录。
