# 子系统研究:Response / 返回对象

> 研究范围:`scrapling/engines/toolbelt/custom.py`(Response 类)、`scrapling/parser.py`(Selector 中被 Response 复用的部分)、`scrapling/engines/toolbelt/convertor.py`(各引擎如何构造 Response)、`docs/api-reference/response.md`。
> 版本:vendor 快照 v0.4.10(未修改)。
> 写给以后维护 `/v0/fetch` 封装的自己。**结论均标了出处;源码与 docs 冲突时以源码为准。**

---

## 1. 职责概述

`Response` 是 **所有** Fetcher(静态 `Fetcher`、`DynamicFetcher`、`StealthyFetcher`)统一返回的对象,作用是把 curl_cffi / Playwright 三套底层库的返回抹平成同一种类型。

关键事实:**`Response` 直接继承 `Selector`**(`custom.py:28` `class Response(Selector)`)。也就是说,一个 Response 对象**本身就是一个已经解析好的 lxml 选择器树**,你不需要再 `Selector(response.text)` 二次解析——直接 `response.css(...)` / `response.xpath(...)` 就能选元素。

Response 在 Selector 之上只是**多挂了几个 HTTP 元数据属性**(status/reason/cookies/headers/request_headers/history/meta/request/captured_xhr),并覆写了 `body` 属性。构造签名见 `custom.py:42-56`。

三个构造入口都在 `convertor.py` 的 `ResponseFactory`:
- `from_http_request`(`convertor.py:301`)—— 静态 `Fetcher`(curl_cffi)。
- `from_playwright_response` / `from_async_playwright_response`(`convertor.py:82` / `:229`)—— `DynamicFetcher` 与 `StealthyFetcher`(Playwright)。

---

## 2. 关键 API / 参数详解

### 2.1 Response 自带的 HTTP 元数据属性(来自 `custom.py:__init__`)

| 属性 | 类型 | 出处 | 说明 |
|---|---|---|---|
| `status` | `int` | `custom.py:61` | HTTP 状态码。**始终是属性,不是异常。** |
| `reason` | `str` | `custom.py:62` | 状态短语(如 "OK"、"Forbidden")。Playwright 有时给空,会用 `StatusText.get(status)` 兜底(`convertor.py:119`)。 |
| `cookies` | `dict` 或 `tuple[dict,...]` | `custom.py:63` | 静态请求是 `dict`(`convertor.py:317`);浏览器是 `tuple(dict,...)`(`convertor.py:139`)。**两条链类型不一致,别假设是 dict。** |
| `headers` | `dict` | `custom.py:64` | 响应头。 |
| `request_headers` | `dict` | `custom.py:65` | 发出去的请求头。 |
| `history` | `list[Response]` | `custom.py:66` | 重定向链。静态来自 curl_cffi(`convertor.py:321`);浏览器由 `_process_response_history` 重建(`convertor.py:39`)。 |
| `meta` | `dict` | `custom.py:79` | 元数据(如 proxy)。传非 dict 会 `TypeError`(`custom.py:76-77`)。 |
| `request` | `Optional[Request]` | `custom.py:80` | 仅 spider 框架用,fetch 场景恒为 `None`。 |
| `captured_xhr` | `list[Response]` | `custom.py:81` | 浏览器会话开 `capture_xhr` 时抓到的 XHR/fetch 响应。 |

注意 `encoding`、`url` 是**构造参数**,实际存储在父类 `Selector` 上(`parser.py:121-123`,`self.url` / `self.encoding`)。`method` 只用于日志(`custom.py:74`),**不作为属性存储**——想知道方法只能看 `request_headers` 或自己记。

### 2.2 从 Selector 继承的内容/选择器 API(`parser.py`)

