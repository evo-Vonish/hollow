# SearXNG 配置系统研读笔记(06-settings)

> 研读对象(相对 `/home/user/hollow/vendor/searxng/`,commit a643858,未修改):
> - `searx/settings_loader.py` —— 配置文件的查找、加载、合并逻辑
> - `searx/settings_defaults.py` —— schema、类型校验、环境变量覆盖
> - `searx/settings.yml` —— 出厂默认配置(3439 行,含全部 engines)
> - `container/settings.template.yml` —— 官方容器镜像的用户配置模板
>
> 服务对象:P1 目标是 `POST /v0/search`,我们的 FastAPI 网关只调 SearXNG 的 JSON API。
> 本笔记回答"我们自己的最小 `settings.yml` 该怎么写、哪些键必须显式设置、环境变量能覆盖什么"。

---

## 一、职责概述

配置系统解决三件事:

1. **找文件**:决定去哪里读用户自定义的 `settings.yml`(`settings_loader.get_user_cfg_folder`)。
2. **合并**:把用户配置叠加到出厂默认配置上(`settings_loader.update_settings`,`use_default_settings` 机制)。
3. **校验与环境变量覆盖**:把合并后的 dict 过一遍 schema,填默认值、做类型校验、用 `SEARXNG_*` 环境变量覆盖(`settings_defaults.apply_schema` + `SettingsValue.__call__`)。

三步顺序执行,最终产出一个 `settings` dict,供 `webapp.py`、`engines`、`search` 等模块全局读取。

---

## 二、架构与关键流程

### 加载入口:`load_settings()`(settings_loader.py:194-227)

```
load_settings(load_user_settings=True)
  ├─ cfg = load_yaml(DEFAULT_SETTINGS_FILE)          # 先读出厂默认 searx/settings.yml (:199)
  ├─ cfg_folder = get_user_cfg_folder()              # 决定用户配置目录 (:200)
  ├─ if 无用户配置目录: return cfg                    # 只有默认配置 (:202-203)
  ├─ 定位 cfg_file(目录/文件两种模式)               # (:205-213)
  ├─ if 文件不存在: return cfg                        # (:214-215)
  ├─ user_cfg = load_yaml(cfg_file)                  # (:218)
  └─ if is_use_default_settings(user_cfg):
         update_settings(cfg, user_cfg)              # 合并模式 (:220-223)
     else:
         cfg = user_cfg                              # 完全替换模式 (:224-225)
```

注意:`load_settings` 只做"文件层"的合并,**不做 schema 校验**。schema 校验(填默认值 + 类型检查 + 环境变量覆盖)在 `settings_defaults.apply_schema` 里,由更上层的 `searx/__init__.py` 调用(本子系统范围外,但结论确定:`OUTPUT_FORMATS`、`SETTINGS`、`apply_schema` 都在 `settings_defaults.py`)。

### schema 校验:`apply_schema()`(settings_defaults.py:142-179)

对 `SCHEMA`(:182-273)递归遍历。每个叶子是一个 `SettingsValue` 实例,`SettingsValue.__call__`(:91-101)做三件事:
1. 值缺失(`_UNDEFINED`)→ 用 `default`(:92-93)。
2. 若该键绑定了 `environ_name` 且该环境变量存在 → **用环境变量的字符串值覆盖**(:95-98);若类型是 `bool`,走 `STR_TO_BOOL` 映射(:97-98)。
3. `check_type_definition` 类型校验(:100),不符合抛 `ValueError`,`apply_schema` 收集错误,顶层若有错抛 `ValueError("Invalid settings.yml")`(:177-178)。

---

## 三、逐问题详解

### 问题 1:配置加载顺序与 `SEARXNG_SETTINGS_PATH`

**加载顺序(settings_loader.py):**
1. 永远先加载出厂默认 `searx/settings.yml`(`DEFAULT_SETTINGS_FILE`,:36、:199)。
2. 再按 `get_user_cfg_folder()` 找用户配置目录,加载其中的 `settings.yml` 并合并/替换。

**`get_user_cfg_folder()` 的三条规则(settings_loader.py:66-115):**

