# SearXNG 源码研究 · 子系统「HTTP API 表面」

> 研究对象:`vendor/searxng/`(commit a643858,未修改)
> 研读范围:`searx/webapp.py`、`searx/query.py`、`searx/webadapter.py`、`searx/webutils.py`、`searx/preferences.py`、`searx/results.py`、`searx/result_types/`、`searx/settings.yml`、`searx/settings_defaults.py`
> 目的:为我们的 FastAPI 网关 `POST /v0/search` 提供「调用 SearXNG JSON API」的准确契约。
> 所有结论均标注 `文件:行号` 或函数名出处;不确定项集中在文末「待实测确认」。

---

## 一、职责概述

SearXNG 是一个 Flask 应用(`searx/webapp.py`)。对我们有用的只有一个端点:

- **`/search`**(`webapp.py:616`,`methods=['GET', 'POST']`)——真正干活的搜索端点。
- `/config`(`webapp.py:1244`)——返回引擎/分类/语言清单的 JSON,可用于我们启动时探测可用引擎。
- `/healthz`(`webapp.py:597`)——纯文本 `OK`,可做健康探针。

搜索请求的处理链路是:

```
HTTP 请求
  └─ pre_request()            webapp.py:458   合并 GET+POST 参数、加载 preferences/cookie
      └─ search()             webapp.py:616   选择 output_format、校验 formats
          └─ get_search_query_from_webapp()  webadapter.py:221  把 form 映射成 SearchQuery
              └─ RawTextQuery                query.py:250       解析 q 里的 bang/修饰符
          └─ SearchWithPlugins.search()      → ResultContainer  实际并发查各引擎
          └─ webutils.get_json_response()    webutils.py:162    format=json 时序列化返回体
```

**关键架构事实:参数解析全部发生在 `webadapter.py`,而不是 `webapp.py`。** 我们要精确掌握「哪个参数怎么被解释」,主战场是 `webadapter.py` 和 `query.py`。

---

## 二、架构与关键流程

### 2.1 GET 与 POST 参数如何合并(决定我们该用哪种请求)

`pre_request()`(`webapp.py:480-495`)把两处参数合并进 `sxng_request.form`:

```python
# webapp.py:482-485
sxng_request.form = dict(sxng_request.form.items())      # 先取 POST 表单
for k, v in sxng_request.args.items():                   # 再取 GET query string
    if k not in sxng_request.form:                       # POST 优先,GET 只补缺
        sxng_request.form[k] = v
```

含义:
- **POST 参数优先于 GET**;同名时 GET 被忽略。
- `request.form` 是 Flask 的表单解析结果 —— **只解析 `application/x-www-form-urlencoded` / `multipart` 的 body,不解析 JSON body。** 所以我们的网关调用 SearXNG 时**必须用 form-encoded**(`data=...`),发 `application/json` 的 body 会被完全忽略、只剩 `q` 缺失报错。
- GET query string 同样有效,因此调试时可以直接浏览器 `?q=...&format=json`。

### 2.2 output_format 的双重闸门

`search()`(`webapp.py:625-631`):

```python
output_format = sxng_request.form.get('format', 'html')
if output_format not in OUTPUT_FORMATS:          # OUTPUT_FORMATS=['html','csv','json','rss']  settings_defaults.py:23
    output_format = 'html'
if output_format not in settings['search']['formats']:   # 实例配置的白名单
    flask.abort(403)
```

两道闸门:
1. `format` 不在 `['html','csv','json','rss']` → 静默降级为 `html`。
2. `format` 合法但不在**实例的 `search.formats` 配置**里 → **HTTP 403**。

所以要拿到 JSON,实例配置里必须显式开启 `json`(见 Q3)。

### 2.3 结果容器与去重合并

各引擎结果汇入 `ResultContainer`(`results.py:53`)。`get_ordered_results()`(`results.py:191-247`)会:
- 按 `score` 降序排序;
- **跨引擎去重合并**:同一条结果被多个引擎命中时,合并成一条,其 `engines`(集合)记录所有命中引擎,`positions` 记录各引擎给的排名,`engine`(单数)是主引擎。
- 按 category/template 分组重排(`max_count=8`, `max_distance=20`)。

