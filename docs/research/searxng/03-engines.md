# SearXNG 子系统研究:引擎系统(engines / enginelib)

> 快照:`vendor/searxng/` @ commit a643858(未修改)。
> 本文所有相对路径均相对 `vendor/searxng/`。行号以该快照为准。
> 读者对象:以后要维护 AI Research Browser 网关、需要理解"我们调的这个 SearXNG 到底怎么把一次查询拆给各引擎"的自己。

---

## 一、职责概述

引擎系统负责三件事:

1. **加载 & 注册**:启动时把 `settings.yml` 里 `engines:` 列表的每一项,映射到 `searx/engines/<engine>.py` 这个 Python 模块,套上默认属性,注入 traits,注册进全局字典 `engines` / `categories` / `engine_shortcuts`。见 `searx/engines/__init__.py`。
2. **单引擎适配器**:每个 `searx/engines/*.py` 是一个"命名空间模块",通过约定的模块级变量(`categories`、`paging`、`time_range_support`…)和函数(`request`、`response`、可选 `fetch_traits`/`init`/`setup`)对接一个真实搜索源。共 249 个引擎文件(`ls searx/engines/*.py | wc -l` = 249)。
3. **traits(语言/地区映射)**:把 SearXNG 内部的 locale(babel 风格,如 `zh-TW`)映射到各引擎自己的语言/地区代号(如 DDG 的 `tw-tzh`、Brave 的 `ca`)。见 `searx/enginelib/traits.py`。

注意:引擎"适配器"本身**不发 HTTP**。它只在 `request()` 里往一个 `params` 字典里填 `url`/`method`/`data`/`headers`/`cookies`,由上层 processor(`searx/search/processors/online.py`)真正发请求,再把响应交回 `response()` 解析。这条边界对我们 P1 很重要:失败暴露、超时、封禁全在 processor 层,不在引擎里。

---

## 二、架构与关键流程

### 2.1 加载流程(启动时一次)

`load_engines(settings['engines'])`(`engines/__init__.py:311`)遍历 YAML 列表,对每一项:

```
load_engines            # __init__.py:311  清空全局字典,逐条 load_engine
  └─ load_engine        # __init__.py:90
       ├─ load_module(module_name + '.py', ENGINE_DIR)   # 按 engine: 字段 import 模块
       ├─ check_engine_module        # :78  防 name collision(如 network 被 import 成模块)
       ├─ update_engine_attributes   # :187 合并 about、把 engine_data 覆盖到模块属性、补默认值
       ├─ update_attributes_for_tor  # :235
       ├─ EngineTraitsMap.from_data().set_traits(engine)  # :146-149 注入 traits(下详)
       ├─ is_engine_active           # :259 inactive / onions 无 tor → 跳过
       ├─ is_missing_required_attributes  # :241 任何非 _ 开头属性为 None → 报错跳过
       ├─ call_engine_setup          # :271 可选同步 setup()
       └─ 若引擎无 tab 分类,append DEFAULT_CATEGORY('other')  # :162
  └─ register_engine    # :296 写入 engines[name] / engine_shortcuts[shortcut] / categories[cat]
```

关键点:
- `inactive: true` 的项在 `load_engines` 里**在 load 之前**就 `continue` 跳过(`:318`),连模块都不 import。
- load 失败(缺 tor 等)不会 crash,而是把该项标记 `inactive=True` 并 error 日志(`:326-331`)。
- **但**若出现重名 `name` 或重复 `shortcut`,`register_engine` 直接 `sys.exit(1)`(`:299`、`:304`)——整个进程退出。配置引擎时这是硬约束。
- 同样,`load_module` 抛 `SyntaxError/ImportError/RuntimeError` 等会 `sys.exit(1)`(`:133-135`)。

### 2.2 默认属性(每个引擎的"出厂设置")

`ENGINE_DEFAULT_ARGS`(`__init__.py:32-55`)定义了没在模块/YAML 里显式设置时的兜底值。对 P1 关心的几个:

