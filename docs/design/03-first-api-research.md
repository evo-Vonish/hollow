# 第一个 API 研究:`/v0/research` —— q → SearXNG → 并行 Scrapling → 返回

> 目标:传入 `q` → 走 SearXNG 召回 → 取 top-N 结果 → **并行** Scrapling 抓取 → 返回带正文的结果。
> 这是把两个基建**首次串起来**的端到端端点(项目书里的 `/v0/research` 组合端点)。
> 本文是**动手前的研究结论**,所有可行性判断都经过本环境实测(见 §2)。
> 依据:`docs/research/searxng/`(11 篇)、`docs/research/scrapling/`(10 篇)。

---

## 1. 范围决策:MVP = 搜索 + 并行「静态」抓取

**先通水,再换粗管**(v2 核心原则)。第一个 API 只做最可靠的一条链路:

- ✅ **纳入 MVP**:SearXNG 搜索 → 取 top-N → 并行**静态** Fetcher 抓取 → trafilatura 净化(可选)→ 返回。
- ⏭ **推迟到第二版**:三档升级链的动态/隐身档(浏览器)。理由见 §2.3——浏览器档在当前沙箱有网络/版本适配问题,且静态档已覆盖研究场景绝大多数页面(arxiv/wikipedia/新闻/文档)。MVP 遇到静态抓不到的页面,**显式标记 `fetch_status` 让调用方看见**,不静默失败,符合底线。

一句话:**第一版把整条管路打通、每个环节可观测,再逐档加粗。**

---

## 2. 集成面可行性(本环境实测结论)

### 2.1 SearXNG 侧:HTTP 调用本地进程 ✅ 已跑通

- 部署:`SEARXNG_SETTINGS_PATH=searxng/settings.yml python -m searx.webapp`,`/healthz` 200。
- 调用:`POST /search`,form-urlencoded,`format=json`,固定浏览器风格请求头。
- 实测:`engines=wikipedia,arxiv` 返回 10 条结构化结果;`unresponsive_engines` 确认是 `[["engine","msg"]]`;强制超时返回 `[["arxiv","timeout"]]`;熔断返回 `"Suspended: too many requests"` 前缀。
- **架构选择:第一版用 HTTP 调本地 SearXNG 进程**(而非进程内 import)。理由:零 Flask-context 耦合、与已实测路径一致、风险最低。进程内库集成(方案 B)留作后续优化——它能拿到结构化的 `error_type`/`suspended`,但不是 MVP 必需。

### 2.2 Scrapling 静态档:可用,但沙箱要关 impersonate ⚠️ 已跑通

- 安装:`pip install "scrapling[fetchers]==0.4.10"` → 装好 `curl_cffi 0.15.0`。
- **关键实测发现**:`curl_cffi` 默认 `impersonate="chrome"`(模拟浏览器 TLS 指纹)——**本沙箱的策略代理会重置这种握手**(`curl (35) Connection reset`)。关掉 impersonate(`Fetcher.get(url, impersonate=None)`)则**过代理成功**(arxiv 200 / 42KB / 4652 字符可见正文)。
  - **沙箱 vs 生产的差异**:生产是 VPS 直连互联网,`impersonate="chrome"` 正是卖点(绕反爬),应保留;**只有在本沙箱**因走 egress 代理才需 `impersonate=None`。→ 用配置项 `HOLLOW_IMPERSONATE`(默认 chrome,沙箱设 None)区分,不写死。
- 代理与 CA:`CURL_CA_BUNDLE=/root/.ccr/ca-bundle.crt` + `HTTPS_PROXY` 环境变量,curl_cffi 自动遵循。

### 2.3 Scrapling 浏览器档:沙箱有两道坎(推迟)

- Chromium 预装在 `/opt/pw-browsers/chromium-1194/`,但 Scrapling 钉的 playwright 1.61.0 找 `chromium-1228`(**版本不匹配**)。可用 `executable_path` 指向预装的 1194 绕过——实测浏览器**能启动**。
- 但启动后**导航失败**:Chromium 没走 egress 代理(需 `--proxy-server=` + NSS 信任 CA)。这是沙箱网络适配问题,可解但非 MVP 必需。
- 结论:浏览器档**推迟**。生产 VPS 直连时这两坎都不存在(装匹配的 chromium + 直连网络)。

### 2.4 trafilatura(净化,P3 能力)