| 优先级 | 条件 | 行为 | 出处 |
|---|---|---|---|
| 规则 1 | `SEARXNG_SETTINGS_PATH` 指向一个**目录**(如 `/etc/mysxng/`) | 该目录作为配置目录,用户设置读 `<目录>/settings.yml` | :99-103 |
| 规则 2 | `SEARXNG_SETTINGS_PATH` 指向一个**文件**(如 `/etc/mysxng/myinstance.yml`) | 该文件即用户设置,其父目录用作其他配置(engines 等)的目录 | :104-105 |
| 规则 3 | 未设 `SEARXNG_SETTINGS_PATH`,且 `/etc/searxng` 目录存在 | 用 `/etc/searxng` | :109-113 |
| 兜底 | 以上都不满足 | 返回 `None`,只有默认配置生效 | :86、:114-115 |

关键细节:
- 若 `SEARXNG_SETTINGS_PATH` **已设置但路径不存在** → 抛 `EnvironmentError`(:106-107),启动直接失败(不是静默忽略)。
- 有一个未公开的内部测试开关 `SEARXNG_DISABLE_ETC_SETTINGS`(值为 `1`/`true`)会禁用规则 3 的 `/etc/searxng` 兜底(:95-97、:109)。**注释明确说"仅供内部测试、未文档化"**,生产别用。
- 规则 2("文件"模式)在 `load_settings` 里的处理:取该文件的 basename 作为 `settings_yml`(:206-208),这样"配置目录 = 父目录,配置文件名 = 该文件名"。用途是"多 profile 切换"(测试场景,见 :79-82 docstring)。

**对我们(Docker 部署):** 官方容器镜像默认把用户配置挂在 `/etc/searxng/settings.yml`(走规则 3)。我们最省心的做法就是把自己的最小 `settings.yml` 挂到容器 `/etc/searxng/settings.yml`,不设 `SEARXNG_SETTINGS_PATH`。

---

### 问题 2:`use_default_settings` 机制,以及我们的最小 `settings.yml` 怎么写

**开关判定:`is_use_default_settings()`(settings_loader.py:182-191):**
- `use_default_settings: true` → 合并模式(返回 True)。
- `use_default_settings:` 是一个 **dict** → 也是合并模式(返回 True)。
- `false` 或**缺省(不写这个键)** → **完全替换模式**(返回 False,:189)。
- 其他值 → 抛 `ValueError`(:191)。

**合并模式的具体行为:`update_settings()`(settings_loader.py:127-179):**

1. **非 engines 的普通键**:用户配置深度合并进默认配置(`update_dict` 递归,:118-124、:131-136)。dict 递归 merge,标量直接覆盖。
2. **`categories_as_tabs`**:若用户提供,整体覆盖(:138-140)。
3. **`plugins`**:若用户提供(非 None),整体覆盖(:142-144)。
4. **engines** 特殊处理(:146-177):
   - `use_default_settings.engines.remove: [名字...]` → 从默认 engine 列表里**删掉**这些 name(:151、:158-159)。
   - `use_default_settings.engines.keep_only: [名字...]` → 只**保留**这些 name(:152、:162-163)。
   - 顶层 `engines:` 列表 → 逐个按 `name` 匹配:命中默认 engine 则深度合并覆盖其字段(:170-172);未命中则作为**新增自定义 engine 追加**(:173-174)。

**两种写法对比(给我们的决策依据):**

- **合并模式(`use_default_settings: true`)**:继承 SearXNG 内置的 ~200 个 engine 定义,只写差异。优点:省事、跟随上游默认。缺点:默认 engine 列表庞大,启用状态由每个 engine 的 `disabled` 决定,升级镜像时默认集会变。
- **完全替换模式(不写 `use_default_settings`)**:我们的文件就是全部配置。但这样必须自己列全 `engines`(否则 `engines: []`,没有任何搜索引擎)——不现实。

**结论:我们用合并模式 `use_default_settings: true`**(与 `container/settings.template.yml` 一致),只覆盖 P1 关心的少数键。若将来要收敛引擎白名单,再用 `use_default_settings.engines.keep_only` 精确控制。