| 属性 | 默认 | 含义 |
|---|---|---|
| `engine_type` | `"online"` | processor 类型 |
| `paging` | `False` | 支持翻页 |
| `max_page` | `0` | 0=不限;否则末页上限 |
| `time_range_support` | `False` | 支持时间范围 |
| `safesearch` | `False` | 支持安全搜索 |
| `language_support` | `False` | 支持语言/locale |
| `categories` | `["general"]` | 分类 |
| `disabled` | `False` | 默认不参与搜索但用户可开 |
| `inactive` | `False` | 彻底移除 |
| `weight` | `1.0` | 结果排名权重 |
| `shortcut` | `"-"` | `!bang` 前缀 |
| `timeout` | `settings.outgoing.request_timeout` | 单引擎超时 |

覆盖优先级(`update_engine_attributes` `:187-232`):**模块里写的值 → 被 YAML `engine_data` 覆盖 → 二者都没有才用 `ENGINE_DEFAULT_ARGS`**。即 YAML 能覆盖模块声明(例如 brave 模块里 `time_range_support=False`,但 `settings.yml` 的 `brave` 项设 `time_range_support: true`,YAML 赢——见 `settings.yml:3216`)。

一个隐藏校验:若 `engine_traits.json` 里该引擎有 `languages` 但引擎 `language_support` 为 False,启动直接 `raise ValueError`(`:231-232`)。

### 2.3 引擎失败如何暴露(P1 `meta.engines_failed` 的源头)⭐

这是本子系统对 P1 最关键的一条链路。引擎的失败**不在引擎模块里处理**,而在 `searx/search/processors/` + `searx/results.py`:

1. `OnlineProcessor.search`(`processors/online.py:241-284`)用一串 `except` 兜住所有异常:
   - `ssl.SSLError` / `httpx.TimeoutException` / `httpx.HTTPError` → `handle_exception(..., suspend=True)`
   - `SearxEngineCaptchaException` / `SearxEngineTooManyRequestsException` / `SearxEngineAccessDeniedException` → `handle_exception(..., suspend=True)`(`:275-281`)
   - 其它 `Exception` → `handle_exception(...)`(不 suspend)
2. 超时:线程被主线程标记 `_timeout` 时,`extend_container`(`processors/abstract.py:226-228`)调用 `handle_exception(rc, 'timeout', False)`。
3. `handle_exception`(`processors/abstract.py:175-201`)做三件事:
   - 把异常类名规范成字符串 `error_message = 模块名.类名`(如 `searx.exceptions.SearxEngineCaptchaException`;内置异常无模块前缀,如 `timeout` 是裸字符串)。
   - `result_container.add_unresponsive_engine(self.engine.name, error_message)`(`:189`)。
   - 累加 metrics,若 `suspend=True` 则调用 `suspended_status.suspend(...)` 让引擎进入熔断。

4. `ResultContainer.add_unresponsive_engine`(`results.py:249-255`)把它存成一个具名元组:
   ```python
   class UnresponsiveEngine(t.NamedTuple):   # results.py:47-50
       engine: str
       error_type: str
       suspended: bool
   ```
   存进 `self.unresponsive_engines: set[UnresponsiveEngine]`(`results.py:75`)。默认 `suspended=False`。
5. 若引擎当前处于熔断态,`extend_container_if_suspended`(`abstract.py:235-241`)会以 `suspended=True` 加入 unresponsive 集合,`error_type` 为熔断原因字符串。

**JSON API 输出(⚠️ P1 必读的坑)**:`get_json_response`(`webutils.py:162-174`)把该集合塞进 `unresponsive_engines` 字段,但**经过 `get_translated_errors`**(`webutils.py:70-82`):

```python
translated_errors.append((unresponsive_engine.engine, error_msg))
```

也就是说,原版 SearXNG 的 **JSON `/search?format=json` 里 `unresponsive_engines` 是 `[engine_name, 已翻译的人类可读文案]` 二元组列表** —— 原始的 `error_type`(异常类名)和 `suspended` 布尔**被丢弃/压平**了(`suspended=True` 只是给文案加个 `"Suspended: "` 前缀,`:78-79`)。`exception_classname_to_text`(`webutils.py:~50-67`)是一张把类名映射到 i18n 文案的表,未命中就用泛化的 `None` 文案。

对我们的意义:**如果我们的网关直接消费 SearXNG 的 JSON API,拿到的 `unresponsive_engines` 已经是翻译后的模糊文案,拿不到结构化的 `error_type`/`suspended`**。要在 `meta.engines_failed` 里给出 `(engine, error_type, suspended)` 这种结构化信息,需要在网关侧自己保留原始 error_type(见"对 P1 的影响")。

