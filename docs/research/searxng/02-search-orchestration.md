# SearXNG 子系统研究:搜索编排核心

> 研究员:SearXNG 源码研究员(子系统「搜索编排核心」)
> 源码快照:`/home/user/hollow/vendor/searxng/`(vendor 目录,内容对应上游 commit a643858,仓库内 commit `bc1bc05`)
> 研读范围:`searx/search/__init__.py`、`searx/search/models.py`、`searx/search/processors/*.py`
> 关联模块(为把链路讲清楚而顺带读的):`searx/results.py`、`searx/network/__init__.py`、`searx/engines/__init__.py`、`searx/settings.yml`
> 面向对象:以后要维护这套网关的自己。重细节、重出处。

---

## 职责概述

「搜索编排核心」是 SearXNG 把「一个用户查询」扇出到「N 个搜索引擎」、并发执行、
在统一的截止时间(deadline)内收集/合并结果、并显式记录失败引擎的那一层。它由三块组成:

1. **数据模型层** `searx/search/models.py`:`EngineRef`(引擎+分类的引用)和 `SearchQuery`(一次搜索的全部参数容器)。
2. **编排层** `searx/search/__init__.py`:`Search` / `SearchWithPlugins` 类。负责扇出请求、起线程、算超时、join、把结果塞进 `ResultContainer`。
3. **处理器层** `searx/search/processors/*.py`:每种 `engine_type`(online / offline / online_dictionary / online_currency / online_url_search)一个 `EngineProcessor` 子类。负责「把 `SearchQuery` 翻译成该引擎的请求参数」+「实际发请求」+「异常/超时归类」。

结果的收集容器是 `ResultContainer`(`searx/results.py`),失败引擎的账本(`unresponsive_engines`)也记在这里。这是我们 P1 里 `meta.engines_failed` 的直接数据源。

---

## 架构与关键流程

### 一次搜索的调用栈(自顶向下)

```
SearchWithPlugins.search()            # __init__.py:201  (含插件 hook + close())
 └─ Search.search()                   # __init__.py:174
     ├─ search_external_bang()        # __init__.py:59   命中 !bang 直接返回重定向
     ├─ search_answerers()            # __init__.py:71   命中本地 answerer 直接返回
     └─ search_standard()             # __init__.py:160
         ├─ _get_requests()           # __init__.py:78   逐引擎构造请求 + 算 actual_timeout
         └─ search_multiple_requests()# __init__.py:136  起线程并发 + join(带 deadline)
             └─ (每线程) PROCESSORS[name].search(...)   # 各处理器的 search()
                 └─ extend_container() # abstract.py:220 把结果/超时写回 ResultContainer
```

### 关键数据结构

- `EngineRef(name, category)`:`models.py:8`。`__slots__ = 'name','category'`。一次查询里每个「引擎×分类」是一个 `EngineRef`。
- `SearchQuery`:`models.py:27`,`@typing.final`。字段见 `__init__`(`models.py:31`):
  `query, engineref_list, lang='all', safesearch(0/1/2)=0, pageno=1, time_range(day/week/month/year|None)=None, timeout_limit=None, external_bang=None, engine_data={}, redirect_to_first_result=None`。
  - `categories` 是从 `engineref_list` 里去重推导出来的 property(`models.py:62`),**不是**独立字段。
  - 构造时会尝试 `babel.Locale.parse(lang, sep='-')`,失败则 `self.locale=None`(`models.py:56-60`)——注意分隔符是 `-`,即 `en-US` 而非 `en_US`。
- `ResultContainer`:`results.py:53`。持有 `main_results_map`、`infoboxes`、`suggestions`、`answers`、`corrections`、`unresponsive_engines`、`timings`、`redirect_url` 等,内部用 `RLock` 保护(`results.py:79`)。

---

## 逐问题详解

### 问题 1:一次 SearchQuery 从构造到返回的完整生命周期