> ⚠️ 容易踩的坑:`container/settings.template.yml`(全文见下)只有 4 个键:
> ```yaml
> use_default_settings: true
> server:
>   secret_key: "ultrasecretkey"
>   image_proxy: true
> ```
> 它**没有开 `search.formats: [json]`**,所以官方模板开箱即用时 **JSON API 是 403 的**(见问题 5)。这是我们必须显式加的第一条。

---

### 问题 3:`SEARXNG_` 前缀环境变量覆盖了哪些键

环境变量覆盖**不是通配**,只有 schema 里显式绑了 `environ_name` 的键才支持。逐条枚举(settings_defaults.py `SCHEMA`,:182-273):

| 环境变量 | 覆盖的配置键 | 类型 | 默认 | 出处 |
|---|---|---|---|---|
| `SEARXNG_DEBUG` | `general.debug` | bool | False | :184 |
| `SEARXNG_PORT` | `server.port` | int/str | 8888 | :214 |
| `SEARXNG_BIND_ADDRESS` | `server.bind_address` | str | 127.0.0.1 | :215 |
| `SEARXNG_LIMITER` | `server.limiter` | bool | False | :216 |
| `SEARXNG_PUBLIC_INSTANCE` | `server.public_instance` | bool | False | :217 |
| `SEARXNG_SECRET` | `server.secret_key` | str | (无默认,必填) | :218 |
| `SEARXNG_BASE_URL` | `server.base_url` | False/str | False | :219 |
| `SEARXNG_IMAGE_PROXY` | `server.image_proxy` | bool | False | :220 |
| `SEARXNG_METHOD` | `server.method` | POST/GET | POST | :222 |
| `SEARXNG_REDIS_URL` | `redis.url`(已弃用) | None/False/str | False | :227 |
| `SEARXNG_VALKEY_URL` | `valkey.url` | None/False/str | False | :230 |

关键点:
- **`search.formats` 没有环境变量**(:210 只有 `SettingsValue(list, OUTPUT_FORMATS)`,无 `environ_name`)→ 想开 JSON API 只能改 `settings.yml`,不能靠环境变量。
- **engines 启停没有环境变量** → 只能靠 `settings.yml`。
- bool 类型的环境变量走 `STR_TO_BOOL`(:38-45),接受 `0/false/off/1/true/on`(大小写不敏感,:98);传其他字符串会 `KeyError` → 被 `apply_schema` 捕获记为错误(:98、:168-171)。
- 环境变量的值永远是**字符串**;对 int 类型(如 `SEARXNG_PORT`)不会自动转 int——`server.port` 的类型定义是 `(int, str)`(:214),所以字符串能过校验;但纯 int 类型的键(如果有绑环境变量的)要小心。这一点 P1 用不到,记录备查。

---

### 问题 4:`server.secret_key` / `base_url` / `bind_address` 等部署必填项

| 键 | 默认值 | 是否必须改 | 说明与出处 |
|---|---|---|---|
| `server.secret_key` | schema 无默认(:218);出厂 yml 里是占位符 `"ultrasecretkey"`(settings.yml:105) | **必须改** | schema 里 `SettingsValue(str, environ_name='SEARXNG_SECRET')` 没给 `default`,意味着若既没在 yml 写、也没设 `SEARXNG_SECRET`,值为 `None`,类型校验 `str` 失败 → 启动报错。占位符 `ultrasecretkey` 是不安全的公开值,生产必须换。可用 `SEARXNG_SECRET` 环境变量注入。 |
| `server.base_url` | False(:219);出厂 yml `false`(settings.yml:94) | 建议设 | 实例对外 URL,影响生成的绝对链接。反代/对外暴露时应设为真实 URL。P1 若只内网 `gateway→searxng`,可留 false。|
| `server.bind_address` | 127.0.0.1(:215) | **容器里必须改** | 默认只绑本机回环。Docker 里 SearXNG 要被网关容器访问,需绑 `0.0.0.0`(通过 `SEARXNG_BIND_ADDRESS=0.0.0.0` 或 yml)。否则跨容器连不上。|
| `server.port` | 8888(:214) | 视情况 | 容器内监听端口。|
| `server.limiter` | False(:216) | 见问题 5 | 见下。|
| `server.public_instance` | False(:217) | 保持 False | 开启后启用只面向公开实例的特性,会更严格。P1 内部服务保持 False。|
| `server.method` | POST(:222) | 建议 GET 或无所谓 | 默认 POST。这是**表单/浏览器交互**的默认方法,不影响我们直接 POST/GET 打 `/search` API。|