### 2.4 每条结果的 engine 归属(P1"返回每条结果的 engine 来源")⭐

- 每个 `Result` 有 `engine: str|None`(`result_types/_base.py:249`)和 `engines: set[str]`(`:408`)。
- `ResultContainer.extend`(`results.py:82-108`)在收结果时设 `result.engine = result.engine or engine_name`(`:93`);LegacyResult 走 `result["engine"] = ... or engine_name`(`:108`)。
- 去重合并:当不同引擎返回同一 URL,`merge_two_main_results`(`results.py:332-350`)把 `other.engine` 加进 `origin.engines`(`:350`)。`normalize_result_fields` 里也会把自己的 engine 塞进 engines 集合(`_base.py:439-440`、`:576-577`)。
- `as_dict`(`_base.py:339-340`)把 `__struct_fields__` 全序列化,**含 `engine` 和 `engines`**。JSON API 的每条 result 因此天然带 `engine`(首个命中的引擎)和 `engines`(所有命中该结果的引擎集合)。P1 的"每条结果的 engine 来源"直接可用,无需额外工作 —— 但注意 `engines` 是 set,`JSONEncoder.default` 把 set 转成 list(`webutils.py:157-158`)。

---

## 三、逐问题详解

### 问题 1:一个引擎适配器的解剖

一个 online 引擎模块(以 `arxiv.py` 最简洁为范本)由两部分构成:

**(A) 模块级声明变量**(被 `load_engine` 读取,决定引擎能力):
- `about: dict`(`arxiv.py:26-33`)—— 元信息,合并进 `EngineAbout`(`enginelib/__init__.py:183-225`)。
- `categories: list[str]`(`arxiv.py:35`,`["science", "scientific publications"]`)。
- 能力开关:`paging`(`arxiv.py:36`)、`time_range_support`、`language_support`、`safesearch`、`max_page`。这些就是 `Engine` 抽象类(`enginelib/__init__.py:228-416`)里注释的那批属性。
- 引擎私有配置:如 arxiv 的 `base_url`、`arxiv_max_results`;brave 的 `brave_category`、`Goggles`、`safesearch_map`、`time_range_map`。

**(B) 约定函数**:
- `request(query, params) -> None`(`arxiv.py:68-75`):**唯一职责是填 `params`**。填 `params["url"]`(必填,填 `None` 表示"这次不发请求",见 ddg `:366`)、`params["method"]`、`params["data"]`、`params["headers"]`、`params["cookies"]`、`params["url"]` 等。参数怎么映射全在这里:
  - 翻页:arxiv 用 `start = (pageno-1)*max_results`(`:72`);ddg 用 `offset = 10 + (pageno-2)*15`(`duckduckgo.py:434`)。
  - 时间范围:ddg `time_range_dict = {"day":"d","week":"w",...}`(`:214`),填进 `data["df"]` + cookie(`:446-449`);brave `time_range_map = {"day":"pd",...}`(`:189-194`)填 `args["tf"]`。
  - 语言/地区:通过 `traits.get_region(...)` / `traits.get_language(...)` 转换(下节)。
  - safesearch:brave 用 `safesearch_map = {2:"strict",1:"moderate",0:"off"}`(`:183`)写 cookie(`:221`)。ddg 声明 `safesearch=True` 但 **request 里没有用 `params["safesearch"]`** —— 它的注释说 DDG-lite 用户不能选、结果被服务端过滤(`duckduckgo.py:207-208`)。这是一个"声明支持但实现是空操作"的例子,值得警惕。
- `response(resp) -> EngineResults`(`arxiv.py:78-129`):解析 `resp`(`SXNG_Response`),用 `res.add(res.types.Paper(...))` / `MainResult(...)` / `Answer(...)` 等构造结果。返回 `EngineResults` 对象(新式)或 `list[dict]`(wikipedia 仍用旧式 `results.append({...})`,`wikipedia.py:190-208`)。
- 可选:`fetch_traits(engine_traits)`(离线抓取语言/地区表,只在 `./manage` 工具跑,不在请求路径)、`init`/`setup`(启动初始化,`enginelib/__init__.py:364-402`)。