注意:**`SearchQuery` 的构造不在本子系统内**。本层拿到的是已经构造好的 `SearchQuery`(上游由 `searx/webapp.py` + `searx/search/__init__` 之外的 `searxng.query` / `webadapter` 组装)。我们 P1 是自己构造 `SearchQuery`,所以这块要自己负责。本层的生命周期从 `Search.__init__` 开始:

1. **构造 Search**:`Search.__init__`(`__init__.py:50`)。建一个空的 `ResultContainer`,`start_time=None`,`actual_timeout=None`。
2. **进入 search()**:`Search.search`(`__init__.py:174`)。`self.start_time = default_timer()`(`timeit.default_timer`,单调时钟)。
3. **短路 1 - external bang**:`search_external_bang`(`__init__.py:59`)。若 `search_query.external_bang` 命中,`result_container.redirect_url` 被设为字符串,直接返回 True,**整个搜索结束**(不发任何引擎请求)。
4. **短路 2 - answerers**:`search_answerers`(`__init__.py:71`)。本地 answerer(如计算器、随机数等)命中就 `extend` 进容器并返回 True,**不发引擎请求**。
5. **标准搜索**:`search_standard`(`__init__.py:160`):
   - `_get_requests()`(`__init__.py:78`)遍历 `engineref_list`:
     - 取 `processor = PROCESSORS.get(engineref.name)`(`__init__.py:87`)。引擎不存在/init 失败未注册 → `continue` 跳过(**这类引擎不会出现在 engines_failed 里,是静默跳过**,见 open_questions)。
     - `processor.extend_container_if_suspended(...)`(`__init__.py:94` → `abstract.py:235`):若引擎处于熔断(suspended)状态,直接往容器写一条 `unresponsive_engine(name, reason, suspended=True)` 并 `continue`,**不发请求**。
     - `processor.get_params(search_query, category)`(`__init__.py:98`):把 `SearchQuery` 翻译成该引擎的请求参数(见问题 3)。返回 `None` 表示「引擎不支持此查询条件」(分页/time_range/max_page 不支持,或专用处理器的语法不匹配)→ `continue`,**静默跳过,不记 failed**。
     - 成功则 `counter_inc(... 'sent')` 并把 `(name, query, request_params)` 追加进 `requests`,同时 `default_timeout = max(default_timeout, processor.engine.timeout)`(`__init__.py:108`,注意是 **max**)。
   - 算 `actual_timeout`(`__init__.py:110-134`,见问题 2)。
   - `search_multiple_requests(requests)`(`__init__.py:136`):并发执行(见问题 2)。
6. **插件后处理 + 收尾**:回到 `SearchWithPlugins.search`(`__init__.py:201`):`pre_search` hook → `super().search()` → `post_search` hook → **`result_container.close()`**(`__init__.py:207` → `results.py:183`)。`close()` 会给每条结果算 `score`(`calculate_score`,`results.py:17`)并置 `_closed=True`。
7. **返回**:返回 `self.result_container`。之后取结果用 `get_ordered_results()`(`results.py:191`,按 score 排序 + 分类分组),失败引擎读 `result_container.unresponsive_engines`。

> 重要:`close()` 之后再调 `extend` / `add_unresponsive_engine` / `add_timing` 会被拒绝(分别见 `results.py:85`、`results.py:251`、`results.py:259`),只打日志不写入。超时后姗姗来迟的引擎线程写回的结果会被丢弃 —— 这正是「deadline 之外的结果不要」的机制。

### 问题 2:多引擎并发(线程模型)与每引擎超时计算

**线程模型**(`search_multiple_requests`,`__init__.py:136`):

