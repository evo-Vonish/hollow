# SearXNG 出站网络层(searx/network/)研究笔记

> 快照:commit a643858,未修改。路径均相对 `/home/user/hollow/vendor/searxng/`。
> 本笔记覆盖子系统「出站网络层」,即 SearXNG 向各搜索引擎发 HTTP 请求的整套机制:httpx 客户端管理、outgoing 配置项、重试、HTTP 错误 → CAPTCHA/限流异常识别、以及引擎 suspend 联动。
> 阅读对象:以后维护这套网关的自己。重点在"这些机制对我们只调 JSON API 的网关意味着什么"。

---

## 一、职责概述

`searx/network/` 是 SearXNG 的**出站 HTTP 层**,把"引擎代码里一句同步的 `searx.network.get(url)`"翻译成"在一个后台 asyncio 事件循环里跑的 httpx 异步请求",并统一管理:

- **连接池 / HTTP2 / TLS**:每个引擎默认一个独立的 `Network` 实例(隔离连接池、代理、源 IP、超时)。
- **代理与源 IP 轮换**:支持 http/socks 代理列表和多源 IP 的 round-robin。
- **超时**:线程级超时上下文 + httpx timeout。
- **重试**:应用层重试循环(不是 httpx transport 层重试)。
- **HTTP 错误语义化**:`raise_for_httperror` 把 429 / 403 / Cloudflare / reCAPTCHA 等识别成带"惩罚时长"的专用异常,上层据此把引擎 suspend 掉。

四个文件分工:

| 文件 | 职责 |
| --- | --- |
| `network/client.py` | 底层 httpx 客户端/传输构造(`new_client`、`get_transport*`)、SSLContext 缓存与密码套件洗牌、后台 asyncio 事件循环 `init()` |
| `network/network.py` | `Network` 类(连接池参数、代理/源IP轮换、重试循环 `call_client`)、`initialize()` 从 settings 建所有网络 |
| `network/__init__.py` | 同步门面:`request/get/post/...`、`multi_requests`、`stream`,以及线程级超时/网络上下文(`THREADLOCAL`) |
| `network/raise_for_httperror.py` | HTTP 响应 → SearXNG 引擎异常(CAPTCHA/TooManyRequests/AccessDenied) |

**注意本层不做 suspend 状态管理**。异常在这里抛出,真正的"暂停这个引擎 N 秒"逻辑在 `searx/search/processors/abstract.py` 的 `SuspendedStatus`。二者是抛异常 → 捕获并 suspend 的关系(见第五节)。

---

## 二、架构与关键流程

### 2.1 一个后台事件循环,同步门面包异步

`client.py:213-240` 的 `init()`(模块导入时即执行)启动一个 **daemon 线程**跑 `asyncio.new_event_loop().run_forever()`,全局 `LOOP` 保存它。引擎代码调用的是 `network/__init__.py:96 request()` 这种同步函数,内部用 `asyncio.run_coroutine_threadsafe(network.request(...), get_loop())` 把协程扔进那个后台循环,再 `future.result(timeout)` 阻塞等结果(`__init__.py:101-108`)。超时会被转成 `httpx.TimeoutException`(`__init__.py:107-108`)。

含义:全进程只有一个事件循环线程,所有引擎的并发请求都在它上面跑;引擎侧看到的是普通的同步 requests 风格 API。

### 2.2 请求路径(一次引擎搜索)

1. `search/processors/online.py:124 init_network_in_thread()` 先给当前工作线程设三样东西:`set_timeout_for_thread(timeout_limit)`、`reset_time_for_thread()`、`set_context_network_name(engine.name)`(`online.py:126-130`)。于是本线程后续所有 `searx.network.*` 调用都走该引擎的 `Network` 且带该超时。
2. `online.py:_send_http_request()` 组装 headers/cookies/verify/redirects/`raise_for_httperror`,调 `searx.network.get/post`。
3. `__init__.py:request()` 取线程网络(`get_context_network`)、算超时(`_get_timeout`)、投递到事件循环。
4. `network.py:call_client()` 是核心:拿 client → 发请求 → 判重试 → `patch_response()`(内含 `raise_for_httperror`)。
5. 异常冒泡回 `online.py:search()` 的 try/except,分类后 `handle_exception(..., suspend=?)`。

### 2.3 Network / Client / SSLContext 三层缓存

