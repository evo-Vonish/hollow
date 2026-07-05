# SearXNG 综合总览 · 为 `POST /v0/search` 网关而写

> 快照:`vendor/searxng/` @ commit `a643858`(未修改;仓库内 vendor 提交 `bc1bc05`)。
> 本文整合 10 份子系统研读笔记(`01-*.md` ~ `10-*.md`),给出:整体架构与一次 `format=json` 请求的端到端生命周期、`/v0/search` 契约字段映射、P1 部署与对接行动清单、风险坑点清单、待实测问题汇总,以及跨笔记矛盾的源码裁决。
> 架构约束(不可动摇):**原版 SearXNG 用官方 Docker 镜像独立部署,一行源码不改;我们的 FastAPI 网关只调它的 `/search?format=json` HTTP API。**
> 读者:以后维护这套网关的自己 + 首席架构师复盘。所有结论标 `文件:行号`。

---

## 0. 一句话结论(TL;DR)

SearXNG 对我们只是一个"HTTP JSON 搜索聚合器":我们 `POST form-urlencoded` 到 `/search?format=json`,它并发扇出到多引擎、在统一 deadline 内收集去重、返回一个**只有 7 个顶层键**的 JSON。对 P1 最关键的三件事:

1. **要能拿到 JSON**,必须在部署侧 `settings.yml` 把 `search.formats` 开出 `json`(默认只有 `html`,否则 `403`)。
2. **`meta.engines_failed` 的上游是 JSON 的 `unresponsive_engines`**,但它已被压平成 `[[engine, 翻译文案], ...]`——**丢失了结构化 `error_type` 与 `suspended` 布尔**;且 `display_error_messages:false` 的引擎失败会彻底不出现(默认 `True`,别去关它)。真正做到"禁止静默丢弃"必须靠**集合差集对账**兜底。
3. **`count` 参数 SearXNG 根本不支持**——只能在网关侧截断/多页聚合。

---

## 1. 整体架构与端到端生命周期

### 1.1 ASCII 架构图(我们视角)

```
                         ┌───────────────────────────────────────────────────────┐
   用户 ── HTTP ──▶ FastAPI 网关 (我们写)                                          │
                     │  · 参数白名单校验 (engines/categories/time_range/…)         │
                     │  · bang 防护 (拦 !! : < 开头 token)                          │
                     │  · count 截断/多页聚合                                       │
                     │  · engines_failed 对账 (请求集 − 结果集 − unresponsive 集)   │
                     └───────┬───────────────────────────────────────────────────┘
                             │  POST form-urlencoded, format=json
                             │  固定请求头 (UA/Accept: text/html/Accept-Language=en)
                             ▼
   ┌──────────────────────────── Docker 网络内 (SearXNG 不发布宿主端口) ───────────────────────────┐
   │  SearXNG (Granian WSGI, 单 worker)                                                             │
   │                                                                                                │
   │  webapp.py /search (616)                                                                        │
   │    ├─ pre_request (458): 合并 POST form + GET args, POST 优先, 只解析 form-urlencoded          │
   │    ├─ 403 闸门 (630): format 不在 settings.search.formats → abort(403)                          │
   │    ├─ get_search_query_from_webapp (webadapter.py:221): form → SearchQuery                      │
   │    │     └─ RawTextQuery (query.py:250): 解析 q 里的 ! : < !! 修饰符 (会架空参数)               │
   │    ├─ SearchWithPlugins.search (search/__init__.py:201)                                         │
   │    │     ├─ pre_search 插件钩子                                                                 │
   │    │     ├─ Search.search (174):                                                                │
   │    │     │    ├─ search_external_bang → redirect_url (短路, 663 提前 302, 连 json 也被劫持)     │
   │    │     │    ├─ search_answerers → 命中即短路, 只回 answers, 不调引擎                          │
   │    │     │    └─ search_standard (160):                                                         │
   │    │     │         ├─ _get_requests (78): 逐引擎 get_params 翻译参数; 算 actual_timeout         │
   │    │     │         │      · 不支持 paging/time_range/超 max_page → return None → 静默跳过        │
   │    │     │         │      · 熔断中 → add_unresponsive(suspended=True), 不发请求                  │
   │    │     │         └─ search_multiple_requests (136): 每引擎一个裸 Thread 全并发                 │
   │    │     │              └─ 各 processor.search → online.py 发 HTTP                               │
   │    │     │                   └─ searx/network/ (每引擎独立 httpx 连接池)                         │
   │    │     │                        └─ raise_for_httperror: 429/403/CF/reCAPTCHA → 语义化异常     │
   │    │     │                             └─ handle_exception → add_unresponsive_engine + suspend   │
   │    │     ├─ on_result 插件 (逐条改写, 如 tracker_url_remover 去追踪参数改 url)                   │
   │    │     ├─ post_search 插件 (追加 answers)                                                      │
   │    │     └─ result_container.close (183): 打分 calculate_score, 置 _closed                       │
   │    └─ 输出分叉:                                                                                 │
   │         · redirect_url? (663) → 302  ← 外部 bang 在这里劫持 json                                 │
   │         · format==json (672) → webutils.get_json_response (162) → JSON                           │
   │         · redirect_to_first_result (695, section 4) → 302  ← 只影响 html/csv/rss, 不碰 json      │
   └────────────────────────────────────────────────────────────────────────────────────────────────┘
```