`Engine` 抽象基类(`enginelib/__init__.py:228`)注释说 "This class is currently never initialized and only used for type hinting" —— 引擎实际是**模块**(`types.ModuleType`),不是类实例。全局字典类型即 `dict[str, Engine | types.ModuleType]`(`__init__.py:64`)。

### 问题 2:引擎如何注册与加载(settings.yml ↔ 代码)

- **`name`**:SearXNG 内部/结果页显示用的唯一名。不能含下划线(`__init__.py:117`),必须小写(否则转小写并 warn,`:121`)。重名 → `sys.exit`。
- **`engine`**:指向 `searx/engines/<engine>.py` 的文件名(去 `.py`)。**多个 name 可以共用一个 engine 模块**。典型:模块 `brave.py` 的 `brave_category`(`brave.py:154`)支持 5 类(search/images/videos/news/goggles),但默认 `settings.yml` 只配了 **4 个引擎**(`brave`/`brave.images`/`brave.videos`/`brave.news`,`settings.yml:3213-3241`);第 5 个 `brave.goggles` 的模板存在但**整段被注释掉**(`settings.yml:3243-3251`),默认不加载。各引擎靠 YAML 里不同的 `brave_category` 区分。DDG 则相反,`duckduckgo`、`duckduckgo_web`、`duckduckgo_extra` 是不同模块(`settings.yml:863-885`)。
- **`shortcut`**:`!bang` 前缀,如 `ddg`、`br`、`arx`(`settings.yml:865`/`3215`/`478`)。全局唯一,重复 → `sys.exit`。
- 我们 P1 四个引擎的 YAML 现状:
  - `arxiv`(`settings.yml:476-478`):无 `disabled`,**默认启用**。
  - `duckduckgo`(`:863-865`):无 `disabled`,默认启用,engine 模块是 `duckduckgo`(html no-JS)。
  - `wikipedia`(`:537-538`):默认启用。
  - `brave`(`:3213-3220`):默认启用,YAML 里额外开了 `time_range_support: true`、`paging: true`、`categories: [general, web]`。
- 注意大量引擎带 `disabled: true`(如 `360search:305`),`disabled` 与 `inactive` 区别:`disabled` 仍加载、用户可手动开;`inactive` 直接不加载。

### 问题 3:enginelib traits(语言/地区映射)机制

**目的**:SearXNG 用统一的 babel locale(如 `fr-BE`、`zh-TW`)表示用户选的语言/地区,但各引擎有自己的代号。traits 就是这张双向映射表 + 查表逻辑。

- 数据结构 `EngineTraits`(`traits.py:36-121`)是个 dataclass:
  - `regions: dict[str,str]` —— SearXNG region tag → 引擎 region 代号(`:40`)。
  - `languages: dict[str,str]` —— SearXNG lang tag → 引擎 lang 代号(`:57`)。
  - `all_locale: str|None` —— SearXNG 的 `"all"` 映射到引擎的哪个值(`:74`)。DDG 是 `wt-wt`(`duckduckgo.py:550`),brave 是 `all`(`brave.py:475`)。
  - `custom: dict` —— 引擎自定义,如 wikipedia 的 `wiki_netloc`(哪个语言用哪个域名,`wikipedia.py:257-264`)、brave 的 `ui_lang`(`brave.py:99-112`)、ddg 的 `lang_region`(`duckduckgo.py:288`)。
- **查表**:`get_language(searxng_locale, default)`(`traits.py:87-101`)和 `get_region(...)`(`:103-117`)。`"all"` 特判返回 `all_locale`,否则委托 `locales.get_engine_locale`(babel 风格的"最佳匹配"降级:精确 → 语言级 → 默认)。引擎在 `request()` 里调用,如 ddg `traits.get_region(params["searxng_locale"], traits.all_locale)`(`duckduckgo.py:370`)。
- **数据来源**:持久化在 `searx/data/engine_traits.json`(`traits.py:186`),通过 `ENGINE_TRAITS` 常量加载(`from_data` `:194-200`)。这张 JSON 是**离线**用 `fetch_traits()` 抓各引擎官网/JS 生成的(`fetch_traits` `traits.py:202-233`,逐引擎调用模块的 `fetch_traits`),不在请求路径。⇒ **我们 Docker 部署原版镜像,这张表已内置,无需自己抓。**
- **注入**:`load_engine` 里 `EngineTraitsMap.from_data().set_traits(engine)`(`__init__.py:146-149`)。`set_traits`(`traits.py:235-254`)按 `engine.name` 找,找不到再按 `engine.engine`(共享模块的多引擎复用同一 traits,如 brave.images 复用 brave)。`_set_traits_v1`(`:149-180`)处理 YAML 里写死单一 `language:`/`region:` 的情况(把 traits 收窄到那一个),并据此设置 `engine.language_support`。
- 若引擎不支持语言(`language_support=False`),traits 基本为空,`get_language/get_region` 走默认。

