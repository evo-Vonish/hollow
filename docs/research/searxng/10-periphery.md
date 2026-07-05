# SearXNG 外围子系统速览(plugins / answerers / autocomplete / favicons / cache / infopage)

> 研读快照:`/home/user/hollow/vendor/searxng/`,commit `a643858`(未修改)。
> 所有行号均对应该快照。相对路径均相对 `vendor/searxng/`。
> 本文写给"以后维护这套网关的自己":重点是搞清楚这些外围功能**在纯 headless JSON API 场景下到底跑不跑、跑起来会不会动我们的 `/v0/search` 返回结果**。

---

## 职责概述

这几个模块都是"围绕搜索主流程"的外围能力,和我们 P1 关系的强弱差别很大:

| 模块 | 作用 | 是否影响 `/search` 的 JSON 返回 | 默认开关 |
|---|---|---|---|
| `searx/plugins/` | 在搜索前/后、以及逐条结果上做钩子(改写/删除/加结果) | **会**(on_result 改结果、post_search 加 answer) | 各插件独立,见 `settings.yml:225` |
| `searx/answerers/` | 关键词触发的本地即时应答(统计/随机) | **会**,且会**短路引擎搜索**(见下) | 全部内置加载,无开关 |
| `searx/autocomplete.py` | 搜索框输入建议 | **不影响**,只服务 `/autocompleter` 端点 | 默认关(`autocomplete: ""`) |
| `searx/favicons/` | 结果 URL 旁的站点图标代理/缓存 | **不影响**,纯 HTML 模板函数 | 默认关(`favicon_resolver: ""`) |
| `searx/cache.py` | 通用 SQLite 过期缓存底座 | 间接(被 favicon/weather/currencies 等用) | 随使用方 |
| `searx/infopage/` | 渲染 `/info/<page>` 帮助文档 | 完全无关 | 与搜索无关 |

一句话结论:**autocomplete 和 favicons 对我们纯 API 场景天生无害且默认关闭;真正会改动我们返回结果的是 plugins 和 answerers。**

---

## 架构与关键流程

### 搜索主流程里外围功能的挂载点

`SearchWithPlugins.search()`(`searx/search/__init__.py:201-209`)是网关实际会命中的类。执行顺序:

1. `plugins.STORAGE.pre_search(...)`(`:203`)——返回 False 可**整体取消**搜索。
2. `super().search()` → `Search.search()`(`:174-179`):
   - 先 `search_external_bang()`(`!!bang` 重定向,返回 `redirect_url`);
   - 否则 `search_answerers()`(`:177`)——**若命中 answerer 且产出非空结果,直接 return,不再跑引擎**(`:177-178` 的 `if not self.search_answerers(): self.search_standard()`);
   - 否则 `search_standard()` 真正并发调各引擎。
3. 逐条结果回调 `_on_result`(`:198-199`)→ `plugins.STORAGE.on_result(...)`——**每条引擎结果都过一遍所有启用插件**,插件可返回 False 删掉该条,或原地改写。
4. `plugins.STORAGE.post_search(...)`(`:206`)——插件可**追加**结果(如 answer)。
5. `result_container.close()`。

### 插件"是否启用"的判定链(关键)

- 每个插件类有 `active: ClassVar[bool]`(`plugins/_core.py:74-75`),由 `settings.yml` 的 `plugins:` 段配置(`_core.py:178-189` 的 `PluginCfg.active`,`__init__.py:107-109` 从 `searx.get_setting("plugins")` 加载)。
- 请求级别的"用户启用清单"在 `webapp.py:510-516` 构建:
  ```python
  sxng_request.user_plugins = []
  allowed_plugins = preferences.plugins.get_enabled()
  disabled_plugins = preferences.plugins.get_disabled()
  for plugin in searx.plugins.STORAGE:
      if (plugin.id not in disabled_plugins) or plugin.id in allowed_plugins:
          sxng_request.user_plugins.append(plugin.id)
  ```
- `preferences.plugins` 的默认值就是各插件的 `active`(`preferences.py:332-333`:`{plugin.id: plugin.active for plugin in plugins}`)。
- 三个钩子执行时都过滤 `p.id in search.user_plugins`(`_core.py:256/270/293`)。

