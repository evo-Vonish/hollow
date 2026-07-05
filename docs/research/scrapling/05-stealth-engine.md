# 05 · 隐身引擎 StealthyFetcher / 反反爬

> 研究对象:Scrapling v0.4.10（未修改快照，路径 `vendor/scrapling/`）
> 覆盖源码:`scrapling/engines/_browsers/_stealth.py`、`_base.py`、`_validators.py`、`_types.py`、
> `scrapling/engines/toolbelt/fingerprints.py`、`scrapling/engines/constants.py`、
> `scrapling/fetchers/stealth_chrome.py`;官方文档 `docs/fetching/stealthy.md`
> 面向:以后维护我们 FastAPI 封装的自己。结论均标注出处;源码与文档冲突处以源码为准并已指出。

---

## 职责概述

`StealthyFetcher` 是三档升级链的**最后一档**:当静态 `Fetcher`(HTTP)和浏览器渲染 `DynamicFetcher`
都过不了反爬(尤其 Cloudflare)时启用。它是「带反检测的浏览器渲染器」。

调用层级(自外向内):

- `scrapling/fetchers/stealth_chrome.py` — `StealthyFetcher.fetch / async_fetch` 门面(classmethod)。
- `scrapling/engines/_browsers/_stealth.py` — `StealthySession`(同步)/ `AsyncStealthySession`(异步)会话管理 + `fetch` + `_cloudflare_solver`。
- `scrapling/engines/_browsers/_base.py` — `SyncSession/AsyncSession`(页面池、context 生命周期)+ `StealthySessionMixin`(stealth flag 生成、`_detect_cloudflare`)。
- `scrapling/engines/_browsers/_validators.py` — `StealthConfig`(参数校验 + 默认值)。

### 关键的、与直觉不符的事实(先记住这几条)

1. **底层不是 Camoufox / 不是 Firefox。** v0.4.10 的 StealthyFetcher 底层是
   **Patchright 驱动的 Chromium**。`_stealth.py:8-9` 导入 `from patchright.sync_api import sync_playwright`
   / `from patchright.async_api import async_playwright`,`start()` 里用
   `self.playwright.chromium.launch_persistent_context(...)`(`_stealth.py:93`、`:368`)。
   文档 `stealthy.md:259-261` 明确写:「This fetcher used a custom version of Camoufox as an engine
   **before version 0.3.13**, which was replaced by **patchright**」。
   → 任务背景里「camoufox?firefox-based?」的猜测在本版本**不成立**。Camoufox 仅作为可选的、需自己
   继承 `start()` 重写的遗留方案存在(`stealthy.md:259-337`),默认路径完全不碰它。
2. **`humanize`、`geoip`、`os`(OS 随机化)、`firefox_user_prefs` 这些 Camoufox 时代的参数在本版本源码里已完全不存在。**
   全仓 `grep humanize|geoip|os_random|firefox_user_prefs|camoufox scrapling/` 命中 0 行(仅出现在 docs 的遗留示例中)。
   → 我们封装**不要**去传这些参数,传了会被 msgspec 校验拒绝(`StealthConfig` 是 `Struct`,未知字段报错)。
3. **`solve_cloudflare` 默认 `False`。** 见 `_validators.py:148`
   (`class StealthConfig ... solve_cloudflare: bool = False`)。默认情况下 StealthyFetcher **不会**主动解 Cloudflare,
   只会把当前页面(可能就是 challenge 页)原样返回。要过 CF **必须显式 `solve_cloudflare=True`**。
4. **一次性门面 `StealthyFetcher.fetch()` 每次调用都会开一个全新的浏览器再关掉**
   (`stealth_chrome.py:62-63` `with StealthySession(**kwargs) as engine: return engine.fetch(url)`)。
   对我们的 VPS 是灾难级开销 → 见下文「对 P2 的影响」,我们要自己持有长驻的 `AsyncStealthySession`。

---

## 关键 API / 参数详解

### 1. StealthyFetcher vs DynamicFetcher 的实际区别