| 成员 | 类型 | 出处 | 语义(**易踩坑**) |
|---|---|---|---|
| `body` | `bytes`(Response 覆写) | `custom.py:83-86` | **原始未处理的响应体字节。** Response 把它 cast 成 `bytes`。这是喂 trafilatura 的首选,见第 5 节。 |
| `html_content` | `TextHandler`(str 子类) | `parser.py:344-352` | **lxml 重新序列化**当前树的 inner HTML(`tostring(..., method="html")`)。**不是原文**——默认会去掉注释、去 cdata、补 doctype。 |
| `text` | `TextHandler` | `parser.py:268-277` | ⚠️ **陷阱:这是根元素 `<html>` 自己的 `.text`,几乎恒为空/空白**,不是整页文字。想要整页可见文本用 `get_all_text()`。 |
| `get_all_text(separator, strip, ignore_tags=('script','style'), valid_values)` | `TextHandler` | `parser.py:279-329` | 递归收集所有可见文本节点,默认忽略 script/style。这才是"页面纯文本"。 |
| `prettify()` | `TextHandler` | `parser.py:361-374` | pretty-print 版 html_content。 |
| `css(selector, ...)` | `Selectors` | `parser.py:568` | CSS3 选择,内部转成 XPath 后走 `xpath()`。选不到返回**空 `Selectors`**,不抛异常。语法非法抛 `SelectorSyntaxError`(`parser.py:622-626`)。 |
| `xpath(selector, ...)` | `Selectors` | `parser.py:628` | XPath 选择,支持关键字当 XPath 变量。 |
| `urljoin(relative_url)` | `str` | `parser.py:331-333` | 用 response.url 拼相对链接。 |
| `attrib` / `has_class` / `parent` / `children` / `next` / `previous` / `below_elements` 等 | | `parser.py:335+` | 常规 DOM 遍历。 |

`_raw_body` 是 slot 字段(`parser.py:77`),`body` 属性就是读它。构造时:Response 若拿到 str 会先 `content.encode("utf-8")`(`custom.py:57-58`),再进 `Selector.__init__`,`self._raw_body = content`(`parser.py:154`)——**所以 Response 的 `_raw_body` 恒为 bytes**,`response.body` 恒返回 bytes。

### 2.3 `StatusText`(`custom.py:230-307`)
静态 map,状态码 → 短语,`StatusText.get(code)` 未知码返回 `"Unknown Status Code"`。仅用于兜底 reason。

---

## 3. 逐问题解答

### Q1. Fetcher 返回的 Response 有哪些属性/方法?
见第 2 节两张表。核心记忆点:
- HTTP 元数据:`status / reason / cookies / headers / request_headers / history / meta / captured_xhr`(+构造参数 `url` / `encoding`)。
- 内容:`body`(原始字节)、`html_content`(重序列化 str)、`text`(⚠️根元素文本,基本没用)、`get_all_text()`(整页文本)、`prettify()`。
- 选择器:`css()` / `xpath()` / `urljoin()` / `attrib` / DOM 遍历族。
- Response 本身就是 Selector,`str(response)` 返回 `<status url>`(`custom.py:146-147`),`response.html_content` 才是页面 HTML。

### Q2. 如何拿到"最终渲染后的 HTML"?区分静态 body 与浏览器渲染后 DOM?
**关键在 `convertor.py:122-127`(sync)与 `:270-275`(async)**:

```python
if page and "html" in final_response.all_headers().get("content-type", ""):
    page_content = cls._get_page_content(page).encode("utf-8")   # 渲染后 DOM
    encoding = "utf-8"
else:
    page_content = final_response.body()                          # 原始响应体
```

- **静态 `Fetcher`**:`from_http_request` 直接把 `curl_response.content`(原始 HTTP 响应体字节)塞进 `content`(`convertor.py:313`)。**没有 JS 执行,body 就是服务器原样返回的 HTML。**
- **`DynamicFetcher` / `StealthyFetcher`**:只要响应 content-type 含 `html`,`page_content` 取的是 **`page.content()`**(经 `_get_page_content` 带重试封装,`convertor.py:199-211`),即 **Playwright 序列化的当前 DOM——已经过 JS 渲染**。非 HTML(如 JSON/二进制)才回退到 `final_response.body()`。

**结论:无论静态还是浏览器,渲染后/最终的 HTML 都落在同一个地方——`response.body`(bytes)。** 上层不需要区分引擎去取不同属性;差异已在 ResponseFactory 内部抹平。`response.html_content` 是对这份 body 解析成树后再序列化的结果(会被 lxml 规整)。

