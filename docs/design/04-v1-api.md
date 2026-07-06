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
| `top_n` | 5(1~20) | 最大抓取条数(最大找出量) |
| `timeout` | 15(≤60) | **单 URL** 抓取超时(秒) |
| `budget` | 不设(≤300) | **整单**时间预算(秒,从收到请求起算,**含搜索阶段**);抓取侧到点未完成的显式标 `timeout`;搜索侧被预算切断时每个请求引擎都记入 `engines_failed`(reason 带 budget exceeded)、items 为空——两侧都不静默超支,不变量不破 |
| `concurrency` | 5(1~8) | 并行抓取数(进程级另有全局闸 16) |
| `max_content_chars` | 不截断(≥100) | 单条净化正文截断上限;`word_count` 保留全文长度 |
| `escalate` | true | **三档升级链**(2026-07-07 拍板):static 档 blocked/failed 时自动升级 dynamic(chromium)、仍失败再升 stealthy(patchright 反检测);timeout 不升级。任一档成功即用其结果,`engine_used` 标最终档位;走完仍失败时 error 保留完整升级历史。浏览器档进程级闸 2 + 每请求闸 2。**本版不主动解 Cloudflare**(vendor 的 solve_cloudflare 是不可中断的无上限循环,会致线程泄漏——审查确认;碰 CF 墙即返回 blocked,如实上报) |
| `purify` | true | trafilatura 净化,失败回退 raw HTML |
| `stream` | false | SSE 语义事件流 |

非流式响应:`{"id":"res_…","object":"research","created":…,"query","scenes","engines",
"items":[{"object":"research.item",…}],"search":{…},"fetch":{…}}`。
不变量:`len(items) == fetch.requested == ok+failed+timeout+blocked`(预算耗尽路径同样成立)。

### POST /v1/research + `"stream": true`(SSE)

```
data: {"object":"research.event","event":"research.search.completed","id":"res_…","search":{…},"selected":5}
data: {"object":"research.event","event":"research.item.completed","id":"res_…","index":2,"item":{…含 content}}
data: …(每条来源完成即推,完成顺序 ≠ 选取顺位,靠 index 对位;预算耗尽的占位条目也走此事件)
data: {"object":"research.event","event":"research.completed","id":"res_…","research":{汇总,items 不重复携带 content}}
data: [DONE]
```

搜索阶段错误发生在开流前,走标准 HTTP 错误;开流后一切失败以占位 item 呈现,不断流。
客户端断连时服务端取消未完成任务。

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
| 401 | `invalid_api_key` | HOLLOW_API_KEY 已设置且 Bearer 不匹配 |
| 502 | `upstream_unavailable` | SearXNG 不可达/非 200/非 JSON |

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
