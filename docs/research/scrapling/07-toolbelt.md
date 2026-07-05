# 07 · Toolbelt(指纹 / 代理 / 广告域 / 导航)

> 研读快照:`vendor/scrapling/`(Scrapling v0.4.10,未修改)
> 覆盖文件:`scrapling/engines/toolbelt/` 下的 `fingerprints.py`、`proxy_rotation.py`、`ad_domains.py`、`navigation.py`、`convertor.py`、`custom.py`,以及 `docs/api-reference/proxy-rotation.md`
> 结论均标注出处(文件:行号 / 函数名 / 参数名)。源码与 docs 冲突时以源码为准。

---

## 一、职责概述

Toolbelt 是 Scrapling 引擎层的“工具带”,不直接对外,是被三个 Fetcher(静态 `Fetcher`、`DynamicFetcher`、`StealthyFetcher`)复用的底层零件。分工:

| 文件 | 职责 | 谁在用 |
| --- | --- | --- |
| `fingerprints.py` | 用 browserforge 生成“像真浏览器”的 HTTP 头 / User-Agent | 静态引擎 `static.py`、浏览器引擎 `_config_tools.py` |
| `proxy_rotation.py` | 线程安全的代理轮换器 `ProxyRotator` + 代理错误识别 | **三个引擎都消费**:浏览器(`_controllers.py` / `_stealth.py`)与静态(`static.py`)均支持 |
| `ad_domains.py` | 内置 ~3500 条广告/追踪域名 frozenset | `block_ads=True` 时被 `_validators.py` 合入拦截清单(**仅浏览器**) |
| `navigation.py` | 请求拦截 handler(按资源类型/域名 abort)+ 代理字符串校验 | 浏览器引擎 `_base.py`;`construct_proxy_dict` 被所有需要代理的地方用 |
| `convertor.py` | `ResponseFactory`:把 Playwright / curl_cffi 的原始响应统一成 `Response` | 三个引擎全部 |
| `custom.py` | 统一返回类型 `Response(Selector)`、`StatusText`(状态码→短语)、`BaseFetcher` | 全局 |

一句话总结:**这一层里没有“魔法反爬”,反爬主力在 `_stealth.py`(见 05 笔记)。Toolbelt 提供的是指纹头、代理轮换、请求级广告拦截、响应归一化这几样基础设施。**

---

## 二、关键 API / 参数详解

### 2.1 `fingerprints.py` —— 生成头,不做校验

- `generate_headers(browser_mode: bool | str = False) -> Dict`(`fingerprints.py:37`)
  - 底层是 `browserforge.headers.HeaderGenerator(...).generate()`(`fingerprints.py:56`)。
  - `browser_mode=False`(默认,给静态 HTTP 用):OS 取 `("windows","macos","linux")` 全集,浏览器池 = chrome v149 + firefox ≥142 + edge ≥140(`fingerprints.py:48-55`)。目的是生成多样化的普通请求头。
  - `browser_mode="chrome"`:只生成 chrome v149 头(`chrome_version`,`fingerprints.py:17,46`)。
  - `browser_mode=True`(非 "chrome" 的真值):chromium v149 头(`chromium_version`,`fingerprints.py:16,46`)。浏览器模式下只匹配当前 OS + chrome,以免和真实启动的浏览器指纹自相矛盾(注释 `fingerprints.py:43-44`)。
  - 版本号是**硬编码**的:`chromium_version = 149` / `chrome_version = 149`(`fingerprints.py:16-17`)。注释说明因为 Playwright 不启动就拿不到浏览器版本(`fingerprints.py:15`)。→ 升级 Scrapling 时这两个数字会漂移,属于需实测的点。
- `get_os_name()`(`fingerprints.py:20`,`lru_cache(1)`):把 `platform.system()` 映射成 `"linux"/"macos"/"windows"`,未知则返回 browserforge 的全 OS 集合让它自选。我们的 VPS 是 Linux → 返回 `"linux"`。
- `__default_useragent__`(`fingerprints.py:59`):**模块导入时**就 `generate_headers(browser_mode=False)` 生成一个默认 UA 字符串,静态引擎在用户没给 UA 时回退用它(`static.py:188`)。
- **重要判断:本文件只“生成 / generate”,不做任何“校验 / validate”。** 没有 JA3/TLS 指纹、没有一致性检测、没有反检测校验逻辑。真正的 TLS 指纹伪装在别处(静态引擎靠 curl_cffi 的 `impersonate`,浏览器靠 camoufox/patchright,见 03/05 笔记)。所以问题“fingerprints 生成/校验什么”的答案是:**只生成 browser-like 的 HTTP 请求头 + UA,不做校验。**