这对我们「返回每条结果的 engine 来源」很关键:**来源信息在 `engines` 字段(集合),不是 `engine` 单数字段**。

---

## 三、逐问题详解

### Q1. `/search` 的全部请求参数及语义

下表全部来自 `webadapter.py`(除 `format`)。**注意:SearXNG 没有 `count` / `limit` / `per_page` 参数**(见 Q5)。

| 参数 | 出处 | 语义与校验 |
|---|---|---|
| `q` | `webadapter.py:243`, `webapp.py:634` | **必填**。空则 `format=html` 回首页,其余返回 `{"error":"No query"}` + HTTP 400(`webapp.py:642`)。`q` 会先经 `RawTextQuery` 解析 bang/修饰符(见 Q4),真正送引擎的是**剥掉修饰符后的** `raw_text_query.getQuery()`(`webadapter.py:254`)。 |
| `format` | `webapp.py:626` | 默认 `html`。取值 `html/csv/json/rss`。双重闸门见 §2.2。 |
| `pageno` | `webadapter.py:48-52` (`parse_pageno`) | 默认 `'1'`。必须是数字且 `>=1`,否则抛 `SearxParameterException('pageno', ...)` → HTTP 400。 |
| `safesearch` | `webadapter.py:75-92` (`parse_safesearch`) | 未传时取 preferences/实例默认(`search.safe_search`,默认 `0`,`settings.yml:42`)。传了必须是数字,且 `0<=x<=2`(0 关闭 / 1 中等 / 2 严格),越界抛异常 → 400。若实例把 `safesearch` 锁定(`preferences.cfg.lock`),用户传值被忽略。 |
| `time_range` | `webadapter.py:95-101` (`parse_time_range`) | 允许值:`''`/`'None'`(→ `None`)、`day`、`week`、`month`、`year`。其他值抛异常 → 400。引擎不支持 time_range 时该引擎被**静默跳过**(`processors/abstract.py:264-265`)。 |
| `language` | `webadapter.py:55-72` (`parse_lang`) | 需匹配 `VALID_LANGUAGE_CODE` 正则或等于 `'auto'`,否则抛异常 → 400。优先级:`q` 里的 `:lang` 修饰符 > `language` 参数 > preferences 默认(`webadapter.py:61-66`)。`'auto'` 会用客户端 locale 或退回 `'all'`(`webadapter.py:266-267`)。 |
| `categories` | `webadapter.py:117-119` (`parse_category_form`) | 逗号分隔;逐个 `strip` 后**只保留已知分类**(未知分类被静默丢弃,不报错)。 |
| `category_<name>` | `webadapter.py:120-132` | 布尔开关式:值非 `'off'` 则加入该分类,`'off'` 则移除。用于 HTML 表单的 checkbox,我们一般用不到。 |
| `engines` | `webadapter.py:181-189` (`parse_generic`) | 逗号分隔引擎名;`strip` 后**只保留 `engines` 字典里存在的引擎**(未知的静默丢弃)。每个映射为 `EngineRef(name, engines[name].categories[0])` —— 即挂到该引擎的**第一个分类**上。 |
| `engine_data-<engine>-<key>` | `webadapter.py:212-218` (`parse_engine_data`) | 引擎翻页/游标状态透传,键名形如 `engine_data-google-xxx`。一般由前一次结果回填,我们初期用不到。 |
| `timeout_limit` | `webadapter.py:104-114` | 每次搜索的超时秒数(float)。也可用 `q` 里的 `<n` 修饰符设置(见 Q4)。 |
| `preferences` | `webapp.py:487-488` | 编码后的偏好 blob;存在时**优先于**逐字段 `parse_dict`。我们不用。 |
| `redirect_to_first_result` | 经 `q` 的 `!!`(无参)触发,`query.py:240-247` | 若为真且有结果,`/search` 302 到第一条结果 URL(`webapp.py:695-696`)。**注意(复核补正):此判断位于模板渲染段(section 4),在 `format=json` 提前 return(`webapp.py:672-675`)之后**——所以 `!!`(手气不错)**不会**劫持 JSON 请求;真正会劫持 JSON 的是**外部 bang** `!!g`,它经 `redirect_url` 在 `webapp.py:663` 提前 302(早于 json 分支)。 |
| Cookie | `webapp.py:474` | preferences 也会从 cookie 解析。无状态调用时不受影响。 |