- **每个引擎请求一个裸 `threading.Thread`**,没有线程池(`__init__.py:142`)。target 是 `copy_current_request_context(PROCESSORS[name].search)`——用 Flask 的 `copy_current_request_context` 把当前请求上下文复制进子线程(`__init__.py:141`),因为处理器里可能读 Flask 全局(如 `flask_babel.get_locale()`,见 online_currency)。
- 所有线程共享同一个 `search_id = str(uuid4())` 作为 `th.name`(`__init__.py:138,146`)。这是后面 join 时识别「本次搜索的线程」的标记。
- 每个线程挂两个自定义属性:`th._timeout = False`(`__init__.py:147`)、`th._engine_name = engine_name`(`__init__.py:148`)。
- 起完所有线程后,`for th in threading.enumerate()`(`__init__.py:151`)遍历**全进程**线程,只处理 `th.name == search_id` 的:
  - `remaining_time = max(0.0, self.actual_timeout - (default_timer() - self.start_time))`(`__init__.py:153`)——**每个线程重新计算剩余时间**,基于共享的 `start_time` 和 `actual_timeout`。
  - `th.join(remaining_time)`(`__init__.py:154`)。
  - join 返回后若 `th.is_alive()`(说明超时了):设 `th._timeout = True`,`result_container.add_unresponsive_engine(engine_name, 'timeout')`,记 error 日志(`__init__.py:155-158`)。
- **关键结论:整个 `search_multiple_requests` 的墙钟耗时上界 ≈ `actual_timeout`**(所有 join 共享同一 deadline,`remaining_time` 单调递减到 0)。引擎数量不影响总耗时上界。这是我们 P1 控制总耗时的基石。

**超时值的计算**(`_get_requests`,`__init__.py:110-134`):

- `default_timeout = max(所有被选中引擎的 engine.timeout)`(`__init__.py:108`)。单个引擎的 `engine.timeout` 默认 = `settings['outgoing']['request_timeout']`(默认 `3.0`,见 `settings.yml:179` 和 `engines/__init__.py:46`),可被引擎配置覆盖。**注意是 max 不是 min**——挂一个 timeout=10 的慢引擎会把整体 deadline 抬到 10。
- `max_request_timeout = settings['outgoing']['max_request_timeout']`(`__init__.py:111`)。默认在 `settings.yml:181` 是**注释掉的**,即默认 `None`(无上限)。
- `query_timeout = search_query.timeout_limit`(用户/调用方传入)。
- 四种组合(`__init__.py:115-126`):

  | max_request_timeout | timeout_limit(query) | actual_timeout |
  |---|---|---|
  | None | None | `default_timeout`(所有引擎 timeout 的 max) |
  | None | 有 | `min(default_timeout, query_timeout)` |
  | 有 | None | `min(default_timeout, max_request_timeout)` |
  | 有 | 有 | `min(query_timeout, max_request_timeout)` |

  **陷阱**:当 `max_request_timeout=None`(默认)且我们传 `timeout_limit` 时,公式是 `min(default_timeout, query_timeout)`——**只能把 deadline 往下压,压不过 `default_timeout`**。想让某引擎多等,靠 `timeout_limit` 是抬不上去的,只能改引擎自己的 timeout 或设 `max_request_timeout` 后再传更大的 `timeout_limit`(此时走第 4 行,`default_timeout` 完全不参与)。

**两级超时是对齐的**:`search_multiple_requests` 把 `self.actual_timeout` 作为第 5 个参数传给处理器的 `search()`(`__init__.py:144`)。online 处理器在线程内 `init_network_in_thread(start_time, timeout_limit=actual_timeout)`(`online.py:249,124`)→ `searx.network.set_timeout_for_thread(actual_timeout, start_time)`。网络层 `_get_timeout`(`network/__init__.py:73`)再算 httpx 的实际超时 = `timeout + 0.2(overhead) - (now - start_time)`(`network/__init__.py:88-91`)。所以 **httpx 层的读超时和主线程 join 的 deadline 用的是同一个 `actual_timeout` + 同一个 `start_time`**,两者一起在同一时刻到期。

### 问题 3:processors 各类型的分工

