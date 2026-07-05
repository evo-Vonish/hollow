# Scrapling 三档 Fetcher 公共 API 研究笔记

> 子系统:三档 Fetcher 公共 API(P2 入口)
> 版本:Scrapling v0.4.10(`vendor/scrapling/`,未修改)
> 结论一律以源码为准,doc 冲突处已标注。所有行号相对 `vendor/scrapling/`。

---

## 1. 职责概述

Scrapling 对外暴露三个「档位」的抓取类,全部返回统一的 `Response` 对象。P2 要用的就是这三个:

| 档位 | 类名 | 引擎 | 方法 | timeout 单位 | 内存 |
|------|------|------|------|------|------|
| 静态 HTTP | `Fetcher` / `AsyncFetcher` | curl_cffi | `get/post/put/delete` | **秒** | 极低 |
| 浏览器渲染 | `DynamicFetcher` | Playwright(Chromium) | `fetch/async_fetch` | **毫秒** | 中(数百 MB) |
| 隐身模式 | `StealthyFetcher` | patchright(反检测 Chromium) | `fetch/async_fetch` | **毫秒** | 中(数百 MB) |

关键提醒:**Scrapling 没有内置任何"三档自动升级"逻辑**。三个类互相独立,升级链必须我们在网关里自己编排(详见第 6 节)。

---

## 2. 导入路径与构造方式

### 2.1 导入

两条路径都可用,都是懒加载(`__getattr__`):

```python
# 推荐(官方 docs/fetching/choosing.md:34 用法)
from scrapling.fetchers import Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher
# 也可从顶层包
from scrapling import Fetcher, AsyncFetcher, DynamicFetcher, StealthyFetcher
```

出处:`scrapling/fetchers/__init__.py:11-34`(`_LAZY_IMPORTS` + `__all__`);顶层 `scrapling/__init__.py:14-24`。

### 2.2 别名与 Session 类(重要)

- `PlayWrightFetcher = DynamicFetcher`,向后兼容别名,见 `fetchers/chrome.py:97`。
- 除三个 Fetcher 外,`fetchers/__init__.py:23-34` 还导出了 **Session 类**:
  - `FetcherSession`(HTTP 会话,可复用 curl 连接)
  - `DynamicSession` / `AsyncDynamicSession`(浏览器会话,带 page pooling)
  - `StealthySession` / `AsyncStealthySession`(隐身浏览器会话,带 page pooling)
- **P2 强相关**:两个浏览器档 Fetcher 的 `fetch()` classmethod 内部是 `with <Session>(**kwargs) as session: return session.fetch(url)`(`DynamicFetcher` 用 `DynamicSession`,`chrome.py:50-51`;`StealthyFetcher` 用 `StealthySession`(变量名 `engine`),`stealth_chrome.py:62-63`),即**每调用一次就新建并销毁一个浏览器**。要做浏览器实例池(上限 2-3、复用),应直接用 `AsyncDynamicSession` / `AsyncStealthySession` 这些 Session 类,而不是反复调 classmethod。见第 6 节。

### 2.3 构造方式

三个 Fetcher **都不需要实例化**,直接调用 classmethod:

```python
Fetcher.get(url)            # @classmethod,requests.py:31-33
DynamicFetcher.fetch(url)   # @classmethod,chrome.py:10-11
StealthyFetcher.fetch(url)  # @classmethod,stealth_chrome.py:13-14
```

可选:调用前用 `Fetcher.configure(...)` 或类属性设置**解析器(Selector)**配置(`adaptive`、`keep_comments` 等),这些会 merge 进 `selector_config`。见 `choosing.md:40-54`。对 P2(只要 raw HTML 交给 P3 trafilatura)基本用不到。

---

## 3. 方法签名与全部参数(含默认值)

所有 Fetcher 方法签名形式都是 `method(cls, url: str, **kwargs: Unpack[TypedDict])` —— 参数不是显式列出的,而是通过 `Unpack[TypedDict]` 声明,**真实默认值定义在下游 msgspec 校验模型里**,不在方法签名上。下面给的是实际生效的默认值。

### 3.1 Fetcher / AsyncFetcher(静态 HTTP)

方法:`get / post / put / delete`(**没有 `fetch`**)。
- `Fetcher.get` 返回 `Response`(`requests.py:32`)
- `AsyncFetcher.get` 返回 `Awaitable[Response]`,需 `await`(`requests.py:52`)

参数默认值来自 `engines/static.py` 的 `_ConfigurationLogic.__init__`(`static.py:72-89`)与 `FetcherSession.__init__`(`static.py:659-696`):