**校验失败的统一行为**:`get_search_query_from_webapp` 抛 `SearxParameterException` 时,`search()` 捕获并返回 `index_error(output_format, e.message)` + **HTTP 400**(`webapp.py:655-657`);对 `format=json` 即 `{"error": "<message>"}`(`webapp.py:551-553`)。其他异常 → HTTP 500(`webapp.py:658-660`)。

### Q2. `format=json` 返回体的完整结构

序列化在 `webutils.get_json_response()`(`webutils.py:162-174`):

```python
data = {
    'query': sq.query,
    'results': [_.as_dict() for _ in rc.get_ordered_results()],
    'answers': [_.as_dict() for _ in rc.answers],
    'corrections': list(rc.corrections),
    'infoboxes': rc.infoboxes,
    'suggestions': list(rc.suggestions),
    'unresponsive_engines': get_translated_errors(rc.unresponsive_engines),
}
```

**顶层键就这 7 个,没有别的。** 尤其注意:

- **没有 `number_of_results`**(总数)。
- **没有 `paging`**(是否有下一页)。`paging` 只传给 HTML 模板(`webapp.py:765`),JSON **不含**。
- **没有 `engine_data`**、没有 `infoboxes` 之外的元信息。
- `corrections` / `suggestions` 是从 `set` 转 `list`,**顺序不确定**。