三层核心(SearXNG 内部):
- **编排层** `search/__init__.py` + `search/models.py`:`SearchQuery` 容器 → `Search`/`SearchWithPlugins` 扇出/并发/超时/收尾。
- **处理器层** `search/processors/*.py`:按 `engine_type`(online / offline / online_dictionary / online_currency / online_url_search)分派;负责参数翻译、发请求、异常/超时/熔断归类。
- **结果层** `results.py` + `result_types/`:`ResultContainer` 去重合并、打分排序、登记 `unresponsive_engines`。

### 1.2 一次 `format=json` 请求的生命周期(时间顺序)

1. **入口与合并**:`pre_request`(webapp.py:458)把 POST form 与 GET args 合并(POST 优先),**只解析 `application/x-www-form-urlencoded`**——发 JSON body 会被完全忽略。
2. **格式闸门**:`format` 不在 `['html','csv','json','rss']` 静默降级 html;合法但不在实例 `search.formats` 里 → **HTTP 403**(webapp.py:630-631)。
3. **无 q**:`format=json` 时返回 `{"error":"No query"}` + HTTP 400(webapp.py:642)。
4. **构造 SearchQuery**:`webadapter.py` 解析各参数并校验(见 §2 映射表);`RawTextQuery`(query.py:250)解析 `q` 里的 bang/修饰符,剥离后才是真正搜索词。校验失败抛 `SearxParameterException` → HTTP 400。
5. **短路检查**:external bang → `redirect_url`(**json 也会被 302**,webapp.py:663);answerer 命中 → 只回 `answers`、**不调任何引擎**(`results` 空、`unresponsive_engines` 空)。
6. **扇出**:`_get_requests` 逐引擎 `get_params` 翻译参数;不适用(不支持分页/time_range、超 max_page、online_* 语法不匹配)→ `return None` **静默跳过**(既不进 results 也不进 unresponsive);熔断中 → 以 `suspended=True` 记入 unresponsive 并跳过。每个可发引擎起一个**裸 `threading.Thread`**(无线程池),全并发。
7. **超时收束**:所有线程共享 `start_time + actual_timeout` 的 deadline,逐线程 `join(remaining_time)`。**总墙钟上界 ≈ `actual_timeout`,与引擎数无关**。超时线程被标记 `_timeout=True`,记 `add_unresponsive_engine(engine,'timeout')`;迟到结果被 `_closed`/`_timeout` 两道防线丢弃。
8. **失败记账**:所有异常/超时/熔断经 `handle_exception`(abstract.py:175)统一写入 `ResultContainer.unresponsive_engines`(三元组 `UnresponsiveEngine(engine, error_type, suspended)`,results.py:47),**但仅当 `engines[name].display_error_messages` 为 `True`(默认 True)才写**(results.py:254)。
9. **融合**:`ResultContainer` 按 `hash(result)` 去重合并(template + parsed_url去scheme + img_src),累积 `engines`(多来源集合)与 `positions`;`close()` 时 `calculate_score` 打分。
10. **插件后处理**:on_result 逐条改写(tracker_url_remover 会改 `result.url`),post_search 追加 answers。
11. **序列化**:`webutils.get_json_response`(webutils.py:162-174)输出 7 键 JSON;`unresponsive_engines` 经 `get_translated_errors`(webutils.py:70-82)压平为 `[[engine, 翻译文案], ...]`。

---

## 2. `/v0/search` 契约 ↔ SearXNG JSON API 字段映射