### 2.2 `proxy_rotation.py` —— `ProxyRotator`

- 类 `ProxyRotator`(`proxy_rotation.py:39`),`__slots__` + `threading.Lock`,线程安全。
- 构造:`ProxyRotator(proxies: List[ProxyType], strategy: RotationStrategy = cyclic_rotation)`(`proxy_rotation.py:51-55`)。
  - `ProxyType = Union[str, Dict[str, str]]`(`core/_types.py:37`)。
  - str 形式:`"http://user:pass@host:8080"`;dict 形式:`{"server":..., "username":..., "password":...}`,dict **必须**含 `server` 键否则 `ValueError`(`proxy_rotation.py:78-79`)。
  - `proxies` 为空 → `ValueError`(`proxy_rotation.py:64-65`)。
- `get_proxy() -> ProxyType`(`proxy_rotation.py:88`):加锁后调用策略函数拿下一个代理并推进 index。
- 策略签名 `RotationStrategy = Callable[[List[ProxyType], int], Tuple[ProxyType, int]]`(`proxy_rotation.py:6`)。默认 `cyclic_rotation`(`proxy_rotation.py:33`)顺序轮转、到尾回环。**可自定义策略**(如随机、按失败率),只要满足签名即可。
- `is_proxy_error(error: Exception) -> bool`(`proxy_rotation.py:27`):把异常字符串小写后匹配 `_PROXY_ERROR_INDICATORS`(`proxy_rotation.py:7-15`,含 `net::err_proxy`、`connection refused`、`connection timed out`、`could not resolve proxy` 等)。浏览器控制器用它决定“这次失败是不是代理问题,要不要换代理重试”(`_controllers.py:199`)。
- **配置入口(将来给引擎挂代理时用):**
  - 单个静态代理:`proxy=` 参数,三个 Fetcher 都支持(如 `DynamicFetcher.fetch(..., proxy="http://...")`,`chrome.py:36`;静态引擎 `static.py` 也吃 `proxy`)。字符串会经 `construct_proxy_dict` 校验转成 Playwright 格式(`_validators.py:107`)。
  - 代理轮换:`proxy_rotator=ProxyRotator([...])`,是浏览器 `PlaywrightSession` TypedDict 的字段(`_types.py:79`),经 msgspec 结构 `PlaywrightConfig` 校验(`_validators.py:76`);静态引擎的 `RequestsSession` TypedDict 里也有此字段(`_types.py:38`)。因此**通过 `DynamicFetcher.fetch(..., proxy_rotator=...)` / `StealthyFetcher.fetch(...)` 的 `**kwargs` 就能传**(one-shot 签名是 `Unpack[PlaywrightSession]`,`chrome.py:11`)。
  - **约束:`proxy` 与 `proxy_rotator` 互斥**,同时给会 `ValueError`(浏览器 `_validators.py:102-106`;静态 `static.py:91-94`)。
  - **三个引擎都消费 `proxy_rotator`**:浏览器在每次 fetch 时 `self._config.proxy_rotator.get_proxy()`(`_controllers.py:136-137`;`_stealth.py:219-220`、异步 `495-496`),并为该请求新建带此代理的 context(`_base.py:482-494` `_build_context_with_proxy`);**静态引擎同样支持**——`static.py:89` 读入 `proxy_rotator`,`static.py:249-250` 在每次请求 `self._proxy_rotator.get_proxy()`,并对代理错误换代理重试(`static.py:264`)。故静态档不是“只认单个 `proxy` 字符串”。
  - 用过的代理会写进 `Response.meta={"proxy": proxy}`(`_controllers.py:191`),方便回溯。
- 文档差异:`docs/api-reference/proxy-rotation.md:13` 教从 `scrapling.fetchers import ProxyRotator`;源码里 `fetchers/__init__.py:2` 确实再导出了,`toolbelt/__init__.py:1` 也导出。两条 import 路径都可用,无冲突。

### 2.3 `ad_domains.py` —— 内置广告域清单