> 官方容器 `settings.template.yml` 里唯一显式设的部署项就是 `secret_key`(占位)和 `image_proxy: true`。真正部署时官方镜像的 entrypoint 会用 `SEARXNG_SECRET` 等环境变量覆盖(见问题 3)。

---

### 问题 5:与 P1 直接相关的键清单 + 推荐最小配置草稿

#### 5.1 `search.formats` —— **P1 头号必改项**

- schema:`SettingsValue(list, OUTPUT_FORMATS)`,`OUTPUT_FORMATS = ['html', 'csv', 'json', 'rss']`(settings_defaults.py:23、:210)。
- 出厂 `settings.yml:85-86` 把它**收窄成只有 `[html]`**。
- 消费点(**关键**):`webapp.py:630-631`
  ```python
  if output_format not in settings['search']['formats']:
      flask.abort(403)
  ```
  也就是说 **如果 `json` 不在 `search.formats` 里,`POST /search?format=json` 直接返回 HTTP 403**。
- **P1 动作:必须在我们的 `settings.yml` 写 `search.formats: [json, html]`**(至少含 `json`)。

#### 5.2 engines 启停

- 每个 engine 有 `disabled` 字段,默认 False(enginelib/__init__.py:329-331;engines/__init__.py 默认表 `"disabled": False`)。`disabled: true` 表示"默认不用,但用户可手动激活"。
- 另有 `inactive`(:333-334):`inactive: true` 表示"从设置里彻底移除该 engine"。二者区别:disabled 仍可被显式请求,inactive 被剔除。
- **重要机制(webadapter.py:181-189)**:当请求显式带 `engines=名字1,名字2` 参数时,这些 engine 通过
  ```python
  EngineRef(engine_name, engines[engine_name].categories[0])
  ```
  直接加入查询列表,**绕过 `disabled_engines` 过滤**(`disabled_engines` 只在"按 category 展开"的分支 :197、:207 生效)。
  → 结论:**我们的网关只要在 `engines` 参数里显式点名,就能调用那些默认 `disabled: true` 的引擎**,不需要改 `settings.yml` 去逐个启用。这对 P1 的 `engines` 参数映射非常关键。
- 若某 engine 名字不在 `engines` 全局注册表里(拼错、被 remove/keep_only 剔除、或 inactive),`webadapter.py:185` 的 `if engine_name in engines` 会**静默跳过**它。P1 要注意:我们应自己校验请求的 engine 名,避免"用户点了名却被悄悄丢弃",这与 P1"禁止静默丢弃"的目标一致——但这是**请求侧**的丢弃,和 `meta.engines_failed`(执行侧失败)是两回事。

#### 5.3 `outgoing`(超时/连接池,影响 P1 的可靠性与 engines_failed)

schema:settings_defaults.py:249-268。P1 相关子键:
- `outgoing.request_timeout`(默认 3.0,:251;出厂 yml 3.0):**每引擎默认超时**。engine 自身可覆盖(见 engines 里各自的 `timeout`)。超时会转化为 engine 失败 → 应体现在 `meta.engines_failed`。
- `outgoing.max_request_timeout`(默认 None,:254):所有 engine 超时的上限封顶。
- `outgoing.pool_connections`(100,:255)、`outgoing.pool_maxsize`(schema 默认 10 :256,出厂 yml 20 :189):连接池。高并发时可能要调。
- `outgoing.enable_http2`(True,:252)、`outgoing.retries`(0,:261)、`outgoing.proxies`、`outgoing.using_tor_proxy`。P1 初期用默认即可。

