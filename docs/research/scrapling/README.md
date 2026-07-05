# Scrapling 研读笔记索引

> 对象:**Scrapling v0.4.10**(`vendor/scrapling/`,未修改快照)。
> 目的:为 AI Research Browser P2 抓取网关(`POST /v0/fetch` 三档升级链:Fetcher → DynamicFetcher → StealthyFetcher)提供源码级落地依据。
> 全部结论**以源码为准**;笔记间矛盾已在 `00-overview.md` §7 回源码裁决。行号相对 `vendor/scrapling/`。

## 阅读顺序

先读 **[00-overview](./00-overview.md)** 拿到全局架构、三档链落地设计、封装骨架、部署清单与风险台账;需要某子系统细节时再翻对应分册。

| 文件 | 子系统 | 一句话简介 |
|---|---|---|
| [00-overview.md](./00-overview.md) | **综合总览** | 架构图 + 三档升级链判定伪代码 + 参数/字段映射 + 浏览器池骨架 + 部署清单 + 风险&待实测台账 + 笔记勘误裁决。**先读这份。** |
| [01-fetchers-api.md](./01-fetchers-api.md) | 三档 Fetcher 公共 API | 三个 Fetcher 均免实例化 classmethod、返回统一 Response;无内置升级机制;timeout 单位静态=秒/浏览器=毫秒;默认值在 msgspec 校验模型里。 |
| [02-response-object.md](./02-response-object.md) | Response 返回对象 | `Response(Selector)` 子类;raw HTML 用 `.body`(bytes,渲染后已抹平);status 是属性、网络错误是异常;CF 检测只在浏览器链。 |
| [03-static-engine.md](./03-static-engine.md) | 静态引擎(Fetcher 底层) | 底层 curl_cffi + `impersonate` TLS 指纹;HTTP 4xx/5xx 不抛异常(返回带状态码的 Response),仅传输层错抛 `CurlError`;每请求一次性 session。 |
| [04-dynamic-engine.md](./04-dynamic-engine.md) | 动态引擎(DynamicFetcher) | 原版 playwright + Chromium;classmethod 每次新建整浏览器;`max_pages` 只 async 生效;**默认不含 `--disable-dev-shm-usage`,小 VPS 必须手动加**。 |
| [05-stealth-engine.md](./05-stealth-engine.md) | 隐身引擎(StealthyFetcher) | 底层 **patchright**(非 camoufox/firefox);`solve_cloudflare` 默认 False 且开启抬 timeout≥60s;solver 无测试覆盖、超时不抛错可能返回仍被拦页面。 |
| [06-concurrency-lifecycle.md](./06-concurrency-lifecycle.md) | 并发/会话/进程生命周期 | 有真 async、无浏览器实例池(只有 PagePool)、无硬超时/无进程 kill/无 `__del__`;网关必须自补信号量 + `wait_for` + 超时后 close 重建。含可运行 `BrowserSessionPool` 骨架。 |
| [07-toolbelt.md](./07-toolbelt.md) | Toolbelt(指纹/代理/广告域/导航) | fingerprints 只生成不校验;`ProxyRotator` 三引擎都消费;`ad_domains` 3526 条(block_ads,仅浏览器);`construct_proxy_dict`/`StatusText` 可复用。 |
| [08-parser-selectors.md](./08-parser-selectors.md) | Parser 与选择器 | Response 即 Selector,解析开箱即用;判正文过薄用 `len(get_all_text(strip=True))`(勿用 `len(body)`);交 P3 用 `.body`;`clean_text` 方法不存在;全程 `adaptive=False`。 |
| [09-install-cli-integrations.md](./09-install-cli-integrations.md) | 安装/CLI/集成/MCP | `pip install "scrapling[fetchers]==0.4.10"` + 构建期 `playwright install[-deps] chromium`(Debian,禁 Alpine);MCP `bulk_*` 是并发抓取模板但需自补信号量/超时/占位。 |
