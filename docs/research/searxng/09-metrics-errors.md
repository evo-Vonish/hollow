# SearXNG 子系统研究:指标与错误记录(metrics / error recorder)

> 研读范围(相对 `vendor/searxng/`):`searx/metrics/`(`__init__.py` / `error_recorder.py` / `models.py`)、`searx/exceptions.py`、`searx/openmetrics.py`;为搞清调用链,连带读了 `searx/search/processors/abstract.py`、`searx/search/processors/online.py`、`searx/search/__init__.py`、`searx/results.py`、`searx/webapp.py`、`searx/webutils.py`、`searx/network/raise_for_httperror.py`、`searx/settings_defaults.py`。
> 快照 commit:a643858(未修改)。
> 所有结论标注了 `文件:行号 / 函数名`。不确定的进「待实测确认」。

---

## 职责概述

这个子系统干三件事:

1. **计数(metrics)**:每个引擎的发送次数、成功次数、错误次数、分数,以及耗时/结果数的直方图。存在进程内存里(`CounterStorage` / `HistogramStorage`),进程重启即清零。
2. **错误归类记录(error recorder)**:把引擎抛出的异常/主动上报的错误,归纳成「错误上下文」(代码位置 + 异常类名 + 消息),按 `(engine, ErrorContext) -> count` 累加。用于 `/stats` 页面的「技术报告」和可靠性百分比。
3. **对外暴露**:`/stats`(HTML)、`/stats/errors`(JSON)、`/metrics`(OpenMetrics/Prometheus,需鉴权)。

注意一个**边界**:引擎「临时停用(suspended)」状态**不在这个子系统里**,而在 `search/processors/abstract.py` 的 `SuspendedStatus`,是进程内存的独立结构,**不经过任何 stats 端点暴露**。它只在「每次搜索的结果」里通过 `unresponsive_engines`(带 `suspended=True`)透出。这点对 P1 很关键。

---

## 架构与关键流程

### 存储层(`searx/metrics/models.py`)

- `Histogram`(models.py:17):固定桶宽 `_width`、固定桶数 `_size` 的直方图,带线程锁。`percentage(p)` 求分位数,`sum` / `count` / `average`。
- `CounterStorage`(models.py:130):`dict[tuple(str...), int]`,`configure()` 预注册键置 0,`add(value, *args)` 累加,`get(*args)` 读取(注意:`get` 直接 `self.counters[args]`,键不存在会 KeyError)。
- `VoidHistogram` / `VoidCounterStorage`(models.py:161-168):`enable_metrics=False` 时用的空实现,`observe`/`add` 变 no-op。

### 初始化(`searx/metrics/__init__.py:initialize`,70-108)

`enabled=True` 时创建真实存储,否则用 Void 版本。对每个引擎注册这些键:

- 计数器(metrics/__init__.py:96-101):
  - `('engine', <name>, 'search', 'count', 'sent')` — 实际发出的请求数
  - `('engine', <name>, 'search', 'count', 'successful')` — 成功返回并被接收的数
  - `('engine', <name>, 'search', 'count', 'error')` — 错误数
  - `('engine', <name>, 'score')` — 引擎得分
- 直方图(metrics/__init__.py:103-108):`result.count`、`time.http`、`time.total`。

### 计数在哪里被打(调用链)

搜索主循环 `searx/search/__init__.py`:
- 送请求前先查停用:`processor.extend_container_if_suspended(...)`(search/__init__.py:94),若已停用则 `continue`,**不打 `sent`**。
- 未停用才 `counter_inc('engine', name, 'search', 'count', 'sent')`(search/__init__.py:102)。

处理器基类 `searx/search/processors/abstract.py`:
- 成功路径 `_extend_container_basic`(abstract.py:203-218):`counter_inc(... 'successful')`,并 `histogram_observe` 记 `time.total` / `time.http`。
- 成功后 `extend_container`(abstract.py:220-233)调用 `self.suspended_status.resume()` 清零停用状态。
- 失败路径 `handle_exception`(abstract.py:175-201):
  - `result_container.add_unresponsive_engine(name, error_message)` 记入不可用引擎;
  - `counter_inc('engine', name, 'search', 'count', 'error')`;
  - 异常对象走 `count_exception`,字符串消息走 `count_error`;
  - 若 `suspend=True`,调用 `self.suspended_status.suspend(...)`。

### 错误记录器(`searx/metrics/error_recorder.py`)