**含义:** 只要客户端不发 `disabled_plugins` cookie/参数,`active: true` 的插件就会在**每个搜索请求**(含 JSON API)默认生效。我们网关不发这些偏好,所以 **SearXNG 的 `settings.yml` 里 `active: true` 的插件对我们全部默认生效**。要关只能改 SearXNG 侧的 `settings.yml`(这是部署配置,不算改源码)。

### answerer 存储与触发

- `AnswerStorage` 是个 dict,key=关键词,value=answerer 列表(`answerers/_core.py:135-141`)。
- 启动时 `STORAGE.load_builtins()` 扫描 `searx/answerers/` 下非 `_` 开头的 `.py`,自动注册(`_core.py:104-109`,`__init__.py:47-48`)。**无 settings 开关**,内置 answerer 恒定加载。
- `ask(query)`(`_core.py:143-164`):取 query 第一个词当关键词,命中才调用;answerer 的 `engine` 字段被写成 `f"answerer: {keyword}"`(`_core.py:161`)。

### JSON 返回结构里外围功能落在哪

`webapp.py:755-768`(response_index 的 kwargs,`format=json` 走同一份数据):
```python
results = results,
answers = result_container.answers,       # answerer + 部分插件的 Answer 落这里
infoboxes = result_container.infoboxes,
unresponsive_engines = webutils.get_translated_errors(result_container.unresponsive_engines),
```
所以 answerer/calculator/unit_converter 产出的 `Answer` 会进 `answers`;engine 失败进 `unresponsive_engines`(那块归 results.py 研究员,这里只提一句:它是我们 `meta.engines_failed` 的上游)。

---

## 逐问题详解

### 问题 1:plugins 机制 + 默认启用清单;哪些会改写结果;tracker_url_remover 对我们有用吗

**机制**:抽象基类 `Plugin`(`plugins/_core.py:68`)提供三个钩子(`_core.py:141-175`):
- `pre_search` → bool,False 取消搜索;
- `on_result(request, search, result)` → bool,False 丢弃该条结果,可原地改写 `result`;
- `post_search` → 可返回一批结果追加进 container。
- 另有 `keywords`(`_core.py:77-81`):非空时该插件只在 query 首词命中关键词时才跑 `post_search`(`_core.py:295-298`)。

**默认启用清单**(来自 `searx/settings.yml:225-258`):

| 插件 id | 默认 active | 钩子 | 对结果的影响 |
|---|---|---|---|
| `calculator` | **true** | (本快照无 post_search,见下) | 见待确认问题 |
| `hash_plugin` | true | post_search(keywords=md5/sha1/…) | 首词是 hash 算法时追加一条 Answer(`hash_plugin.py:25,41`) |
| `self_info` | true | post_search(keywords=ip/user-agent) | query 为 `ip`/`user-agent` 时回显请求 IP/UA(`self_info.py:27,44-59`) |
| `unit_converter` | true | post_search(`unit_converter.py:47`) | 识别"数值+单位"追加换算 Answer |
| `ahmia_filter` | true | on_result(`ahmia_filter.py:38`) | 仅当 `outgoing.using_tor_proxy` 为真才真正加载(`:46-49`),否则 `init` 返回 False 被移除;过滤 onion 黑名单 |
| `hostnames` | true | on_result(`hostnames.py:124`) | **仅当 `settings.yml` 有 `hostnames:` 段才加载**(`:150-152`),可改写/删除/重排结果 |
| `time_zone` | true | post_search(keywords=time/timezone/now/clock/timezones,`time_zone.py:26,39`) | 命中时区关键词追加 Answer |
| `tracker_url_remover` | **true** | on_result(`tracker_url_remover.py:44`) | **删除结果 URL 里的追踪参数**(utm_* 等) |
| `infinite_scroll` | false | 纯 UI(`infinite_scroll.py:27` preference_section=ui) | 只影响前端主题,对 JSON 无意义 |
| `oa_doi_rewrite` | **false** | on_result | 把论文链接改写到开放获取镜像(`oa_doi_rewrite.py`) |
| `tor_check` | false | — | tor 出口节点检测 |