### 问题 4:四个 P1 常开引擎的特点与反爬风险

#### duckduckgo(`duckduckgo.py`)—— 反爬最重
- 用的是 **html no-JS 端点** `https://html.duckduckgo.com/html/`(`:210`),POST form。`categories=["general","web"]`,`paging/time_range_support/language_support/safesearch` 全 True(`:203-207`)。
- **`vqd` 机制**:DDG 的 bot 防护核心。第 2 页起必须带 `vqd`(query+UA 的哈希,缓存在 `EngineCache` SQLite 1 小时,`:230-251`)。**第 1 页不需要 vqd**;若翻页时没 vqd,代码**主动 raise `SearxEngineCaptchaException(suspended_time=0)`**(`:418-421`)而不是硬发请求——因为无 vqd 的翻页请求会被 DDG 立刻识别为 bot 并触发 CAPTCHA、降低本机 IP 声誉。
- **UA 必须固定**:`_HTTP_User_Agent = gen_useragent()`(模块加载时生成一次,`:220`),因为 vqd 依赖 UA,UA 变了缓存的 vqd 就失效。request 里强制 `headers["User-Agent"] = _HTTP_User_Agent`(`:382`)。
- 大量 `Sec-Fetch-*` 头 + `Referer` 模拟真实浏览器(`:384-389`)。
- CAPTCHA 检测:response 里查 `//form[@id='challenge-form']`(`is_ddg_captcha` `:459-463`),命中就 raise。
- 限制:query ≥ 500 字符直接放弃(`:364-367`);中文 locale(`zh*`)翻页会 403,直接放弃(`:423-427`)。
- **IP 级封禁**(非 session):文档大段说明 DDG 封的是 IP,且 2025 Q3/Q4 改过机制,过去是"滑动窗口 ~1h 无请求自动解封"(`:117-126`)。⇒ **多用户共享一个出口 IP 时,DDG 极易被封;P1 若默认开 DDG 需考虑速率/代理。**

#### brave(`brave.py`)—— HTML 抓取,分类多
- **一个模块五种用途**,靠 `brave_category`(`search`/`images`/`videos`/`news`/`goggles`)分流,`response` 里 if 分派(`:264-286`)。
- `search`/`goggles` 才支持 `paging`(`max_page=10`,超了会被当 bot,`:174-180`)和 `time_range`(`time_range_map={"day":"pd",...}`,`:189-194`)。模块默认 `paging=False`/`time_range_support=False`,靠 YAML 显式开(`settings.yml:3216-3217`)。
- safesearch 走 cookie(`safesearch_map`,`:183`、`:221`)。语言支持很弱:文档明说 Brave 索引基本只支持 locale、不支持语言,中文/阿拉伯语结果质量低(`:59-64`、`:83-92`)。region 用两位国家码(`:226-227`),ui_lang 存 `custom["ui_lang"]`。
- **反爬**:纯 HTML 解析(`use_official_api=False`),`_parse_search` 靠 CSS class xpath(`//div[contains(@class,'snippet ')]`,`:293`)。风险:①页面结构变了解析就碎(会抛 `KeyError`/xpath 异常 → unresponsive);②翻页 > 10 页触发 bot 标记(`:178-180`);③无官方 API、无 key,IP 抓取限流风险。注意 `settings.yml` 给 brave.images/videos/news 配了独立 `network: brave`(`:3224` 等)以隔离网络。

