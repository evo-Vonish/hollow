# SearXNG 源码研读笔记 · 目录索引

> 对象:`vendor/searxng/` @ commit `a643858`(未修改)。
> 目的:为 AI Research Browser 的 FastAPI 网关 `POST /v0/search` 提供"如何正确调用 SearXNG JSON API"的完整契约。
> 架构约束:原版 SearXNG 用官方 Docker 镜像独立部署,一行源码不改;网关只调 `/search?format=json`。

## 阅读顺序

**先读 [`00-overview.md`](./00-overview.md)** —— 综合总览:整体架构图与一次 `format=json` 请求的端到端生命周期、`/v0/search` 契约字段映射表(参数/返回/`engines_failed`↔`unresponsive_engines`)、P1 部署与对接行动清单、风险坑点清单(按严重度)、跨笔记矛盾的源码裁决、待实测问题汇总。

## 子系统笔记(每份一行简介)

| # | 文件 | 子系统 | 一句话内容 |
|---|---|---|---|
| 00 | [`00-overview.md`](./00-overview.md) | 综合总览 | 架构+生命周期+契约映射+行动清单+风险+裁决+待实测,10 份笔记的入口。 |
| 01 | [`01-api-surface.md`](./01-api-surface.md) | HTTP API 表面 | `/search`(webapp.py:616)的请求参数、form-urlencoded 调用、`format=json` 的 403 闸门与 7 键返回体、bang 修饰符如何架空参数。 |
| 02 | [`02-search-orchestration.md`](./02-search-orchestration.md) | 搜索编排核心 | `SearchQuery`/`Search` 如何扇出到多引擎、裸线程全并发、`actual_timeout` 计算、失败三元组 `unresponsive_engines` 记账。 |
| 03 | [`03-engines.md`](./03-engines.md) | 引擎系统 | 引擎加载/注册、单引擎适配器(request/response)、traits 语言地区映射、四个候选引擎(wikipedia/arxiv/brave/ddg)反爬特点。 |
| 04 | [`04-results-fusion.md`](./04-results-fusion.md) | 结果容器与融合排序 | `ResultContainer` 去重合并(hash 规则)、`calculate_score` 打分、`engine`/`engines` 来源字段、`unresponsive_engines` 的 JSON 降级。 |
| 05 | [`05-network.md`](./05-network.md) | 出站网络层 | 每引擎独立 httpx 连接池、outgoing 配置项、重试、`raise_for_httperror` 把 429/403/CF/reCAPTCHA 语义化、suspend 联动。 |
| 06 | [`06-settings.md`](./06-settings.md) | 配置系统 | 配置加载/合并(`use_default_settings`)、schema 校验、`SEARXNG_*` 环境变量白名单、最小 `settings.yml` 草稿。 |
| 07 | [`07-limiter-botdetection.md`](./07-limiter-botdetection.md) | 限流与机器人检测 | limiter 默认关;开启后对 `format=json` 的多处误杀(`API_MAX=4/小时`、Accept 须含 text/html);`pass_ip` 才是白名单。 |
| 08 | [`08-deployment.md`](./08-deployment.md) | 容器化部署 | 官方 compose 两服务(core+valkey)、Granian(非 uwsgi)、entrypoint、可砍 valkey 单容器、`/healthz` 探活。 |
| 09 | [`09-metrics-errors.md`](./09-metrics-errors.md) | 指标与错误记录 | 进程内计数/直方图、`errors_per_engines` 错误归类、`/stats/errors`(JSON)与 `/metrics`(需鉴权)、异常体系与停用时长。 |
| 10 | [`10-periphery.md`](./10-periphery.md) | 外围子系统速览 | plugins(tracker_url_remover 改 url)、answerers(命中短路引擎)、autocomplete/favicons(默认关且不进 json)、cache 不缓存结果。 |

## 关键结论速记(详见 00)

- **开 JSON**:`settings.yml` 必须 `search.formats: [json, html]`,否则 `403`。
- **调用**:`POST /search`,form-urlencoded,`format=json`+`q`,列表逗号 join。
- **`engines_failed`**:来自 `unresponsive_engines`,但已压平为 `[[engine, 翻译文案]]`——丢结构化 `error_type`/`suspended`;`display_error_messages:false` 会静默丢弃(默认 true);靠"请求集 − 结果集 − 失败集"差集兜底。
- **每条结果来源**:用 `results[].engines`(复数),不用 `engine`(单数)。
- **`count` 无对应**:网关侧截断/多页聚合。
- **bang 防护**:拦 `q` 里 `!` `:` `<` `!!` 前缀 token(`!!g` 会让 json 请求 302)。
- **limiter**:保持关闭,免 valkey,限流放网关层。