**"会改写/删除结果"的**(on_result 类,对 JSON `results` 有实质副作用):`tracker_url_remover`、`hostnames`、`ahmia_filter`、`oa_doi_rewrite`。其余是 post_search 加 Answer(只进 `answers`,不动 `results`)。

**tracker_url_remover 对我们有用吗**:有用且建议**保留**。它在 `on_result` 里对每条结果调 `result.filter_urls(self.filter_url_field)`(`tracker_url_remover.py:44-58`),内部用 `searx.data.TRACKER_PATTERNS.clean_url` 去掉 URL 上的追踪参数。对"给 AI 消费的干净 URL"是净收益,且是纯本地正则、无网络开销。唯一注意:它会**修改 `result.url`**,若我们要保留原始 URL 需自己在网关侧另存。

> 注:tracker 模式数据 `TRACKER_PATTERNS.init()` 在插件 `init` 时加载(`tracker_url_remover.py:40-42`);`hostnames` 的正则从 `settings.yml` 的 `hostnames:` 段或外部 yml 文件加载(`hostnames.py:147-177`)。

### 问题 2:answerers 是什么

关键词触发的**本地即时应答**,不走引擎、纯本地计算。内置两个:

- **statistics**(`answerers/statistics.py`):关键词 `min/max/avg/sum/prod/range`(`:18-25,31`)。首词命中且后续参数能被 `babel.numbers.parse_decimal` 解析为数字时,返回 `Answer`(如 `avg 1 2 3`);参数非数字则返回空(`:51-55`)。
- **random**(`answerers/random.py`):关键词 `random`(`:53`),子类型 string/int/float/sha256/uuid/color(`:56-63`)。

**对 P1 最重要的坑**:`Search.search()` 在 `search_answerers()` 返回非空时会**跳过整个引擎搜索**(`search/__init__.py:177-178`)。也就是说,如果最终用户查询恰好以 `random` / `min` / `avg` 等词开头且能产出应答,`/v0/search` 会**只返回一个本地 answer、零条网页结果**,`meta.engines_failed` 也会是空(因为压根没调引擎)。这是静默的行为分叉,网关需要意识到并可能要在文档里说明或做防御。

answerer 无 settings 开关(`__init__.py:48` 强制 `load_builtins()`),要禁用只能改源码——与"不改源码"冲突。所以只能在**网关侧感知**这个短路行为。

### 问题 3:autocomplete 后端与开关

- 后端字典 `backends`(`autocomplete.py:371-390`):`360search/baidu/bing/brave/dbpedia/duckduckgo/google/mwmbl/naver/privacywall/quark/qwant/seznam/sogou/startpage/swisscows/wikipedia/yandex`,每个是 `(query, locale) -> list[str]`。
- 入口 `search_autocomplete(backend_name, query, locale)`(`:393`),未知后端返回 `[]`(`:394-396`)。
- **开关**:`settings.yml:46` `autocomplete: ""`(默认空=关);`autocomplete_min: 4`(`:48`)最少输入字符数。
- **只被 `/autocompleter` 端点调用**(`webapp.py:806-832`),`/search` 主流程完全不碰它。

**结论**:纯 API 场景 autocomplete 无关紧要,默认已关。我们网关不代理 `/autocompleter`,可完全忽略。

### 问题 4:favicons 代理/缓存默认开不开;纯 API 要不要关

- 默认**关**:`settings.yml:51` `favicon_resolver: ""`。
- `favicon_url(authority)`(`favicons/proxy.py:195-237`)是**纯 Jinja 模板函数**(`webapp.py:435` 注入到模板 kwargs),`resolver` 为空或非法时直接 `return ""`(`proxy.py:218-221`)。
- 它生成的是 `data:` URL 或 `/favicon_proxy?…` 路由(`proxy.py:230-237`),**只在 HTML 主题里渲染**;`/search?format=json` 的结果 dict 里**不含 favicon 字段**(JSON kwargs 见 `webapp.py:755-768`,无 favicon)。
- 缓存底座在 `favicons/cache.py`(`FaviconCacheConfig`),命中时把图标缓存成 data URL(`proxy.py:97-108`)。

