# SearXNG 实测验证记录 · 第一轮(云端沙箱,无 Docker)

> 日期:2026-07-05
> 环境:云端容器,Python 3.11.15,出站流量经 HTTPS 代理(数据中心 IP)
> 部署方式:**本地 Python 进程直跑 vendor 源码**(`SEARXNG_SETTINGS_PATH=searxng/settings.yml PYTHONPATH=vendor/searxng python -m searx.webapp`),零 Docker——验证了"不依赖 Docker"方向可行
> 配置:[`searxng/settings.yml`](../../searxng/settings.yml)(06 号笔记推荐草稿)
> 本文逐条对账 [`00-overview.md`](./00-overview.md) §6 的待实测问题。

## 一、部署本身

| 验证点 | 结果 |
|---|---|
| 源码直跑(非 Docker) | ✅ `python -m searx.webapp` 起服务,`/healthz` 返回 200 |
| 依赖安装 | ✅ `requirements.txt` 19 个依赖 venv 内顺利安装 |
| 最小 settings.yml | ✅ 06 号笔记草稿一次跑通 |
| 启动期引擎注册失败 | ⚠️ 仅 `ahmia`/`torch`(暗网源,已在 L1 踢出名单),无关紧要 |
| limiter.toml 缺失 | ⚠️ 仅 WARNING,limiter 关闭时不影响 |

## 二、契约/序列化(逐条关闭待实测问题)

| 00-overview §6 待实测项 | 实测结果 |
|---|---|
| `format=json` 需开 formats | ✅ 开 `formats:[json,html]` 后返回 7 键 JSON |
| JSON 顶层键 | ✅ 确认恰好 7 键:query/results/answers/corrections/infoboxes/suggestions/unresponsive_engines |
| `unresponsive_engines` 序列化形态 | ✅ 二维数组 `[["arxiv","timeout"]]` |
| `suspended` 如何体现 | ✅ 文案前缀 `"Suspended: too many requests"`(非独立布尔) |
| `parsed_url` 形态 | ✅ 6 元素数组 `["http","arxiv.org","/abs/...","","",""]`,不是对象 |
| `score`/`positions` 是否在 JSON | ✅ 均在;`score` 可直接排序 |
| 结果富字段 | ✅ arxiv 走 `paper.html` 模板,带 doi/authors/pdf_url/tags/journal 等 |
| `publishedDate` 格式 | ✅ ISO8601 字符串 `2022-11-04T10:14:47` |
| answerer 短路 | ✅ `avg 1 2 3` → answers 有值、results=0、unresponsive=0;answer 的 `engine` 字段是 `answerer: avg` |
| 外部 bang 劫持 json | ✅ `!!g test` + `format=json` → **HTTP 302 跳转 google**(网关必须做 bang 防护) |
| count 不可控 | ✅ ddg `pageno=1`→10 条、`pageno=2`→15 条,每页条数不固定 |
| 强制超时 | ✅ `timeout_limit=0.05` → `unresponsive:[["arxiv","timeout"]]`、results=0 |

## 三、引擎真实可用性(数据中心 IP,本轮关键数据)

> ⚠️ 这些结果**与出口 IP 强相关**。本环境是云端数据中心 IP,住宅 IP 结果会不同。

| 引擎 | 结果 | error_type | 归属 |
|---|---|---|---|
| wikipedia | ✅ 好 | — | 官方 API 压舱石 |
| arxiv | ✅ 好(富字段) | — | 官方 API 压舱石 |
| **duckduckgo** | ✅ **好(10 条)** | — | 反爬最重但本轮通过 → "可用需限速"判断成立 |
| **baidu** | ✅ **好(8 条)** | — | 上游默认关,实测可用 |
| **sogou** | ✅ **好(9 条)** | — | 上游默认关,实测可用 |
| **bilibili** | ✅ **好(20 条)** | — | 上游默认关,实测可用 |
| bing | ✅ 好(10 条) | — | |
| brave | ❌ 封 | `too many requests` | 数据中心 IP 被限流 |
| startpage | ❌ 封 | `access denied` | IP 声誉 |
| mojeek | ❌ 封 | `access denied` | IP 声誉 |
| quark | ❌ 挂 | `HTTP error` | 中文源里唯一失败 |

## 四、对设计的直接影响

1. **中文场景担忧被证伪**:baidu/sogou/bilibili 实测全通(裁剪分析 §5 遗留点 2 关闭)。中文默认集可放心纳入这三个;quark 剔除。
2. **通用默认集需按 IP 环境重配**:草案主力 brave/startpage/mojeek 在数据中心 IP 全部被封。纯数据中心部署下,更靠谱的通用集是 **duckduckgo + bing + 中文系 + wikipedia/arxiv**;brave/startpage 依赖住宅 IP 或代理才稳。
3. **"禁止静默丢弃"底线实测成立**:所有被封引擎都如实出现在 `unresponsive_engines`,error_type 可解析(too many requests / access denied / HTTP error / timeout)。
4. **DDG 进默认集**(裁剪分析 §5 遗留点 1 关闭):本轮通过,按"进 + 单独限速"落地。

## 五、仍未覆盖(留待后续)

- 住宅 IP / 代理下 brave/startpage/mojeek 的真实可用性;
- 长时间高频调用下 DDG 的封禁触发阈值(vqd 机制、IP 级封禁窗口);
- Cloudflare 站点触发 `SearxEngineCaptchaException` 的实际形态(本轮未命中 CF 站);
- 非 en locale 下错误文案变化(本轮固定 `ui.default_locale: en`,未测其他语言)。