### Q3. status code 与网络错误如何体现?异常还是属性?
- **HTTP 状态码 → 属性**:`response.status`(int)。403/404/503 等都是**正常返回一个 Response**,不抛异常。所以"被反爬挡了"表现为 `status in (403, 429, 503)` + body 里是挑战页,而**不是**异常。
- **网络错误 → 异常**:静态链底层 curl_cffi 失败会抛 `CurlError`,`requests.py` 里重试 `retries` 次(默认 3),**耗尽后 `raise`**(`engines/static.py:260-273` / `:477-491`)。也就是连不上/DNS失败/超时这类会**冒泡成异常**,不会给你一个 Response。
- **浏览器链**:`page.content()` 取内容失败会在 `_get_page_content` 内重试(最多 20 次 ×500ms),仍失败抛 `RuntimeError`(`convertor.py:211`);取 body 的其他异常被 `try/except` 吞掉,`page_content = b""`(`convertor.py:128-130`),即**返回一个 body 为空的 Response**。Playwright 导航级超时(`timeout` 参数,默认 30000ms)会由 Playwright 抛 `TimeoutError`。
- **Cloudflare 检测只在浏览器链存在**:`_base.py:545 _detect_cloudflare()` 判**挑战类型**——靠 `cType: 'non-interactive'|'managed'|'interactive'` 字符串匹配(`_base.py:563-570`)返回对应类型,或命中内嵌 turnstile 脚本 `script[src*="challenges.cloudflare.com/turnstile/v"]` 返回 `"embedded"`(`_base.py:572-575`),都不中返回 `None`;**注意它不看 `<title>Just a moment...</title>`**。`<title>Just a moment...</title>` / "Verifying you are human." 那几个文本是 `_stealth.py` 的**求解轮询循环**(`_stealth.py:121/132/149/164/177` 等)用来判断挑战页是否还在的条件,不是检测入口。**静态 Fetcher 不做任何 Cloudflare 识别**——它只会给你一个 403/503 的 Response,body 是挑战页。

> **对 P2 的直接含义**:我们的 `fetch_status` 不能只 try/except。既要 catch 异常(→ `failed`/`timeout`),也要**检查 `response.status` 和 body 特征**来判 `blocked`。

### Q4. Response 与选择器(css/xpath)如何结合?
因为 `Response is-a Selector`,直接 `response.css("h1::text")` / `response.xpath("//title/text()")` 即可,返回 `Selectors`(list 子类)。选不到返回空列表(不抛),语法错抛 `SelectorSyntaxError`。取文本用 `::text` 伪元素或 `.get_all_text()`。**P2 本期不需要选择器**(raw HTML 交给 P3 的 trafilatura),但判"正文够长/疑似 SPA"时可以顺手用 `response.get_all_text()` 估算正文长度。

### Q5. 要把 raw HTML 传给 trafilatura,取哪个属性最稳妥?
**首选 `response.body`(bytes)。** 理由:
1. 它是**最忠实的原文**——静态链是服务器原样字节;浏览器链是 `page.content()` 的 utf-8 编码,即渲染后完整 DOM(含 `<head>`/`<script>`,trafilatura 需要这些做元数据/正文提取)。
2. `html_content` 是 lxml `tostring` 重序列化的结果,**默认丢注释、strip cdata、补 doctype**(`parser.py:344-352` + 解析参数 `parser.py:142-151`),对结构做过手术,不如原文保真。
3. `text` 基本是空的(见 Q1 陷阱),绝不能用。

trafilatura 的 `extract()` 同时接受 str 和 bytes;直接传 `response.body`(bytes)即可,它内部会自行探测编码。若下游坚持要 str,用 `response.body.decode(response.encoding, errors="replace")`(`response.encoding` 见 `custom.py:51` 参数 / `parser.py:123`)。

> 备选:若发现某些站点 body 里混入乱码/畸形导致 trafilatura 报错,再退一步用 `response.html_content`(已是被 lxml 清洗过的合法 str)。默认走 `body`。

---

## 4. 对 P2 的影响与行动建议

### 4.1 统一的取 HTML 姿势(与引擎无关)
```python
def raw_html_from(resp) -> bytes:
    # resp.body 恒为 bytes;静态=原始响应体,浏览器=渲染后 DOM(utf-8)
    return resp.body  # 直接喂 trafilatura(P3)
```