### 2.1 请求参数映射(我们的入参 → SearXNG form 字段)

调用方式:**`POST /search`,body = form-urlencoded(非 JSON),必带 `format=json` 与 `q`**。列表类参数用逗号 join。

| 我们的 `/v0/search` 参数 | SearXNG form 字段 | 取值/校验(源码出处) | 备注 |
|---|---|---|---|
| `q`(查询词) | `q` | 必填;先经 `RawTextQuery` 剥 bang(query.py:250) | **必须先做 bang 防护**(见 §4) |
| `engines`(list) | `engines`(逗号分隔 name) | 未知名**静默丢弃**;映射到 `EngineRef(name, engines[name].categories[0])`(webadapter.py:181-189) | **显式点名绕过 `disabled` 过滤**——默认 disabled 的引擎点名仍会调用 |
| `categories`(list) | `categories`(逗号分隔) | 每项须在 `searx.engines.categories`,未知**静默丢弃**(webadapter.py:117-119) | 合法性看全局注册表,不看 `categories_as_tabs` |
| `time_range` | `time_range` | 仅 `day/week/month/year`(或空);其他 → **HTTP 400**(webadapter.py:95-101) | 引擎不支持时该引擎**静默跳过** |
| `language` | `language` | `VALID_LANGUAGE_CODE` 正则或 `auto`;非法 → 400(webadapter.py:55-72) | locale 用 `-` 分隔(`zh-TW`);`:lang` bang 会覆盖它 |
| `safesearch` | `safesearch` | 数字 `0/1/2`,越界 → 400(webadapter.py:75-92) | 部分引擎"声明支持但实现空操作"(如 ddg) |
| `count` | **无对应** | SearXNG 全仓库无 count/limit/per_page | **网关侧自行截断/多页聚合**;每引擎每页条数不可控 |
| (超时旋钮) | `timeout_limit`(float) | 也可用 `q` 里 `<n` 修饰符;经 `max_request_timeout` 协商(search/__init__.py:111-126) | 需部署侧先配 `outgoing.max_request_timeout` 才能真正主导(见 §3/§4) |
| `pageno`(分页) | `pageno` | 数字 ≥1,否则 400(webadapter.py:48-52) | 每引擎各自翻第 pageno 页;超 `max_page` 静默跳过 |

**固定请求头**(即使关 limiter 也作为防御性默认):自定义非黑名单 `User-Agent`、`Accept: text/html,...`(必须含 `text/html`)、`Accept-Language: en-US,en;q=0.9`(非空)、`Accept-Encoding: gzip, deflate`;并固定 UI locale 为 `en` 以稳定失败文案。

### 2.2 响应字段映射(SearXNG JSON → 我们的返回)

JSON 顶层**只有 7 键**(webutils.py:164-172),别指望其他:
`query` / `results` / `answers` / `corrections` / `infoboxes` / `suggestions` / `unresponsive_engines`。
**没有** `number_of_results`(总数)、**没有** `paging`(是否有下一页)、**没有** `engine_data`——这些网关得自算。

| 我们的返回字段 | SearXNG JSON 来源 | 结构/注意 |
|---|---|---|
| 每条结果的 `engine` 来源 | `results[].engines`(数组) | **用复数 `engines`(所有命中引擎),不要用单数 `engine`(仅首个主引擎)** |
| 结果排序 | `results[].score`(float) | SearXNG 融合后最终分,可直接排序;跨实例不可比(依赖 `weight`);`priority=low` 分 0 沉底 |
| 结果 URL | `results[].url` | 若开 tracker_url_remover 会被去追踪参数改写;`parsed_url` 是 **6 元素数组** `[scheme,netloc,path,params,query,fragment]` 不是对象 |
| 结果元数据 | `title/content/publishedDate/template/category/positions/...` | `publishedDate` 是 ISO8601 串;`length`(timedelta)→秒数 float;`category` 被重写为引擎首个分类,非请求 category |
| 分页元信息 `number_of_results`/`has_next_page` | **无** | 网关自算(靠结果数/pageno) |
| 直答 | `answers[]`(与 results 结构不同) | answerer/plugin 的 `engine` 名是 `answerer:<kw>` / `plugin:<id>`,**非真实引擎**,单独归类 |
| 信息框 | `infoboxes[]` | LegacyResult dict,字段异构(urls/attributes/img_src);wikipedia 默认只进这里(见 §4) |
| **`meta.engines_failed`** | `unresponsive_engines` | 见 §2.3 —— **本契约最关键的落差** |

