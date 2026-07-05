# SearXNG 子系统研究:限流与机器人检测(limiter / botdetection)

> 研读范围(相对 `vendor/searxng/`):`searx/limiter.py`、`searx/limiter.toml`、`searx/botdetection/`
> 快照 commit:`a643858`(未修改)
> 面向读者:以后维护这套网关的自己。所有结论均标出处(文件:行号 / 函数名)。

---

## 职责概述

limiter 的动机写在 `limiter.py:1-25` 的模块 docstring:SearXNG 本身会把用户请求转发给上游搜索引擎,因此在上游眼里 SearXNG 就是个"爬虫代理"。如果有人拿脚本狂刷 SearXNG,上游会给 SearXNG 发 CAPTCHA 或直接封 SearXNG 的 IP。为了自保,limiter 的任务就是**把疑似 bot 的请求在 SearXNG 入口就挡掉**,别让它们打到上游。

实现方式:limiter 注册一个 Flask `before_request` 钩子(`limiter.py:251` `app.before_request(pre_request)`),在每个请求进入业务逻辑前跑一遍 `filter_request`(`limiter.py:147`),命中规则就直接返回 `429`(或 302 重定向),不再进入搜索流程。

限流/检测的具体规则都在 `botdetection/` 包里,按"检查器"拆分成多个模块,每个模块暴露一个 `filter_request(network, request, cfg)`,返回 `None` 表示放行、返回 `werkzeug.Response` 表示拦截。

**对我们最关键的一句话结论:limiter 默认是关的;一旦开启,它对 `format=json` 的 API 请求有多处会误杀,尤其是每 IP 每小时只允许 4 次 API 请求(`API_MAX=4`)。**

---

## 架构与关键流程

### 启用条件与安装流程(`limiter.py:initialize` 222-251)

```
initialize(app, settings):
    cfg = get_cfg()                       # 读 limiter.toml(+ /etc/searxng/limiter.toml 覆盖)
    valkey_client = valkeydb.client()
    botdetection.init(cfg, valkey_client) # 即使 limiter 不启用也会执行(self_info 插件要用)
    if not (settings['server']['limiter'] or settings['server']['public_instance']):
        return                            # <-- 默认从这里返回,不注册钩子
    if not valkey_client:
        logger.error(...)
        if public_instance: sys.exit(1)   # public 模式没 valkey 直接退出进程
        return                            # limiter 模式没 valkey 就静默不装
    _INSTALLED = True
    if public_instance:
        cfg.set('botdetection.ip_limit.link_token', True)  # 强制打开 link_token
    app.before_request(pre_request)       # <-- 只有走到这里才真正生效
```

关键点:
- 默认值:`settings.yml:97 limiter: false`、`settings.yml:100 public_instance: false`。所以**开箱即用状态下 limiter 完全不生效**,`before_request` 钩子根本没注册。
- 只要 `limiter=true` **或** `public_instance=true` 任一为真,就尝试安装。
- 没有 valkey 且非 public → 静默不安装(`limiter.py:243 return`);没有 valkey 且 public → `sys.exit(1)` 直接杀进程(`limiter.py:242`)。
- public 模式会**强制**打开 `link_token`(`limiter.py:247-249`),忽略 toml 里的设置。
- `botdetection.init`(`botdetection/__init__.py:22`)总会调用,它把全局 cfg 和 valkey client 塞进模块级单例,给 self_info 插件等复用;但这不等于 limiter 生效。

### 请求过滤主流程(`limiter.py:filter_request` 147-209)

顺序(这个顺序很重要,是短路的):

1. `request.path == '/healthz'` → 直接放行(`:154`)。
2. `network.is_link_local` → 直接放行(`:159`)。注意这里用的是 **network**(经过前缀聚合后的网段),link-local 指 `169.254.0.0/16` / `fe80::/10`。
3. **pass-list**:`ip_lists.pass_ip` 命中 → 放行(`:171-174`)。这是最高优先级白名单,后续所有检查(含 UA、ip_limit)全部跳过。
4. **block-list**:`ip_lists.block_ip` 命中 → 直接 `429`(`:176-179`)。
5. **对所有请求**跑一组检查(`:183-189`),当前只有 `http_user_agent` 一个。
6. **仅对 `request.path == '/search'`**(`:193`)再跑一组检查(`:195-206`),顺序:
   `http_accept` → `http_accept_encoding` → `http_accept_language` → `http_user_agent`(又跑一次)→ `http_sec_fetch` → `ip_limit`。
