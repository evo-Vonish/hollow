# /v1 正式 API 面 —— 参考 OpenAI 的格式与调用方式(不仿制其端点)

> 状态:已落地(2026-07-06)。实现:`api/v1.py`(+ `api/registry.py` 读注册表)。
> 用户原话:"参考他的格式和调用方式,没让你照抄" —— 借鉴 OpenAI 的 API 设计惯例,
> 域模型仍是 hollow 自己的(query/scene/engines/items/search/fetch)。
> `/v0/research` 保留(design/03 契约,共享同一编排器),`/v1` 是对外的正式面。

## 一、借鉴了什么(格式与调用方式)

| OpenAI 惯例 | hollow 落法 |
|---|---|
| 对象封套 | 响应带 `id: "res_<hex>"`、`object: "research"`、`created`(unix 秒);条目带 `object: "research.item"` |
| 列表形状 | `GET /v1/engines`、`GET /v1/scenes` 统一 `{"object":"list","data":[...]}` |
| 错误形状 | `{"error": {message, type, param, code}}` + 正确的 HTTP 状态码 |
| Bearer 鉴权 | `HOLLOW_API_KEY` 设置时校验 `Authorization: Bearer`(仅 /v1/*;/v0、/healthz 是内部面) |
| `stream: true` → SSE | **语义化事件流**(不是 token delta):每条来源抓完净化完立即推送 |
| usage 账目 | 我们的 "usage" 就是 `search` + `fetch` 双侧账目(召回/引擎失败/ok/failed/timeout/blocked/耗时) |

## 二、端点

### POST /v1/research

```jsonc
{
  "query": "锂电池回收技术",     // 必填
  "scene": "zh",                 // 可选,注册表 meta.scenes 场景
  "engines": ["arxiv"],          // 可选,点名(优先于 scene);拒 L1 removed 与未知名
  "top_n": 5,                    // 1~8
  "purify": true,
  "timeout": 15,                 // 每 URL 秒
  "language": "auto", "time_range": null, "safesearch": 0,
  "stream": false
}
```

引擎解析优先级:`engines` > `scene` > 网关默认集(拍板的国际+国内 6 源)。
**点名校验**:L1 removed → 400 `engine_removed`;不在注册表 → 400 `unknown_engine`
(SearXNG 对无效 engines 会静默回退默认集——实测踩过,网关层必须挡住)。

非流式响应(整体形状;`search`/`fetch` 字段与 design/03 §4 的 meta 完全一致,不变量不变):

```jsonc
{
  "id": "res_9f2c…", "object": "research", "created": 1783270000,
  "query": "…", "scene": "zh", "engines": ["baidu","quark","sogou","bilibili"],
  "items": [ { "object": "research.item", "url": …, "fetch_status": "ok", "content": "…", … } ],
  "search": { "engines_requested": […], "engines_used": […], "engines_failed": […], "results_total": 38, "took_ms": 3641, "q_sanitized": false },
  "fetch":  { "requested": 5, "ok": 4, "failed": 1, "timeout": 0, "blocked": 0, "took_ms": 1585 }
}
```

### POST /v1/research + `"stream": true`(SSE)

```
data: {"object":"research.event","event":"research.search.completed","id":"res_…","search":{…},"selected":5}
data: {"object":"research.event","event":"research.item.completed","id":"res_…","index":2,"item":{…含 content}}
data: …(每条来源完成即推,完成顺序 ≠ 选取顺位,靠 index 对位)
data: {"object":"research.event","event":"research.completed","id":"res_…","research":{汇总对象,items 不重复携带 content}}
data: [DONE]
```

- 搜索阶段的错误发生在开流之前,仍走标准 HTTP 错误(客户端好处理);
  开流后的一切失败以占位 item 事件呈现(fetch_status/error),**不断流**。
- 客户端断连时服务端取消未完成的抓取任务(编排器 finally 收尾)。

### GET /v1/engines

343 源注册表直出,支持 `?status=pool|default|removed`、`?scene=zh`、`?type=web`、`?tier=T0` 过滤;
条目带 tier/type/status(removed 带 removed_reason,default 带 scenes,有账必露)。

### GET /v1/scenes

`{"object":"list","data":[{"id":"zh","object":"scene","engines":[…]},…]}`

## 三、错误码表

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_parameter` | 请求体校验失败(pydantic;全局 handler 统一转封套,不走 FastAPI 默认 422) |
| 400 | `invalid_query` | q 经 bang/filter 清洗后为空 |
| 400 | `engine_removed` / `unknown_engine` | 点名了 L1 源 / 注册表外的名字 |
| 400 | `unknown_scene` | scene 不在注册表 |
| 401 | `invalid_api_key` | HOLLOW_API_KEY 已设置且 Bearer 不匹配 |
| 502 | `upstream_unavailable` | SearXNG 不可达/非 200/非 JSON |

## 四、用法示例(httpx)

```python
import httpx, json

# 非流式
r = httpx.post("http://127.0.0.1:8080/v1/research",
               json={"query": "锂电池回收技术", "scene": "zh", "top_n": 3},
               timeout=120)
pack = r.json()
print(pack["id"], pack["fetch"], [i["url"] for i in pack["items"]])

# 流式:逐条来源到达即处理
with httpx.stream("POST", "http://127.0.0.1:8080/v1/research",
                  json={"query": "quantum error correction", "stream": True},
                  timeout=120) as resp:
    for line in resp.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            ev = json.loads(line[6:])
            print(ev["event"])
```