### 2.3 `engines_failed` ↔ `unresponsive_engines`(确切结构 + 三层落差)

**底层真身(进程内,拿不到)** `results.py:47-50`:
```python
class UnresponsiveEngine(t.NamedTuple):
    engine: str        # 引擎名
    error_type: str    # 'timeout' | 异常全限定名(如 httpx.ConnectTimeout / searx.exceptions.SearxEngineTooManyRequestsException) | 熔断 suspend_reason
    suspended: bool     # True=熔断跳过(本轮没真正请求)
```

**JSON 出口(我们实际拿到的)** `get_translated_errors`(webutils.py:70-82):
```
unresponsive_engines: [ [engine_name, "已翻译且合并的人类文案"], ... ]   # 按引擎名排序的二维数组
```
- `error_type` 被 `exception_classname_to_text`(**webutils.py:41-67**,已实测确认此行号)映射成本地化文案:`timeout` / `HTTP error` / `HTTP connection error` / `HTTP protocol error` / `network error` / `proxy error` / `CAPTCHA` / `too many requests` / `access denied` / `server API error` / `parsing error`(即 `SearxEngineXPathException`/`KeyError`/`JSONDecodeError`/`lxml.etree.ParserError`)/ `SSL error: certificate validation has failed`;**表外一律兜底 `unexpected crash`**(key=`None`)。
- `suspended=True` 只体现为文案前缀 `gettext('Suspended') + ': '`(webutils.py:78-79),不再是独立布尔。
- 文案随 UI locale 变(gettext);**故必须固定实例 locale=en**。

**三层"静默丢弃"口子(P1 必须显式补上)**:
1. **`display_error_messages:false` 的引擎失败不写 unresponsive**(results.py:254)。默认 `True`(engines/__init__.py:47,已实测),**部署时别关它**;仍需靠对账兜底防误配。
2. **能力不适用被跳过**(不支持 paging/time_range、超 max_page、online_* 语法不匹配):`get_params` 返回 None,既不进 results 也不进 unresponsive——这是"不适用"不是"失败"。
3. **引擎名未注册/init 失败**:`_get_requests` 直接 continue,无痕。

**网关兜底算法**:
```
requested        = 本次请求的 engines 集合
seen_in_results  = ∪ results[].engines
seen_in_failed   = { e[0] for e in unresponsive_engines }
engines_failed   = seen_in_failed            (原样透出 [engine, message];message 含 "Suspended: " 前缀者标 suspended=true)
engines_silent   = requested − seen_in_results − seen_in_failed   (归入 engines_failed, error_type=silent/unknown, 或单列)
```
> `suspended=True`(熔断跳过)语义上是"暂时不可用"而非"本次真失败",可在 `engines_failed` 里保留 `suspended` 标记以便产品区分/告警。熔断状态按 **network** 分组进程级共享(abstract.py:120-122),会跨请求跨引擎相互影响。

---

## 3. P1 部署与对接行动清单(按执行顺序,已去重整合)

### 3.1 SearXNG `settings.yml`(合并模式,挂到容器 `/etc/searxng/settings.yml`)

```yaml
use_default_settings: true          # 合并模式:继承上游全部引擎定义,只覆盖下面几项

server:
  secret_key: "<强随机>"            # 必改:占位符 ultrasecretkey 不安全;推荐用 SEARXNG_SECRET 注入
                                     # (挂了自己的 settings.yml 后 entrypoint 不再自动随机化)
  bind_address: "0.0.0.0"           # 容器内必须(但 Granian 实际绑 GRANIAN_HOST=::,此键对 Granian 无效,留着无害)
  limiter: false                     # 关闭:内部可信调用,限流放网关层做,且免 valkey 依赖
  public_instance: false             # 保持 false(true 会强制 link_token,把 API 客户端掐死)
  base_url: false                    # 内网直连可留 false(待实测图片代理/分页链接是否受影响)

search:
  formats: [json, html]             # ★头号必改:不含 json 则 /search?format=json 一律 403
  safe_search: 0

outgoing:
  request_timeout: 5.0              # 每引擎默认超时
  max_request_timeout: 10.0         # ★显式配置,否则网关传的 timeout_limit 受 default_timeout 牵制只能压低不能主导
```
- **不列 `engines:`**:靠网关请求的 `engines=` 显式点名即可调用任意引擎(含默认 disabled 的)。若要收敛白名单,用 `use_default_settings.engines.keep_only: [...]`(此时 `use_default_settings` 从 true 变 dict,仍是合并模式)。
- **引擎特调**(可选):`wikipedia` 若要进主结果流须配 `display_type: [list]`(默认只进 infobox);`arxiv` 不在 general 分类(在 science),要么点名 `engines=arxiv` 要么 `categories=science`;所有引擎保持 `display_error_messages` 默认 true。注意**重名 name / 重复 shortcut 会让容器启动即 `sys.exit(1)`**。
- **环境变量**只覆盖白名单键(`SEARXNG_SECRET`/`_BIND_ADDRESS`/`_LIMITER`/...);**`search.formats` 无环境变量,只能改 yml**。