- MVP 可选:先返回 raw HTML(`resp.body`),或轻量接一个 trafilatura 抽正文。建议**第一版就接 trafilatura 基线**(单依赖、F-Score 0.909),让"研究结果"直接可读,而不是塞给调用方一坨 HTML。净化失败则回退 raw HTML,标记 `purified=false`。

---

## 3. 端到端编排流程

```
POST /v0/research { q, ... }
        │
        ▼
① 搜索:POST 本地 SearXNG /search?format=json（固定头）
        │   → results[] + meta.unresponsive_engines
        ▼
② 选取:从 results 里取 top-N（默认 5，≤8）
        │   · 去重（按 host+path）· 跳过明显非网页（pdf 可选）
        │   · 保留 engine 来源、score、title、url
        ▼
③ 并行抓取:asyncio.gather(N 个 静态 Fetcher.get)
        │   · asyncio.Semaphore 限并发（默认 5）
        │   · 每 URL asyncio.wait_for 硬超时（默认 15s）
        │   · 每 URL 独立 try：失败/超时原样占位，绝不拖垮整体
        ▼
④ 净化:trafilatura 抽正文（可选，失败回退 raw HTML）
        │   · word_count = 可见文本字符数
        ▼
⑤ 组装:Evidence Pack 风格返回
        · 每条 item 绑定 url/title/engine/fetched_at/content/fetch_status
        · meta 汇总:搜索侧 engines_failed + 抓取侧成败计数
```

**关键设计:搜索侧与抓取侧的失败是两套,分别汇报**——搜索引擎哑了进 `meta.search.engines_failed`,URL 抓取失败进对应 item 的 `fetch_status`。两套都遵守"禁止静默丢弃"。

---

## 4. 端点契约

### 请求

```jsonc
POST /v0/research
{
  "q": "revenge bedtime procrastination",   // 必填
  "engines": ["duckduckgo", "wikipedia", "arxiv"],  // 可选,默认精选稳定集
  "categories": "general",     // 可选
  "language": "auto",          // 可选
  "time_range": null,          // 可选
  "safesearch": 0,             // 可选
  "fetch_top_n": 5,            // 抓取前 N 条,默认 5,≤8
  "purify": true,              // 是否 trafilatura 净化,默认 true
  "fetch_timeout": 15          // 每 URL 抓取超时(秒),默认 15
}
```

### 响应(Evidence Pack v0 风格)

```jsonc
{
  "query": "...",
  "created_at": "2026-07-05T15:00:00Z",
  "items": [
    {
      "url": "https://...",
      "title": "...",
      "engine": "arxiv",              // 来自搜索的来源引擎(可溯源)
      "score": 4.2,                   // 搜索融合分
      "fetched_at": "2026-07-05T15:00:03Z",
      "fetch_status": "ok",           // ok | failed | timeout | blocked
      "engine_used": "static",        // 本期恒为 static;二版起 static/dynamic/stealthy
      "http_status": 200,
      "word_count": 3241,
      "purified": true,               // 净化是否成功
      "content": "# 正文标题\n\n净化后正文…"   // purify=false 或净化失败则为 raw HTML
    },
    {
      "url": "https://blocked.example.com",
      "title": "...",
      "engine": "duckduckgo",
      "fetch_status": "blocked",
      "engine_used": "static",
      "http_status": 403,
      "content": null,
      "error": "static tier blocked (403); browser upgrade deferred to v2"
    }
  ],
  "meta": {
    "search": {
      "engines_requested": ["duckduckgo", "wikipedia", "arxiv"],
      "engines_used":      ["duckduckgo", "wikipedia", "arxiv"],
      "engines_failed":    [],       // 来自 SearXNG unresponsive_engines
      "results_total": 28,           // 搜索召回总数
      "took_ms": 842
    },
    "fetch": {
      "requested": 5,
      "ok": 4, "failed": 0, "timeout": 0, "blocked": 1,
      "took_ms": 3120
    }
  }
}
```

**不变量(可测试)**:`items` 数量 = `fetch.requested` = `ok+failed+timeout+blocked`——每个被选中的 URL 必有一条占位记录,禁止静默丢弃。

---

## 5. 代码结构与依赖

```
hollow/
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app + /v0/research 路由 + /healthz
│   ├── config.py            # 环境配置(SearXNG URL、impersonate、超时、并发)
│   ├── searx_client.py      # 封装 SearXNG HTTP 调用(固定头、参数映射、失败对账)
│   ├── fetcher.py           # 封装 Scrapling 静态抓取(信号量 + wait_for + 占位)
│   ├── purifier.py          # trafilatura 净化(失败回退 raw HTML)
│   ├── orchestrator.py      # 编排:search → select → gather-fetch → purify → assemble
│   └── models.py            # Pydantic 请求/响应模型
├── searxng/settings.yml     # 已有
└── requirements.txt         # fastapi, uvicorn, httpx, scrapling[fetchers], trafilatura, pydantic
```