#### 5.4 `categories_as_tabs`

- schema:`SettingsValue(dict, CATEGORIES_AS_TABS)`(:270),默认含 general/images/videos/news/map/music/it/science/files/social media(:26-37)。
- 合并模式下若用户提供会整体覆盖(settings_loader.py:138-140)。
- 作用主要是 **UI 的 tab 展示**。对 P1(直接打 JSON API,传 `categories` 参数)**基本无影响**——`categories` 参数的合法性校验看的是 `searx.engines.categories` 全局注册表(webadapter.py:118、:124),不是 `categories_as_tabs`。P1 **不需要动这个键**。

#### 5.5 `server.limiter`(P1 需要显式关掉)

- schema:`SettingsValue(bool, False, 'SEARXNG_LIMITER')`(:216),默认 False。
- 作用:对实例做请求限流、拦一些 bot。**限流依赖 valkey/redis**(见 `searx/limiter.py`,本子系统范围外)。
- **P1 建议保持 False**(或用 `SEARXNG_LIMITER=false`):我们的网关是唯一的、可信的内部调用方,开 limiter 只会误伤自己、还引入 valkey 依赖。若将来要防护,应该在**我们自己的 FastAPI 网关**层做限流,而不是让被代理的 SearXNG 限流。

#### 5.6 其他 P1 请求参数的合法值(来自 webadapter.py,供网关做入参校验)

- `time_range`:只接受 `day/week/month/year`(或空/None),否则抛 `SearxParameterException`(webadapter.py:95-101)。
- `safesearch`:必须是数字,范围由 `search.safe_search` 的 schema `(0,1,2)` 约束(settings_defaults.py:194;webadapter.py:79-84)。
- `categories`:逗号分隔,每个值必须在 `searx.engines.categories` 里(webadapter.py:117-119)。
- `language`:值域是 `SXNG_LOCALE_TAGS`(`all`/`auto` + `sxng_locales`,settings_defaults.py:24、:198-199)。
- `engines`:逗号分隔的 engine name(webadapter.py:181-189)。

> 这些校验规则属于 API 层,详见 01-api-surface / 02-search-orchestration 笔记;这里从"配置驱动了哪些值域"的角度记录。

#### 5.7 推荐的最小 `settings.yml` 草稿(挂到容器 `/etc/searxng/settings.yml`)

```yaml
# hollow AI Research Browser —— SearXNG 网关用最小配置
# 部署方式:Docker 独立部署原版 SearXNG,本文件挂到容器 /etc/searxng/settings.yml
# 合并模式:继承上游全部默认 engine 定义,只覆盖下面这几项

use_default_settings: true

server:
  # 必须改:占位符不安全。生产用环境变量 SEARXNG_SECRET 注入更好。
  secret_key: "CHANGE_ME_OR_SET_SEARXNG_SECRET"
  # 容器内被网关容器访问,必须绑 0.0.0.0(也可用 SEARXNG_BIND_ADDRESS=0.0.0.0)
  bind_address: "0.0.0.0"
  port: 8888
  # 内部可信调用方,关闭限流(避免误伤 + 免 valkey 依赖)
  limiter: false
  public_instance: false
  # 内网直连,不需要对外绝对链接
  base_url: false

search:
  # ★P1 头号必改:开 JSON API,否则 format=json 返回 403
  formats:
    - json
    - html
  # 安全搜索默认值(0=off / 1=moderate / 2=strict),按需
  safe_search: 0

outgoing:
  # 每引擎默认超时;失败/超时会体现在 meta.engines_failed
  request_timeout: 5.0
  max_request_timeout: 10.0
```

说明:
- **不列 `engines:`**。合并模式下继承上游默认引擎;要调用默认 `disabled: true` 的引擎,由网关在请求的 `engines=` 参数里显式点名即可(见 5.2)。
- 若后续要收敛引擎白名单,追加:
  ```yaml
  use_default_settings:
    engines:
      keep_only: [google, duckduckgo, bing, brave, wikipedia, ...]
  ```
  注意此时 `use_default_settings` 从 `true` 变成 dict,合并逻辑仍走"合并模式"(is_use_default_settings 对 dict 返回 True,:187-188)。