两者都是 Chromium + Playwright API,但引擎和配置不同:

| 维度 | DynamicFetcher | StealthyFetcher |
|---|---|---|
| Playwright 驱动 | **原版 playwright**(`_controllers.py:4-11`) | **patchright**(`_stealth.py:8-9`) |
| 浏览器 | chromium/chrome | chromium/chrome(同,`_base.py:469` `channel`) |
| 启动 flags | `DEFAULT_ARGS` 12 条(`constants.py`) | `DEFAULT_ARGS + STEALTH_ARGS` 约 60+ 条(`_base.py:526`) |
| context 选项 | 基本(`color_scheme=dark, device_scale_factor=2`,`_base.py:420`) | 追加 `is_mobile=False, has_touch=False, service_workers=allow, ignore_https_errors=True, screen/viewport=1920x1080, permissions=[geolocation,notifications]`(`_base.py:508-519`) |
| 反检测专属参数 | 无 | `allow_webgl / hide_canvas / block_webrtc / solve_cloudflare`(`_types.py:117-125`、`_validators.py:144-148`) |
| Cloudflare solver | 无 | `_cloudflare_solver()`(`_stealth.py:107 / 382`) |

**Patchright 才是核心反检测层。** 它是 playwright 的补丁分支(Kaliiiiiiiiii-Vinyzu/patchright),消除
`Runtime.enable` 等 CDP 泄漏、隐藏 Playwright 指纹。这对应文档 `stealthy.md:31-32` 的「bypasses CDP runtime
leaks」「isolates JS execution, removes many Playwright fingerprints」。DynamicFetcher 用原版 playwright,
所以带可被检测的 CDP 特征。

### 2. 反检测手段逐个说明

所有反检测最终落成 **Chromium 启动 flags + context 选项 + patchright 补丁**,不是运行时 JS 注入(除 canvas 噪声是 flag)。

- **指纹伪装 / UA 生成**(`fingerprints.py`):headless 且未显式给 `useragent` 时,用 browserforge 生成一个
  与浏览器版本(`chromium_version = 149`,`fingerprints.py:16`)匹配的真实 UA(`_base.py:446-451`)。
  headful 模式下用浏览器自带 UA。locale/timezone 通过 context 的 `locale`/`timezone_id` 设置(`_base.py:438-443`)。
- **`solve_cloudflare`**(默认 `False`,`_validators.py:148`):启用后在导航完成后调用 `_cloudflare_solver`
  自动解 Turnstile/Interstitial。**副作用:一旦为 True,`timeout` 若 < 60000 会被强制抬到 60000**
  (`_validators.py:150-155` `StealthConfig.__post_init__`)。
- **`humanize`**:本版本**无此参数**(Camoufox 遗留)。人类化行为改由 `_cloudflare_solver` 内部用
  `randint` 生成点击坐标与 `delay`(`_stealth.py:156-159`)实现,不可配置。
- **`geoip`**:本版本**无此参数**(Camoufox 遗留)。代理下防 DNS 泄漏改用 `dns_over_https`
  (`_base.py:458-463`,追加 `--dns-over-https-templates=...cloudflare-dns.com/dns-query` flag)。
- **`block_webrtc`**(默认 `False`,`_validators.py:147`):防本地 IP 泄漏。实现是追加 Chromium flag
  `--webrtc-ip-handling-policy=disable_non_proxied_udp` + `--force-webrtc-ip-handling-policy`(`_base.py:528-532`)。
- **`hide_canvas`**(默认 `False`,`_validators.py:146`):canvas 加噪防指纹。实现是 flag
  `--fingerprinting-canvas-image-data-noise`(`_base.py:539-540`)。
- **`allow_webgl`**(默认 `True`,`_validators.py:145`):默认开。关掉会追加
  `--disable-webgl / --disable-webgl-image-chromium / --disable-webgl2`(`_base.py:533-538`)。
  **文档和源码都建议保持开启**(很多 WAF 现在检测 WebGL 是否可用,`stealthy.md:69`)。
