# SearXNG 子系统研究:结果容器与融合排序

> 研读范围:`searx/results.py`、`searx/result_types/`(`_base.py`、`__init__.py`、`answer.py` 等)
> 关联出口:`searx/webutils.py`(JSON 序列化)、`searx/search/processors/abstract.py`(失败引擎写入)
> 快照 commit:vendor 内 a643858(本仓库 vendor 提交 bc1bc05,未改 SearXNG 源码)
> 面向对象:以后维护 AI Research Browser 网关、需要读懂 SearXNG JSON 语义的自己。

---

## 一、职责概述

`ResultContainer`(`results.py:53`)是**一次搜索里所有引擎结果的汇聚点**。多个引擎线程并发把各自的结果 `extend()` 进同一个容器,容器负责:

- **归一化**每条结果的字段(URL、标题、正文、日期);
- **去重合并**:内容等价的结果(不同引擎命中同一 URL)合并成一条,并累计它们的来源引擎与命中位置;
- **打分与排序**:`close()` 时给每条主结果算 `score`,`get_ordered_results()` 按 score 降序 + 分类分组;
- **旁路结果收集**:infoboxes(信息框)、answers(直答)、suggestions(建议词)、corrections(纠错词)、engine_data、paging 标志;
- **失败引擎登记**:`unresponsive_engines`(我们 `meta.engines_failed` 的直接数据源)。

容器有个**一次性关闭**语义:`close()`(`results.py:183`)置 `_closed=True` 并触发打分。关闭后再 `extend` / `add_unresponsive_engine` / `add_timing` 都会被拒绝(分别见 `results.py:85`、`results.py:251`、`results.py:259`),只打日志不写入。

结果对象有两套类型:

- **新式 `Result` / `MainResult`**(msgspec.Struct,`_base.py:243` / `:354`)——正在推进的强类型;
- **`LegacyResult`**(`_base.py:443`,`dict` 子类)——旧引擎返回裸 dict 时的兼容包装。

两者在容器里混用,`extend()` 用 `isinstance(result, Result)` 分流(`results.py:92`)。**绝大多数常规搜索引擎目前仍走 LegacyResult 路径**(引擎 `response()` 返回 `list[dict]`),这点对我们解析 JSON 很关键。

---

## 二、架构与关键流程

### 2.1 写入路径 `extend(engine_name, results)`(`results.py:82`)

对每条结果:

1. **新式 `Result`**(`results.py:92`):补 `engine`、`normalize_result_fields()`;若是 `BaseAnswer` → `answers.add`;若是 `MainResult` → `_merge_main_result`;否则抛 `NotImplementedError`。
2. **裸 dict**(`results.py:107`):包成 `LegacyResult`、归一化,然后按 key 分派:
   - `suggestion` → `suggestions`(set)
   - `answer` → `answers`(发 DeprecationWarning)
   - `correction` → `corrections`(set)
   - `infobox` → `_merge_infobox`
   - `engine_data` → `engine_data[engine][key]`
   - 其余(即普通网页结果)→ `_merge_main_result`

末尾若 `engine_name` 在注册表里,记 metrics 并按 `eng.paging` 置 `self.paging`(`results.py:148-152`)。

> 注意:`main_count` 是「本引擎本次贡献的主结果条数」,同时被当作每条结果的 `position`(排名位次)传给 `_merge_main_result`。也就是说 **position = 该结果在这个引擎返回列表里的序号(从 1 起)**,而不是全局排名。

### 2.2 打分与排序

`close()`(`results.py:183`)遍历 `main_results_map`,对每条算 `calculate_score`,并把分数累加进引擎的 metrics counter。

`get_ordered_results()`(`results.py:191`)两趟:

- **第 1 趟**:按 `score` 降序 `sorted`(`results.py:202`)。
- **第 2 趟**:按「分类 + 模板 + 是否有图」分组重排(`results.py:210-244`),让同类结果聚在一起(`max_count=8`、`max_distance=20` 的启发式)。这一步会**重写 `res.category`**为该引擎的 `engine.categories[0]`(`results.py:214`)——即 JSON 里每条结果的 `category` 是「引擎的首个分类」,不是查询请求的 category。

结果被缓存进 `_main_results_sorted`,重复调用返回同一份。

### 2.3 JSON 出口(`webutils.py:162` `get_json_response`)

这是我们网关实际会打到的形态。整个 JSON 顶层结构:

```python
{
  'query': sq.query,
  'results': [_.as_dict() for _ in rc.get_ordered_results()],
  'answers': [_.as_dict() for _ in rc.answers],
  'corrections': list(rc.corrections),
  'infoboxes': rc.infoboxes,
  'suggestions': list(rc.suggestions),
  'unresponsive_engines': get_translated_errors(rc.unresponsive_engines),
}
```

序列化器 `JSONEncoder`(`webutils.py:149`):`msgspec.Struct → to_builtins`、`datetime → isoformat`、`timedelta → total_seconds`、`set → list`。

---

## 三、逐问题详解

### 问题 1:多引擎结果如何合并(去重规则、URL 归一化)

**入口** `_merge_main_result(result, position)`(`results.py:167`):

```python
result_hash = hash(result)
merged = self.main_results_map.get(result_hash)
if not merged:
    result.positions = [position]
    self.main_results_map[result_hash] = result
    return
merge_two_main_results(merged, result)
merged.positions.append(position)
```

去重完全靠 **`hash(result)` 相等**判定「同一条结果」,`main_results_map` 是 `dict[int, result]`(键就是 hash)。

**hash 规则**(`MainResult.__hash__` `_base.py:420`;`LegacyResult.__hash__` `_base.py:536` 里普通结果分支代码相同):

```python
hash(
  f"{self.template}"
  + f"|{url.netloc}|{url.path}|{url.params}|{url.query}|{url.fragment}"
  + f"|{self.img_src}"
)
```

关键点:
- 判等维度 = **template + parsed_url(去掉 scheme)+ img_src**。
- **scheme 不参与 hash** → `http://x/a` 与 `https://x/a` 视为同一条(合并时会优先保留 https,见下)。
- **query/fragment 参与 hash** → `?a=1` 与 `?a=2`、`#x` 与 `#y` 视为不同结果。**不做 UTM/追踪参数剥离,不做尾斜杠归一**。所以「同一篇文章带不同 query 参数」不会被 SearXNG 合并。
- `netloc` 原样(**不剥 `www.`**;`_base.py:73` 里对 infobox 的 www 剥除是被注释掉的)。
- 图片结果特殊:`LegacyResult` 若 `template == "images.html"`,hash = `template|url|img_src`(`_base.py:542`),即图片按完整 url + img_src 去重。
- `parsed_url` 为空会**抛 ValueError**(`_base.py:426` / `:553`)——没有 url 的主结果不允许进入合并。

**URL 归一化** `_normalize_url_fields`(`_base.py:41`),在 `normalize_result_fields()` 里被调:
- 若有 `url` 无 `parsed_url` → `urllib.parse.urlparse(url)`;
- 给 parsed_url 补默认 scheme `http`(`_base.py:57`),`path` 原样;
- 用 `parsed_url.geturl()` 回写 `url`。
- 归一化很轻:**只补 scheme,不小写 host、不去尾斜杠、不排序 query、不删片段**。

**合并动作** `merge_two_main_results(origin, other)`(`results.py:332`):
- 正文取更长的(`len(other.content) > len(origin.content)`,`results.py:335`);
- 标题取更长的(`results.py:340`);
- `origin.defaults_from(other)`(`results.py:345`):**origin 里缺失(UNSET / "" / None)的字段用 other 补齐**(`_base.py:342` / LegacyResult `:579`);
- `origin.engines.add(other.engine)`(`results.py:350`)——**这就是多引擎命中的来源集合累积处**;
- **scheme 升级**:若 origin 是非 s 结尾 scheme 而 other 是 s 结尾(https/ftps),把 origin 的 scheme 换成 other 的并回写 url(`results.py:352-356`)。

命中位置:`merged.positions.append(position)`(`results.py:181`)——每被一个引擎命中一次就多一个 position 元素。`positions` 的长度 = 命中该结果的引擎次数,直接进入打分。

> 合并里的胜出「代表引擎」`result.engine` 不会被改写(始终是第一个插入的引擎);但 `engines` 集合包含所有命中引擎。JSON 里两个字段都有:`engine`(单个,首个)与 `engines`(集合)。

### 问题 2:score 如何计算(引擎权重 / 位置 / 多引擎加成)

`calculate_score(result, priority)`(`results.py:17`):

```python
weight = 1.0
for result_engine in result['engines']:            # 遍历所有命中引擎
    if hasattr(engines.get(result_engine), 'weight'):
        weight *= float(engines[result_engine].weight)
weight *= len(result['positions'])                 # 命中次数放大
score = 0
for position in result['positions']:
    if priority == 'low':   continue
    if priority == 'high':  score += weight
    else:                   score += weight / position
return score
```