7. 全过 → 放行(`:208`)。

> 注意:`/search` 的 JSON API(`GET/POST /search?format=json`)走的就是 `/search` 这条路径,**上面第 6 步的全套检查都会作用于我们的 API 调用**。这是最大的坑。
>
> 另注:`botdetection/http_connection.py`(检查 `Connection: close`)这个模块**存在但没有被 `filter_request` 引用**(`limiter.py:183-206` 的两个列表里都没有它),所以它当前不生效,可忽略。

---

## 逐问题详解

### 问题 1:limiter 开关在哪、默认状态?

**两个开关,任一为真即启用**(`limiter.py:233`):

| 开关 | 位置 | 默认值 | 出处 |
|---|---|---|---|
| `server.limiter` | `settings.yml` | `false` | `settings.yml:97` |
| `server.public_instance` | `settings.yml` | `false` | `settings.yml:100` |

- 默认两者都 `false` → limiter 不安装,`before_request` 不注册,**对任何请求零影响**(`limiter.py:233-234`)。
- `limiter=true` 但没配 valkey → 记 error 日志后**静默不装**(`limiter.py:236-243`),即 limiter 相当于没开。
- `public_instance=true` 但没配 valkey → **`sys.exit(1)` 直接退出**(`limiter.py:241-242`)。
- `public_instance=true` 会额外强制 `link_token=true`(`limiter.py:247-249`),覆盖 `limiter.toml` 的值。

细粒度规则开关在 `limiter.toml`(见问题 4),但**总开关是 `settings.yml` 里的这两个**。

### 问题 2:启用后对 `format=json` 的 API 请求有什么影响?

假设 limiter 已启用、我们的网关请求打到 `/search?format=json`。逐个检查器分析(按 `filter_request` 里的执行顺序):

#### (a) `http_user_agent`(对所有请求生效,`limiter.py:183-189`;`/search` 里又跑一次 `:199`)

- 逻辑:取 `User-Agent` 头,缺省值为字符串 `'unknown'`(`http_user_agent.py:62`);用正则 `USER_AGENT`(`http_user_agent.py:29-43`)做 `match`,命中就返回 `429`(`:63-64`)。
- 正则里包含:`unknown`、`curl`(大小写不敏感)、`wget`、`Scrapy`、`python-requests`、`Go-http-client`、`Java`、`okhttp`、`HttpClient`、`Python`、`libwww-perl`、`Ruby`、以及一堆爬虫 UA、`HeadlessChrome`、`.*PetalBot.*` 等。
- **对我们的杀伤**:
  - 不设 UA → `'unknown'` → **被拦**。
  - 用 `httpx`/`requests`/`aiohttp` 默认 UA(如 `python-requests/2.x`、`python-httpx/0.x`)→ 命中 `python-requests` / `Python` → **被拦**。(注意 `Python` 会 `match`,而 `re.match` 是从头匹配,`python-httpx` 里的 `Python` 首字母是小写 `p`,但正则里有独立的 `python-requests` 分支和 `Python` 分支——`httpx` 默认 UA 形如 `python-httpx/0.27.0`,开头 `python-` 会被 `python-requests` 分支?不会,它要求完整 `python-requests`;但 `.*PetalBot.*` 用了 `.*`……需实测 `python-httpx` 是否命中,见待确认。保守起见必须自定义 UA。)
  - 结论:**网关必须发送一个不在黑名单里的、像浏览器的 `User-Agent`**,否则被 429。

#### (b) `http_accept`(仅 `/search`,`limiter.py:195`)

- 逻辑:`if 'text/html' not in request.accept_mimetypes: 429`(`http_accept.py:35-36`)。
- **对我们的杀伤(严重)**:JSON 客户端通常发 `Accept: application/json`,里面**没有 `text/html`** → **被拦 429**。
- 结论:调用 `/search?format=json` 时,`Accept` 头**必须包含 `text/html`**(例如直接照抄浏览器的 `Accept: text/html,application/xhtml+xml,...`)。这一点非常反直觉。

#### (c) `http_accept_encoding`(仅 `/search`,`limiter.py:196`)