- `count_exception(engine_name, exc, secondary=False)`(error_recorder.py:174):`enable_metrics=False` 直接返回。用 `inspect.trace()` 拿调用栈,`get_exception_classname` 得异常全限定名,`get_messages` 抽取消息,构造 `ErrorContext` 交给 `add_error_context`。
- `count_error(engine_name, log_message, log_parameters, secondary=False)`(error_recorder.py:187):同样先查 `enable_metrics`,用 `inspect.stack()`,记录人工消息。
- `add_error_context`(error_recorder.py:87-90):`errors_per_engines[engine][error_context] += 1`,并 `engines[engine].logger.warning(...)`。
- `ErrorContext`(error_recorder.py:25-84):字段 `filename, function, line_no, code, exception_classname, log_message, log_parameters, secondary`,实现了 `__eq__`/`__hash__`,所以「同一处代码 + 同一异常类 + 同一消息」会被合并计数。
- `get_trace`(error_recorder.py:93-100):从栈里挑出属于 `searx/engines` 或 `searx/search/processors` 的那一帧作为「出错位置」,拿不到就用栈顶。
- `secondary=True` 的错误(如软重定向超限,online.py:216)在可靠性计算里**被排除**。

### 异常类型体系(`searx/exceptions.py`)

```
SearxException (base, :10)
├─ SearxParameterException (:14)          查询缺参数
├─ SearxSettingsException (:29)           配置加载错误
└─ SearxEngineException (:38)             引擎内部错误
   ├─ SearxXPathSyntaxException (:42)
   └─ SearxEngineResponseException (:52)  无法解析引擎结果
      ├─ SearxEngineAPIException (:56)            网站返回应用级错误
      ├─ SearxEngineXPathException (:113)
      └─ SearxEngineAccessDeniedException (:60)   网站封锁访问 ★带 suspended_time
         ├─ SearxEngineCaptchaException (:88)          返回验证码
         └─ SearxEngineTooManyRequestsException (:99)  返回 429
```

**`SearxEngineAccessDeniedException` 是「会导致停用」这条支线的根**(exceptions.py:60-85):
- 类属性 `SUSPEND_TIME_SETTING` 指向配置键;构造时若不给 `suspended_time`,就从 `get_setting(SUSPEND_TIME_SETTING)` 取默认值。
- 默认停用时长(settings_defaults.py:202-208):
  - `SearxEngineAccessDenied` = 86400 秒(1 天)
  - `SearxEngineCaptcha` = 86400 秒(1 天)
  - `SearxEngineTooManyRequests` = 3600 秒(1 小时)
  - Cloudflare 验证码 `cf_SearxEngineCaptcha` = 1296000 秒(15 天)
  - `recaptcha_SearxEngineCaptcha` = 604800 秒(7 天)

### HTTP 状态码 → 异常映射(`searx/network/raise_for_httperror.py`)

`raise_for_httperror`(:61-79):
- 先查 Cloudflare / reCAPTCHA 特征页面 → 抛 `SearxEngineCaptchaException` / `SearxEngineAccessDeniedException`(带对应长停用时间)。
- HTTP 402 / 403 → `SearxEngineAccessDeniedException`(默认 1 天)。
- HTTP 429 → `SearxEngineTooManyRequestsException`(默认 1 小时)。
- 其它 >=400 → `resp.raise_for_status()` 抛 httpx 的 `HTTPStatusError`(不触发长停用,只触发 5 秒短停用,见下)。

---

## 逐问题详解

### 问题 1:引擎级错误如何被计数与归类

两条并行的记录:

1. **粗粒度计数器** `search.count.error`:每次 `handle_exception` 无脑 +1(abstract.py:191)。这是「总错误次数」,`/metrics` 不直接暴露它,但 `/stats` 页的可靠性由它间接影响。
2. **细粒度错误上下文** `errors_per_engines`:`count_exception` / `count_error` 把错误归类成 `ErrorContext`(代码位置 + 异常类名 + 消息),按内容去重累加(error_recorder.py:20, 87-89)。

归类的「异常类名」由 `get_exception_classname`(error_recorder.py:151-157)算出全限定名,例如 `searx.exceptions.SearxEngineTooManyRequestsException`、`httpx.ConnectTimeout`、`json.decoder.JSONDecodeError`。

`get_messages`(error_recorder.py:131-148)按异常类型抽消息:`HTTPError` → `(status_code, reason, hostname)`;`SearxEngineAPIException` / `AccessDenied` → 其 message;XPath 异常 → xpath + message;其它 → 空元组。