拆解:
- **引擎权重**:所有命中引擎的 `weight` 连乘(默认权重 1.0,配置见 `settings.yml` 的 `engines[].weight`)。多引擎命中且各自 weight>1 会连乘放大。
- **多引擎命中加成**:体现在两处 —— `weight *= len(positions)`(命中次数线性放大),以及后面对每个 position 累加。综合下来,被 N 个引擎命中的结果 score 约等于 `∏weight × N × Σ(1/position_i)`,对单引擎结果是碾压式优势。
- **位置**:默认分支 `score += weight / position` —— **排名越靠前(position 越小)得分越高**。position 从 1 起。
- **priority**(`MainResult.PriorityType`,`_base.py:404`,取值 `""` / `"high"` / `"low"`,由 hostnames 插件等设置):
  - `high`:每个 position 都加满 `weight`(不衰减);
  - `low`:直接跳过,`score` 恒为 0(沉底);
  - 默认 `""`:位置衰减累加。

> 对我们:JSON 里每条结果的 `score` 已经是 SearXNG 融合后的最终分,**能直接用来做跨引擎排序**,不必自己重算。但注意 score 依赖实例侧的 `engines[].weight` 配置,不同 SearXNG 实例分值不可比。

### 问题 3:`unresponsive_engines` 的确切数据结构与语义 ★

**类型定义**(`results.py:47`):

```python
class UnresponsiveEngine(t.NamedTuple):
    engine: str        # 引擎名(engine.name)
    error_type: str    # 错误类型字符串
    suspended: bool    # 是否因熔断被挂起
```

容器字段:`self.unresponsive_engines: set[UnresponsiveEngine] = set()`(`results.py:75`)——**是一个 set,元素是三元组 NamedTuple**。set 去重按三个字段的组合;同一引擎不同 error_type 会是两条。

**写入口** `add_unresponsive_engine(engine_name, error_type, suspended=False)`(`results.py:249`):
- 两道关卡:
  1. `_closed` 检查(`results.py:251`):close 之后调用只记 error 日志,**丢弃**(超时后姗姗来迟的失败不再登记)。
  2. **`display_error_messages` 检查**(`results.py:254`):`if searx.engines.engines[engine_name].display_error_messages:` 为真才 add。**若某引擎配 `display_error_messages: false`,它挂了也不会进 `unresponsive_engines` —— 一个合法的静默丢弃口子**(对 P1「禁止静默丢弃」是风险点)。

**三个 error_type / suspended 的来源**(谁调 add):

| 调用点 | error_type 取值 | suspended |
|---|---|---|
| 主线程超时判定 `search/__init__.py:157` | `'timeout'`(字面量) | `False`(默认) |
| 通用异常处理 `processors/abstract.py:189` | 若为异常:`module + Class.__qualname__`,如 `httpx.TimeoutException`、`searx.exceptions.SearxEngineAPIException`;若为字符串:原样 | `False`(默认) |
| 熔断短路 `processors/abstract.py:237` | `suspended_status.suspend_reason` | **`True`** |

`error_type` 的具体构造在 `handle_exception`(`abstract.py:182-189`):`module_name + exception_class.__qualname__`(builtins 省略前缀)。所以是**异常类的全限定名字符串**,不是错误消息。

**语义总结**:
- `engine` = 出问题的引擎名;
- `error_type` = 机器可读的错误标识(超时是 `'timeout'`,其余多为异常全限定名);
- `suspended=True` 专指「该引擎已被熔断挂起,本次直接短路没真正请求」,`suspend_reason` 作为 error_type。

**★ 重大坑:JSON 出口丢失了结构**。`get_json_response` 用 `get_translated_errors(rc.unresponsive_engines)`(`webutils.py:171` → `:70`)转成:

```python
translated_errors.append((unresponsive_engine.engine, error_msg))  # 只剩 (engine, 翻译后消息串)
```

- `error_type` 被 `exception_classname_to_text` 映射表(`webutils.py` 顶部)翻译成**面向用户的本地化字符串**(如 `"server API error"`、`"timeout"` 对应的文案),且经 `gettext` 受实例 UI 语言影响;
- `suspended=True` 只是给消息**加前缀 `"Suspended: "`**(`webutils.py:78-79`),不再是独立布尔;
- 最终按引擎名排序,返回 `list[[engine, message]]`。