- **Network**:每引擎一个(或多引擎共享,见 3.1)。持有连接池参数、代理 cycle、源 IP cycle、重试配置。
- **httpx.AsyncClient**:一个 Network 内部按 `key=(verify, max_redirects, local_address, proxies)` 缓存多个 client(`network.py:190-215 get_client`)。因为源 IP / 代理是轮换的,每换一个组合就可能新建并缓存一个 client。连接池(`httpx.Limits`)是**每 client** 的。
- **SSLContext**:全局按 `(proxy_url, cert, verify, trust_env)` 缓存于 `SSLCONTEXTS`(`client.py:51-58`),每次取用都 `shuffle_ciphers` 洗牌密码套件(TLS 指纹规避,`client.py:28-48`)。注释说每个 SSLContext 占 >500KB,而"大约每引擎一个 network",所以内存敏感 —— 这也是默认禁用明文 HTTP 的原因(见 3.2)。

---

## 三、逐问题详解

### 问题 1:httpx 客户端如何管理(连接池、HTTP/2、每引擎独立 network?)

**每引擎独立 Network:是。** `network.py:initialize()`(336-420)构建全部网络:

- 先建三个基础网:`__DEFAULT__`、`ipv4`(local_addresses=0.0.0.0)、`ipv6`(::)(`network.py:388-390`)。
- `outgoing.networks` 里显式声明的命名网络(`network.py:393-394`)。
- **遍历所有引擎**:若引擎没写 `network` 属性,就用引擎自身覆盖过的 outgoing 默认值单独建一个以引擎名为 key 的 Network(`network.py:397-405`);若 `engine.network` 是 dict 就用该 dict 建;若是 str 则作为**引用**共享已存在的同名网络(`network.py:410-412`)。
- 额外建 `image_proxy` 网络,参数同 default 但强制 `enable_http2=False`(`network.py:414-420`)。

**连接池:** `client.py:174-178` 用 `httpx.Limits(max_connections=pool_connections, max_keepalive_connections=pool_maxsize, keepalive_expiry=keepalive_expiry)`。注意命名映射(`network.py:355-357`):

- `outgoing.pool_connections` → `max_connections`(池上限,默认 100)
- `outgoing.pool_maxsize` → `max_keepalive_connections`(保活连接数;`settings.yml:189` 设 20,而 `settings_defaults.py:256` 默认 10)
- `outgoing.keepalive_expiry` → `keepalive_expiry`(默认 5.0s)

池是每个 httpx client 独立的,而一个 Network 可能有多个 client(见 2.3),所以实际连接上限会随源 IP/代理组合数放大。

**HTTP/2:** `outgoing.enable_http2`(默认 True,`settings_defaults.py:252`)透传给每个 transport(`client.py:145-157`、`149 http2=http2`)。`image_proxy` 网络强制关掉(`network.py:419`)。

**明文 HTTP 默认禁用(易踩坑):** `Network.__init__` 的 `enable_http` 默认 True,但 `initialize()` 的 `default_params['enable_http']=False`(`network.py:352`),所以**所有从 settings 建出来的引擎网络都禁用明文 HTTP**。实现方式:`new_client` 里给 `http://` 挂一个 `AsyncHTTPTransportNoHttp`(`client.py:192-193`),它对任何 http 请求抛 `httpx.UnsupportedProtocol('HTTP protocol is disabled')`(`client.py:79-80`)。这个空 transport 的构造函数被故意置空,就是为了不创建那 500KB 的 SSLContext(`client.py:61-77`)。

### 问题 2:outgoing 配置项各自作用

配置定义在 `settings_defaults.py:249-268`,样例注释在 `settings.yml:177-219`。逐项:

| 配置键 | 默认 | 作用 / 出处 |
| --- | --- | --- |
| `request_timeout` | 3.0 | 引擎级 `timeout` 的默认值(`engines/__init__.py:46`)。不是直接给 httpx,而是先变成 `engine.timeout`,再进超时协商(见下)。 |
| `max_request_timeout` | None | **全局超时硬上限**。`search/__init__.py:111-126` 用它和用户 `timeout_limit`、引擎默认超时协商出 `actual_timeout`:None 时不封顶;否则 `actual_timeout=min(..., max_request_timeout)`。 |
| `pool_connections` | 100 | `httpx.Limits.max_connections`(`client.py:175`)。 |
| `pool_maxsize` | 10(settings.yml 覆盖为 20) | `httpx.Limits.max_keepalive_connections`(`client.py:176`)。 |
| `keepalive_expiry` | 5.0 | 保活连接空闲存活秒数(`client.py:177`)。 |
| `enable_http2` | True | 是否启用 HTTP/2(`client.py:149`)。 |
| `verify` | True | TLS 校验;可为 bool 或证书路径 str(`settings_defaults.py:253`)。透传到 `get_sslcontexts`(`client.py:51-58`)。引擎可用 params.verify 覆盖(`online.py:175-177`)。 |
| `max_redirects` | 30 | httpx client 的 `max_redirects`(`client.py:201-206`),是 Network 缓存 client 的 key 之一。 |
| `retries` | 0 | **应用层**重试次数,进 `self.retries`,由 `call_client` 使用(见问题 3)。注意 **不是** httpx transport 层的 retries —— 后者被硬编码为 0(`network.py:207`)。 |
| `proxies` | None | 代理,str 或 {pattern: url/列表}(见问题 5)。 |
| `source_ips` | None | 出站源 IP,→ `local_addresses`(`network.py:358`)。str/list/CIDR,轮换使用(`network.py:114-135`)。 |
| `using_tor_proxy` | False | 是否走 Tor;会触发 `check_tor_proxy` 校验(`network.py:167-188`)。 |
| `extra_proxy_timeout` | 0 | **只在 Tor 场景加到 `engine.timeout`**(`engines/__init__.py:238`,函数名 `update_attributes_for_tor`,仅当 `using_tor_proxy` 且引擎有 `onion_url` 时执行)。普通代理并不会自动加这个补偿,这是个反直觉点。 |
| `networks` | {} | 命名网络定义,供引擎用 `network: <name>` 引用(`network.py:393-394`)。 |
| `useragent_suffix` | '' | UA 后缀,和 network 层无关(在 `utils.gen_useragent`)。 |

**超时叠加细节:** `__init__.py:_get_timeout`(73-93)在把请求投递前会 `timeout += 0.2`(开销补偿),并减去自 `start_time` 起已耗时;若没显式 timeout 则回退到线程超时,再兜底 120s。这是"每次请求的 httpx timeout",和上面 `actual_timeout`(整轮搜索的墙钟预算)是两层。

### 问题 3:重试逻辑(retries / retry_on_http_error)

核心在 `network.py:call_client()`(272-301):

```
retries = self.retries            # = outgoing.retries,默认 0
while retries >= 0:
    client = get_client(...)
    response = await client.request(...)
    if is_valid_response(response) or retries <= 0:
        return patch_response(...)   # patch_response 里做 raise_for_httperror
    # 否则(is_valid_response=False 且还有重试余量)落到底部 retries -= 1 继续
```

- `retries=0`(默认)→ 循环体只跑一次,永不重试。
- **`retry_on_http_error`** 决定"HTTP 状态码算不算失败要重试",由 `is_valid_response`(`network.py:262-270`)判定:
  - `True` → 400–599 全部算失败;
  - list → 状态码在列表里算失败;
  - int → 等于该码算失败;
  - 默认 **False** → `is_valid_response` 恒 True,即**默认不因 HTTP 错误码重试**,只因下面的异常重试。
  - ⚠️ `retry_on_http_error` 在 `initialize()` 的 default_params 里被写死 `False`(`network.py:363`),**没有对应的顶层 outgoing 配置键**,只能通过 `outgoing.networks.<name>` 或引擎自带 network dict 设置。
- **异常重试**(`network.py:288-301`):
  - `httpx.RemoteProtocolError`(服务器断连):第一次**不消耗** retries,关掉 client 换新的重来(`was_disconnected` 标记,`network.py:288-295`);第二次若 `retries<=0` 就抛。
  - `httpx.RequestError` / `httpx.HTTPStatusError`:`retries<=0` 才抛,否则 `retries-=1` 重试。
- **transport 层 retries 恒为 0**:`get_client` 调 `new_client(..., 0, ...)`(`network.py:207`),即 httpx 自己的连接级重试禁用。所有重试都在应用层这个 while 循环里。

### 问题 4:raise_for_httperror 里 CAPTCHA/TooManyRequests 等如何被识别与抛出(与 suspend 联动)

**识别与抛出**(`network/raise_for_httperror.py`):`raise_for_httperror(resp)`(61-79)只在 `status_code >= 400` 时动作,顺序:

1. `raise_for_captcha`(56-58)→
   - **Cloudflare**(`raise_for_cloudflare_captcha`,33-46):仅当 `Server` 头以 `cloudflare` 开头。
     - challenge(429/503 带 `__cf_chl_jschl_tk__=`,或 403 带 `__cf_chl_captcha_tk__=`,`is_cloudflare_challenge` 16-26)→ `SearxEngineCaptchaException(suspended_time=cf_SearxEngineCaptcha=1296000s≈15天)`。
     - firewall(403 + `cf-error-code 1020`,`is_cloudflare_firewall` 29-30)→ `SearxEngineAccessDeniedException(suspended_time=cf_SearxEngineAccessDenied=86400s)`。
   - **reCAPTCHA**(`raise_for_recaptcha`,49-53):503 且正文含 `https://www.google.com/recaptcha/` → `SearxEngineCaptchaException(recaptcha_SearxEngineCaptcha=604800s≈7天)`。
2. `402` / `403` → `SearxEngineAccessDeniedException`(默认 suspend 86400s)。
3. `429` → `SearxEngineTooManyRequestsException`(默认 suspend 3600s)。
4. 其它 ≥400 → `resp.raise_for_status()` → 原生 `httpx.HTTPStatusError`。

这些异常的类层次(`exceptions.py:60-110`):`SearxEngineCaptchaException` 和 `SearxEngineTooManyRequestsException` 都**继承** `SearxEngineAccessDeniedException`,后者携带 `.suspended_time`(默认值取各自 `SUSPEND_TIME_SETTING`,即 `search.suspended_times.*`,`settings_defaults.py:202-209`)。

**调用点**:`network.py:patch_response()`(246-260)在 `do_raise_for_httperror` 为真时调用 `raise_for_httperror`,失败先 `logger.warning` 再原样重抛。`do_raise_for_httperror` 默认 True(`network.py:238-244 extract_do_raise_for_httperror`;online 引擎 params 也默认 `raise_for_httperror=True`,`online.py:109`、`192`)。

**与 suspend 联动**(`search/processors/`):

- `online.py:search()`(275-281)显式 catch `SearxEngineCaptchaException / SearxEngineTooManyRequestsException / SearxEngineAccessDeniedException` → `handle_exception(..., suspend=True)`;超时(259-266)和其它 httpx 错误(267-274)也 `suspend=True`;未知异常(282-284)`suspend=False`。
- `abstract.py:handle_exception()`(175-201):① `result_container.add_unresponsive_engine(engine, error_message)` 记录失败(error_message = 异常的 `module.QualName`,如 `searx.exceptions.SearxEngineTooManyRequestsException`);② 更新 metrics;③ 若 `suspend`,从 `SearxEngineAccessDeniedException.suspended_time` 取时长,调 `self.suspended_status.suspend(suspended_time, error_message)`。
- `abstract.py:SuspendedStatus.suspend()`(91-102):`continuous_errors += 1`;`suspend_end_time = now + suspended_time`(若 `suspended_time is None` 则取 `min(max_ban_time_on_fail=120, ban_time_on_fail=5)=5s`);记 `suspend_reason`。
- **下一次搜索前的短路**:`abstract.py:extend_container_if_suspended()`(235-241)在派发请求前检查 `is_suspended`,若还在暂停期就**直接** `add_unresponsive_engine(engine, suspend_reason, suspended=True)` 并跳过请求(根本不发)。成功一次则 `resume()` 清零(`abstract.py:104-109`、被 `extend_container` 调,232-233)。
- **suspend 状态的粒度**:`SUSPENDED_STATUS` 以 network 的 `id()` 为 key(`abstract.py:120-122`),所以**共享同一 network 的多个引擎共享 suspend 状态**。

**失败记录的最终数据结构**:`results.py:47-50` `UnresponsiveEngine(engine, error_type, suspended)` 三元组 NamedTuple,存 `ResultContainer.unresponsive_engines` 这个 set(`results.py:75`、`add_unresponsive_engine` 249-255)。

### 问题 5:将来给引擎挂代理,配置入口在哪

三种入口,优先级从全局到单引擎:

1. **全局** `outgoing.proxies`(`settings.yml:202-205` 注释样例;`settings_defaults.py:262`)。格式两种:
   - str:视为 `all://`(`network.py:141-142`)。
   - dict:`{pattern: url}` 或 `{pattern: [url1, url2]}`,列表会 round-robin 轮换(`network.py:150-156 get_proxy_cycles`)。pattern 支持 requests 风格简写,经 `PROXY_PATTERN_MAPPING`(`network.py:28-39`)映射成 httpx 的 `http://`/`https://`/`socks5://` 等。