### 3.2 docker-compose(单容器,不部署 valkey)

```yaml
name: hollow
services:
  searxng:
    image: docker.io/searxng/searxng:2026.x.x-xxxxxxxxx   # 固定 日期-短哈希 tag,禁用 latest
    restart: unless-stopped
    expose: ["8080"]                        # 不写 ports:,不发布宿主端口;仅同网络 gateway 可达
    volumes:
      - ./core-config/:/etc/searxng/:Z      # 放上面的 settings.yml
      - searxng-cache:/var/cache/searxng/
    environment:
      - SEARXNG_SECRET=<强随机>
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/healthz"]  # /healthz 永远绕过 limiter
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
  gateway:
    build: ./gateway
    depends_on:
      searxng: { condition: service_healthy }
    expose: ["8000"]                        # 只有 gateway 经 Caddy 对外
volumes:
  searxng-cache:
```
- Web server 是 **Granian**(非 uwsgi),单 worker;并发不足优先调 `GRANIAN_BLOCKING_THREADS` 而非加 worker(加 worker 翻内存 + 破坏 botdetection)。
- 需持久化 `/etc/searxng`(配置)与 `/var/cache/searxng`(faviconcache 等);valkey `/data` 只在开 limiter 时才需。
- 升级:固定 tag + `down/pull/up`,升级前 diff 官方新版 `docker-compose.yml` / `.env.example`。

### 3.3 limiter 决策

**保持关闭(`limiter:false` + `public_instance:false`,默认值)**——这是最干净方案。理由:我们是唯一可信内网调用方,limiter 开启后对 `/search?format=json` 有多处误杀,尤以 **`ip_limit` 的 `API_MAX=4/小时`(写死常量,不可 toml 调)** 直接堵死高频代理。若运维坚持开:必须把网关来源 IP 加入 `limiter.toml` 的 `[botdetection.ip_lists] pass_ip`(**`trusted_proxies` 不是白名单**),并接受 valkey 成为硬依赖。

### 3.4 FastAPI 网关对接要点

1. **调用**:`POST /search`,form-urlencoded,`format=json` + `q`,列表参数逗号 join;固定请求头(§2.1)。
2. **入参白名单预校验**:`time_range`∈{day,week,month,year}、`safesearch`∈{0,1,2}、`categories`/`engines` 对照 SearXNG `/config` 返回的注册表,提前拒非法值;**自校验 engine 名合法性**避免 webadapter.py:185 静默跳过。
3. **bang 防护**:检测/转义 `q` 中首字符为 `!` `:` `<` 的 token,尤其拦截 `!!` 外部 bang(否则 SearXNG 返回 302 而非 JSON,连 json 请求也被劫持)。
4. **count 自实现**:截断合并结果,或循环拉多页 `pageno` 凑数(注意放大上游引擎请求 → DDG 等反爬压力)。
5. **`engines_failed` 对账**(§2.3 算法):全量透出 `unresponsive_engines` + 集合差集兜底 silent 失败;解析 `Suspended:` 前缀为 `suspended` 标记;建"英文文案→reason 枚举"映射表(源:webutils.py:41-67)。
6. **区分错误类型**:被 limiter 拦是整包 HTTP 429/302(无 JSON body);answerer 短路是 200+`answers` 非空+`results` 空+`unresponsive` 空(非全引擎失败,别误报);引擎部分失败是 200+JSON 内 `unresponsive_engines`。三者映射到不同网关错误。
7. **每条结果来源**取 `results[].engines`(复数数组);answers 里的 `answerer:`/`plugin:` 前缀来源单独归类。
8. **网关 HTTP client 超时**略大于 SearXNG `actual_timeout`(deadline + 网络 overhead),避免网关先超时切断正常的 engines_failed 汇报。
9. **探活**用 `/healthz`(纯文本 OK,永远绕过 limiter)。
10. **结果缓存**必须网关自建;SearXNG 的 `cache.py` 只缓存图标/汇率/引擎元数据,不缓存搜索结果。
11. **plugins 决策**:部署侧显式写全 `plugins:` 段并 review;建议保留 `tracker_url_remover`(纯本地去追踪参数,给 AI 干净 URL,但会改 `result.url`,需原始 URL 则网关另存);担心 `self_info` 回显内网 IP/UA 可设 `active:false`。