### 4.2 fetch_status 判定(异常 + 属性双通道)
```python
CF_MARKERS = ("Just a moment...", "Verifying you are human", "cf-browser-verification")

def classify(resp) -> str:
    body_txt = resp.body[:4096].decode(resp.encoding, "replace")
    if resp.status in (403, 429, 503) or any(m in body_txt for m in CF_MARKERS):
        return "blocked"
    if resp.status >= 400:
        return "failed"
    return "ok"
```
- **超时 / 网络错误**:靠 `try/except`(curl 抛 `CurlError`→耗尽后原异常;Playwright 抛 `TimeoutError`/`RuntimeError`)。捕获后置 `timeout` 或 `failed`,**原样占位,禁止丢弃**——符合硬约束。
- **静态链没有 Cloudflare 检测**,所以第一档 `Fetcher` 命中 `blocked`(靠上面的 status/marker 判定)时,升级到 `StealthyFetcher`。

### 4.3 三档升级链落点(与 Response 相关的部分)
```
Fetcher(curl) → resp.status==200 且 len(resp.get_all_text()) 够长  → ok, engine_used="static"
              → 正文过薄/明显 SPA(body 短、<div id=root> 空壳)     → 升级 DynamicFetcher
              → classify()=="blocked"                              → 升级 StealthyFetcher
```
"正文够长"用 `len(resp.get_all_text().strip())` 估算(`parser.py:279`),阈值待实测(见开放问题)。

### 4.4 StealthyFetcher 的 Cloudflare 求解要显式打开
**`solve_cloudflare` 默认 `False`**(`_validators.py:148` `solve_cloudflare: bool = False`,TypedDict 声明在 `_types.py:121/125`,消费点 `_stealth.py:253/529`)。**只检测不求解**。若我们要让隐身档真正过 Turnstile/Interstitial,需在调用 `StealthyFetcher.fetch(url, solve_cloudflare=True, ...)` 时显式传入。注意:开启后会阻塞等待解题,**耗时更长**,须配合我们更长的单 URL 超时预算。

### 4.5 超时与内存约束对接
- `StealthyFetcher.fetch` 的 `timeout` 参数单位是**毫秒**,默认 30000(`fetchers/stealth_chrome.py:28`)。我们的 asyncio 单 URL 超时应设得 ≥ 这个值再加缓冲,或反过来把 Scrapling 的 timeout 收紧到我们的预算内。
- 浏览器实例池上限 2-3、asyncio 信号量:这些在 Scrapling 之外由我们网关控制;Response 层无关。用 `async_fetch`(`stealth_chrome.py:66`)对接 asyncio。

---

## 5. 待实测确认的问题(open_questions)

1. `response.body` 对**非 utf-8** 站点(如 GBK/Shift-JIS)静态抓取时:curl_cffi 给的是原始字节,`response.encoding` 是否可靠地反映真实编码?trafilatura 直接吃 bytes 时的编码探测是否与之一致?需拿中日韩站点实测。
2. "正文过薄/疑似 SPA"的阈值:`len(get_all_text())` 多少字符算薄?SPA 空壳(`<div id="app"></div>`)在静态档下 `get_all_text()` 具体返回什么、能否稳定识别?需样本测试。
3. 静态 `Fetcher` 遇 Cloudflare 时的**确切 status 与 body 特征**(是 403 还是 503?body 是否一定含 "Just a moment...")需真实抓一个 CF 站点确认,以校准 `classify()` 的 marker 列表。
4. `StealthyFetcher(solve_cloudflare=True)` 求解失败时的行为:是抛异常、还是返回仍带挑战页的 Response?`_stealth.py` 里的 while 循环是否有上限、会不会卡到我们的超时?需实测。
5. 浏览器链在 content-type **不含 `html`** 却其实是 HTML(某些站点返回 `text/plain`)时,会走 `final_response.body()` 而非 `page.content()`,拿到的是**未渲染**内容。这种边界站点占比多大、要不要在网关侧强制用 `page.content()`?需观察。
6. `history` 里的 Response 的 `content` 被显式设为 `""`(`convertor.py:54`),即重定向中间跳的 body 拿不到——确认我们 P2 不依赖中间跳 body(应该不依赖)。