- 逻辑:`Accept-Encoding` 拆逗号后,若既无 `gzip` 也无 `deflate` → `429`(`http_accept_encoding.py:36-38`)。
- 对我们:大多数 HTTP 客户端默认会带 `gzip`,一般不会中招;但若显式禁用压缩就会被拦。发 `Accept-Encoding: gzip, deflate` 即可。

#### (d) `http_accept_language`(仅 `/search`,`limiter.py:197`)

- 逻辑:`Accept-Language` 为空(未设或空串)→ `429`(`http_accept_language.py:32-33`)。
- 对我们:脚本客户端默认不发这个头 → **被拦**。必须显式带上,例如 `Accept-Language: en-US,en;q=0.9`。

#### (e) `http_sec_fetch`(仅 `/search`,`limiter.py:201`)

- 逻辑(`http_sec_fetch.py:77-107`):
  - `if not request.is_secure: return None`(`:83-87`)。**只有 HTTPS 请求才检查**。我们网关走内网 HTTP → 直接放行,不受影响。
  - 即便是 HTTPS,只对"受支持浏览器 UA"(Chrome≥80/Firefox≥90/Safari≥16.4,`is_browser_supported`)才检查(`:91`)。脚本 UA 不匹配这些浏览器版本 → 跳过。
  - 只有 `Sec-Fetch-Mode` 不在 `('navigate','cors')` 时会真正 `return redirect`(`:92-95`)。
  - **源码 bug**:`Sec-Fetch-Site`(`:97-100`)和 `Sec-Fetch-Dest`(`:102-105`)两处构造了 `flask.redirect(...)` 但**忘了 `return`**,所以这俩实际不生效。只有 `Sec-Fetch-Mode` 会拦。
- 对我们:内网 HTTP 调用完全不触发;即便走 HTTPS,只要 UA 不是"真浏览器版本号"也不触发。基本无影响。

#### (f) `ip_limit`(仅 `/search`,`limiter.py:202`)—— **对 API 的头号杀手**

`ip_limit.py:92-148`:

- 若 `network.is_link_local` 且未开 `filter_link_local` → 放行(`:101-103`)。
- **API 硬限流(`:105-108`)**:
  ```python
  if request.args.get('format', 'html') != 'html':
      c = incr_sliding_window(..., 'ip_limit.API_WINDOW:'+network, API_WINDOW)
      if c > API_MAX:
          return too_many_requests(...)   # 429
  ```
  - `API_WINDOW = 3600`(秒)、`API_MAX = 4`(`ip_limit.py:79-83`)。
  - 含义:**同一网段每小时最多 4 次非 html(即 json/csv/rss)请求,第 5 次起 429**。这对"网关代理大量 API 调用"是致命的。
- 之后还有 `link_token` 分支(仅当 `botdetection.ip_limit.link_token=true`,`:110-137`):
  - 调 `link_token.is_suspicious()`(`link_token.py:73`)判断该"网络会话"是否请求过 `/client<token>.css`。API 客户端**永远不会**去请求那个 CSS → 永远 `suspicious=True`。
  - suspicious 时用更严的窗口:`SUSPICIOUS_IP_WINDOW=30天/SUSPICIOUS_IP_MAX=3`(`:85-89`)、`BURST_MAX_SUSPICIOUS=2`(`:67`)、`LONG_MAX_SUSPICIOUS=10`(`:76`)。超过 `SUSPICIOUS_IP_MAX=3` 直接 302 重定向到首页(`:123-127`)。
  - **public_instance 会强制打开 link_token**(`limiter.py:249`),所以 public 模式下 API 客户端会被这套"可疑 IP"逻辑迅速掐死(每 30 天窗口只准 3 次)。
- 未开 link_token 时走"vanilla"分支:`BURST_MAX=15`/20s、`LONG_MAX=150`/600s(`:140-146`, 常量 `:61-74`)。但注意 API 请求在到这之前已经先过了 `API_MAX=4` 那道更严的坎。

**问题 2 小结**:limiter 一旦启用,我们的 `format=json` 调用要同时满足:UA 不在黑名单、`Accept` 含 `text/html`、`Accept-Encoding` 含 gzip/deflate、`Accept-Language` 非空,并且**每 IP/网段每小时不超过 4 次**(public 模式下更被 link_token 逻辑压到近乎不可用)。伪装头还能凑,`API_MAX=4/小时` 这条基本堵死了"网关高频代理"的路。