**触发点**:唯一往 metrics 打错误的正规入口是 `handle_exception`(abstract.py:175)。此外 `online.py:216` 在软重定向超限时直接 `count_error(..., secondary=True)`(引擎可能仍返回有效结果,所以只记不停)。个别引擎(startpage/quark/duckduckgo,见 grep 命中)也会主动抛这些异常,最终仍汇到 `handle_exception`。

**百分比归一化**:`get_engine_errors`(metrics/__init__.py:111-139)把每个 `ErrorContext` 的计数换算成 `percentage = round(20 * count / sent) * 5`,即以 5% 为粒度。`get_reliabilities`(:142-163)则 `reliability = 100 - Σ(非 secondary 错误的 percentage)`;`sent==0` 时 `reliability=None`。

### 问题 2:引擎 suspended(临时停用)机制

**结构**:`SuspendedStatus`(abstract.py:78-109),字段 `continuous_errors / suspend_end_time / suspend_reason`。全局注册表 `SUSPENDED_STATUS`(abstract.py:29)。

**关键:停用状态按「网络」而非「引擎」共享**(abstract.py:120-122):key = 该引擎的 network 对象 id;没有独立 network 才退回引擎名。**多个共用同一 network 的引擎会共享同一份停用状态**——一个被封,同组可能一起被视为停用。

**什么错误触发**(online.py `search`,241-284,`suspend=True`):
- `ssl.SSLError`(:255)
- `httpx.TimeoutException` / `asyncio.TimeoutError`(:259)
- `httpx.HTTPError` / `httpx.StreamError`(:267)
- `SearxEngineCaptchaException` / `SearxEngineTooManyRequestsException` / `SearxEngineAccessDeniedException`(:275)
- 兜底 `except Exception`(:282)**不停用**(`suspend=False`),只记错误。
- 另:主线程已超时不再等待时,`extend_container` 走 `handle_exception(..., 'timeout', False)`(abstract.py:226-228),也**不停用**。

**停多久**(abstract.py `suspend`,91-102):
- 若异常是 `SearxEngineAccessDeniedException` 家族,`suspended_time = exception.suspended_time`(abstract.py:199-200),即上面那些 86400/3600/1296000/604800 的长时间。
- 否则(SSL/超时/普通 HTTPError)`suspended_time=None` → `min(max_ban_time_on_fail=120, ban_time_on_fail=5) = 5` 秒(settings_defaults.py:200-201)。**默认这个 min 恒等于 5 秒**(因为 5 < 120)。
- `suspend_end_time = default_timer() + suspended_time`;`continuous_errors += 1`。

**恢复逻辑**:
- `is_suspended`(abstract.py:87-89):`suspend_end_time >= default_timer()`,纯粹靠墙钟到点自动恢复,没有后台任务。
- 一次成功搜索后 `extend_container` 调 `resume()`(abstract.py:233 → 104-109),立刻清零 `continuous_errors / suspend_end_time / suspend_reason`。
- **注意**:`continuous_errors` 只自增、从不在 `suspend()` 里用于加长时间——停用时长不随连续错误递增(与某些实现的指数退避不同)。它只有 `resume()` 会清零,目前代码里没读它做决策。

**停用期间的行为**:下次搜索在发请求前 `extend_container_if_suspended`(abstract.py:235-241)命中 → `add_unresponsive_engine(name, reason, suspended=True)` 并 `return True`,主循环 `continue`,**该引擎这轮既不发请求也不计 `sent`**(search/__init__.py:94-95 在 :102 之前)。

### 问题 3:`/stats` 与 `/stats/errors` 暴露什么(JSON 能拿吗)

- **`/stats`**(webapp.py:1085-1143):渲染 `stats.html`,**是 HTML 不是 JSON**。数据来自 `get_engines_stats`(耗时分位数、结果数、score)+ `get_reliabilities`(可靠性 %、错误列表)。按 `sxng_request.preferences.validate_token` 过滤引擎(公开引擎无 token 限制会通过)。想要机器可读得自己解析 HTML,不划算。
- **`/stats/errors`**(webapp.py:1146-1150):`jsonify(get_engine_errors(filtered_engines))`,**是 JSON,可直接拿**。结构:`{ engine_name: [ {filename, function, line_no, code, exception_classname, log_message, log_parameters, secondary, percentage}, ... ] }`,按 percentage 降序。同样按 token 过滤。
- 两者都是**进程内存聚合**(自进程启动累计),重启清零;`percentage` 粒度 5%;分母是 `sent` 次数(停用轮次不计入分母)。
- `robots.txt` 明确 `Disallow: /stats`(webapp.py:1177)。