**因此:SearXNG 的 JSON `unresponsive_engines` 字段 = `[[引擎名, 翻译后的错误文案], ...]`,拿不到原始 `error_type` 全限定名,也拿不到独立的 `suspended` 布尔。** 我们 `meta.engines_failed` 若要结构化 error_type,靠标准 JSON API 无法获得(除非改 SearXNG 或解析文案,后者脆弱)。

### 问题 4:JSON 里每条 result 有哪些字段

`results` 数组来自 `get_ordered_results()` 每条 `as_dict()`。

- **新式 `MainResult`**:`as_dict()`(`_base.py:339`)= `{f: getattr(self,f) for f in __struct_fields__}`。字段全集(`_base.py:354-418`):
  `url`、`engine`、`parsed_url`、`template`、`title`、`content`、`img_src`、`iframe_src`、`audio_src`、`thumbnail`、`publishedDate`、`pubdate`、`length`、`views`、`author`、`metadata`、`priority`、`engines`、`open_group`、`close_group`、`positions`、`score`、`category`。
- **`LegacyResult`**:`as_dict()` 返回 dict 本身(`_base.py:482`),字段 = 引擎塞进去的原始 key **并集** `__init__` 里补齐的默认字段(`_base.py:490-505`):`url`、`template`、`engine`、`parsed_url`、`title`、`content`、`img_src`、`thumbnail`、`priority`、`engines`、`positions`、`score`、`category`、`publishedDate`(+ 引擎自定义 key,如 videos 引擎的 `length`、`author` 等)。

对 P1 直接可用的关键字段:
- **`engine`**(str,首个命中引擎)与 **`engines`**(set→JSON array,所有命中引擎)—— **每条结果的引擎来源就在这里**,`engines` 是我们标注多引擎来源的正解。
- **`score`**(float)—— 融合后最终分,可直接排序。
- **`positions`**(list[int])—— 各引擎命中位次;`len(positions)` = 命中引擎数。
- **`category`**(str)—— 注意是引擎首个分类(被 `get_ordered_results` 重写),非请求 category。
- **`publishedDate`**:序列化为 ISO8601 字符串(`JSONEncoder` 对 datetime → `isoformat()`);仅当引擎给了合法日期(≥1900,否则 `_normalize_date_fields` `_base.py:234` 置 None)。
- **`pubdate`**:`publishedDate.strftime('%Y-%m-%d %H:%M:%S%z')`(`_base.py:238`),已废弃但仍可能出现。
- **`content` / `title`**:去空白归一(`WHITESPACE_REGEX` `_base.py:106-108`);若 content == title 则 content 清空(`_base.py:109-111`)。
- **`parsed_url`**:`ParseResult` 是 NamedTuple → JSON 里会变成一个**6 元素数组** `[scheme, netloc, path, params, query, fragment]`(json 默认把 namedtuple 当 tuple→list)。**别指望它是对象**,要 host 就自己从 `url` 解析或取该数组第 2 个元素。
- **`template`**:如 `default.html`、`images.html`、`videos.html`,可用于判断结果形态。

> 各结果类型(image/video/paper/…)的额外字段散在 `result_types/*.py` 与各 engine 里;LegacyResult 会原样透传,不做白名单过滤。

### 问题 5:infoboxes / answers 的融合逻辑

**answers**(`AnswerSet`,`answer.py:46`):
- 内部是 `_answerlist`(list);`add()`(`answer.py:58`)按 `hash(answer)` 去重 —— 已存在相同 hash 就不加。
- `Answer.__hash__`(`answer.py:88`)= `hash(self.answer)`(答案文本)。即**同文本直答只留一条**,不合并、不累积来源。
- 迭代时按 `template` 排序(`answer.py:67`)。
- JSON 里 `answers` = 每条 `as_dict()`。

**infoboxes**(`results.py:154` `_merge_infobox`):
- 按 `infobox.id` 合并:新框有 `id` 且已有同 `id` 框 → `merge_two_infoboxes`(`results.py:272`),否则 append。无 id 的框直接 append(**不去重**)。
- `merge_two_infoboxes`(`results.py:272`):
  - **代表引擎按权重**:`weight2 > weight1` 时把 `origin.engine` 换成权重更高的(`results.py:278`);
  - `origin.engines |= other.engines`(引擎集合并);
  - `urls` 按 `entity` 或 `url` 去重后追加(`results.py:283-299`);
  - `img_src`:origin 空则取 other,否则权重高者胜(`results.py:301-305`);
  - `attributes` 按 `label`/`entity` 去重追加(`results.py:307-323`);
  - `content` 取更长的(`results.py:325-329`)。
