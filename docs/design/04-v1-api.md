# /v1 正式 API 面 —— 参考 OpenAI 的格式与调用方式(不仿制其端点)

> 状态:已落地(2026-07-06,两轮拍板)。实现:`api/v1.py`(+ `api/registry.py` 读注册表)。
> 用户原话:"参考他的格式和调用方式,没让你照抄" —— 借鉴 OpenAI 的 API 设计惯例,
> 域模型仍是 hollow 自己的。`/v0/research` 保留(design/03 契约,共享编排器),`/v1` 是对外正式面。
> 二轮拍板(AskUserQuestion):**搜索与研究拆两个端点**(不做 fetch 开关);场景多选取并集、
> 自定义引擎并入;单 URL 超时与整单预算**分开做**;top_n 上限放宽到 20;
> 新增 knowledge/social 场景(注册表 defaults 42→50);zh 短期保留场景、中期升级 region 修饰符。

## 一、借鉴了什么(格式与调用方式)

| OpenAI 惯例 | hollow 落法 |
|---|---|
| 对象封套 | `id: "res_/srch_<hex>"`、`object: "research"/"search"`、`created`(unix 秒);条目带 `object: "research.item"/"search.result"` |
| 列表形状 | `GET /v1/engines`、`GET /v1/scenes` 统一 `{"object":"list","data":[...]}` |
| 错误形状 | `{"error": {message, type, param, code}}` + 正确的 HTTP 状态码(含全局校验错误 handler,无裸 422) |
| Bearer 鉴权 | `HOLLOW_API_KEY` 设置时校验(仅 /v1/*;/v0、/healthz 是内部面) |
| `stream: true` → SSE | **语义化事件流**(不是 token delta),每条来源完成即推送 |
| 按能力拆资源 | completions/embeddings 之于 OpenAI ≈ search/research 之于 hollow |
| usage 账目 | `search` + `fetch` 双侧账目(召回/引擎失败/ok/failed/timeout/blocked/耗时) |

## 二、端点

### POST /v1/search —— 纯搜索召回(秒级,不抓全文)

```jsonc
{
  "query": "锂电池回收技术",       // 必填
  "scenes": ["zh", "news"],       // 可选,多选,引擎取并集
  "engines": ["arxiv"],           // 可选,自定义点名,并入场景集
  "language": "auto", "time_range": null, "safesearch": 0
}
```

响应:`{"id":"srch_…","object":"search","created":…,"query":…,"scenes":…,"engines":[实际引擎集],
"results":[{"object":"search.result","url","title","engine","score","snippet"}…],"search":{账目}}`

### POST /v1/research —— 搜索 + 并行抓取 + 净化

搜索侧参数与 /v1/search 相同,另有抓取五件套 + stream:

| 参数 | 默认 | 说明 |
|---|---|---|
| `top_n` | 5(1~20) | 想要的**成功正文条数**(target ok);fast 模式凑够即停 |
| `mode` | `balanced` | **一根旋钮 速度/广度↔质量/难度**(2026-07-07 拍板,详 docs/design/05):`fast` 超召回(池 top_n×3)+凑够即砍+不升级+8s;`balanced` 默认(= 引入前行为);`thorough` 死磕每条+升级全开+30s。是预设,`escalate`/`timeout` 显式传值仍覆盖 |
| `budget` | 不设(≤300) | **整单**时间预算(秒,含搜索阶段);到点即停,搜索侧被切每个引擎记入 `engines_failed`,抓取侧未完成的计入 `cancelled`——两侧都不静默超支 |
| `concurrency` | 5(1~8) | 并行抓取数(进程级另有全局闸 16),与候选池独立 |
| `max_content_chars` | 不截断(≥100) | 单条净化正文截断上限;`word_count` 保留全文长度 |
| `timeout` | 跟随 mode | **单 URL** 抓取超时(秒);缺省跟随 mode 预设,显式传值覆盖 |
| `escalate` | 跟随 mode | **三档升级链**:static blocked/failed 时升 dynamic(chromium)、仍失败升 stealthy(patchright 反检测);timeout 不升级。`engine_used` 标最终档位,走完仍失败 error 留完整升级历史。浏览器档进程级闸 2 + 每请求闸 2。**本版不主动解 Cloudflare**(vendor solver 是不可中断无上限循环,会致线程泄漏——审查确认;碰 CF 墙返回 blocked) |
| `purify` | true | trafilatura 净化,失败回退 raw HTML |
| `stream` | false | SSE 语义事件流 |

非流式响应:`{"id":"res_…","object":"research","created":…,"query","scenes","engines",
"items":[{"object":"research.item",…}],"search":{…},"fetch":{…}}`。
`fetch` 账目:`{target, pool, requested, ok, failed, timeout, blocked, cancelled, stopped_reason, took_ms}`。
不变量:`requested == len(items) == ok+failed+timeout+blocked+no_content`;`pool == requested + cancelled`;`ok <= target`。
`stopped_reason` ∈ {`target_reached`, `pool_exhausted`, `budget`},被砍候选显式计入 `cancelled`,禁止静默丢弃。

> **相关性重排(2026-07-14,搜索质量批)**:搜索与抓取之间插了一层零成本词汇重排(api/rerank.py):按 (query,title,snippet) 相关性排候选(标题命中≫正文,SearXNG 原分只当兜底 prior),纯噪声(标题零命中+snippet 空,如撤稿空壳)不进抓取池。每条 research.item 带 `relevance`(重排分)与 `rank`(最终位次);最终 items 先 ok 后其它、组内按 relevance 降序,故 `items[0]` 是最相关的可读结果。`/v1/search` 结果也按 relevance 排序并带该字段。`score` 仍是 SearXNG 原分(可溯源)。

### POST /v1/research + `"stream": true`(SSE)

```
data: {"object":"research.event","event":"research.search.completed","id":"res_…","search":{…},"selected":5}
data: {"object":"research.event","event":"research.item.completed","id":"res_…","index":2,"item":{…含 content}}
data: …(每条来源完成即推,完成顺序 ≠ 选取顺位,靠 index 对位)
data: {"object":"research.event","event":"research.completed","id":"res_…","research":{汇总,含 fetch 账目;items 不重复携带 content}}
data: [DONE]
```

搜索阶段错误发生在开流前,走标准 HTTP 错误;开流后每条抓完即 item 事件;
够了/预算到的候选不产 item,计入 completed 的 `cancelled`。客户端断连时服务端取消未完成任务。

### POST /v1/fetch —— 按 URL 直取:抓取 + 净化,不经搜索(2026-07-14)

审计"最高投入产出比"项 + vonish 集成需要(典型两跳:`/v1/search` 拿 URL → 客户端挑选 → `/v1/fetch` 取正文)。
复用 research 的全套抓取设施:三档升级链、内容闸门、SSRF netguard、20MB 大小上限。

```jsonc
{
  "urls": "https://example.com/a",   // 或数组(≤10,HOLLOW_FETCH_URLS_MAX);借 OpenAI embeddings input 惯例
  "mode": "balanced",                // 只取 escalate/timeout 预设(直取无候选池,fast 的超召回不适用)
  "timeout": null, "escalate": null, // 显式传值覆盖 mode 预设(同 research)
  "purify": true, "max_content_chars": null, "concurrency": 5
}
```

响应:`{"id":"ftch_…","object":"fetch","created":…,"items":[{"object":"fetch.item",…}],"fetch":{账目}}`。

- `items[]` **按输入顺序**返回(用户点名的顺序即意图顺序,无 relevance/rank 概念);每条
  `url` 是点名的原始 URL,重定向后落点 ≠ 输入时另给 `final_url`(可溯源);其余字段同 research.item
  (fetch_status / engine_used / http_status / word_count / purified / content / error / fetched_at)。
- 账目:`{submitted, requested, deduped, ok, failed, timeout, blocked, no_content, took_ms}`。
  不变量:`requested == len(items) == ok+failed+timeout+blocked+no_content`;`submitted == requested + deduped`
  (重复 URL 去重保序,移除数显式入账,不静默消失)。
- 错误分界:**结构性错误**(非 http/https 绝对 URL → 400 `invalid_url` 指明第几条;超上限 → 400
  `invalid_parameter`)是客户端 bug,拒整单;**运行时拦截**(netguard 内网目标 → `blocked`,抓取失败
  → `failed`)进 item 显式状态,与 research 一致(底线②)。
- **不挡** PDF 等扩展名(research 的候选跳过是自动选择;这里是用户显式点名,尊重意图,抓不出正文
  就如实 `no_content`;PDF 专用通道记待办)。
- 本版无 `budget`(≤10 条且单 URL timeout 已兜底;最坏时长 ≈ ceil(10/concurrency)×timeout×档数)、
  无 `stream`(vonish 场景不需要);都记待办,传了会进 `ignored_params` 如实回报。

### GET /v1/engines · GET /v1/scenes

注册表直出:`?status=pool|default|removed`、`?scene=zh`、`?type=web`、`?tier=T0` 过滤;
scenes 现有 9 个:general / knowledge / dev / academic / news / social / images / av / zh。

## 三、引擎选取语义

最终引擎集 = **∪(各场景引擎) ∪ 自定义引擎**,去重保序;都不传用网关默认集(拍板的国际+国内 6 源)。
点名校验:L1 removed → 400 `engine_removed`;不在注册表 → 400 `unknown_engine`
(SearXNG 对无效 engines 会静默回退默认集——实测踩过,网关层必须挡住)。

## 四、错误码表

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_parameter` | 请求体校验失败(全局 handler 统一封套,不走 FastAPI 默认 422) |
| 400 | `invalid_query` | query 经 bang/filter 清洗后为空 |
| 400 | `engine_removed` / `unknown_engine` | 点名了 L1 源 / 注册表外的名字 |
| 400 | `unknown_scene` | scenes 里有注册表外的场景 |
| 400 | `invalid_search_param` | SearXNG 判定透传参数非法(如 `time_range`/`language` 取值错);此前误报 502 |
| 400 | `invalid_url` | /v1/fetch 点名了非 http(s) 绝对 URL(消息指明第几条) |
| 401 | `invalid_api_key` | HOLLOW_API_KEY 已设置且 Bearer 不匹配 |
| 502 | `upstream_unavailable` | SearXNG 不可达/5xx/非 JSON(真上游故障) |

**两个响应级字段(禁止静默丢弃 · 底线②):**
- `answers`:SearXNG 的 infobox/answer 归一后透出(`[{object:"answer",type:"infobox"|"answer",title,content,url,img_src,engine}]`)。一个只产出 infobox 的查询(如 wikipedia+"Python")不再是 `results:[]` 的假失败。/v1/search 与 /v1/research 均有。
- `ignored_params`:请求里未被识别的字段名(拼错的 `engine`、别家产品的 `exclude_domains` 等)。`extra="allow"` 收进后如实回报,不静默吞掉客户端的意图。仅在非空时出现。

## 五、用法示例(httpx)

```python
import httpx, json

# 纯召回:秒级拿 URL 清单
r = httpx.post("http://127.0.0.1:8080/v1/search",
               json={"query": "锂电池回收技术", "scenes": ["zh", "news"]}, timeout=30)
print([x["url"] for x in r.json()["results"]])

# 研究:多场景并集 + 整单预算 + 流式
with httpx.stream("POST", "http://127.0.0.1:8080/v1/research",
                  json={"query": "quantum error correction", "scenes": ["academic", "dev"],
                        "top_n": 10, "budget": 30, "stream": True}, timeout=60) as resp:
    for line in resp.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            ev = json.loads(line[6:])
            print(ev["event"])
```