| 参数 | 默认 | 出处 |
|------|------|------|
| `timeout` | **30(秒!)** | `static.py:78` `kwargs.get("timeout", 30)` |
| `headers` | `{}` | `static.py:79` |
| `impersonate` | `"chrome"`(最新 Chrome 指纹) | `static.py:73` |
| `stealthy_headers` | `True`(自动加真实浏览器头 + Google referer) | `static.py:74` |
| `proxy` | `None`(格式 `http://user:pass@host:port`) | `static.py:76` |
| `proxies` | `{}` | `static.py:75` |
| `retries` | `3` | `static.py:80` |
| `retry_delay` | `1` 秒 | `static.py:81` |
| `follow_redirects` | `"safe"`(挡内网 IP,防 SSRF) | `static.py:82` |
| `max_redirects` | `30`(-1 无限) | `static.py:83` |
| `verify` | `True` | `static.py:84` |
| `http3` | `False` | `static.py:86` |
| `params` / `cookies` / `auth` | 见 GET 专属 TypedDict | `_types.py:51-55` |
| `data` / `json` | 仅 POST/PUT/DELETE | `_types.py:58-61` |

**没有 `wait_selector`、`network_idle`、`page_action` 这类参数** —— 那是浏览器档专属。
注意 `timeout` 单位是**秒**,和下面两档的毫秒完全不同,封装时极易踩坑。

### 3.2 DynamicFetcher(浏览器渲染)

方法:`fetch`(同步,`chrome.py:11`)/ `async_fetch`(异步,`chrome.py:54`),都返回 `Response`。
参数 TypedDict = `PlaywrightSession`(`_types.py:64-97`),**真实默认值在 `PlaywrightConfig`(msgspec Struct,`_validators.py:59-94`)**:

| 参数 | 默认 | 出处 `_validators.py` |
|------|------|------|
| `timeout` | **30000(毫秒)** | `:78` |
| `headless` | `True` | `:63` |
| `network_idle` | `False` | `:65` |
| `load_dom` | `True`(等 JS 全部执行) | `:66` |
| `wait` | `0`(返回前额外等待毫秒) | `:71` |
| `wait_selector` | `None` | `:67` |
| `wait_selector_state` | `"attached"` | `:68` |
| `google_search` | `True`(设 Google referer) | `:70` |
| `page_action` | `None`(导航后跑的自动化函数,签名 `(page)`) | `:73` |
| `page_setup` | `None`(导航前跑的函数) | `:74` |
| `proxy` | `None`(str / dict{server,username,password} / tuple) | `:75` |
| `extra_headers` | `None` | `:77` |
| `useragent` | `None`(不给则自动生成真实 UA) | `:86` |
| `disable_resources` | `False`(丢字体/图片等提速) | `:64` |
| `block_ads` | `False` | `:89` |
| `blocked_domains` | `None` | `:88` |
| `real_chrome` | `False` | `:84` |
| `cdp_url` | `None`(连已有浏览器) | `:85` |
| `cookies` | `[]` | `:69` |
| `locale` | `None` | `:83` |
| `timezone_id` | `""` | `:72` |
| `init_script` | `None`(须绝对路径 JS 文件) | `:79` |
| `user_data_dir` | `""`(空则建临时目录) | `:80` |
| `retries` | `3` | `:90` |
| `retry_delay` | `1` | `:91` |
| `max_pages` | `1`(page pool 上限) | `:62` |
| `selector_config` | `{}` | `:81` |
| `additional_args` | `{}`(直传 Playwright context) | `:82` |
| `extra_flags` | `None`(额外浏览器启动 flag) | `:87` |
| `dns_over_https` | `False` | `:94` |

参数说明字符串见 `chrome.py:12-40`(fetch 的 docstring)。

### 3.3 StealthyFetcher(隐身模式)

方法:`fetch` / `async_fetch`(`stealth_chrome.py:13`、`:65`),返回 `Response`。
参数 TypedDict = `StealthSession`(`_types.py:117-121`),**默认值在 `StealthConfig`(继承 `PlaywrightConfig`,`_validators.py:144-155`)**。

除继承 3.2 的全部字段外,隐身档**额外**有:

| 参数 | 默认 | 出处 |
|------|------|------|
| `solve_cloudflare` | **`False`**(解 Cloudflare Turnstile/Interstitial) | `_validators.py:148` |
| `allow_webgl` | `True`(关掉会被 WAF 抓,别关) | `_validators.py:145` |
| `hide_canvas` | `False`(canvas 加噪防指纹) | `_validators.py:146` |
| `block_webrtc` | `False`(防本地 IP 泄漏) | `_validators.py:147` |