#### wikipedia(`wikipedia.py`)—— 官方 API,最稳
- 用**官方 REST API** `rest_v1_summary_url`(`:93`,`https://{netloc}/api/rest_v1/page/summary/{title}`),`use_official_api=True`(`:71`)。**基本无反爬风险**,是 P1 最可靠的引擎。
- **不支持 paging / time_range**(模块没声明,默认 False),`language_support=True`(`:75`)。每种语言是**独立域名**(不是一个 wiki 服务所有语言),靠 `custom["wiki_netloc"]` 选域名(`get_wiki_params` `:137-145`)。
- LanguageConverter:中文等语言靠 `Accept-Language` 头返回不同字形(简/繁/港/台),`wiki_lc_locale_variants`(`:110-127`)。
- request 里 `query.title()` 化(`:150-151`),`raise_for_httperror=False`(自己处理 404/400,`:157`、`:167-181`)。response 返回 `infobox` 和/或 `list` 两种(`display_type`,默认只 `["infobox"]`,`:77`)⇒ **默认 wikipedia 结果进 infobox,不进主结果列表!** 若 P1 想让 wiki 结果出现在普通结果流,需要在 YAML 配 `display_type: [list]` 或 `[list, infobox]`。这是个容易踩的坑。
- 返回的是**旧式 `list[dict]`**(`:190-208`),不是 `EngineResults`。

#### arxiv(`arxiv.py`)—— 官方 API,学术
- 官方 API `https://export.arxiv.org/api/query`(`:45`),返回 **Atom/RSS XML**,`use_official_api=True`。**无反爬风险**。
- `paging=True`(`start=(pageno-1)*10`,每页 10,`:37`、`:72`),**不支持 time_range / language**。
- `categories=["science","scientific publications"]`(`:35`)⇒ **默认不在 `general` 分类**,P1 若按 category=general 搜不会命中 arxiv;要么显式 `engines=[arxiv]`,要么按 `categories=[science]` 请求。
- 结果类型是 `Paper`(`:115`),字段丰富:doi/authors/journal/pdf_url/tags/comments,对学术研究场景很有价值。
- search_query 前缀写死 `all:`(`arxiv_search_prefix`,`:38`、`:71`),即全字段搜。

### 问题 5:只启用引擎子集、设权重和分类

- **启用子集**:两条路
  1. **每次请求指定**:SearXNG 搜索 API 支持 `engines=`(逗号分隔 name)或 `!bang`(用 shortcut)或 `categories=` 参数,只跑子集。这属于搜索编排层(见 02-search-orchestration 笔记),引擎层这边只要引擎已加载即可被选中。
  2. **全局配置**:`settings.yml` 里给不想要的引擎设 `disabled: true`(仍加载、可临时开)或 `inactive: true`(不加载)。我们 P1 用 Docker 部署,可以自定义挂载 `settings.yml` 把默认引擎列表裁到只留 duckduckgo/brave/wikipedia/arxiv 等,其余 `disabled: true`。
  ⚠️ 引擎"是否参与某次搜索"最终由**搜索层按 category/engines 参数**决定。**已确认**(`webadapter.py:181-189`):带 `engines=` 显式点名时,引擎直接从 `engines[name]` 取,**不经过 `disabled_engines` 过滤** —— 即 `disabled: true` 的引擎用 `engines=` 点名仍会被调用。只有走 category 展开的引擎(`get_engineref_from_category_list` `:158-169`)才会被 `disabled_engines` 过滤。
  ⚠️ 另一个坑(`webadapter.py:183`):用 `engines=` 点名时,传给引擎的 `engine_category` = 该引擎 `categories[0]`(**第一个分类**)。brave 是 `general`,arxiv 是 `science`。若引擎 `response`/`request` 依赖 category(如 brave 靠 `brave_category` 而非这个,不受影响;但某些引擎会看 `params["category"]`),要注意点名调用时的分类是列表首项。