- **`os` 随机化**:本版本**无此参数**。OS 由运行机器决定(`fingerprints.py:13` `platform_system()`),
  UA/headers 匹配当前 OS。→ 在 Linux VPS 上生成的就是 Linux 指纹。
- **headless 检测规避**:`--start-maximized`、`--window-position=0,0` 等在 `STEALTH_ARGS`
  (`constants.py`),`--disable-blink-features=AutomationControlled` 也在其中。
- **`HARMFUL_ARGS`**:`ignore_default_args` 掉 playwright 默认会加的 `--enable-automation` 等暴露自动化的 flag(`constants.py`、`_base.py:423`)。

### 3. `_cloudflare_solver` 与「是否解出」的判定(对我们 `blocked` 至关重要)

检测(`StealthySessionMixin._detect_cloudflare`,`_base.py:544-577`):在页面 HTML 里找
`cType: 'non-interactive'` / `'managed'` / `'interactive'` 字面量,或内嵌 turnstile 脚本
`script[src*="challenges.cloudflare.com/turnstile/v"]` → 返回 `"non-interactive"/"managed"/"interactive"/"embedded"`,都没有则返回 `None`。

解题(`_stealth.py:107-182` 同步 / `:382-457` 异步,均标了 `# pragma: no cover`,即**官方没有单测覆盖**):

1. 等 networkidle(5s),`_detect_cloudflare` 判类型;无挑战直接返回。
2. `non-interactive`:轮询直到页面标题不再是 `<title>Just a moment...</title>`。
3. 其他类型:定位 turnstile iframe/box,用 `page.mouse.click` 带随机延迟点击验证框,再轮询
   `Just a moment...` 是否消失(最多 ~10s / 100 次),仍在则**递归重试**自己。