上表四项(`solve_cloudflare` / `allow_webgl` / `hide_canvas` / `block_webrtc`)才是 `StealthConfig` 真正**新增**的字段(`_validators.py:145-148`)。注意 `user_data_dir` 并非隐身档专属——它是 `PlaywrightConfig` 的字段(`_validators.py:80`,默认 `""`,空则建临时目录),`DynamicFetcher` 同样接受;只是隐身档的 docstring(`stealth_chrome.py:48`)把它列了出来、浏览器档 docstring 没列。

**关键联动**:若 `solve_cloudflare=True` 且 `timeout < 60000`,`StealthConfig.__post_init__` 会**强制把 timeout 抬到 60000ms**(`_validators.py:150-155`)。封装时若靠短 timeout 兜底,开 Cloudflare 求解会失效,需另加外层硬超时。

参数说明字符串见 `stealth_chrome.py:15-52`。
注意 `DynamicFetcher` 有的 `block_ads`、`dns_over_https` 在 `StealthConfig` 里也继承了(`PlaywrightConfig` 字段),但隐身档 docstring 没逐条列全,以 `StealthConfig` 结构为准。

---

## 4. 返回对象是否统一 —— 是,完全统一

三档全部返回 `scrapling.engines.toolbelt.custom.Response`(三个 fetcher 文件都 `from ...custom import Response`)。
`Response` 定义:`toolbelt/custom.py:28` `class Response(Selector)` —— **它是 Selector 的子类**,即返回值既是响应又是可 CSS/XPath 查询的解析对象。

常用属性(`custom.py:42-86`、`choosing.md:71-81`):

| 属性 | 含义 | 出处 |
|------|------|------|
| `.status` | HTTP 状态码 | `custom.py:61` |
| `.reason` | 状态消息 | `custom.py:62` |
| `.headers` | 响应头 dict | `custom.py:64` |
| `.request_headers` | 请求头 | `custom.py:65` |
| `.cookies` | 响应 cookies | `custom.py:63` |
| `.history` | 重定向历史 list | `custom.py:66` |
| `.body` | **原始 body(bytes)** | `custom.py:84-86` |
| `.html_content` | HTML 字符串 | `parser.py:345`(Selector 属性) |
| `.get_all_text()` | 提取全部可见文本 | `parser.py:279` |
| `.meta` | 元数据 dict(如 proxy) | `custom.py:79` |
| `.encoding` | 编码 | — |

**P3 交接**:raw HTML 用 `response.body`(bytes)或 `response.html_content`(str)。判断"正文过薄疑似 SPA"可用 `len(response.get_all_text())` 或 `len(response.body)`。自 v0.4 起 `body` 恒为 bytes(`choosing.md:86`)。

---

## 5. 官方"何时用哪档"建议

出处:`docs/fetching/choosing.md:17-27` 对比表。归纳:

- **Fetcher**:纯 HTTP 请求就能拿到内容的基础站点。最快、内存最省、无浏览器、无 JS。反爬能力 ⭐⭐。
- **DynamicFetcher**:JS 动态渲染站(SPA)、轻量自动化、中小级别防护。有浏览器、能跑 JS。stealth ⭐⭐⭐。
- **StealthyFetcher**:动态站 + 复杂防护(Cloudflare 等)。隐身能力 ⭐⭐⭐⭐⭐,反爬选项最全。内存与 Dynamic 同级(数百 MB)。

这条建议正好对应 P2 的三档升级链:Fetcher → DynamicFetcher → StealthyFetcher。

---

## 6. 有没有现成自动升级? —— 没有,必须自己编排

逐个读完三个 fetcher 源码:`Fetcher`(requests.py)、`DynamicFetcher`(chrome.py)、`StealthyFetcher`(stealth_chrome.py)彼此**零耦合**,没有任何"失败后自动换更高档"的逻辑。`solve_cloudflare` 只是隐身档内部解 CF 挑战的开关,不是跨档升级。

因此 P2 的 auto 三档链**完全由我们的网关实现**。

### 对 P2 的影响与行动建议

**(a) 浏览器实例池:别用 classmethod,用 Async*Session**
classmethod `fetch` 每次 `with DynamicSession(...)` 新建+销毁浏览器(`chrome.py:50`)。VPS 上限 2-3 个浏览器实例的约束,应:
- 用 `AsyncDynamicSession` / `AsyncStealthySession` 作为长驻会话,靠 `max_pages`(默认 1,`_validators.py:62`,上限 50)控制并发页数;
- 或维持一个 `asyncio.Semaphore(2~3)` 包住 classmethod 调用。前者省内存(共享浏览器进程),更契合小 VPS。