**能否当监控数据源?** `/stats/errors` 能反映「哪些引擎在报错、报的什么错、大致占比」,可作为**引擎健康度的粗信号**;但它**不含「当前是否 suspended」「停到何时」**这类实时状态,也没有 sent/successful 的绝对计数(只有百分比)。要精确健康度(绝对成功率、当前停用),`/stats/errors` 不够。

### 问题 4:OpenMetrics / Prometheus 端点

- **存在**:`/metrics`(webapp.py:1153-1169),函数 `stats_open_metrics`。
- **怎么开**(webapp.py:1155-1161):
  1. `settings.general.enable_metrics` 为真(默认 `True`,settings_defaults.py:189);
  2. `settings.general.open_metrics` 设为**非空口令**(默认 `''` 即关闭,settings_defaults.py:190);
  3. 请求需带 **HTTP Basic Auth**,`authorization.password` 必须等于该口令(用户名不校验)。否则 401;两个开关任一没开返回 404。
- **输出**(metrics/__init__.py `openmetrics`,243-294,格式类 `openmetrics.py:OpenMetricsFamily`):`text/plain`,含
  - `searxng_engines_response_time_total_seconds`(gauge)
  - `searxng_engines_response_time_processing_seconds`(gauge)
  - `searxng_engines_response_time_http_seconds`(gauge)
  - `searxng_engines_result_count_total`(counter)
  - `searxng_engines_request_count_total`(counter,= sent_count)
  - `searxng_engines_reliability_total`(counter,= reliability %)
  每条按 `engine_name` 标签展开。**注意 `OpenMetricsFamily.__str__`(openmetrics.py:46-51):某数据点为 0/falsy 时该行被跳过**,所以 sent=0 或 reliability=0 的引擎不会出现在输出里(可能误以为「没这个引擎」)。
- 没有单独暴露「当前 suspended 引擎」或「error 计数器」的 metric。

### 问题 5:与 `unresponsive_engines` 的关系

`unresponsive_engines` 是**每次搜索请求**的产物,和 metrics 的「累计」正交:

- 定义:`UnresponsiveEngine = NamedTuple(engine: str, error_type: str, suspended: bool)`(results.py:47-50)。
- 写入:`ResultContainer.add_unresponsive_engine(engine, error_type, suspended=False)`(results.py:249-255),**仅当 `engine.display_error_messages` 为真才加入**(results.py:254)。
- 两个来源:
  1. 本轮真的失败 → `handle_exception` 传 `error_type=异常全限定名或 'timeout'`,`suspended=False`(abstract.py:189)。
  2. 本轮因之前被封而跳过 → `extend_container_if_suspended` 传 `suspended=True` 且 `error_type=suspend_reason`(abstract.py:237-239)。
- 关系链:**metrics 的 error 计数、error_recorder 的归类、suspended 状态,三者在失败时由同一个 `handle_exception` 一起更新;`unresponsive_engines` 是它们对「本次请求调用方」的可见输出**。metrics 是累计视图,`unresponsive_engines` 是单次视图。

**JSON API 里怎么透出**(webutils.py:162-174 `get_json_response`):`'unresponsive_engines': get_translated_errors(rc.unresponsive_engines)`。而 `get_translated_errors`(webutils.py:70-82)把 `error_type`(异常类名)**翻译成人类文案**,并在 `suspended=True` 时前缀 `'Suspended: '`,最终返回 `sorted([(engine_name, 翻译后的消息), ...])`。

⚠️ **对 P1 的坑**:SearXNG 原生 JSON 的 `unresponsive_engines` 是 `[引擎名, 已翻译且已合并的文案]`,**丢掉了原始异常类名、丢掉了 `suspended` 布尔、也不带停用时长**。若我们直接透传上游 JSON,拿不到结构化的失败原因。翻译表见 webutils.py:41-67(如 `SearxEngineTooManyRequestsException` → "too many requests"、`SearxEngineCaptchaException` → 验证码文案等),不在表里的走默认 "unexpected crash"。

---

## 对 P1 的影响与行动建议

P1 要「返回每条结果的 engine 来源 + 在 `meta.engines_failed` 显式暴露失败引擎(禁止静默丢弃)」。基于上述源码:

1. **失败引擎的唯一权威来源就是上游 JSON 的 `unresponsive_engines`**。我们必须把它映射进 `meta.engines_failed`,不能丢。但要清楚它已被翻译+合并,原始异常类名/suspended 位在 JSON 层已丢失。
2. **若需要结构化失败原因(exception 类名 / 是否 suspended / 停用时长),原生 `/search?format=json` 给不了**。可选方案:
   - (推荐,零改源码)接受「翻译后文案」作为 `reason`,并对文案做已知映射(参照 webutils.py:41-67 那张表反推),`suspended` 位可从文案是否以本地化的 "Suspended: " 前缀判断——但依赖 locale,脆弱;建议我们请求上游时固定 `locale`/语言以稳定前缀,或干脆只透传文案不解析语义。
   - (需要改源码,违背 P1「不改一行」原则,不建议)在上游加结构化输出。
3. **区分「失败」与「被停用跳过」**:上游把两者都塞进 `unresponsive_engines`,`suspended=True` 者本轮压根没发请求。P1 的 `engines_failed` 语义上应把这两类都算「未产出结果」,但如果要更精细(如「暂时不可用 vs 本次报错」),需要在文案前缀层面区分,见上一条的脆弱性警告。
4. **`meta` 里可选加健康度**:若我们想给出引擎健康度,可**旁路**调用上游 `/stats/errors`(JSON,免鉴权、按 token 过滤)拿错误占比;但它是进程累计、粒度 5%、不含实时 suspended,只能当「趋势参考」,不宜作为单次请求的失败判定依据——**单次失败仍以 `unresponsive_engines` 为准**。
5. **Prometheus 监控**:若我们要监控上游引擎健康,可在 Docker 部署时给 SearXNG 设 `general.open_metrics=<口令>`(默认关),然后用 Basic Auth 抓 `/metrics`。注意 0 值行会被省略(openmetrics.py:47),抓取端要容忍缺失序列。这属于运维监控,与 P1 的 `engines_failed` 响应字段是两条线,别混用。
6. **计数分母语义**:统计口径上,被 suspended 跳过的引擎不计入 `sent`(search/__init__.py:94-95),所以上游 reliability/占比天然排除了「主动跳过」的轮次——我们若自己统计成功率要注意对齐这个口径。
7. **`display_error_messages` 会吞掉失败引擎**:`add_unresponsive_engine` 仅在引擎 `display_error_messages=True` 时记录(results.py:254)。若某引擎该开关被关,它失败了也**不会出现在 `unresponsive_engines`**——这正是「静默丢弃」的上游成因。P1 要「禁止静默丢弃」,需注意:我们无法在网关层补回这类被上游隐藏的失败(除非改源码或旁路比对「请求的引擎集合 vs 返回结果里出现的引擎集合」)。**建议 P1 用「请求引擎集 − 出现在 results 里的引擎集 − unresponsive_engines 集 = 疑似静默失败集」来兜底暴露**。

---

## 待实测确认的问题

1. `/search?format=json` 返回体里 `unresponsive_engines` 的确切 JSON 形状(是 `[[engine, msg], ...]` 二元数组还是对象?`get_translated_errors` 返回元组列表,经 `json.dumps` 后应为二维数组)——需实际起服务打一发确认序列化结果。
2. `results` 数组里每条结果是否带 `engine` 与 `engines` 字段、字段名与是否总存在(P1 要「每条结果的 engine 来源」,需确认原生 `as_dict()` 输出)——本轮未读 `result_types`,不在我负责范围,建议交叉核对「结果融合」子系统的笔记。
3. `display_error_messages` 的默认值与哪些引擎默认关闭——决定「静默失败兜底」是否必要,需查引擎默认配置(不在本子系统)。
4. 固定请求 `locale` 时,"Suspended: " 前缀与各异常文案是否稳定可解析(依赖 gettext 翻译),需实测不同 language 参数下的文案。
5. `/stats/errors` 在生产默认配置下是否对匿名请求开放(`validate_token` 对无 token 引擎放行,但是否受其它中间件/限流影响)——需实测 Docker 默认镜像。
6. `SUSPENDED_STATUS` 按 network 共享的实际影响面:默认 `settings.yml` 里哪些引擎共用 network、会不会出现「A 引擎被封导致 B 引擎显示 suspended」——需结合 network 配置实测。
7. `/metrics` 的 Basic Auth 是否只认 password、用户名随意——源码看是(webapp.py:1160 只比 `authorization.password`),但需实测 proxy/网关是否会剥离 Authorization 头。