**「解出」的唯一信号 = 页面 HTML 里不再含 `<title>Just a moment...</title>`**
(`_stealth.py:177 / 452`)。注意 solver 靠 `log.info("Cloudflare captcha is solved")` 记录,
**函数本身返回 `None`,不返回布尔**。solver 内部所有超时都是「继续」而非「抛异常」
(如 `_stealth.py:166-168`「didn't disappear after 10s, continuing...」)→ **即使没真解出,fetch 也可能正常返回一个仍是 challenge 的页面**。

→ 因此我们**不能只信任 fetch 成功返回**,必须自己在返回的 `Response` 上二次判定是否仍被拦(见 P2 行动)。

### 4. `Response` 状态从哪来

`ResponseFactory.from_playwright_response`(`toolbelt/convertor.py:82` / 异步 `:229`)用**主文档导航响应**
(`final_response`,由 `_base.py:165-180` 的 response handler 捕获 `resource_type=="document"` 且
`is_navigation_request()` 的响应)填 `status` / `reason`。Cloudflare challenge 页通常返回
**HTTP 403 或 503**。没有任何字段叫 `blocked` —— 「blocked」需要我们自己派生。

### 5. 页面池与并发(`_base.py`)

- 同步 `StealthySession.__init__` 调 `super().__init__()`(`_stealth.py:74`)→ `SyncSession` 默认
  `max_pages=1`(`_base.py:54`)。**即使传 `max_pages=3`,同步会话池仍是 1**(同步不消费该值)。
- 异步 `AsyncStealthySession.__init__` 传 `max_pages=self._config.max_pages`(`_stealth.py:350`)→ **只有异步会话尊重 `max_pages`**。
- 池是**同一个浏览器进程里的多个 tab**,不是多个浏览器(`stealthy.md:243-248`)。`max_pages=3` = 1 个 Chromium + 最多 3 个 tab。
- 异步取页:池满时每 0.05s 轮询,最长 `self._max_wait_for_page = 60`s(`_base.py:227, 290-300`),超时抛 `TimeoutError`。
- `max_pages` 校验范围 `1..50`(`_validators.py:54` `PagesCount = Annotated[int, Meta(ge=1, le=50)]`)。

### 6. 重试

`fetch` 外层 `for attempt in range(self._config.retries)`(`_stealth.py:217 / 493`),`retries` 默认 **3**
(`_validators.py:90`),`retry_delay` 默认 **1**s(`:91`)。任何异常(含 goto 超时、`_wait_for_page_stability`
的 load 超时)都会触发重试,`retries` 用尽后**重新抛出**。代理错误经 `is_proxy_error` 单独打日志。
→ **单次 `fetch()` 最坏耗时 ≈ retries × (timeout + retry_delay)**。solve_cloudflare 下 timeout≥60s,3 次重试可达约 180s+。

---

## 逐问题解答

### Q1 · StealthyFetcher 底层与 DynamicFetcher 的区别

- 底层是 **Patchright + Chromium**(不是 Camoufox/Firefox)。出处:`_stealth.py:8-9, 93`;`stealthy.md:259-261`。
- 与 DynamicFetcher(原版 playwright + chromium)相比,多了:patchright 反 CDP 泄漏补丁、`STEALTH_ARGS`
  60+ flag、stealth context 选项(`_base.py:508-519`)、`allow_webgl/hide_canvas/block_webrtc/solve_cloudflare`
  四个专属参数、`_cloudflare_solver`。**文档 docs `stealthy.md:3` 说「very similar…main difference is anti-bot」是准确的,但没点明驱动从 playwright 换成 patchright 这条底层差异——以源码为准。**

### Q2 · 反检测手段

见上文「2. 反检测手段逐个说明」。要点:`humanize/geoip/os` **在本版本不存在**;有效的是
`solve_cloudflare / block_webrtc / hide_canvas / allow_webgl` 四个 + `dns_over_https` + patchright 本体。

### Q3 · 如何判断 Cloudflare 是否被解决(对应 `fetch_status=blocked`)

Scrapling **不直接返回**「是否被拦」。内部信号是页面 HTML 是否还含 `<title>Just a moment...</title>`
(`_stealth.py:177`)以及 `_detect_cloudflare` 是否命中(`_base.py:544-577`)。我们的 `blocked` 判定应组合:

1. `response.status` ∈ {403, 429, 503}(challenge 常见码);**或**
2. `StealthySessionMixin._detect_cloudflare(response.html_content)` 返回非 `None`;**或**
3. 页面标题/正文含 `Just a moment...` / `Verifying you are human` / `Checking your browser`。

命中任一 → `fetch_status=blocked`。注意:`solve_cloudflare=True` 且真解出时,status 应回到 200 且上述标记消失。

### Q4 · 单实例内存 / 启动开销

- 底层是完整 **Chromium**,以 `launch_persistent_context`(带临时 user_data_dir)启动(`_stealth.py:93`)。
  一个 headless Chromium 进程 + tab,常驻约数百 MB(经验值 ~150–400MB,**源码未给具体数字**,进 open_questions)。
- 文档明确 Camoufox 曾有「high memory issues」,换 patchright 部分是为此(`stealthy.md:261`)——即便如此仍是重量级浏览器。
- **一次性门面 `StealthyFetcher.fetch()` 每 URL 开关一次浏览器**(`stealth_chrome.py:62`),启动成本(进程 fork + profile 目录 + flag 应用)叠加在每次请求上 → 这是「数百 MB × 每实例」和「池上限 2-3」约束的根源:VPS 上并行 3 个以上 Chromium 就可能 OOM。

### Q5 · 常见失败模式与超时行为

- `page.goto` 返回 `None` → 抛 `RuntimeError("Failed to get response for {url}")`(`_stealth.py:250-251`),进重试。
- 导航/等待超时:`page.set_default_navigation_timeout/set_default_timeout`(`_base.py:303-304`,单位 ms),
  `goto` 或 `_wait_for_page_stability` 的 `wait_for_load_state("load")` 超时抛异常 → 进重试;`retries` 用尽后抛出。
- `network_idle` 等待**不会**致命:`_wait_for_networkidle` 吞掉所有异常(`_base.py:137-140` / `324-327`)。
- 异步池满等页超 60s → `TimeoutError`(`_base.py:298-300`)。
- `wait_selector`、`page_action`、`page_setup` 内部异常只 `log.error` **不中断**(`_stealth.py:258-270`)——
  所以 `wait_selector` 没等到目标元素**不会**报错,可能返回不完整页面。
- solver 内所有超时都「continue」而非抛错(见 Q3)→ **可能返回一个仍被拦的页面且 fetch 视为成功**。
- **Scrapling 自身没有「超时杀进程」机制**:超时只是 Playwright 操作级异常 + 重试。要满足我们「超时杀进程回收」硬约束,必须在封装层用 `asyncio.wait_for` 包住,并在超时后主动 `await session.close()`。

---

## 对 P2 的影响与行动建议

### 决策 1:不要用一次性门面,自己持有长驻 `AsyncStealthySession`

`StealthyFetcher.fetch/async_fetch`(classmethod)每次都 `with StealthySession(...)` 新开+关闭浏览器
(`stealth_chrome.py:62, 114`),在 VPS 上不可接受。改为在网关启动时建一个(或少量)长驻异步会话,
用 `max_pages` 控制同一浏览器内的并发 tab,外层再套我们自己的 `asyncio.Semaphore`。

```python
# 网关内单例:一个 Chromium 进程,最多 3 个并发 tab
from scrapling.fetchers import AsyncStealthySession

class StealthPool:
    def __init__(self, max_pages: int = 3):
        # solve_cloudflare 会把 timeout 抬到 >=60000;这里显式给足
        self._session = AsyncStealthySession(
            headless=True,
            max_pages=max_pages,        # 只有异步会话尊重此值(_stealth.py:350)
            block_webrtc=True,
            allow_webgl=True,           # 保持开启,WAF 会查(stealthy.md:69)
            disable_resources=True,     # 省内存/带宽;注意个别站会加载不完
            timeout=60_000,
            solve_cloudflare=True,      # 默认 False,必须显式开(_validators.py:148)
            retries=1,                  # 默认 3;我们在外层自控重试与总超时预算
        )
        # 我们的信号量:与 max_pages 对齐,别超过它,否则会在池里排队最长 60s
        self._sem = asyncio.Semaphore(max_pages)

    async def start(self):
        await self._session.start()     # 显式启动,失败会 raise(_stealth.py:352-380)

    async def close(self):
        await self._session.close()     # 关 context/browser/playwright(_base.py:237-254)

    async def fetch_one(self, url: str, per_url_timeout: float = 75.0):
        async with self._sem:
            # 硬超时兜底:Scrapling 自身不杀进程(_base.py 只有操作级超时+重试)
            return await asyncio.wait_for(self._session.fetch(url), timeout=per_url_timeout)
```

要点:
- `retries=1` 是刻意的。默认 3 会让单 URL 最坏耗时 ≈ `3 × (60s + 1s)` ≈ 180s+,拖垮我们的 per-URL 预算。
- `per_url_timeout` 要**大于** `timeout`(solve_cloudflare 下 ≥60s),否则 `wait_for` 永远先于 goto 触发。
- **信号量的 permits 必须 ≤ `max_pages`**;超了会在池里排队(每 0.05s 轮询,最长 60s 后 `TimeoutError`),白等。

### 决策 2:`fetch_status` 派生逻辑(封装层实现,Scrapling 不提供)

```python
from scrapling.engines._browsers._base import StealthySessionMixin

_BLOCK_MARKERS = ("Just a moment...", "Verifying you are human", "Checking your browser")

def classify(resp) -> str:
    html = resp.html_content or ""             # Response 继承 Selector
    if StealthySessionMixin._detect_cloudflare(html) is not None:   # _base.py:544
        return "blocked"
    if resp.status in (403, 429, 503):
        return "blocked"
    if any(m in html for m in _BLOCK_MARKERS):
        return "blocked"
    if len((resp.get_all_text(strip=True) or "")) < MIN_BODY_LEN:   # 正文过薄阈值,P2 自定
        return "failed"        # 交给升级链或标记失败
    return "ok"
```

- `_detect_cloudflare` 是**静态方法**,可直接 import 复用,不必自己写正则(`_base.py:544-577`)。
- `asyncio.TimeoutError`(来自我们的 `wait_for`)→ `fetch_status="timeout"`,并**在此之后 `close()` 掉整个 session 再重建**
  ,以满足「超时杀进程回收」——因为一个 tab 卡死可能拖累同浏览器其他 tab。
- `fetch()` 抛出的其它异常(重试耗尽的 `RuntimeError` 等)→ `fetch_status="failed"`,原样占位、不静默丢弃。

### 决策 3:升级链里 StealthyFetcher 的触发条件

三档链中,只有当 Dynamic 档返回 `blocked`(用上面 `classify` 判定命中 CF 标记 / 403·503)时才升到 Stealthy,
且**必须带 `solve_cloudflare=True`**(默认 False 不会解)。Stealthy 档仍返回 `blocked` → 最终 `fetch_status=blocked`,不再升级。

### 决策 4:内存与池上限

- 每个 `AsyncStealthySession` = 1 个 Chromium 进程(数百 MB)。**优先「1 个 session + max_pages=2~3」而不是多个 session**
  ,因为 tab 共享进程内存,比多进程省得多(`stealthy.md:252-257` "Memory efficiency")。
- 若确需多 session(如隔离不同代理),session 数 × 单进程内存要控制在 VPS 余量内,总并发浏览器 ≤ 2~3。
- 启动可加 `disable_resources=True`(丢 font/image/media 等,`constants.py EXTRA_RESOURCES`)省内存,但注意个别站会因此永远加载不完(`stealthy.md:86`)。
- 已在 `STEALTH_ARGS` 里的 `--disable-dev-shm-usage`(`constants.py`)对小 `/dev/shm` 的 VPS 是好事,无需再配。

### 决策 5:参数卫生

只传 `StealthConfig` 认识的字段(`_validators.py:59-155` 全量列表)。**不要**传 `humanize/geoip/os/firefox_user_prefs`
——msgspec `convert` 遇未知/类型不符字段抛 `ValidationError`,被包成 `TypeError("Invalid argument type: ...")`(`_validators.py:251-252`)。

---

## 待实测确认的问题(open_questions)

1. 单个 headless patchright-Chromium 会话在我们 Linux VPS 上的**实际常驻内存**(RSS)与启动耗时具体是多少?
   源码/文档均未给数字,「数百 MB」需实测(建议 max_pages=1/2/3 各测,观察是否线性)。
2. `_cloudflare_solver` 全部 `# pragma: no cover`(无官方测试),**对当前(2026)Cloudflare Turnstile 的真实成功率**未知;
   需用真实 CF 站点(如 `nopecha.com/demo/cloudflare`)实测,并确认失败时是否稳定表现为「返回仍含 Just a moment 的页面」。
3. `solve_cloudflare` 经**逐请求 override**(`validate_fetch`,`_validators.py:178-215`)传入时,`timeout` 的自动抬升
   (`StealthConfig.__post_init__` 只在构造 StealthConfig 时生效)是否会漏掉——即 per-fetch 只传 `solve_cloudflare=True`
   而没传 `timeout` 时,实际用的是会话级 timeout 还是被抬到 60000?需实测。建议**在会话级就把 timeout 设 ≥60000**规避此坑。
4. `disable_resources=True` 在我们目标站集合上是否会触发「永远加载不完」→ 命中我们的 per-URL 超时?需按站点白/黑名单实测。
5. 我们用 `asyncio.wait_for` 强杀后调用 `session.close()` 重建,对同 session 其它并发 tab 的影响(是否会误伤正在进行的请求)——
   需实测决定「杀单 tab」还是「杀整个 session」的粒度;必要时按 max_pages=1 换取隔离性。
6. patchright 与本仓依赖的 playwright 版本组合是否已随 `pip install scrapling` 装好(patchright 需自己的
   `patchright install chromium`)?部署脚本需确认 `patchright` 浏览器已安装,否则 `start()` 会失败。