**(b) timeout 单位陷阱**
- `Fetcher.get(timeout=...)` 单位是**秒**(默认 30,`static.py:78`);
- `DynamicFetcher/StealthyFetcher.fetch(timeout=...)` 单位是**毫秒**(默认 30000)。
封装层务必统一入参口径再换算下发。

**(c) Scrapling 的 timeout 不是"总墙钟硬超时"**
它是 Playwright 各操作/等待的 per-operation timeout(见 docstring "used in all operations and waits")。要满足 P2「每 URL 强制超时、超时杀进程回收」,必须在**外层**再包一个 `asyncio.wait_for(..., total_timeout)`,并在超时时关闭该 session/page。尤其 `solve_cloudflare=True` 会把内部 timeout 抬到 ≥60000ms(`_validators.py:154`),不设外层硬超时会长时间挂住实例。

**(d) 升级判据落点**
- Fetcher 档:拿 `response.status` + `len(response.get_all_text())`/`len(response.body)` 判断是否成功且正文够长;过薄 → 升 DynamicFetcher。
- DynamicFetcher 档:若命中 Cloudflare/反爬(可查 body 是否含 `Just a moment...` / status 403 / challenge iframe)→ 升 StealthyFetcher 并开 `solve_cloudflare=True`。
- 每次调用都 try/except 包裹,失败原样占位、写 `fetch_status` + `engine_used` + `fetched_at`,禁止静默丢弃。

**(e) 最小可运行片段**

Fetcher(静态,注意 timeout 秒):
```python
from scrapling.fetchers import Fetcher
resp = Fetcher.get("https://example.com", timeout=20, stealthy_headers=True)
print(resp.status, len(resp.body), resp.html_content[:200])
```

DynamicFetcher(浏览器,异步 + 外层硬超时):
```python
import asyncio
from scrapling.fetchers import DynamicFetcher

async def run():
    # classmethod:每调用新建/销毁一个浏览器
    resp = await DynamicFetcher.async_fetch(
        "https://spa.example.com",
        timeout=30000,          # 毫秒
        network_idle=True,
        wait_selector="main",
    )
    return resp
resp = asyncio.run(asyncio.wait_for(run(), timeout=45))  # 外层墙钟兜底
```

StealthyFetcher(隐身 + 解 Cloudflare):
```python
import asyncio
from scrapling.fetchers import StealthyFetcher

async def run():
    resp = await StealthyFetcher.async_fetch(
        "https://protected.example.com",
        solve_cloudflare=True,  # timeout 会被内部抬到 >=60000ms
        headless=True,
        network_idle=True,
    )
    return resp
resp = asyncio.run(asyncio.wait_for(run(), timeout=90))
```

复用浏览器的会话式(更省内存,推荐用于池):
```python
import asyncio
from scrapling.fetchers import AsyncStealthySession

async def run(urls):
    async with AsyncStealthySession(max_pages=3, headless=True) as session:
        return await asyncio.gather(*(session.fetch(u) for u in urls))
```
> 注:`AsyncStealthySession` 的确切 `__init__` / `fetch` 签名需实测确认(见待办)。

---

## 7. 待实测确认的问题(open questions)

1. `AsyncDynamicSession` / `AsyncStealthySession` 作为长驻会话时,`fetch()` 的每请求可覆盖参数集(`PlaywrightFetchParams` / `StealthFetchParams`,`_types.py:100-125`)与构造期参数如何 merge、`max_pages` 池满时的等待行为(`_base.py:290-299` 有 60s `_max_wait_for_page` 硬等待)—— 需实测确认对 P2 并发编排的影响。
2. Fetcher 静态档遇 Cloudflare 时的具体返回形态(status? body 内容?)未在源码固化,升级判据需实跑几个真实 CF 站采样确定阈值。
3. 外层 `asyncio.wait_for` 超时后,如何干净关闭正在跑的 `Async*Session` / 回收 patchright 浏览器子进程(避免僵尸进程占内存)—— Scrapling 未提供显式 kill API,需实测 `__aexit__` / `session.close` 行为。
4. `disable_resources=True` 对 trafilatura 正文抽取是否有害(丢图片/CSS 一般无害,但可能影响 SPA 首屏渲染完整性)—— 需实测。
5. `Response.get_all_text()` 与 `len(body)` 哪个更适合做"正文过薄"判据,阈值多少 —— 需用样本站校准。
6. `solve_cloudflare=True` 的实际成功率与耗时分布(源码 `_cloudflare_solver` 在 `# pragma: no cover`,未被测试覆盖)—— 需实测,决定是否值得默认开启。