所有处理器继承 `EngineProcessor`(ABC,`abstract.py:112`),抽象方法只有 `search()`(`abstract.py:291`)。类型注册在 `ProcessorMap.processor_types`(`processors/__init__.py:49`)。`ProcessorMap.init`(`processors/__init__.py:57`)按引擎的 `engine_type` 属性(默认 `"online"`,`processors/__init__.py:71`)选处理器类。`PROCESSORS` 是全局单例(`processors/__init__.py:103`),按引擎名存实例。

处理器共同职责(在 `EngineProcessor`):
- `get_params(search_query, category)`(`abstract.py:243`):基础版。做**通用的「跳过」判断**——`pageno>1` 且引擎不支持分页(`abstract.py:255`)、超过 `max_page`(`abstract.py:259`)、有 `time_range` 但引擎不支持(`abstract.py:264`)→ 返回 `None`。否则组装基础 `RequestParams`(`abstract.py:267`):`query/category/pageno/safesearch/time_range/engine_data/searxng_locale`,外加 `language`。
- `handle_exception(...)`(`abstract.py:175`):把异常/字符串归一成 `error_message`(如 `httpx.TimeoutException` → `"httpx.TimeoutException"`),调 `result_container.add_unresponsive_engine`,记 metrics,按需 `suspend`。
- `extend_container(...)`(`abstract.py:220`):结果写回的统一入口。**先检查当前线程 `_timeout` 属性**(`abstract.py:226`)——若主线程已判定超时(设了 `_timeout=True`),即使引擎实际返回了结果也走 `handle_exception(..., 'timeout')`,**不采纳**这些迟到结果;否则 `_extend_container_basic`(写结果 + 记 timing + metrics)并 `suspended_status.resume()`(`abstract.py:233`,清零熔断计数)。
- `extend_container_if_suspended(...)`(`abstract.py:235`):熔断短路(见问题 4)。

各子类只重写 `get_params`(加自己的参数/语法过滤)和/或 `search`:

- **OfflineProcessor**(`offline.py:11`,`engine_type="offline"`):`search()`(`offline.py:16`)**不发 HTTP**,直接 `self.engine.search(query, params)`(同步、在本线程内跑)。异常处理:`ValueError` 只记日志**不记 error**(`offline.py:27`),其它 `Exception` 走 `handle_exception`(不 suspend)。用于本地数据源(SQL、命令行等)。
- **OnlineProcessor**(`online.py:113`,`engine_type="online"`):最常用。`search()`(`online.py:241`)在线程内先 `init_network_in_thread`,再 `_search_basic`(`online.py:225`:调 `engine.request()` 填 url → 空 url 则 return None → `_send_http_request` → `engine.response()` 解析)。`get_params`(`online.py:132`)在基础参数上加 HTTP 相关(headers、User-Agent、`Accept-Language` 等)。异常分门别类(`online.py:255-284`):`ssl.SSLError` / `httpx.TimeoutException`+`asyncio.TimeoutError` / `httpx.HTTPError`+`StreamError` / captcha+429+access-denied 这四类都 **`suspend=True`**(触发熔断);其它 `Exception` 不 suspend。
- **OnlineDictionaryProcessor**(`online_dictionary.py:37`,`engine_type="online_dictionary"`):继承 Online。只重写 `get_params`(`online_dictionary.py:42`):要求 query 匹配 `.*?([a-z]+)-([a-z]+) (.+)$`(如 `en-de hello`),解析出 from/to 语言,不匹配就返回 `None`(该引擎跳过)。翻译词典类。
- **OnlineCurrencyProcessor**(`online_currency.py:50`,`engine_type="online_currency"`):继承 Online。`get_params`(`online_currency.py:55`)要求 query 匹配 `(\d+) (cur) in|to (cur)`(如 `10 usd to eur`),解析金额+币种(查 `CURRENCIES` 表),不匹配返回 `None`。汇率换算类。
- **OnlineUrlSearchProcessor**(`online_url_search.py:32`,`engine_type="online_url_search"`):继承 Online。`get_params`(`online_url_search.py:37`)要求 query 含 http/ftp/data:image URL,否则 `None`。反查 URL 类。