---

## 4. 风险与坑点清单(按严重度)

| 严重度 | 坑点 | 出处 | 应对 |
|---|---|---|---|
| 🔴 阻断 | 不开 `search.formats:[json]` → `/search?format=json` 全 403 | webapp.py:630 | settings.yml 必改(§3.1) |
| 🔴 阻断 | 外部 bang `!!g foo` 让 `/search` 在 663 提前 302,**连 json 请求也被劫持**(早于 json 分支 672) | webapp.py:663 | 网关 bang 防护(§3.4.3) |
| 🔴 阻断 | limiter 开启后 `API_MAX=4/小时`(写死常量)堵死高频代理;且 `Accept` 须含 text/html、UA 不能是 python-* | ip_limit.py:79-108, http_accept.py:35 | 保持 limiter 关闭 + 固定请求头 |
| 🟠 高 | `unresponsive_engines` 丢失结构化 `error_type`/`suspended`,只剩翻译文案;文案随 locale 变 | webutils.py:70-82 | 固定 locale=en + 文案→reason 映射表 |
| 🟠 高 | `display_error_messages:false` 的引擎失败**彻底不进** unresponsive(静默丢弃) | results.py:254 | 别关该项(默认 true)+ 集合差集对账兜底 |
| 🟠 高 | 能力不适用(不支持 time_range/paging、超 max_page、online_* 语法不匹配)被 `get_params` 静默跳过,无痕 | abstract.py:255-265 | 对账区分 skipped vs failed |
| 🟠 高 | answerer 命中(query 以 random/min/max/avg/sum/prod/range 开头且产出应答)**短路整个引擎搜索**,只回 answers | search/__init__.py:177 | 网关识别"200+answers+results空+unresponsive空"为短路非失败 |
| 🟠 高 | SearXNG 无 `count`/`number_of_results`/`paging`,每引擎每页条数不可控 | 全仓库无解析 | count 网关侧实现 |
| 🟡 中 | `q` 里 `!engine` 置 `specific=True`,**完全跳过** form 的 engines/categories 参数;`:lang`/`<n` 覆盖 language/timeout | webadapter.py:269-276, query.py | bang 防护统一处理 |
| 🟡 中 | `timeout_limit` 在 `max_request_timeout=None`(默认)时只能压低不能主导 deadline | search/__init__.py:118-126 | settings.yml 显式配 max_request_timeout |
| 🟡 中 | 熔断状态按 **network** 分组进程级共享,一个引擎被封可能连累同 network 的其他引擎显示 suspended | abstract.py:120-122 | 监控/告警时知悉;engines_failed 保留 suspended 标记 |
| 🟡 中 | wikipedia 默认 `display_type:[infobox]`,结果只进 `infoboxes` 不进主 `results` | wikipedia.py:77 | 需配 `display_type:[list]` |
| 🟡 中 | arxiv 在 science 分类,不在 general;category=general 搜不到它 | arxiv.py:35 | 点名 engines=arxiv 或 categories=science |
| 🟡 中 | duckduckgo 反爬最重(vqd 机制、IP 级封禁非 session、第2页起需 vqd) | duckduckgo.py:117-251 | 多用户共享出口 IP 慎开 DDG;优先 wikipedia/arxiv 基座 |
| 🟡 中 | tracker_url_remover 默认 active,会原地改 `result.url` | tracker_url_remover.py:44 | 需原始 URL 则网关另存 |
| 🟢 低 | 去重不剥 www/UTM、不归一尾斜杠、忽略 scheme 但保留 query/fragment;同文章带不同 query 是多条 | _base.py:420 | 上层文档说明;更激进去重网关自做 |
| 🟢 低 | 重名 name / 重复 shortcut → 容器启动即 `sys.exit(1)` | engines/__init__.py:299,304 | 裁剪 settings.yml 时检查 |
| 🟢 低 | `parsed_url` 是 6 元素数组不是对象;`length` 是秒数 float | webutils.py:149-159 | 解析时按数组/秒处理 |
| 🟢 低 | 超时后台线程不被 kill,跑到 httpx 自身超时才结束,高 QPS 下可能线程堆积 | search/__init__.py | 压测容量规划 |
| 🟢 低 | 本快照 `calculator` 插件疑似空壳(无 keywords/post_search),`active:true` 名不副实 | plugins/calculator.py | 实测 `1+1` 是否返回 answer |