- `secret_key` 生产环境用 `SEARXNG_SECRET` 环境变量注入更干净(不落盘明文)。

---

## 四、对 P1 的影响与行动建议

1. **必做**:我们的 `settings.yml` 里 `search.formats` 必须包含 `json`,否则 SearXNG 的 `/search?format=json` 全部 403(webapp.py:630-631)。这是 P1 能跑通的前置条件。
2. **必做**:`server.bind_address` 在容器里设为 `0.0.0.0`(或 `SEARXNG_BIND_ADDRESS=0.0.0.0`),否则网关容器连不上。
3. **必做**:`server.secret_key` 换掉占位符,推荐用 `SEARXNG_SECRET` 注入。
4. **建议**:`server.limiter: false`,限流放到我们自己的网关层做。
5. **架构确认**:采用合并模式 `use_default_settings: true`,继承上游引擎定义。P1 的 `engines` 参数直接透传给 SearXNG 的 `engines=` 表单字段即可调用任意(含默认禁用的)引擎——因为显式点名会绕过 disabled 过滤(webadapter.py:181-189)。
6. **入参校验对齐**:网关侧对 `time_range`(day/week/month/year)、`safesearch`(0/1/2)、`categories`、`language`、`engines` 做白名单校验,提前拒绝非法值,避免打到 SearXNG 才报 `SearxParameterException` 或被静默跳过(webadapter.py:95-101、:117-119、:181-189)。
7. **engines_failed 的两类"丢弃"要分清**:
   - **请求侧**:`engines=` 里点了一个不存在/被剔除的引擎名 → webadapter.py:185 静默跳过。网关应自己校验并回报"未知引擎"。
   - **执行侧**:引擎超时/报错 → 属于 SearXNG 运行期失败,应从 result_container 的失败信息(见 04-results-fusion 笔记的 `unresponsive_engines`)映射到 `meta.engines_failed`。配置层的 `outgoing.request_timeout` 决定何时判超时。
8. **不需要动的键**:`categories_as_tabs`(纯 UI)、`server.method`(表单默认方法,不影响我们直接指定 format 的 API 调用)、`plugins`(默认即可)。

---

## 五、待实测确认的问题

1. `apply_schema` / `SETTINGS` 的实际调用点在 `searx/__init__.py`(本子系统未逐行读),需确认"环境变量覆盖发生在 `update_settings` 合并之后",即环境变量优先级最高。从代码结构推断如此(load → merge → apply_schema 才做 environ 覆盖),但未跑通验证。
2. `search.formats` 里同时留 `html` 是否会带来副作用(如把内部实例暴露成可浏览页面)?若我们完全不需要 HTML,能否只写 `[json]`?需实测 `format=json` 在 `formats: [json]` 下是否正常(理论上可以,因为 :630 只检查 json 是否在列表里)。
3. 官方容器镜像的 entrypoint 具体如何注入 `SEARXNG_SECRET` / `SEARXNG_BIND_ADDRESS`,以及是否强制要求某些环境变量——需读 `container/` 下的启动脚本(本子系统范围外)确认与我们 docker-compose 的对接方式。
4. `disabled: true` 的引擎被显式 `engines=` 请求时,是否还有其他隐藏门槛(如 `tokens` 私有引擎、`inactive`)会导致仍被跳过?需结合 03-engines 笔记与实测确认。
5. `server.limiter: false` 时是否完全不加载 valkey?若开 limiter 是否强依赖 `valkey.url`?需读 `searx/limiter.py` 确认(本子系统范围外)。
6. bool 型 `SEARXNG_*` 环境变量传非法字符串(如 `SEARXNG_LIMITER=yes`)会因 `STR_TO_BOOL['yes']` KeyError 而被记为配置错误——是否导致整个实例启动失败?需实测(推断:apply_schema 收集错误后顶层抛 `ValueError("Invalid settings.yml")`,:177-178,会启动失败)。