> 对 P1 的含义:后三种 online_* 处理器**只在 query 命中特定正则时才会真正发请求**。如果我们把某个词典/汇率引擎放进 `engines` 列表,但 query 不含对应语法,它会静默返回 `None` 被跳过——**这不算失败,不该进 engines_failed**。区分「跳过(不适用)」与「失败(挂了)」是 P1 的核心。

### 问题 4:引擎挂了/超时了,编排层怎么处理,状态记到哪

失败状态全部落在 `ResultContainer.unresponsive_engines`,元素是 `UnresponsiveEngine` 具名元组:

```python
class UnresponsiveEngine(t.NamedTuple):   # results.py:47
    engine: str
    error_type: str
    suspended: bool
```

写入口是 `add_unresponsive_engine(engine_name, error_type, suspended=False)`(`results.py:249`)。它有两道关卡:
1. `_closed` 检查(`results.py:251`):close 之后调用只记 error 日志,**丢弃**。
2. **`display_error_messages` 检查(`results.py:254`)**:只有 `engines[name].display_error_messages` 为真(默认 `True`,见 `engines/__init__.py`)才真正 `add`。**若某引擎配了 `display_error_messages: false`,它挂了也不会进 unresponsive_engines——这是一个静默丢弃的合法口子。** 对 P1「禁止静默丢弃」是关键风险点。

容器是 `set`,重复的三元组会去重(`results.py:75,255`)。

各种失败场景及 `error_type` 取值:

| 场景 | 触发点 | error_type | suspended |
|---|---|---|---|
| 引擎熔断中,直接跳过 | `extend_container_if_suspended` `abstract.py:237` | `suspend_reason`(上次的错误串) | `True` |
| 主线程判定超时 | `search_multiple_requests` `__init__.py:157` | `'timeout'` | `False` |
| 引擎线程内抛异常 | `handle_exception` → `abstract.py:189` | 异常类全名(如 `httpx.ConnectTimeout`)或字符串 | `False` |
| 迟到线程发现自己已被判超时 | `extend_container` `abstract.py:228` | `'timeout'` | `False` |

**熔断(suspend)机制**(`SuspendedStatus`,`abstract.py:78`):
- 状态**按 network 分组共享**,不是按引擎:`SUSPENDED_STATUS.setdefault(key, ...)`,`key = id(network) or engine.name`(`abstract.py:120-122`)。多个引擎共用一个 network 会共享熔断状态。
- `suspend(suspended_time, reason)`(`abstract.py:91`):`continuous_errors += 1`;若未指定时长,取 `min(search.max_ban_time_on_fail, search.ban_time_on_fail)`;设 `suspend_end_time = now + suspended_time`。
- `is_suspended`(`abstract.py:87`):`suspend_end_time >= now`。
- 触发 suspend 的只有 online 处理器里那四类异常(ssl/timeout/httperror/captcha+429+denied,`online.py:257-280`,全都 `suspend=True`)。普通 `Exception` 和 offline 的异常**不触发熔断**。
- 成功一次就 `resume()`(`abstract.py:233,104`)清零。

**metrics 侧写**(与 P1 无直接关系,但同源):`handle_exception` 还会 `counter_inc(...'error')` + `count_exception/count_error`(`abstract.py:191-195`);成功走 `_extend_container_basic` 会 `counter_inc(...'successful')` + 记直方图(`abstract.py:214-218`)。

### 问题 5:服务端调用时对总耗时的可控参数

我们作为服务端直接构造 `SearchQuery` + `Search`,能控制总耗时的旋钮:

1. **`SearchQuery.timeout_limit`**(`models.py:39`):最直接的每次查询旋钮。但受问题 2 的公式约束:
   - 若 `max_request_timeout` 未配(默认 None):`actual_timeout = min(default_timeout, timeout_limit)`——**只能压低,压不过引擎 timeout 的 max**。
   - 若配了 `max_request_timeout`:`actual_timeout = min(timeout_limit, max_request_timeout)`——`default_timeout` 不再参与,可自由设(不超过 max)。