### 问题 3:同机/内网调用,应该关 limiter 还是配 trusted_proxies/白名单?

先厘清 `trusted_proxies` 到底管什么(`trusted_proxies.py`,`ProxyFix` 中间件):

- 它**只负责"确定客户端真实 IP"**,不负责放行。它从 `X-Forwarded-For` 里、跳过属于 `trusted_proxies` 的地址,取第一个"不受信"的地址作为 `remote_addr`(`trusted_proxies.py:66-86`)。
- 若设了 `X-Forwarded-For` 但**没配** `trusted_proxies` → 该头被丢弃、记 error(`:144-148`),回落到 `X-Real-IP` 或 `REMOTE_ADDR`。
- 所以 `trusted_proxies` 是给"SearXNG 在反代后面、要还原真实客户端 IP"用的;**它不能让某个 IP 免检**。把网关 IP 加进 `trusted_proxies` 并不会让网关请求跳过 botdetection——它只影响"算出来的 client IP 是谁"。

真正能免检的是 **pass-list**(`ip_lists.pass_ip`):`filter_request:171-174` 里 pass_ip 命中就 `return None`,**跳过 UA、ip_limit 等所有检查**,优先级最高(`ip_lists.py:35` 注释、`limiter.toml:31-35` 也强调)。link-local 网段也天然放行(`filter_request:159`)。

**明确建议(按推荐度排序):**

1. **首选:直接关掉 limiter(保持默认 `server.limiter: false` / `public_instance: false`)。**
   - 我们的架构是"FastAPI 网关是 SearXNG 的唯一客户端、同机/内网、不对公网暴露 SearXNG"。limiter 的存在意义是防公网滥用,在我们的拓扑下纯属给自己添堵(问题 2 列的每一条都会误杀)。
   - 关掉后 `before_request` 不注册,SearXNG 对我们零限制,`format=json` 想调多少调多少。限流/去重/滥用防护应该放在**我们自己的 FastAPI 网关层**做,粒度和策略都可控。
   - 代价:SearXNG 自身不再对上游做"自保护"式限流。但既然只有我们的网关在调它,上游保护也应由我们网关的调度/退避逻辑负责(参见 network 子系统笔记),不依赖 limiter。

2. **次选(若因其他原因必须开 limiter):把网关来源 IP 加入 `botdetection.ip_lists.pass_ip`,并把 `pass_searxng_org` 保持/关掉视需要。**
   ```toml
   [botdetection.ip_lists]
   pass_ip = [
     '127.0.0.1',        # 同机
     '10.0.0.0/8',       # 或网关所在内网段
   ]
   ```
   - pass_ip 命中即全量免检(`ip_lists.py:49-59` + `filter_request:171`),这是唯一能让网关"既开着 limiter 又不被误杀"的正解。
   - 同时需要正确配置 `trusted_proxies`,否则若网关通过 `X-Forwarded-For` 传递 IP,SearXNG 可能算错 `remote_addr`,导致 pass_ip 匹配的不是你以为的那个 IP。若网关**直连**(不加 XFF 头),`remote_addr` 就是网关的 socket IP,pass_ip 写这个 IP 即可。
   - **不要指望只配 `trusted_proxies` 就能免检——它不是白名单。**

3. **不推荐:开 limiter 但靠伪装请求头 + 忍受 `API_MAX=4/小时`。** 头能伪装,但 4 次/小时的 API 上限无法绕过(除非 pass_ip),对网关不可用。

> 一句话:**关 limiter 是最干净的选择;如果必须开,用 `pass_ip` 白名单,而不是 `trusted_proxies`。**

### 问题 4:`limiter.toml` 各配置段含义

文件:`limiter.toml`(即打包默认值 / schema,`limiter.py:129` `LIMITER_CFG_SCHEMA`);用户可在 `/etc/searxng/limiter.toml` 或 `settings_loader.get_user_cfg_folder()/limiter.toml` 覆盖(`limiter.py:140`)。**只需覆盖要改的键,不必整份复制**(`limiter.py:66-69`)。