**结论**:favicons 对我们**天生不触发**(JSON 路径不调模板函数),默认还关着。**无需额外处理**;哪怕别人把 `favicon_resolver` 打开,也只影响 HTML 页面、不影响我们消费的 JSON。唯一副作用是若开启且走 `/favicon_proxy` 端点会产生对外抓图请求——我们不代理该端点即可。

### 问题 5:cache.py 缓存什么

`searx/cache.py` 是一个**通用的、带 TTL 的 SQLite KV 缓存底座**,不是"搜索结果缓存"。

- `ExpireCacheCfg`(`cache.py:35-70`):`MAXHOLD_TIME` 默认 7 天(`:48`)、`MAINTENANCE_PERIOD` 1h(`:51`)、`MAINTENANCE_MODE` 默认 `auto`(`:55` 自动清理)、value 上限 10KB(`:45`)、密码默认取 `server.secret_key`(`:67`)用于 `secret_hash`。
- 实现类 `ExpireCacheSQLite`(`__all__` 于 `:8`),底层 `searx/sqlitedb.py`,支持 value 用 `pickle` 序列化 + HMAC。
- **使用方**(`grep ExpireCache`):`favicons/proxy.py`、`weather.py`、`data/currencies.py`、`data/tracker_patterns.py`、`enginelib/__init__.py`、`infopage`。

**结论**:它缓存的是"图标/天气/货币汇率/tracker 模式/引擎元数据"等**辅助数据**,**不缓存搜索结果本身**。对 P1 无直接影响;我们要做搜索结果缓存得在网关侧自己实现,不能指望这层。默认 DB 落 `/tmp/sxng_cache_{name}.db`(`cache.py:42-43`),Docker 部署时注意 `/tmp` 持久化无所谓(可重建)。

### 问题 6:纯 headless JSON API 用途下,外围功能建议关/留清单

前提:我们只调 `/search?format=json`,不代理 `/autocompleter`、`/favicon_proxy`、`/info`。控制手段=改 SearXNG 侧 `settings.yml`(部署配置,非改源码)。

**建议保留(对干净结果有益、纯本地无外呼)**:
- `tracker_url_remover`(去追踪参数,给 AI 干净 URL)——保留。
- `hostnames`(仅当我们配置 `hostnames:` 段才生效;可用来屏蔽垃圾域名/重排)——按需保留,默认不配则自动不加载。
- `unit_converter` / `calculator`(本地换算,进 `answers`,无外呼)——保留无害。

**建议关闭 / 无需理会**:
- `autocomplete`:已默认关(`""`),我们也不代理该端点——**无需动**。
- `favicon_resolver`:已默认关且不进 JSON——**无需动**;确保不暴露 `/favicon_proxy`。
- `infinite_scroll`:纯 UI,对 JSON 无意义——可关可留(建议 `active:false`,本来就是)。
- `self_info`:回显请求方 IP/UA,keyword 触发(`ip`/`user-agent`)。若担心终端用户查询恰好是这两个词导致返回内网 IP,可 **`active:false`**。
- `ahmia_filter`:仅在开 `outgoing.using_tor_proxy` 时才加载(`ahmia_filter.py:46-49`),我们不用 tor 则自动不加载——无需动。
- `oa_doi_rewrite` / `tor_check`:默认 false——保持。

**answerers(无开关)**:无法通过配置关闭,只能在**网关侧感知短路行为**(问题 2)。建议:P1 里对返回结果做判断——若 `results` 为空但 `answers` 非空,标注这是"本地应答短路"而非"全引擎失败",避免误报进 `engines_failed`。

**总体判断**:纯 API 场景外围功能**默认配置已经相当安全**(autocomplete/favicon 都关着),真正需要我们主动决策的只有:(a) 是否保留几个 on_result 改写型插件(建议留 tracker_url_remover),(b) answerer 短路引擎的行为要在网关侧处理好。

---