2. **`settings['outgoing']['max_request_timeout']`**(全局,`settings.yml:181` 默认注释掉=None):硬上限。配了它,`timeout_limit` 才能真正主导。**建议 P1 部署时显式配一个值**(如 10.0),否则 `timeout_limit` 的语义受 `default_timeout` 牵制。
3. **`settings['outgoing']['request_timeout']`**(全局默认引擎 timeout,`settings.yml:179`=3.0)+ 每引擎 `timeout` 覆盖:决定 `default_timeout`(取 max)。
4. **选择的引擎集合**:`default_timeout` 是所选引擎 timeout 的 **max**,所以带一个慢引擎会抬高默认 deadline。

**保证**:`search_multiple_requests` 的 join 用 `remaining_time = max(0, actual_timeout - elapsed)`,总墙钟 ≈ `actual_timeout`,**与引擎数量无关**(全并发)。所以只要设好 `actual_timeout`,总耗时就有上界。唯一超出上界的风险是:线程本身 join 超时后我们不 kill 它(Python 无法强杀线程),迟到结果会被 `_closed`/`_timeout` 丢弃,但**后台线程可能还在跑**占资源(见 open_questions)。

---

## 对 P1 的影响与行动建议

P1 目标回顾:`POST /v0/search`,参数 `engines/categories/time_range/language/count/safesearch` 映射到 SearXNG,返回每条结果的 engine 来源,`meta.engines_failed` 显式暴露失败引擎。**架构决策是不改 SearXNG 源码、只调 JSON API**——所以下面区分「直接调 Python 编排层」和「调 Docker 的 JSON HTTP API」两种口径,并都给出映射。

> 注意:按项目书 P1 是「FastAPI 网关调 Docker 里 SearXNG 的 JSON API」,即我们**不直接 import 本层代码**。本层源码是用来理解 JSON API 背后的行为语义,以便正确解释响应字段。以下建议以此为准,同时标注对应的源码语义出处。

1. **参数映射**(SearchQuery 字段 → 我们的 API):
   - `engines` / `categories` → SearXNG 的 `engines=`(逗号分隔引擎名)/ `categories=`(逗号分隔分类)。本层的 `engineref_list` 就是二者展开的结果(`models.py:categories` 是从 engineref 反推的)。
   - `time_range` → `time_range`,合法值仅 `day/week/month/year`(`models.py:38`)。传别的值语义未定义(见 open_questions)。
   - `language` → `language`(HTTP API 参数名),对应 `SearchQuery.lang`,注意 locale 解析用 `-` 分隔(`en-US`)。
   - `safesearch` → `safesearch`,仅 `0/1/2`(`models.py:36`)。
   - `count`:**SearXNG 没有「返回条数」参数**。`SearchQuery` 无 count 字段。条数由 `pageno` + 每引擎返回量决定。P1 的 `count` 只能在网关侧对结果**截断**,不能下推给 SearXNG。这点必须在设计里写明。
2. **engine 来源**:每条结果的来源在结果对象的 `engine` 字段(单来源)和 `engines` 集合(合并去重后可能多来源,见 `merge_two_main_results` `results.py:350` 把 other.engine 加进 `origin.engines`)。JSON API 的每条结果里有 `engine` 字段;跨引擎去重合并后,同一条结果会带多个引擎。P1 要保留 `engines`(复数)才能如实反映「哪些引擎都返回了这条」。
3. **`meta.engines_failed` 的数据源**:SearXNG JSON API 响应里的 **`unresponsive_engines`** 字段,对应本层 `ResultContainer.unresponsive_engines`(`UnresponsiveEngine(engine, error_type, suspended)` 三元组,`results.py:47`)。P1 应把它原样映射成 `engines_failed`,**保留 error_type 和 suspended 两个维度**(suspended=True 表示是熔断跳过而非本次真失败,语义不同)。
4. **静默丢弃的三个口子,P1 必须显式处理**(否则违反「禁止静默丢弃」):
   - a. 引擎名不存在 / init 失败未注册 → `_get_requests` 里 `continue`(`__init__.py:88-91`),**不进 unresponsive_engines**。→ P1 网关应在下发前校验请求的 engines 都在 SearXNG 已注册引擎列表内,对未知引擎主动回报。
   - b. `get_params` 返回 None(分页/time_range/max_page 不支持,或 online_* 语法不匹配)→ `continue`,**不进 unresponsive_engines**。→ 这是「不适用」不是「失败」,P1 可选择放进一个单独的 `engines_skipped`(或 meta 里区分),别混进 failed 也别完全隐藏。
   - c. 引擎配了 `display_error_messages: false` → 挂了也不写 unresponsive_engines(`results.py:254`)。→ P1 部署 SearXNG 时**确保所有引擎 `display_error_messages: true`**(默认就是 true,别去关它),否则失败会真正静默消失,`engines_failed` 会漏报。