2. **命名网络** `outgoing.networks.<name>.proxies`,引擎用 `network: <name>` 引用(`network.py:393-394`、`410-412`)。适合"一组引擎共用一个代理出口"。
3. **单引擎** 在引擎配置里直接写 `network:` dict 或在引擎属性上设 `proxies`(`network.py:397-407` 会把引擎上存在的 `proxies` 等属性并进该引擎专属 network)。

**代理实现**(`client.py:new_client` 180-195):遍历 `proxies` 的每个 pattern:

- socks4/socks5/socks5h → `get_transport_for_socks_proxy`(`client.py:114-142`),走 `httpx_socks.AsyncProxyTransport`(封装为 `AsyncProxyTransportFixed` 以把 python_socks 异常映射成 `httpx.ProxyError`,`client.py:97-111`)。`socks5h://` 会设 `rdns=True`(DNS 在代理端解析,`client.py:121-125`)。
- 其它(http/https 代理) → `get_transport` 用 httpx 原生 `Proxy`(`client.py:154`)。
- 通过 httpx `mounts` 按 pattern 路由(`client.py:201-206`)。

**Tor**:`using_tor_proxy=True` 时,`get_client` 建好 client 后跑 `check_tor_proxy`(`network.py:211-213`),它要求所有 mount 的 transport 都启用 rdns(即 socks5h),并请求 `https://check.torproject.org/api/ip` 验证 `IsTor`(`network.py:167-188`),否则抛 `httpx.ProxyError`。启动期 `check_network_configuration()`(`network.py:318-333`)会对所有 tor 网络预检,失败则 `RuntimeError`。

---

## 四、对 P1 的影响与行动建议

我们的架构是**网关只调 SearXNG 的 JSON API,不改一行源码、不进 Python 层**。所以本层大部分机制对我们是"黑盒里在跑的东西",但有几处直接决定我们 `POST /v0/search` 的行为和 `meta.engines_failed` 能拿到什么:

### 4.1 失败引擎:JSON API 到底给我们什么

关键事实:`webutils.py:162-174 get_json_response` 输出的 JSON 里,失败引擎字段叫 **`unresponsive_engines`**,值是 `get_translated_errors` 的结果 —— 一个**排序后的 `[engine_name, 已翻译的用户文案]` 二元组列表**(`webutils.py:70-82`),**不是**结构化对象:

- 原始的 `error_type`(异常类名,如 `searx.exceptions.SearxEngineTooManyRequestsException`)在 `get_translated_errors` 里被 `exception_classname_to_text` 映射表(`webutils.py:41-67`)翻成本地化文案(如 `"too many requests"`、`"CAPTCHA"`、`"access denied"`、`"timeout"`),**原始类名被丢弃**。
- `suspended=True` 会被前缀成 `"Suspended: " + 文案`(`webutils.py:78-79`)。
- 文案随 SearXNG 的 UI 语言(Accept-Language / locale)变化,`gettext` 本地化。

**对 P1 `meta.engines_failed` 的直接后果**:JSON API 无法直接给出"结构化的失败原因枚举"。我们要么:
(a) 接受降级 —— `engines_failed` 只放 `{engine, message}`,message 直接用 SearXNG 的翻译串;并**强制 SearXNG 以 `en` locale 输出**(请求时带固定 `Accept-Language`/`locale`)以保证串稳定,再在网关侧用一张小映射表把英文串反解成我们的 `reason` 枚举(captcha/too_many_requests/access_denied/timeout/http_error/parse_error/...)。
(b) 若要真正结构化、locale 无关的原因,只能改 SearXNG 源码 —— 与"不改一行"决策冲突,**不建议**。

建议走 (a),并在实测中固化英文串清单(见待确认)。

### 4.2 suspend 会让引擎"静默"从列表里减少 —— 必须显式暴露

被 suspend 的引擎在暂停期内**根本不发请求**(`abstract.py:235-241`),但它仍会作为 `suspended=True` 出现在 `unresponsive_engines` 里,文案带 `Suspended:` 前缀。这正好满足 P1"禁止静默丢弃"的要求 —— 我们**必须把 `unresponsive_engines` 全量透传到 `meta.engines_failed`**,不能只看 `results` 里出现过的引擎。特别注意:一个我们请求了、但因上一轮 429 正被暂停的引擎,不会有任何结果、也不会报新错,只会以 `Suspended: too many requests` 出现在这个列表 —— 漏读它就等于静默丢弃。

### 4.3 我们请求的引擎 vs SearXNG 实际跑的引擎