| 配置段 / 键 | 默认值 | 含义 / 出处 |
|---|---|---|
| `[botdetection] ipv4_prefix` | `32` | 客户端网段聚合的 IPv4 前缀位数;`get_network` 用它把单 IP 归一成网段(`_helpers.py:56-77`)。32 = 精确到单个 IPv4。 |
| `[botdetection] ipv6_prefix` | `48` | 同上,IPv6 用 /48 聚合(一个站点/用户常共享 /48)。`limiter.toml:6-7`。 |
| `[botdetection] trusted_proxies` | `['127.0.0.0/8', '::1']` | 受信反代网段,用于从 `X-Forwarded-For` 还原真实 client IP(`trusted_proxies.py:61-64`)。**不是白名单**。默认注释掉了内网段。`limiter.toml:9-20`。 |
| `[botdetection.ip_limit] filter_link_local` | `false` | 是否也对 link-local 网段做 ip_limit;默认 false = link-local 不限流(`ip_limit.py:101`)。`limiter.toml:24-26`。 |
| `[botdetection.ip_limit] link_token` | `false` | 是否启用 link_token"可疑请求"检测(`ip_limit.py:110`)。public 模式强制置 true(`limiter.py:249`)。`limiter.toml:28-29`。 |
| `[botdetection.ip_lists] block_ip` | `[]` | 黑名单,命中直接 429(`ip_lists.py:62-70`, `filter_request:176`)。`limiter.toml:37-40`。 |
| `[botdetection.ip_lists] pass_ip` | `[]` | 白名单,命中全量免检、优先级最高(`ip_lists.py:49-59`, `filter_request:171`)。`limiter.toml:42-45`。 |
| `[botdetection.ip_lists] pass_searxng_org` | `true` | 是否放行 SearXNG 官方 IP(`check.searx.space`:`167.235.158.251` / `2a01:4f8:1c1c:8fc2::/64`),用于官方巡检。`ip_lists.py:41-59`, `limiter.toml:47-49`。 |

补充:`ip_limit` 里的窗口/阈值(`BURST_MAX`、`LONG_MAX`、`API_MAX`、`SUSPICIOUS_*`)**是 Python 常量,写死在 `ip_limit.py:61-89`,不在 toml 里**,无法通过配置改。想调只能改源码(与"不改 SearXNG 源码"的架构决策冲突,故实际上不可调)。

### 问题 5:valkey / redis 在 limiter 中的角色;不配会怎样

- SearXNG 这个快照版本用的是 **Valkey**(Redis 的开源分叉),模块名 `valkeydb`、配置段 `valkey:`(`settings.yml:121-125`)。注释里也提到可由 `SEARXNG_VALKEY_URL` 环境变量覆盖。默认 `valkey.url: false`(`settings.yml:125`)——即默认不配。
- **谁需要 valkey**:
  - `ip_limit`(滑动窗口计数,`incr_sliding_window`/`drop_counter`,`ip_limit.py:48,106,120,...`)——本质就是把每 IP 的请求计数存 valkey。
  - `link_token`(存 token、存 client 的 ping-key,`link_token.py:80-112`)。
  - `ip_limit.py:9-11` docstring 明说:"This method requires a valkey DB"。
- **单纯的 HTTP 头检查器**(`http_user_agent`/`http_accept`/`http_accept_encoding`/`http_accept_language`/`http_sec_fetch`)以及 `ip_lists`(黑白名单)**不需要 valkey**——它们只看请求头和静态配置。
- **不配 valkey 会怎样**:
  - `limiter=true` 且无 valkey → 记 error 日志,`return`,**limiter 不安装**(`limiter.py:236-243`)。等于限流没开。
  - `public_instance=true` 且无 valkey → **`sys.exit(1)`**,SearXNG 进程直接退出(`limiter.py:241-242`)。
  - limiter 全程未启用(默认)→ 根本不需要 valkey。
  - 底层保护:`valkeydb.get_valkey_client()` 在没有 client 时 `raise ValueError`(`valkeydb.py:18-21`)。`link_token.get_token()` 捕获这个 ValueError 并返回固定串 `'12345678'`(`link_token.py:143-148`),这样即使无 valkey,渲染页面(webapp 需要 token 变量)也不会崩。
- 对我们:既然建议关 limiter(问题 3),**我们的 SearXNG 部署可以完全不配 valkey**,少一个依赖组件。若将来要开 limiter,则必须起一个 valkey/redis 实例并配 `valkey.url`。

---

## 对 P1 的影响与行动建议