5. **总耗时控制**(P1 应显式配置):
   - 在 SearXNG 的 `settings.yml` 里**显式设 `outgoing.max_request_timeout`**(如 10.0),这样网关传的 `timeout_limit`(HTTP API 上是 `timeout_limit=` 或走请求参数,需实测确认字段名)才能真正主导 deadline(`__init__.py:124-126`)。
   - 网关侧自己也要设一个 HTTP client 超时,略大于 SearXNG 的 `actual_timeout`(比如 SearXNG deadline + 网络 overhead),避免网关先于 SearXNG 超时。
6. **响应里可用的其它 meta**:`timings`(每引擎耗时,`results.py:257` 的 `Timing(engine,total,load)`)、`suggestions`、`answers`、`corrections`、`infoboxes`、`paging` 都在 ResultContainer 里,JSON API 通常都吐出来。P1 可选择性透传。

---

## 待实测确认的问题(open_questions)

1. **JSON API 的字段名**:本子系统读的是 Python 编排层。SearXNG 的 `format=json` HTTP 响应里,失败引擎字段究竟叫 `unresponsive_engines` 还是别的,以及它是三元组还是对象数组,需要对 Docker 实例发一次真实请求确认(应去读 `searx/webapp.py` 的 json 序列化,不在本子系统范围)。
2. **`timeout_limit` 在 HTTP API 上的传参方式**:本层 `SearchQuery.timeout_limit` 存在,但通过 HTTP `format=json` 时用哪个 query 参数传(或是否根本不暴露),需要读 webapp 层 / webadapter 确认。若不能经 HTTP 传,则总耗时只能靠 `settings.yml` 的 `max_request_timeout` + 引擎 timeout 控制。
3. **`count` 的替代方案**:确认 SearXNG 是否真的完全无「结果条数」参数、`pageno` 是否是唯一影响返回量的旋钮。目前源码看不到 count,倾向于网关侧截断,但要实测每引擎默认返回量,评估截断是否够用。
4. **未知/非法参数值的行为**:`time_range` 传非 `day/week/month/year`、`safesearch` 传非 0/1/2 时,SearXNG 是报错、忽略还是当默认?本层类型标注是 `Literal` 但运行时不校验(`models.py` 只存不验),实际行为取决于 webapp 的入参解析层,需实测。
5. **超时后后台线程的命运**:主线程 join 超时后不 kill 线程(Python 限制),迟到结果被 `_closed`/`_timeout` 丢弃。这些线程会跑到 httpx 自己的超时才结束。高 QPS 下线程堆积的资源影响需压测确认(与我们直接调 API 关系不大,但影响 Docker 实例容量规划)。
6. **熔断状态的跨请求影响**:`SUSPENDED_STATUS` 按 network 分组、进程级共享(`abstract.py:29,122`)。一个引擎连续失败会被熔断,后续请求直接跳过并回报 `suspended=True`。P1 需决定:`engines_failed` 里 `suspended=True` 的条目要不要和真失败区别对待/告警。这是行为决策,非源码问题。