---

## 5. 跨笔记矛盾的源码裁决

各笔记末尾已各自带"复核修正",彼此结论高度一致。本次通读 + 回源码,发现的分歧与裁决:

1. **`exception_classname_to_text` 表的行号**:笔记 01(待确认 #2)写 `webutils.py:52-67`,笔记 05/09 写 `41-67`。
   **裁决**:回源码确认该 dict **定义于 `webutils.py:41-67`**(41 行 `exception_classname_to_text = {`,67 行 `}`)。05/09 正确,01 有误。**已修正笔记 01**(该行加注复核修正)。

2. **`!!`(手气不错 `redirect_to_first_result`)是否劫持 json**:笔记 01 的 Q1 表把它记为"`/search` 直接 302"(webapp.py:695-696),未区分 format。
   **裁决**:回源码确认 `redirect_to_first_result` 的 302 判断在**模板渲染段 webapp.py:695-696**,位于 `format=json` 提前 return(webapp.py:672-675)**之后**——故 `!!`(手气不错)**不劫持 json 请求**;真正劫持 json 的是**外部 bang** `!!g`(经 `redirect_url` 在 webapp.py:663 提前 302,早于 json 分支)。两者行为不同。**已在笔记 01 补正**此区分。此前各笔记对"外部 bang 劫持 json"的结论无误,只是 01 对"手气不错"表述过粗。

3. **`display_error_messages` 默认值**(笔记 01/04/09 列为待确认,02/03 断言 true):
   **裁决**:回源码确认 `ENGINE_DEFAULT_ARGS["display_error_messages"] = True`(**engines/__init__.py:47**),消费点 results.py:254。**默认 True**,02/03 正确。此待确认项**已关闭**。

4. **`pre_request` 行号**:笔记 01 正文用 458(其复核修正已从 457 改为 458),与其他笔记引用一致。无矛盾。

5. **brave 引擎数量**:笔记 03 复核修正已澄清默认 settings.yml 配 4 个引擎(第 5 个 brave.goggles 整段注释掉),各笔记一致。无矛盾。

6. **`calculate_score` 命名**:笔记 03/04 一致(本快照函数名为 `calculate_score`,results.py:17,旧名 `result_score` 已废)。无矛盾。

> 结论:10 份笔记无实质性相互矛盾的**结论**,仅笔记 01 有两处行号/表述不精确,已回源码裁决并修正。其余"复核修正"均为各研究员自查的行号微调,已在各自笔记内闭环。

---

## 6. 待实测确认问题汇总(去重,标注来源笔记)

> 已由本次通读回源码关闭的:`display_error_messages` 默认值=True(原 01/04/09);`exception_classname_to_text` 行号=41-67(原 01);JSON 顶层 7 键结构(原 01/02/09,已在 webutils.py:164-172 确认);外部 bang vs 手气不错对 json 的劫持差异(原 01)。以下为仍需对**运行中的 Docker 实例**实测的项。

### 6.1 JSON 序列化形态(需对实例抓真实响应)
- `unresponsive_engines` 经 `json.dumps` 的确切形态(应为 `[[engine,msg],...]` 二维数组)与是否恒返回该键(含空列表)。〔01,02,05,09〕
- `parsed_url`(ParseResult)是否真被序列化成 6 元素数组、不报错。〔01,04〕
- `results[]` 常规文本引擎实际带哪些字段(publishedDate/author 等 LegacyResult 透传项);`positions`/`score` 是否确在 json 里未被裁剪。〔01,04,09〕
- `answers[]` 每个 answer 的确切字段(是否含 engine/answer/url);`infoboxes[]` 字段稳定性。〔01,04,10〕

### 6.2 失败文案与 error_type(需触发真实失败采样)
- en locale 下各异常文案的**确切英文串**(含 `Suspended: ` 完整形态),固化"文案→reason"映射表。〔01,04,05,09〕
- 无 cookie 纯 API 调用下 SearXNG 用哪个默认 locale 决定文案语言(`ui.default_locale` 还是请求头)。〔01,05〕
- `unresponsive_engines` 里引擎名是"我们请求用的名"还是"SearXNG 内部规范名",大小写/别名是否一致(影响对账)。〔05〕
- `error_type` 全限定名的完整取值集合(除 timeout 外的限流/SSL/解析异常类名)。〔04,09〕
- 部署的引擎里哪些默认关了 `display_error_messages`(决定静默失败范围)。〔01,02,04,09〕

### 6.3 参数行为与 count
- `time_range`/`safesearch` 传非法值的实际行为(400/忽略/取默认)。〔02〕
- `timeout_limit` 能否经 json 接口传入、用哪个参数名;被 `max_request_timeout` 截断时响应是否有提示。〔02,05〕
- 一次 pageno=1 请求聚合去重后典型条数、各引擎 max_page,评估循环多页凑 count 的请求放大倍数与 DDG 反爬压力。〔01,02,03〕
- SearXNG 是否真的完全无"条数"参数、pageno 是否唯一影响返回量的旋钮。〔02〕

### 6.4 limiter / botdetection(在"关闭"推荐下不阻塞 P1)
- `python-httpx` 默认 UA 是否命中 `http_user_agent.py` 黑名单正则(决定网关必须自定义 UA)。〔07〕
- limiter 关闭 + 无 valkey 时,启动与 `/search?format=json` 全流程是否无异常。〔07〕
- POST body 传 `format=json` 时 `ip_limit` 用 `request.args.get('format')` 取不到 → 是否影响 API_MAX 分支(limiter 开启场景)。〔07〕
- `get_user_cfg_folder()` 在镜像里解析到的目录(limiter.toml 覆盖落点)是否 = `/etc/searxng`。〔07〕
- 内网网关 IP 是否落在 link-local(不在则不能靠 filter_request:159 免检)。〔07〕
- 被 suspend 的引擎在 json 里 `results` 是否绝对为空(避免对账误判)。〔05〕

### 6.5 部署 / 容器(需 docker inspect / 容器内实测)
- `GRANIAN_WORKERS` 实际默认值(推断 1);`__SEARXNG_CONFIG_PATH`/`__SEARXNG_DATA_PATH` 确切取值(推断 /etc/searxng、/var/cache/searxng)。〔08〕
- `SEARXNG_BASE_URL=false` 下 `format=json` 是否报错或产坏的分页/图片代理链接。〔08〕
- 仅加 `search.formats:[json]` 是否足够,还是 public_instance 会额外禁 json。〔08〕
- 基础镜像内是否自带 wget/curl 供 healthcheck。〔08〕
- 环境变量覆盖是否发生在 update_settings 合并之后(优先级最高);`search.formats:[json]` 去掉 html 是否有副作用;非法 bool 环境变量是否导致启动失败。〔06〕
- duckduckgo 的 vqd EngineCache(SQLite)在容器重启后是否持久(冷启动翻页会否 Captcha);镜像是否启动时联网重抓 engine_traits.json(应否)。〔03〕

### 6.6 插件 / answerer 边界(需对实例采样)
- `calculator` 插件在本快照是否 no-op(实测 `1+1`)。〔10〕
- answerer 短路边界:`max apple`(首词命中但参数非数字→应答空)是否正常走引擎不误伤。〔10〕
- `tracker_url_remover` 改 `result.url` 发生在去重之前还是之后(是否影响跨引擎合并)。〔10〕
- `hostnames` 插件在未配 `hostnames:` 段时是否确实自动缺席。〔10〕
- `/stats/errors` 在默认镜像下是否对匿名请求开放;`/metrics` Basic Auth 是否只认 password;网关/代理是否剥离 Authorization 头。〔09〕

---

## 附:四个候选引擎稳定性速查

| 引擎 | 类型 | 稳定性 | P1 注意 |
|---|---|---|---|
| wikipedia | 官方 REST API | ★★★★ 最稳,几乎不失败 | 默认只进 infobox,需 `display_type:[list]` |
| arxiv | 官方 Atom API | ★★★★ 无反爬 | 在 science 分类,非 general |
| brave | HTML 抓取 | ★★ 页面变则解析碎,max_page=10 | 靠 CSS xpath;images/videos/news 配独立 network |
| duckduckgo | HTML no-JS 抓取 | ★ 反爬最重 | vqd 机制、IP 级封禁;共享出口 IP 慎用 |