- `AD_DOMAINS: frozenset`(`ad_domains.py:8`),来源 Peter Lowe 广告/追踪服务器列表(`ad_domains.py:3-5`)。
- **实测条目数 = 3526 条**(`grep -c` 统计)。docs / 引擎 docstring 说“~3,500”(`chrome.py:18`),吻合,是约数。
- 触发方式:`block_ads=True`(默认 `False`,`_validators.py:89`)时,`_validators.py:135-141` 把 `AD_DOMAINS` 合入 `blocked_domains` 集合(与用户自定义的 `blocked_domains` 求并集)。
- **能否在抓取阶段就拦掉广告请求?能——但仅限浏览器引擎。** 拦截发生在 Playwright 的请求路由层:`navigation.create_intercept_handler` 里对命中域名的请求直接 `route.abort()`(`navigation.py:57-61`),请求根本不发出。因此广告/追踪资源在渲染时就被丢弃,页面更快、raw HTML 更干净。
- **静态 `Fetcher`(curl_cffi)不支持 `block_ads`**:`static.py:153` 把 `block_ads` 列入 `skip_keys` 直接忽略。原因显然——静态 HTTP 只拉一个文档,不会去加载广告子请求。
- **与 P3 Purifier(trafilatura)的关系:两者是不同层的“去噪”,互补而非替代。** `block_ads` 是**网络层**拦截(不加载广告 iframe/脚本/像素),减少浏览器渲染负担和 XHR 噪声;trafilatura 是**内容层**正文抽取(从已有 HTML 里挑正文、去导航/页脚)。开了 `block_ads` 能让交给 P3 的 HTML 更清爽,但正文抽取仍必须靠 P3。二者不冲突,建议浏览器档默认开 `block_ads`。

### 2.4 `navigation.py` —— 请求拦截 + 代理校验

- `create_intercept_handler(disable_resources, blocked_domains)` / `create_async_intercept_handler(...)`(`navigation.py:43,70`):返回 Playwright `route` handler。逻辑:
  1. `route.request.resource_type` 命中 `EXTRA_RESOURCES` → abort(`navigation.py:54`)。`EXTRA_RESOURCES`(`engines/constants.py:2`)= font/image/media/beacon/object/imageset/texttrack/websocket/csp_report/stylesheet,即 `disable_resources=True` 时砍掉的“非正文资源”。
  2. 否则若有 `blocked_domains`,用 `_is_domain_blocked` 判域名 → abort,否则 continue(`navigation.py:57-63`)。
- `_is_domain_blocked(hostname, domains)`(`navigation.py:22`):O(1) frozenset 查找 + **沿域名后缀链上溯**,例如 `tracker.ads.doubleclick.net` 会依次查 `ads.doubleclick.net`、`doubleclick.net`,所以清单里放主域即可拦子域(`navigation.py:26-39`)。
- `construct_proxy_dict(proxy_string) -> Dict`(`navigation.py:97`):把 str/dict/tuple 代理规范成 Playwright 的 `{server, username, password}`。str 只接受 scheme ∈ {http,https,socks4,socks5} 且有 hostname,否则 `ValueError`(`navigation.py:106-107`);dict 走 msgspec `ProxyDict` 校验(`navigation.py:16-20,122-128`)。**这是我们自己校验用户传入代理格式的现成工具。**
- 注意:拦截 handler 仅浏览器引擎注册(`_base.py:120-121,308-309`),静态引擎无此机制。

### 2.5 `convertor.py` —— `ResponseFactory`(响应归一化)

- 把三种来源统一成 `Response`:
  - `from_http_request(curl_response, parser_arguments, meta)`(`convertor.py:301`):curl_cffi → Response,直接取 `status_code/reason/encoding/cookies/headers/history`。
  - `from_playwright_response(...)` / `from_async_playwright_response(...)`(`convertor.py:82,229`):Playwright → Response。**关键:HTML 正文取的是 `page.content()`(渲染后 DOM),不是网络响应 body**——当 content-type 含 `html` 时走 `_get_page_content` 抓渲染后 HTML 并强制 utf-8(`convertor.py:122-125,271-273`)。这正是 P2 需要的“浏览器渲染后 HTML”。
  - `_get_page_content(page, max_retries=20)`(`convertor.py:199`):Playwright `page.content()` 在某些平台会抛错,这里做了重试(每次 500ms,最多 20 次)的 workaround。
  - `__extract_browser_encoding`(`convertor.py:28`):正则 `charset=...` 从 content-type 抠编码,Playwright 不自带。
- 对 P2:一般不用直接调 `ResponseFactory`——三个 Fetcher 已经在内部用它并返回成品 `Response`。了解它是为了知道“为什么浏览器档拿到的是渲染后 HTML、编码怎么定的”。

### 2.6 `custom.py` —— `Response` / `StatusText` / `BaseFetcher`