依赖精确 pin:`scrapling[fetchers]==0.4.10`(勿单独 pin playwright)、`trafilatura`、`fastapi`、`uvicorn`、`httpx`、`pydantic`。

### 配置项(区分沙箱/生产)

| 配置 | 沙箱值 | 生产值 | 说明 |
|---|---|---|---|
| `SEARXNG_URL` | `http://127.0.0.1:8888` | 同左或内网 | 本地 SearXNG 进程 |
| `HOLLOW_IMPERSONATE` | `None` | `chrome` | 沙箱走代理须关 TLS 指纹;生产直连保留 |
| `CURL_CA_BUNDLE` | `/root/.ccr/ca-bundle.crt` | 不设 | 沙箱代理 CA |
| `FETCH_CONCURRENCY` | 5 | 5–8 | 并行抓取信号量 |
| `FETCH_TIMEOUT` | 15 | 15–30 | 每 URL 硬超时(秒) |

---

## 6. 并发与容错(硬约束)

- **并行抓取**:`asyncio.gather` + `asyncio.Semaphore(FETCH_CONCURRENCY)`,静态档是纯 HTTP(不吃浏览器内存),并发压力小,但仍限并发防打爆上游。
- **每 URL 硬超时**:`asyncio.wait_for(fetch, timeout)`;静态 curl_cffi 传输层错(`CurlError code 28`)映射 timeout,其余 CurlError 映射 failed。
- **失败隔离**:每个 URL 独立 try,一个失败/超时**只影响自己那条 item**,`gather(return_exceptions=True)` 确保不拖垮整体。
- **占位返回**:任何失败都产出带 `fetch_status` 的 item,`content=null` + `error` 说明。

---

## 7. 动手步骤(拍板后执行)

1. `requirements.txt` + `api/` 骨架;
2. `searx_client.py`:封装已实测的 HTTP 调用(固定头 + 参数映射 + `engines_failed` 对账);
3. `fetcher.py`:封装已实测的静态抓取(`impersonate` 配置化 + 信号量 + wait_for + 占位);
4. `purifier.py`:trafilatura 基线;
5. `orchestrator.py` + `main.py`:串起来 + FastAPI 路由;
6. **端到端实测**:起 SearXNG + 起 FastAPI,`curl POST /v0/research {q:"..."}`,验证 items 带正文、meta 双侧失败可见、不变量成立;
7. `tools/` 下加一键启动脚本(SearXNG + gateway)。

---

## 8. 待你拍板

1. **净化**:MVP 第一版就接 trafilatura(推荐,结果直接可读),还是先返回 raw HTML 更快通水?
2. **默认引擎集**:基于上轮实测(brave/startpage 在数据中心 IP 被封,DDG/baidu/sogou/wikipedia/arxiv 可用),MVP 默认集我建议 `[duckduckgo, wikipedia, arxiv]`(全实测可用)——可否?
3. **fetch_top_n 默认值**:5 条(项目书默认)可以吗?
4. **框架**:FastAPI(项目书选型)确认?

### 拍板结果(2026-07-06,已按此实现)

1. 净化:✅ MVP 接 trafilatura(markdown 输出,失败回退 raw HTML)。
2. 默认引擎集:**国际+国内"都来"** → `[duckduckgo, wikipedia, arxiv, baidu, sogou, quark]`;本地开发前提是系统代理常开(见 docs/research/2026-07-06-local-env-verification.md)。
3. fetch_top_n:✅ 默认 5。
4. 框架:✅ FastAPI。
5. 搜索源侧同轮拍板:DDG 进默认集(限速另议)、中文源按草案全放、应用商店源留池但归小众、alt-video 留 L2、密钥暂不投。

实现落地:`api/`(2026-07-06),经 Opus 对抗审查(15 候选缺陷 → 8 确认 → 全部修复):
bang 防护扩展到 `!/:/<` 前缀 token、纯 bang 清空回 400、`resp.json()` 异常归一 502、
净化侧 gather 加占位兜底、非受信 result 字段防御、专用抓取线程池+进程级闸
(排队不计入超时)、空串环境变量回退默认值。