`engines_failed` 要"显式暴露失败",需要对账:**我们在请求里指定的 engines 集合** 减去 **`results` 里出现的 engine** 减去 **`unresponsive_engines` 里出现的 engine** = "既没结果也没报错"的引擎。这类引擎可能是被 SearXNG 以别的理由跳过的(不支持该 time_range/分页/category、被 disabled 等,见 `abstract.py:get_params` 返回 None 的分支 254-265)。建议 `meta` 里除 `engines_failed` 外再给一个 `engines_no_result` 或类似,避免把"正常无结果"误判为失败。

### 4.4 超时:我们能传给 JSON API 的只有 timeout_limit

SearXNG 的 JSON 接口读表单参数 `timeout_limit`(`webapp.py:776` 有引用),它进 `search_query.timeout_limit` 后与 `outgoing.max_request_timeout` 和引擎默认超时协商(`search/__init__.py:111-126`)。所以:

- P1 若想给用户超时旋钮,应映射到 SearXNG 的 `timeout_limit` 表单字段;
- 但真正的封顶是部署侧 `outgoing.max_request_timeout` / `request_timeout`,这些在**我们独立部署的 SearXNG Docker 的 settings.yml 里**配置,不是每请求可变。建议在 SearXNG settings.yml 里把 `request_timeout`/`max_request_timeout` 调到适合我们 SLA 的值,并在网关侧对 `timeout_limit` 做上限保护。

### 4.5 代理/多出口是部署侧配置,不是网关请求参数

将来给引擎挂代理(4.x 的问题 5),入口全在 **SearXNG 的 settings.yml**(`outgoing.proxies` / `outgoing.networks` / 引擎级 `network`),对我们网关透明。P1 阶段无需在网关实现任何代理逻辑;只需在部署文档里记下这三个入口。若要"按引擎分代理出口",用 `outgoing.networks` + 引擎 `network: <name>` 引用最干净。

### 4.6 具体行动清单

1. 网关请求 SearXNG 时**固定 `Accept-Language`/UI locale 为 `en`**(或显式 `language` 无关的调用),让 `unresponsive_engines` 文案稳定可解析。
2. `meta.engines_failed` **全量映射** JSON 的 `unresponsive_engines`,保留 `suspended` 语义(检测 `Suspended:` 前缀或拆出来单列 `suspended: true`)。
3. 网关侧建一张 `英文错误文案 → reason 枚举` 的映射表(源:`webutils.py:41-67`),把 timeout/CAPTCHA/too many requests/access denied/HTTP error/proxy error/parse error 归类。
4. 做"请求 engines 与返回 engines"对账,区分 `engines_failed`(有错)与"被跳过/无结果"。
5. 部署侧 settings.yml 校准 `request_timeout`、`max_request_timeout`、`pool_connections/pool_maxsize`;代理需求走 `outgoing.proxies`/`networks`。

---

## 五、待实测确认的问题

1. `unresponsive_engines` 二元组里的**引擎名是我们请求时用的名字还是 SearXNG 内部规范名**?大小写/别名是否一致?(影响对账,需实跑 JSON API 比对。)
2. `en` locale 下 `exception_classname_to_text` 各串的**确切英文文本**(含 `Suspended: ` 前缀的完整形态),要以运行实例的 JSON 输出为准固化映射表 —— 源码里是 `gettext(...)`,运行时值取决于翻译目录。
3. JSON API 是否**始终**返回 `unresponsive_engines` 字段(含空列表),还是可能缺省?(`get_json_response` 恒放该键,但需确认我们打的 SearXNG 版本行为一致。)
4. 通过 JSON API 的 **表单/GET 参数能否覆盖 `retries`/`retry_on_http_error`/代理**?从源码看这些是部署级(settings.yml / 引擎 network),**不经**每请求参数,但需实测确认没有隐藏入口。
5. `timeout_limit` 作为表单参数经 JSON 接口传入时,是否被 `max_request_timeout` 静默截断、以及截断后 JSON 是否有任何提示?(源码只 `logger.debug`,响应里可能无痕。)
6. 被 suspend 的引擎在 JSON 响应里,除了进 `unresponsive_engines`,`results` 是否绝对为空 —— 确认"suspend 即完全无结果",以免对账逻辑误判。
7. 单个 Network 因源 IP/代理轮换缓存多个 client 时,`pool_connections` 的实际总并发上限如何叠加(每 client 独立池)—— 若我们高并发压 SearXNG,需实测其真实出站连接规模。