- `Response(Selector)`(`custom.py:28`):所有引擎的统一返回类型。带 `.status`、`.reason`、`.cookies`、`.headers`、`.request_headers`、`.history`、`.meta`、`.body`(bytes,`custom.py:83-86`),同时继承 `Selector` 具备 CSS/XPath 解析能力(P3 可能也会用到,但本期只需 `.body`/`.html_content`)。
- `StatusText.get(status_code)`(`custom.py:303`,`lru_cache`):状态码 → 标准短语的映射表(`custom.py:236-301`,含 100–511)。**给 P2 填 `fetch_status` 的 reason 字段很顺手。**
- `BaseFetcher`(`custom.py:150`):解析器全局配置(`huge_tree`、`adaptive` 等),与 P2 关系不大。

---

## 三、逐问题解答

**Q1. fingerprints 生成/校验什么?**
只**生成**、不校验。用 browserforge 生成 browser-like 的 HTTP 请求头与 User-Agent(`generate_headers`,`fingerprints.py:37`)。三种模式:默认多浏览器多 OS 混合头(静态请求用)、`"chrome"`/`True` 为浏览器模式匹配当前 OS + chrome/chromium v149。版本号硬编码 149(`fingerprints.py:16-17`)。**不含** TLS/JA3 指纹、一致性校验或反检测校验——那些在 curl_cffi impersonate(静态)和 camoufox/patchright(浏览器)里。

**Q2. proxy_rotation 的代理轮换能力与配置入口?**
`ProxyRotator(proxies, strategy=cyclic_rotation)`(`proxy_rotation.py:39`),线程安全,默认顺序轮转、支持自定义策略函数(签名 `proxy_rotation.py:6`),接受 str 或 `{server,...}` dict。配置入口:one-shot fetch 的 `proxy_rotator=` kwarg(浏览器经 `Unpack[PlaywrightSession]` TypedDict 传入,字段 `_types.py:79`;静态经 `RequestsSession`,字段 `_types.py:38`),或单个 `proxy=` 静态代理;二者互斥(浏览器 `_validators.py:102-106`、静态 `static.py:91-94`)。**三个引擎都消费 `proxy_rotator`**——浏览器与静态引擎(`static.py:89,249-250`)均会 `get_proxy()` 轮换。配套 `is_proxy_error`(`proxy_rotation.py:27`)让控制器识别代理故障并换代理重试。

**Q3. ad_domains 是干什么的、能否抓取阶段就拦广告、与 P3 关系?**
是 3526 条(≈3500)广告/追踪域的 frozenset(`ad_domains.py:8`)。`block_ads=True` 时合入 `blocked_domains`(`_validators.py:135-141`),在 Playwright 路由层 `route.abort()` 拦掉命中请求(`navigation.py:57-61`),**能在浏览器抓取阶段就拦广告**(静态 Fetcher 不支持,`static.py:153` 忽略)。与 P3:`block_ads` 是网络层拦截(少加载广告资源、页面更快更干净),trafilatura 是内容层正文抽取,二者互补,正文抽取仍归 P3。

**Q4. navigation/convertor 提供哪些实用工具?**
navigation:`construct_proxy_dict`(代理字符串/字典校验与规范化,`navigation.py:97`)、`create_intercept_handler`/异步版(按资源类型 + 域名拦截请求,`navigation.py:43,70`)、`_is_domain_blocked`(域名后缀链匹配,`navigation.py:22`)。convertor:`ResponseFactory` 把 curl_cffi / Playwright(同步/异步)响应统一成 `Response`(`convertor.py:16`),其中浏览器档取的是**渲染后 `page.content()`**(`convertor.py:122-125`),并从 content-type 抠编码。URL 处理主要是 `urlparse`;真正的 `urljoin` 在 `Response.follow`/`Selector`(`custom.py:137`),不在这两个文件。

**Q5. 哪些对 P2 直接有用、哪些留后续?**
- **P2 直接用:** ① `block_ads=True` + 可选 `blocked_domains`(浏览器两档,给 P3 减噪);② `StatusText.get()` 填 reason;③ `construct_proxy_dict` 校验用户代理格式;④ `is_proxy_error` 帮助区分 `blocked` vs `failed`;⑤ 理解 `Response.status`/`.body` 以映射 `fetch_status`。指纹头是 Fetcher 自动做的,P2 无需干预(顶多传 `useragent`)。
- **留后续:** `proxy_rotator` / 自定义轮换策略(等真正接代理再上);`ResponseFactory` 直接调用(引擎已封装,一般不碰);`Selector`/`follow` 爬虫链路(本项目不做 spider)。

---

## 四、对 P2 的影响与行动建议(可运行最小片段)

### 4.1 广告拦截:浏览器两档默认开 `block_ads`
静态 Fetcher 拦不了广告也无需拦。升级到 Dynamic/Stealthy 时开 `block_ads=True`,让交给 P3 的 HTML 更干净、渲染更快。