- JSON 里 `infoboxes` 直接输出 `rc.infoboxes`(LegacyResult dict 列表)。

---

## 四、对 P1 的影响与行动建议

1. **`meta.engines_failed` 的数据来自 JSON `unresponsive_engines`,但已被降级为 `[[engine, 翻译文案]]`**(`webutils.py:70`)。
   - 原始 `error_type`(如 `httpx.TimeoutException`)和 `suspended` 布尔在标准 JSON API 里**拿不到**;`suspended` 仅体现为文案前缀 `"Suspended: "`。
   - 行动:P1 至少把 `[engine, message]` 原样透出到 `meta.engines_failed`;若产品要结构化 error_type,评估三选一:(a) 用固定 UI 语言实例 + 反查文案映射(脆弱);(b) 给 SearXNG 打小补丁加原始字段(违背「不改源码」原则,需拍板);(c) 接受只给引擎名 + 人读消息。建议先 (c) 落地,把「消息里含 Suspended 前缀」解析成 `suspended` 布尔。

2. **静默丢弃的合法口子:`display_error_messages: false` 的引擎失败不进 `unresponsive_engines`**(`results.py:254`)。P1「禁止静默丢弃」无法在网关层单靠 JSON 弥补 —— 需要通过「请求了哪些 engine」与「JSON 返回了哪些 engine 的结果 + 哪些进了 unresponsive」做**集合差**来推断「既无结果也未报错」的引擎,并把这类引擎也归入 `engines_failed`(标 `error_type=unknown/silent`)。这是我们能做的最强兜底。

3. **每条结果的引擎来源用 `engines` 字段(数组),不是 `engine`(单个)**。P1 要「返回每条结果的 engine 来源」应优先透出 `engines`(全部命中引擎),`engine` 作为主引擎。二者 JSON 都有。

4. **`score` 可直接复用做排序**,不用自研融合。但注意其绝对值依赖实例 `engines[].weight`,跨实例不可比;`priority=low` 的结果 score=0 会沉底。

5. **去重语义要向上层说明**:SearXNG 只按 `template + host + path + params + query + fragment + img_src`(忽略 scheme)去重,**不剥 www、不去 UTM、不归一尾斜杠**。带不同 query 参数的同一文章会是多条。若 P1 想更激进去重,得在网关侧自己做(风险:破坏 SearXNG 的 score/positions 语义)。

6. **`parsed_url` 在 JSON 里是 6 元素数组不是对象**;取 host 用 `url` 自己解析或取数组 index 1。`publishedDate` 是 ISO8601 串,`length`(timedelta)会被序列化成秒数(float)。

7. **answers/infoboxes 结构与主结果完全不同**:answers 无 score/engines 累积(同文本仅一条),infoboxes 是 dict 且字段异构(urls/attributes/img_src)。P1 若要暴露它们,应作为独立 `meta` 或独立数组,别塞进主 `results`。

---

## 五、待实测确认的问题

1. **常规文本引擎(google/bing/duckduckgo 等)返回的 JSON result 到底带哪些字段**:源码显示走 LegacyResult 会原样透传引擎自定义 key,但具体每个引擎给不给 `publishedDate`、`author` 需抓真实 JSON 确认(源码层面只能确定「默认字段一定在」)。
2. **`positions` / `score` 是否真的出现在 JSON `results` 里**:`as_dict` 逻辑显示会带上,但需实测确认没有被某处 `formats`/模板过滤掉(webapp 层可能对 json format 有额外裁剪,未在本子系统范围内读到)。
3. **`suspended` 文案前缀在非英文实例下的样子**:`gettext('Suspended')` 会本地化,若我们靠解析前缀判断 suspended,需锁定实例 UI 语言或改用 `SEARXNG_...` 固定 locale;需实测各 language 下的输出。
4. **`display_error_messages` 的默认值**:代码里读的是 `engines[name].display_error_messages`,默认 True 需在 `engines/__init__.py` 或 settings 默认里确认(本子系统未覆盖引擎注册)。
5. **`error_type` 全限定名的完整取值集合**:除 `'timeout'` 外,实际会出现哪些异常类名,需跑真实失败场景(限流、SSL、解析失败)采样,才能给网关建映射表。
6. **多引擎命中时 `engine` 字段(代表引擎)的确定性**:源码显示是「第一个插入者」,但并发下插入顺序不定,同一查询多次跑 `engine` 可能不稳定;`engines` 集合稳定。需实测确认是否影响幂等。