**`results` 条目字段**(`MainResult.as_dict()` 返回全部 `__struct_fields__`,`result_types/_base.py:339-340` + `354-418`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `url` | str/None | 结果链接 |
| `title` | str | 标题 |
| `content` | str | 摘要 |
| `engine` | str | **主**引擎名(单数)。plugins 前缀 `plugin:`,answerer 前缀 `answerer:`(`_base.py:249-255`) |
| `engines` | set→list | **所有命中该结果的引擎名**。JSONEncoder 把 set 转 list(`webutils.py:157-158`)。**这才是我们要的多来源** |
| `positions` | list[int] | 各引擎给的排名位置 |
| `score` | float | 综合得分,排序依据 |
| `category` | str | 分类 |
| `template` | str | 渲染模板名,默认 `default.html`;`images.html`/`videos.html` 等标识结果类型 |
| `parsed_url` | ParseResult→list | `urllib` 具名元组,JSON 里被序列化成**数组**(6 元素) |
| `img_src` / `thumbnail` / `iframe_src` / `audio_src` | str | 图片/视频/音频类结果的媒体地址 |
| `publishedDate` | datetime→ISO字符串 | JSONEncoder 转 `isoformat()`(`webutils.py:153-154`) |
| `pubdate` | str | publishedDate 的字符串副本(将废弃) |
| `length` | timedelta→秒数 | 视频时长,JSONEncoder 转 `total_seconds()`(`webutils.py:155-156`) |
| `views` / `author` / `metadata` | str | 播放量 / 作者 / 杂项 |
| `priority` | str | `''`/`high`/`low` |
| `open_group` / `close_group` | bool | HTML 分组用,JSON 里恒为 `False`(仅 HTML 分支才会置位,`webapp.py:707-715`) |

> 注意:部分引擎产出的是 **`LegacyResult`(dict 子类)**,其 `as_dict()` 直接返回自身(`_base.py:482-483`),字段随引擎而异,但 `__init__` 会补齐上面这批标准字段(`_base.py:485-505`)。所以**字段存在性基本稳定,但可能夹带引擎自定义键**。

**`answers` 条目**(`result_types/answer.py`):`Answer` 类字段有 `answer`(str,`answer.py:85`)、`url`、`engine`、`template`(`answer/legacy.html`)。`Translations`/`WeatherAnswer` 等有各自结构。

**`infoboxes` 条目**:`LegacyResult`,含 `infobox`(标题)、`urls`(list of `{title,url}`)、`attributes`、`content`、`img_src` 等。

**`unresponsive_engines`——本子系统对 P1 最关键的发现:**

JSON 里它是 `get_translated_errors()` 的返回值(`webutils.py:70-82`):

```python
translated_errors.append((unresponsive_engine.engine, error_msg))
# 返回 sorted(..., key=lambda e: e[0])  —— 按引擎名排序
```

即 JSON 中每一项是 **`[engine_name, "已翻译的人类可读错误串"]`** 二元组(tuple 被 JSON 序列化成数组)。`error_msg` 由 `exception_classname_to_text` 映射 + `gettext` 翻译得到,若引擎被暂停还会前缀 `"Suspended: "`(`webutils.py:78-79`)。

**但底层数据其实更丰富**:`ResultContainer.unresponsive_engines` 是 `set[UnresponsiveEngine]`,而 `UnresponsiveEngine` 是三元组 NamedTuple:

```python
# results.py:47-50
class UnresponsiveEngine(t.NamedTuple):
    engine: str
    error_type: str      # 原始异常类名/错误类型
    suspended: bool
```

**结论:JSON API 丢掉了 `error_type` 原始值和 `suspended` 布尔,只给了翻译后的字符串。** 我们的 `meta.engines_failed` 若想要结构化的 `error_type`,靠 SearXNG 的 JSON 拿不到原始类型,只能拿到翻译串(且受实例 UI 语言影响)。详见 §四行动建议。

> 还有个坑:`add_unresponsive_engine`(`results.py:249-255`)只在 `engine.display_error_messages` 为真时才记录。若某引擎配置了 `display_error_messages: false`,它失败了也**不会**出现在 `unresponsive_engines` 里 —— 静默丢弃发生在 SearXNG 内部,我们无法从 API 层感知。

### Q3. `formats` 配置如何开启 json

- 默认值:`settings_defaults.py:210` 里 `'formats': SettingsValue(list, OUTPUT_FORMATS)`,即代码默认允许全部四种。
- **但发行的 `settings.yml:83-86` 覆盖成了只有 `html`**:

```yaml
# settings.yml:83-86
# formats: [html, csv, json, rss]
formats:
  - html
```

- 闸门:`webapp.py:630` `if output_format not in settings['search']['formats']: flask.abort(403)`。

**所以:开启 JSON 必须在实例的 `settings.yml`(或我们 Docker 的 override settings)里把 `search.formats` 设成包含 `json`**,例如:

```yaml
search:
  formats:
    - html
    - json
```

这属于「部署配置」,不改源码,符合我们「一行源码不改」的约束。

### Q4. `query.py` 的 bang/修饰符语法对透传 query 的影响

`RawTextQuery._parse_query()`(`query.py:280-309`)把 `q` 按空白切分,逐段尝试用 `PARSER_CLASSES`(`query.py:253-259`)解析。**任何一段被识别为修饰符,就从「用户查询词」里剥离,不会送给引擎当搜索词。** 五种修饰符:

| 语法 | 解析器 | 出处 | 效果 |
|---|---|---|---|
| `<n` | TimeoutParser | `query.py:43-69` | 设 `timeout_limit`。`<100` 单位秒(`<3`=3s),`>=100` 单位毫秒(`<850`=0.85s) |
| `:lang` | LanguageParser | `query.py:72-149` | 追加语言到 `languages`,**覆盖 `language` 参数**(`webadapter.py:61-62` 取 `languages[-1]`) |
| `!!bang` | ExternalBangParser | `query.py:151-176` | 外部 bang,设 `external_bang`。**会让 `/search` 直接返回 302 重定向**到外部站点(`webapp.py:662-664`,发生在 format 判断之前,**连 JSON 请求也会被 302**) |
| `!engine` 或 `!category` | BangParser | `query.py:178-238` | 命中引擎名/快捷方式/分类名,加入 `enginerefs` 并置 **`specific=True`** |
| `!!`(单独) | FeelingLuckyParser | `query.py:240-247` | 置 `redirect_to_first_result`,有结果时 302 到第一条 |

**对我们透传 query 的三个致命影响:**

1. **`!engine` 会架空我们的 `engines` 参数。** `webadapter.py:269-276`:当 `raw_text_query.specific` 为真,直接用 `raw_text_query.enginerefs`,**完全跳过** `parse_generic`(即忽略 form 里的 `engines`/`categories`)。所以用户查询词里若含 `!google`,我们精心构造的 engines 白名单会被无视。

2. **`!!g foo` 会把搜索变成 302 重定向**,`format=json` 也不例外(重定向判断在 `webapp.py:663`,早于 JSON 分支 `webapp.py:672`)。我们的网关若原样透传,会收到 3xx 而不是 JSON。

3. **`:en` / `<3` 等会悄悄改语言/超时**,与我们显式传的 `language`/`timeout_limit` 冲突且优先级更高。

**行动含义**:我们的网关必须决定「是否允许用户 query 里的 bang 生效」。若要保证 `engines`/`language` 参数的确定性,应在网关侧**转义或拒绝** query 首字符为 `!` `:` `<` 的 token(或至少检测 `!!` 外部 bang 并特殊处理 302)。SearXNG 自身没有「禁用 bang」的开关。

### Q5. 分页与 count 的关系(能否控制每页条数)

**核心结论:SearXNG 不支持「每页条数 / count / limit」。** 全仓库搜索无任何 `count`/`per_page`/`limit` 请求参数解析。返回多少条,取决于「参与的引擎数 × 各引擎该页返回的条数」(多数引擎每页 ~10 条),经去重合并后的总量不可精确控制。

分页机制:
- `pageno`(`webadapter.py:48-52`)控制页码,**每个引擎各自翻自己的第 `pageno` 页**。
- 上限 `max_page`:`processors/abstract.py:258-261`:

```python
max_page = self.engine.max_page or get_setting("search.max_page")
if max_page and max_page < search_query.pageno:
    return None   # 该引擎被跳过(静默)
```

`search.max_page` 默认 `0`=无限(`settings_defaults.py:211`, `settings.yml:55`);部分引擎自带上限(如 brave=10、google=50、mojeek=10)。超页的引擎直接返回 `None`(静默跳过,**不进 `unresponsive_engines`**)。

- 引擎若不支持翻页(`paging=False`)且 `pageno>1`,同样被跳过(`processors/abstract.py:255-256`)。
- 是否「还有下一页」:`ResultContainer.paging`(`results.py:74`, `150-152`,任一引擎支持翻页即为 `True`),**但如 Q2 所述该字段不在 JSON 里**,JSON 消费者无法从响应判断是否有下一页,只能靠「结果数是否为空」推断。

**对我们 P1 的 `count` 参数**:SearXNG 侧无对应能力。我们的 `count` 只能在网关层实现——要么截断合并后的结果列表到 `count` 条,要么循环拉取多页 `pageno` 直到凑够 `count`(会放大对上游引擎的请求量,需谨慎)。

---

## 四、对 P1 的影响与行动建议

1. **调用方式**:网关调 SearXNG 用 `POST /search`,body 为 **form-urlencoded**(不是 JSON),带 `format=json`。`q` 必填。GET 亦可,但长 query 用 POST 更稳。(依据 §2.1)

2. **部署配置**:Docker 的 SearXNG `settings.yml` 必须把 `search.formats` 设为包含 `json`,否则 `/search?format=json` 返回 **403**。这是配置层改动,不碰源码。(依据 Q3)

3. **参数映射**(我们的 `POST /v0/search` → SearXNG form):
   - `engines`(list)→ 逗号 join 成 `engines`;`categories` 同理。
   - `time_range` → 只接受 `day/week/month/year`,其余(除空)会 400,我们应在网关先校验。
   - `language` → 直接透传,但注意 `:lang` bang 会覆盖它。
   - `safesearch` → `0/1/2`,越界 SearXNG 会 400。
   - `count` → **SearXNG 无此参数**,在网关层自行截断/多页拼装(见 Q5)。

4. **`meta.engines_failed`(禁止静默丢弃)——这是本子系统最需要注意的落差**:
   - SearXNG JSON 的 `unresponsive_engines` 是 `[engine, "翻译后的错误串"]` 数组,**丢失了原始 `error_type` 和 `suspended` 布尔**(原始三元组在 `results.py:47-50`,但不出 API)。
   - 我们的 `meta.engines_failed` 若要结构化 `error_type`,**无法**从 SearXNG JSON 无损获取——只能拿到人类可读串(还受实例 UI 语言影响,建议把 SearXNG 实例 locale 固定为 `en` 以稳定该串)。
   - 更隐蔽的坑:配置了 `display_error_messages: false` 的引擎失败后**根本不进** `unresponsive_engines`(`results.py:254`)。要真正做到「不静默丢弃」,网关应**对比「请求的引擎集合」与「实际在 `results[].engines` + `unresponsive_engines` 里出现过的引擎集合」**,差集即为「无声失败/被跳过」的引擎(可能因超页、不支持 time_range、或 display_error_messages 关闭),显式放进 `engines_failed`。

5. **每条结果的 engine 来源**:用 `results[].engines`(集合/数组,多来源),而非 `engine`(单数,只主引擎)。(依据 Q2)

6. **bang 防护**:透传用户 query 前,网关需处理 `!` `:` `<` `!!` 前缀 token,否则 `engines`/`language` 参数可能被架空,`!!` 外部 bang 还会导致 SearXNG 返回 302 而非 JSON。建议:检测到外部 bang 时在网关层拦截并返回明确错误,或转义首字符。(依据 Q4)

7. **无 `number_of_results` / `paging`**:JSON 顶层没有总数和翻页标志,网关的分页元信息需自己算(依据结果数、`pageno`、我们发起的请求)。

---

## 五、待实测确认的问题

1. **`parsed_url` 在 JSON 里的实际形态**:源码 `JSONEncoder` 未特判 `ParseResult`,靠其 namedtuple 特性被序列化成 6 元素数组——需实测确认 `json.dumps` 对 `ParseResult` 是否真如预期输出数组(而非报错)。(`webutils.py:149-159`)
2. **`unresponsive_engines` 错误串的确切取值集合**:`exception_classname_to_text`(`webutils.py:41-67`,复核修正:原写 52-67 有误,该 dict 实际起于第 41 行)只映射了有限异常类,未映射的走 `[None]` 兜底文案。需实测枚举真实引擎失败时(超时/429/CAPTCHA)输出的具体串,才能在网关做可靠的 `error_type` 反向归类。
3. **实例 UI 语言对错误串的影响**:`gettext` 翻译依赖请求 locale;需确认无 cookie 的纯 API 调用下,SearXNG 用的是哪个默认 locale(`ui.default_locale`?浏览器头?),以固定 `engines_failed` 文案。
4. **`display_error_messages` 默认值**:需确认引擎默认是否 `True`(即默认会上报失败),以及我们部署的引擎里有哪些关闭了它——决定「无声失败」范围有多大。(`results.py:254`,`engines/__init__.py`)
5. **`count`/多页拼装的上游压力**:若网关用循环 `pageno` 凑 `count`,需实测每页典型返回条数与各引擎 `max_page`,评估请求放大倍数。
6. **`answers`/`infoboxes` 条目的字段稳定性**:不同引擎/answerer 产出的 dict 结构差异较大,需实测采样确定我们要不要在 `/v0/search` 里暴露它们。
7. **POST 是否受 limiter/bot 检测影响**:`webapp.py` 装了 `limiter`(`webapp.py:1381`),需确认我们内网调用是否会被限流,以及 `settings.yml` 里 limiter 的关闭方式。