```python
from scrapling.fetchers import DynamicFetcher, StealthyFetcher

# 静态档:block_ads 无效,不用传
# 浏览器档:开 block_ads;还可叠加自定义域名黑名单
resp = DynamicFetcher.fetch(
    url,
    block_ads=True,                       # 合入 ~3526 条广告域,请求级 abort
    blocked_domains={"extra-tracker.com"},# 可选:与 AD_DOMAINS 求并集
    disable_resources=True,               # 顺带砍 font/image/media 等,进一步提速
    timeout=25000,
)
```

> 注意:`disable_resources=True` 会连图片/CSS 一起砍(`EXTRA_RESOURCES`,`constants.py:2`)。若下游需要图片 URL 或依赖 CSS 判定可见性,谨慎开启;纯正文抽取(P3 trafilatura)通常可以开,能显著省内存/带宽——对小 VPS 友好。

### 4.2 用 `StatusText` 统一 `fetch_status` 的 reason

```python
from scrapling.engines.toolbelt.custom import StatusText

def to_fetch_status(resp) -> tuple[str, str]:
    code = resp.status
    reason = resp.reason or StatusText.get(code)   # 兜底短语
    if code == 0 or code is None:
        return "failed", reason
    if code in (403, 429) or "cloudflare" in (reason or "").lower():
        return "blocked", reason
    if 200 <= code < 300:
        return "ok", reason
    return "failed", reason
```

### 4.3 代理格式校验复用现成工具(即使 P2 先不接代理)

```python
from scrapling.engines.toolbelt.navigation import construct_proxy_dict
# 用户传 "http://user:pass@host:8080" 时先校验,不合法直接抛 ValueError
proxy = construct_proxy_dict(user_proxy_string)  # -> {"server","username","password"}
```

### 4.4 区分 blocked / failed / 代理问题

```python
from scrapling.engines.toolbelt.proxy_rotation import is_proxy_error
try:
    resp = StealthyFetcher.fetch(url, solve_cloudflare=True, timeout=30000)
except Exception as e:
    if is_proxy_error(e):
        status = "failed"   # 代理层问题,不是站点封锁
    else:
        status = "timeout" if "timeout" in str(e).lower() else "failed"
    # 失败原样占位,禁止静默丢弃
```

### 4.5 指纹:P2 基本零配置
三个 Fetcher 会自动用 `generate_headers` 生成匹配头。P2 一般不用碰;仅当需要固定 UA(比如某站点白名单)时传 `useragent=`。**不要**自己手搓 UA 去覆盖浏览器档,否则可能和真实浏览器指纹打架(见 `fingerprints.py:43-44` 注释精神)。

---

## 五、待实测确认的问题(open questions)

1. `chromium_version=149 / chrome_version=149` 是 v0.4.10 硬编码(`fingerprints.py:16-17`)。升级 Scrapling 后是否漂移、是否与本机实际 Chromium 版本一致,需实测——不一致可能反成指纹破绽。
2. `block_ads` 的 3526 条清单会不会误伤目标站自身的统计/CDN 子域(如站点用 `*.doubleclick.net` 嵌内容),导致正文缺块?需对我们实际目标站点抽样验证。后缀链匹配(`navigation.py:22`)意味着放主域即拦全部子域,误伤面可能偏大。
3. `disable_resources=True` 砍掉 stylesheet/image 后,是否影响 trafilatura(P3)对“可见正文”的判定或图片提取需求?需与 P3 联调确认默认值。
4. 静态 `Fetcher` **确实支持 `proxy_rotator`**(源码已确认:`static.py:89` 读入、`static.py:249-250` 每请求 `get_proxy()`、`static.py:91-94` 与 `proxy`/`proxies` 互斥、`static.py:264` 代理错误换代理重试)。即三个引擎都能直接吃 `ProxyRotator`,P2/后续做静态档代理轮换无需在网关层自造循环。待实测点仅剩:静态档轮换与浏览器档共享同一 `ProxyRotator` 实例时的并发/线程安全表现(`ProxyRotator` 自带 `Lock`,理论上 OK)。
5. `is_proxy_error` 靠字符串匹配异常消息(`proxy_rotation.py:29`),对超时的分类可能不精确(`connection timed out` 会被判成代理错误)。我们区分 `timeout` vs `failed` 时不能只靠它,需结合自己的 `asyncio.wait_for` 超时信号。
6. 浏览器档 `proxy_rotator` 每次 fetch 新建 context(`_base.py:482`),在“浏览器实例池上限 2-3 + 信号量”约束下的内存/句柄开销,需压测(与 06 并发笔记联动)。