- **权重**:`weight: float`(默认 1.0,`ENGINE_DEFAULT_ARGS:54`)。在结果排名里 `calculate_score`(`results.py:17`,老代码里叫 result_score,本快照已改名)用它乘权(`results.py:24-25`:`weight *= float(engines[result_engine].weight)`)。**infobox** 去重合并时也用 weight 决定"以谁为主"(`merge_two_infoboxes`,`results.py:275-279` 比较 weight1/weight2 挑 `origin.engine`);**主结果** `merge_two_main_results`(`results.py:332-357`)本身不比 weight,而是靠 `calculate_score` 的分数排序决定顺序。⇒ 想让某引擎结果更靠前,YAML 里给它设 `weight: 2.0` 之类。
- **分类**:`categories:` 字段(YAML 可写字符串或 list,`update_engine_attributes:219-222` 会把逗号串 split 成 list)。决定引擎出现在哪个 tab、被哪个 `categories=` 请求命中。无 tab 分类的引擎会被自动加 `other`(`__init__.py:162-163`)。同一模块不同引擎可配不同分类(brave.images→`[images,web]` 等)。

---

## 四、对 P1 的影响与行动建议

1. **`meta.engines_failed` 不能直接抄 SearXNG JSON 的 `unresponsive_engines`**。原版 JSON 输出已被 `get_translated_errors`(`webutils.py:70-82`)压平成 `[engine, 翻译文案]`,**丢了结构化的 `error_type` 和 `suspended`**。我们要的结构化三元组 `(engine, error_type, suspended)` 存活在 `ResultContainer.unresponsive_engines`(`results.py:47-50`),但那是进程内对象,HTTP JSON 拿不到。
   - **行动 A(推荐)**:P1 网关只调 SearXNG 的 `format=json`,先接受"文案级"失败信息;若要更细的 error_type,需要在网关侧维护自己的引擎调用(不现实,因为我们不自己发引擎请求)。→ 现实做法:解析 JSON `unresponsive_engines` 的每个 `[engine, msg]`,把 `msg` 当 `error_type` 透传,`suspended` 从 msg 是否以 "Suspended" 前缀判断(脆弱,依赖 i18n)。**这条要实测确认 JSON 里 suspended 前缀的确切文案。**
   - **行动 B**:接受 SearXNG 的语义——"没出现在结果里、也没在 unresponsive 里"的引擎 = 被 get_params 跳过(不支持该 time_range/paging),这类**不是失败**,不该进 engines_failed。要在网关区分"被跳过"vs"真失败"。
2. **禁止静默丢弃**:好消息是 SearXNG 本身不静默丢弃失败引擎——所有异常都进 `unresponsive_engines`(`abstract.py:189`),超时(`abstract.py:228`)、熔断(`abstract.py:237`)也进。我们只要**完整透传 JSON 的 `unresponsive_engines`**就满足"显式暴露"。要小心的是被 `get_params` 因能力不支持而 `return None` 跳过的引擎(`abstract.py:254-265`)——这些引擎既不在 results 也不在 unresponsive,网关应能推断出"我请求了 N 个引擎,回来 M 个,失败 K 个,静默跳过 = N-M-K"并显式标注。
3. **每条结果的 engine 来源已就绪**:JSON 每条 result 自带 `engine`(首命中引擎)和 `engines`(全部命中引擎的 list)。P1 直接映射即可,无需改 SearXNG。
4. **参数映射对照**(网关 → SearXNG JSON API query):
   - `engines` → `engines=`(逗号分隔 name);`categories` → `categories=`;`language` → `language=`(SearXNG locale,如 `zh-TW`/`all`);`time_range` → `time_range=`(`day/week/month/year`);`safesearch` → `safesearch=`(0/1/2);`count` → SearXNG 无直接"count",靠 `pageno` 翻页,每引擎每页条数由引擎决定(arxiv 10、ddg 变长)。**count 需要我们在网关侧截断/聚合,SearXNG 不保证精确条数。**(需实测)
5. **引擎选型建议(按稳定性)**:wikipedia、arxiv(官方 API,几乎不会失败)> brave(HTML 抓取,结构易变)> duckduckgo(反爬最重,IP 易封)。P1 默认引擎集若含 DDG,务必考虑出口 IP 速率与被封风险;wikipedia 记得配 `display_type: [list]` 否则结果只进 infobox;arxiv 记得它不在 general 分类。
6. **自定义 settings.yml**:Docker 部署时挂载裁剪版 `settings.yml`,把 P1 不用的引擎设 `disabled: true`,减少默认全站搜索的噪声与被封面。权重可在此调。注意重名/重 shortcut 会让容器启动即 `sys.exit(1)`。

---

## 五、待实测确认的问题

见结构化输出 open_questions。