## 对 P1 的影响与行动建议

1. **plugins 会默认改动我们的返回结果**:`active:true` 的 on_result 插件(尤其 `tracker_url_remover`)会**修改 `result.url`**。若 P1 需要"每条结果的原始 URL"精确不变,要么在 SearXNG 侧 `settings.yml` 把它设 `active:false`,要么接受被清洗后的 URL(推荐后者,更干净)。行动:在部署的 `settings.yml` 里显式写全 `plugins:` 段并 code review,别依赖默认。

2. **answerer/keyword 插件会短路引擎搜索**:查询以 `random/min/max/avg/sum/prod/range` 开头且产出应答时,`/v0/search` 只回 `answers`、`results` 为空、无引擎调用。行动:网关在组装 `meta.engines_failed` 时,区分"引擎被短路(未调用)"与"引擎调用失败";前者不应算失败。可通过"是否有 `answers` 且 `results` 空且 `unresponsive_engines` 空"来识别。

3. **`answers`/`infoboxes` 字段要不要透传**:JSON 返回里 answerer/calculator/unit_converter/time_zone/hash 的结果落在 `answers`(`webapp.py:761`),带 `engine="answerer: <kw>"` 或 `plugin: <id>` 前缀。P1 若要"每条结果标注 engine 来源",注意这些**本地应答的 engine 名不是真实搜索引擎**,分类时要单独处理。

4. **autocomplete / favicons 无需在 P1 处理**:默认关且不进 JSON 路径。只需保证网关不对外暴露 `/autocompleter`、`/favicon_proxy`,并在 SearXNG `settings.yml` 保持 `autocomplete: ""`、`favicon_resolver: ""`。

5. **cache.py 不是搜索缓存**:P1 若要做结果缓存必须自建,别误以为 SearXNG 这层帮我们缓存了搜索结果。

6. **pre_search 可整体取消搜索**:目前默认插件里没有会返回 False 的 pre_search(内置插件都没重写 pre_search),但自定义插件时要小心——`pre_search` 任一返回 False 会让整个搜索不执行(`_core.py:253-265`)。

---

## 待实测确认的问题

1. **calculator 插件在本快照(a643858)似乎是空壳**:`searx/plugins/calculator.py` 全文仅 28 行,只在 `__init__` 里设了 `PluginInfo`,**没有 `keywords`、没有 `post_search`/`on_result`**(`grep post_search` 无命中)。按理它应该解析数学表达式并产出 Answer,但本文件里没有任何计算逻辑。需实测:在部署实例上查 `1+1` 是否真返回 answer?若不返回,说明这一版 calculator 确实是 no-op(不影响我们,但 `active:true` 名不副实)。怀疑逻辑可能依赖某处未在本目录的注册,或该 commit 处于重构中间态。

2. **JSON 返回里 `answers` 的确切序列化结构**:本文只确认 answerer/plugin 的结果进 `result_container.answers`(`webapp.py:761`),但 `format=json` 下每个 answer 对象序列化成什么字段(是否含 `engine`、`answer`、`url`)需实测一次 `curl .../search?q=avg+1+2+3&format=json` 确认。

3. **answerer 短路的边界**:`search_answerers` 用 `bool(results)` 判断(`search/__init__.py:75`)。像 `max apple`(首词是 keyword 但参数非数字)时 statistics 返回空 → 不短路、正常走引擎。需实测确认这些"关键词命中但应答为空"的场景不会误伤正常搜索。

4. **`tracker_url_remover` 对 `result.url` 的改写是否会影响去重/评分**:它在 on_result 阶段改 URL,而 SearXNG 的结果合并/去重可能基于 URL。需实测:开启该插件时,不同引擎返回同一页(追踪参数不同)是否被正确合并,以及改写是否发生在去重之前还是之后(涉及 results.py,建议与该子系统研究员对齐)。

5. **`hostnames` 插件加载条件**:确认只有 `settings.yml` 存在 `hostnames:` 段时才加载(`hostnames.py:150-152` 返回 False 移除)。若我们不配置该段,它应自动缺席——需在实例上验证它不出现在启用插件列表。