P1 要实现 `POST /v0/search`,由 FastAPI 网关内部去调 SearXNG 的 `/search?format=json`。limiter 子系统直接决定"我们的网关请求会不会被 SearXNG 自己挡回来"。

1. **Docker 部署 SearXNG 时,保持 `server.limiter: false`、`server.public_instance: false`(默认值即可)。** 这是最省事、最不会踩坑的选择。限流/防滥用在我们自己的网关层做。
2. **不要给 SearXNG 配 valkey**(除非将来决定开 limiter)。少一个依赖。
3. **即便关了 limiter,网关调 `/search?format=json` 时仍建议带上"像样的请求头"**(自定义非黑名单 UA、`Accept` 含 `text/html`、`Accept-Language`、`Accept-Encoding: gzip, deflate`)。原因:(a) 万一有人误开 limiter 也不会立刻全挂;(b) 上游引擎侧也可能看这些头。把这套头固化进网关的 SearXNG client 里当默认值。
4. **如果运维出于合规/复用原因坚持开 limiter**:必须在 `limiter.toml` 的 `[botdetection.ip_lists] pass_ip` 里加入网关的来源 IP/网段,并确认 SearXNG 直连场景下 `remote_addr` 就是该 IP;**绝不能只配 `trusted_proxies` 就以为免检了**。同时要接受 valkey 成为硬依赖。
5. **`meta.engines_failed` 与 limiter 无关**:limiter 是"入口整体 429/302",不是"某个引擎失败"。也就是说如果 limiter 挡了我们,网关拿到的是 HTTP 429/302,而不是一个带 `unresponsive_engines` 的正常 JSON。网关必须能区分"被 limiter 拦(整包 429)"和"搜索成功但部分引擎失败(200 + JSON 内 unresponsive_engines)"这两种情况,分别映射到不同错误。（引擎级失败的暴露在 results/orchestration 子系统,不在本模块。）
6. **健康检查友好**:`/healthz` 永远绕过 limiter(`filter_request:154`)。若网关要探活 SearXNG,可用 `/healthz`,不受任何限流影响。

---

## 待实测确认的问题

1. `python-httpx` 默认 UA(形如 `python-httpx/0.27.0`)是否会命中 `http_user_agent.py` 的 `USER_AGENT` 正则?正则用的是 `re.match`(从头匹配),含独立分支 `python-requests`、`Python`、`.*PetalBot.*`。`Python`(大写 P)分支对小写开头的 `python-httpx` 未必命中,但需实测确认,以决定网关默认 UA 必须覆盖。(源码:`http_user_agent.py:29-64`)
2. limiter 关闭时,`botdetection.init` 仍被调用(`limiter.py:229-231`)。需确认在"没配 valkey + limiter 关闭"下,SearXNG 启动和 `/search?format=json` 全流程无异常(理论上 `get_token` 会走 ValueError 兜底返回 `'12345678'`,但要实测 webapp 渲染/JSON 输出路径不触发别的 valkey 调用)。
3. `format=json` 走 `POST /search` 还是 `GET /search`?`filter_request` 只判 `request.path == '/search'`(`limiter.py:193`),对方法不敏感;但 `ip_limit` 用 `request.args.get('format')`(`ip_limit.py:105`),`args` 是 query string。若我们用 POST body 传 `format=json`,`request.args` 里是否有 `format`?需确认 POST + body 参数时 `API_MAX` 那条分支是否还触发(涉及是否把 format 放 query)。这会影响 limiter 开启场景下的行为,但在我们"关 limiter"的推荐下不阻塞 P1。
4. `get_user_cfg_folder()` 的实际取值(`limiter.py:140`)决定了 `limiter.toml` 覆盖文件的查找路径。Docker 部署里该目录映射到哪、是否等于 `/etc/searxng`,需在实际镜像里确认(影响运维改配置的落点)。
5. `network.is_link_local`(`filter_request:159`)判断的是聚合后的 network 还是原始 IP?`get_network` 用 `strict=False` 生成网段,`is_link_local` 作用在 `IPv4Network/IPv6Network` 上。若网关在 `169.254/16` 或 `fe80::/10` 内则天然免检,但一般内网是 `10/8`、`172.16/12`、`192.168/16`,不属 link-local,不能靠这条免检——需以实际网关 IP 复核。
