# AI Research Browser — 落地项目书 v2.0

> **定位**:搜索引擎 · 爬取引擎 · 浏览引擎
> **版本**:v2.0(第二次落地)
> **日期**:2026-07-05
> **状态**:开工前基线文档
> **上游文档**:《AI Research Browser 白皮书》(2025-05)、《广告过滤与正文保护算法深度研究报告》(2026-05)

---

## 0. 文档定位

本文档是白皮书愿景的**工程落地版**。白皮书回答"为什么做、做成什么样",本文档回答"这一次具体怎么做、按什么顺序做、上一次为什么没做成"。所有架构决策均以 v1 半成品的失败复盘为前提,以"每个阶段独立可用"为验收底线。

一句话概括本项目:一个免费、开源(AGPL-3.0)的深度研究 API 服务——多源搜索召回 → 三档升级全文爬取 → 正文净化 → 可溯源 Evidence Pack,并在此基建之上生长出 Semantic Reader 浏览体验。

第一期**明确不做**:Agent Runtime、多轮自动研究、本地知识库、分布式索引。这些是白皮书的第二、三阶段,依赖本期基建成熟,过早引入只会重演"在垃圾信息上做推理"。

---

## 1. v1 复盘:失败模式与根因

v1 的四个模块中,三个基建模块全部踩了同一个坑:**把该借力的成熟基础设施拿来自研,而真正该自研的差异化层反而没做完。**

| 模块 | v1 做法 | 结果 | 根因 | v2 对策 |
|------|---------|------|------|---------|
| 搜索引擎 | TypeScript 一比一转写 SearXNG,砍到 5 个源 | 召回效果差 | 只抄了架构骨架,丢掉了 200+ 引擎适配器里社区多年积累的解析器维护、反封锁参数、融合调教;并把本该社区承担的维护债转嫁给自己 | 原版 SearXNG 作为独立服务运行,只调 JSON API,一行不改 |
| 爬取引擎 | 纯自研 | 速度慢,分不清静态/动态页面 | "静动判断+按需升级"正是 Scrapling 打磨两年的核心能力,重复造轮子且无积累 | 直接使用 Scrapling 三档引擎(Fetcher / DynamicFetcher / StealthyFetcher) |
| 广告过滤与正文保护 | 按全量设计直接落地 | 残次品 | 复杂度前置:规则引擎+双阶段+三模式一次全上,且阈值无金标测试集校准 | v0 用 trafilatura + 轻量残留清理;全量 AdNoiseFilter 后置到有真实 badcase 之后 |
| 浏览器页面 | 仿 Edge 做了界面 | 半成品 | **方向没有错**——这是四个模块中唯一值得自研的差异化层,问题只是前三个基建拖垮了它 | 保留方向,作为第四阶段主战场 |

**v2 核心原则:借力基建,自研差异化。**

自研代码的边界收缩为三样:API 编排层、Evidence Pack 组装、阅读器前端。自研面积约为 v1 的四分之一,但每一行都是有效产出。

---

## 2. 技术选型与开源协议

### 2.1 选型表

| 组件 | 选型 | 协议 | 选型理由 |
|------|------|------|----------|
| 搜索服务 | SearXNG(原版,Docker 部署) | AGPL-3.0 | 200+ 引擎适配器由社区持续维护;隐私聚合;原生 JSON API |
| 爬取引擎 | Scrapling | BSD-3-Clause | 三档引擎自动升级;StealthyFetcher 开箱绕过 Cloudflare Turnstile;基于 curl_cffi 的 TLS 指纹模拟;Spider 框架备用 |
| 正文抽取 | trafilatura | Apache-2.0(以仓库 LICENSE 为准) | SIGIR 独立评测 F-Score 0.909;内置多层 fallback(自身启发式 → readability-lxml → jusText) |
| API 框架 | FastAPI | MIT | Python 生态与 Scrapling 同栈;异步原生;自动 OpenAPI 文档 |
| 并发治理 | asyncio 信号量(v0)→ 可选 Redis + arq(扩展期) | — | 简单优先,单机够用不引入外部依赖 |
| 反向代理 | Caddy(已部署) | Apache-2.0 | 沿用现有 vonish.dev 基础设施 |
| 阅读器前端(P4) | 待定 | — | 可评估复用 v1 浏览器页面资产 |

**语言栈决策:全栈 Python。** v1 的 TypeScript 资产(SearXNG 转写、自研爬虫)全部退役;AdNoiseFilter 研究报告中的 TS 伪代码在 P3+ 落地时以 lxml 在 Python 内重写评分逻辑,不做 Node sidecar——单人项目,少一条进程间边界就少一类 bug。

### 2.2 协议决策:整体 AGPL-3.0

三条理由:

1. **传染兼容**:SearXNG 是 AGPL,BSD(Scrapling)与 Apache(trafilatura)代码可被吸收进 AGPL 项目,反向不行,故项目协议下限即 AGPL。
2. **零成本防白嫖**:本项目本就免费开源,AGPL 的"网络服务也必须开源"义务对自己零成本,同时法律上阻断他人拿代码搭闭源商业服务。
3. **保留后路**:作为自研部分的版权方,未来可走"AGPL + 商业授权"双协议路线。

技术细节备忘:若仅通过 HTTP 调用独立部署的 SearXNG 实例(进程隔离、不改其代码),API 层理论上可用其他协议;但整体 AGPL-3.0 最省心,且符合上游社区预期。若 fork 或修改 SearXNG 本身,该部分必须 AGPL。

---

## 3. 系统架构

### 3.1 组件视图

```
                    ┌─────────────────────────────────┐
                    │      Client / VonishAgent       │
                    └───────────────┬─────────────────┘
                                    │ HTTPS (Cloudflare CDN → Caddy)
                    ┌───────────────▼─────────────────┐
                    │     API Gateway (FastAPI)       │
                    │  鉴权 · 限流 · 参数校验 · 编排   │
                    └───────┬───────────────┬─────────┘
                            │               │
              ┌─────────────▼──┐      ┌────▼──────────────────┐
              │ Search Service │      │    Crawl Runtime      │
              │ SearXNG (原版)  │      │    Scrapling          │
              │ Docker · JSON  │      │ Fetcher → Dynamic     │
              │ 多引擎召回融合   │      │ → Stealthy 升级链      │
              └────────────────┘      │ 浏览器实例池 + 信号量    │
                                      └────┬──────────────────┘
                                           │ raw HTML
                                      ┌────▼──────────────────┐
                                      │      Purifier         │
                                      │ trafilatura 正文抽取    │
                                      │ + Phase 2 轻量残留清理  │
                                      │ + 删除审计日志          │
                                      └────┬──────────────────┘
                                           │ clean content
                                      ┌────▼──────────────────┐
                                      │   Evidence Builder    │
                                      │ 溯源字段绑定 · Pack 组装│
                                      └───────────────────────┘
```

### 3.2 数据流

**/search 流程**:请求 → 参数映射为 SearXNG 查询(engines、time_range、language 等)→ SearXNG 多引擎并发召回 → 融合去重(SearXNG 内置)→ API 层附加 engine 来源与 meta 状态 → 返回。

**/fetch 流程**:URL 列表 → 队列 → 逐 URL 执行升级链(静态 Fetcher 起步;内容过薄或失败则升 DynamicFetcher;遇 Cloudflare 拦截升 StealthyFetcher)→ Purifier 净化 → 逐 URL 返回内容与显式状态。

### 3.3 部署拓扑

| 节点 | 角色 | 部署内容 | 备注 |
|------|------|----------|------|
| Vultr Tokyo(vonish.dev) | 主节点 | FastAPI + SearXNG(Docker)+ Scrapling 浏览器池 | 一切需要出海流量的组件都在这里;Cloudflare CDN + Caddy 沿用现状 |
| 阿里云北京 | 内网 worker | OCR 等无需出海的任务 | 经 Tailscale 接入;**不进入爬取链路**,爬取流量不绕行 |

**并发治理硬约束**:小规格 VPS 上 StealthyFetcher 每实例吃数百 MB 内存,浏览器实例池上限设 2–3;所有 fetch 任务过 asyncio 信号量;每 URL 强制超时,超时杀进程回收。

---

## 4. API 契约 v0

### 4.1 设计原则

1. **端点解耦**:/fetch 无状态,接收裸 URL 列表,不依赖前置搜索——用户手持 URL 也能直接用。
2. **失败显式**:任何引擎失败、任何 URL 抓取失败必须出现在返回体中,**禁止静默丢弃**(v1 时代 VonishAgent 审计抓到过"静默丢弃来源冲突",同一个错不犯第二次)。
3. **来源可溯**:每条搜索结果带 engine 字段,每条抓取结果带 engine_used 与 fetched_at——一切结论落到真实来源。

### 4.2 `POST /v0/search`

**请求参数**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| q | string | 必填 | 查询词 |
| engines | string[] | 全部启用引擎 | 指定搜索源,如 `["duckduckgo","brave","wikipedia","arxiv"]` |
| categories | string | general | SearXNG 分类:general / news / science / it 等 |
| time_range | string | 无 | day / week / month / year |
| language | string | auto | 如 zh-CN / en |
| count | int | 20 | 返回条数上限,≤ 50 |
| safesearch | int | 0 | 0 / 1 / 2 |

**返回示例**

```json
{
  "query": "revenge bedtime procrastination",
  "results": [
    {
      "title": "…",
      "url": "https://…",
      "description": "…",
      "engine": "duckduckgo",
      "score": 4.2,
      "published_at": "2026-03-11"
    }
  ],
  "meta": {
    "engines_used": ["duckduckgo", "brave", "wikipedia"],
    "engines_failed": [
      { "engine": "google", "reason": "captcha" }
    ],
    "took_ms": 842
  }
}
```

`engines_failed` 是溯源约束的直接体现:哪个引擎哑了,调用方看得见。

### 4.3 `POST /v0/fetch`

**请求参数**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| urls | string[] | 必填 | ≤ 10 条/请求(硬上限,防浏览器池被打爆) |
| mode | string | auto | auto(自动升级链)/ static / dynamic / stealth |
| purify | string | balanced | conservative / balanced / aggressive / off |
| format | string | markdown | markdown / text / html |
| timeout | int | 30 | 每 URL 秒数 |

**返回示例**

```json
{
  "results": [
    {
      "url": "https://…",
      "fetch_status": "ok",
      "engine_used": "dynamic",
      "content": "# 正文标题\n\n净化后正文…",
      "word_count": 3241,
      "fetched_at": "2026-07-05T14:22:31Z"
    },
    {
      "url": "https://blocked.example.com",
      "fetch_status": "blocked",
      "engine_used": "stealth",
      "content": null,
      "word_count": 0,
      "fetched_at": "2026-07-05T14:22:48Z",
      "error": "cloudflare challenge not solved within timeout"
    }
  ]
}
```

`fetch_status` 枚举:`ok / failed / timeout / blocked`。失败条目原样占位返回。

**升级链逻辑(mode=auto)**:

```
Fetcher(静态 HTTP)
  ├─ 成功且正文长度 ≥ 阈值 → 完成
  ├─ 失败 / 正文过薄(疑似 SPA)→ DynamicFetcher(浏览器渲染)
  │     ├─ 成功 → 完成
  │     └─ Cloudflare / 反爬拦截 → StealthyFetcher(隐身模式)
  └─ 每级独立计时,总耗时受 timeout 约束
```

### 4.4 `POST /v0/research`(P3 交付)

组合端点:search → 取 top-N 结果 → 并发 fetch → 组装 Evidence Pack 返回。参数为 search 参数 + `fetch_top_n`(默认 5,≤ 8)+ purify。

### 4.5 Evidence Pack v0 Schema

```json
{
  "query": "…",
  "created_at": "2026-07-05T14:25:00Z",
  "items": [
    {
      "url": "https://…",
      "title": "…",
      "engine": "brave",
      "fetched_at": "2026-07-05T14:24:31Z",
      "fetch_status": "ok",
      "word_count": 3241,
      "content": "净化后正文快照…"
    }
  ]
}
```

五个核心溯源字段(url / title / engine / fetched_at / content)起步,schema 后续演化。该结构可直接映射到 VonishAgent 的 tool call ID 溯源体系:每条数据绑定真实抓取记录,报告中的每个数据点可回溯到具体 item。

### 4.6 通用约定

鉴权:v0 可选 API Key(Header `X-API-Key`),公开实例可关闭。限流:每 Key 每分钟请求数上限(v0 内存计数即可)。错误码:400 参数错误 / 401 鉴权失败 / 429 限流 / 502 上游 SearXNG 不可用 / 504 抓取整体超时。

---

## 5. Purifier 策略

### 5.1 v0 流水线(P3 交付)

```
Scrapling 原始 HTML
  → trafilatura 正文抽取(F-Score 0.909 基线,内置 readability-lxml / jusText fallback)
  → Phase 2 轻量残留清理:
      · 文本密度 + 链接密度评分
      · 清除残留社交分享块、推荐块、订阅框尾巴
  → Markdown 转换
```

三档模式(conservative / balanced / aggressive)在 **API 参数层先占位**,v0 实现为差异有限的简化版(主要控制 Phase 2 的删除阈值),接口不变、实现渐进。

### 5.2 全量 AdNoiseFilter 的后置条件

《研究报告》中的完整设计(Phase 1 规则引擎 + DOM 启发式 + 双评分仲裁)**不在 v0 落地**,启用条件为:

1. 金标测试集建立(先 30 页起步,逐步扩到报告规划的 100+ 页);
2. v0 流水线积累出真实 badcase,证明 trafilatura 基线不够用;
3. 阈值组(0.85 / 0.60 / 0.30)经金标集实测校准——当前为设计值,禁止未校准直接上线。

**服务端约束备忘**(落地 Phase 1 时必读):EasyList 规则在服务端仅纯结构型元素隐藏规则(`##.ad-banner` 类)可用;`:matches-css()`、`display:none` 检测等依赖渲染引擎的选择器不可用;网络拦截规则(`||example.com^`)在服务端 HTML 场景无请求可拦(广告多为客户端 JS 注入)。因此 Rule-based Filter 实际威力小于报告预期,DOM 启发式 + 双评分才是主力——可据此裁剪规则引擎复杂度。

### 5.3 删除审计

保留研究报告的审计设计,v0 落最简版:每次净化记录"删除了哪些节点 + 触发原因 + 所用模式",随日志滚动。`purify=off` 即回退原文能力。

---

## 6. 阶段计划

单人开发、与学业并行,**按里程碑推进,不按日历死线**。每阶段交付物必须独立可用——这是 v1 烂尾的直接解药。

| 阶段 | 内容 | 交付物 | 验收标准 |
|------|------|--------|----------|
| **P1 搜索 API** | SearXNG Docker 部署(settings.yml 开启 `formats: [html, json]`、配置引擎清单与 limiter)+ FastAPI /v0/search | 可公开调用的搜索端点 | 5 类典型 query(资讯 / 学术 / 技术 / 中文 / 时效性)实测召回质量;engines_failed 正确暴露 |
| **P2 爬取 API** | Scrapling 集成(`pip install "scrapling[fetchers]"` + `scrapling install`)、auto 升级链、/v0/fetch、浏览器池 + 信号量 + 超时治理 | 可用抓取端点 | 静态站 / SPA / Cloudflare 站三类各选样本实测;内存峰值不打爆 VPS;失败显式返回 |
| **P3 净化 + Evidence** | trafilatura + Phase 2 轻量清理、purify 参数、/v0/research、Evidence Pack v0、金标测试集 v0(30 页) | 完整研究链路端点 | Evidence Pack 字段完整可溯;purifier on/off A/B 对比正文完整性 |
| **P4 浏览引擎** | Semantic Reader 前端:净化阅读模式、原文/净化双模式切换、资源图谱展示、Evidence 展示 | Web 阅读界面 | 白皮书 5.6 / 5.7 节体验落地;复用评估 v1 页面资产 |
| **P5+ 远期** | Agent Runtime、多轮搜索、本地知识库、分布式索引 | — | 白皮书原路线,基建成熟后启动 |

VonishAgent 接入点:P1 完成即可替换其 web search 后端;P3 完成后 Evidence Pack 直连其 tool call 溯源体系。

---

## 7. 风险登记册

| 风险 | 等级 | 对策 |
|------|------|------|
| 搜索引擎封锁数据中心 IP(Google 验证码首当其冲) | 高 | 引擎多样化(DuckDuckGo / Brave / Mojeek / Wikipedia / arXiv 常开),不押注单一引擎;per-engine 状态入 meta,静默失效可发现 |
| 浏览器实例内存打爆小规格 VPS | 高 | 实例池上限 2–3、信号量排队、每 URL 硬超时 + 进程回收、urls ≤ 10/请求 |
| 单人开发烂尾(v1 已验证的最大风险) | 高 | 每阶段独立可用;"先通水后换粗管";任何模块超预期复杂立即砍 scope 而非硬扛 |
| 目标网站 ToS / 版权灰色地带 | 中 | 定位为开源研究工具而非内容再分发;robots.txt 尊重开关;抓取值仅返回调用方不做公开缓存;AGPL 开源姿态本身即最稳的存在方式 |
| 净化误删正文 | 中 | balanced 默认 + purify=off 回退 + 删除审计日志;aggressive 模式文档标注误删风险 |
| trafilatura 对特定站型抽取质量差 | 中 | 金标集持续收录 badcase;届时按 5.2 条件启动全量 AdNoiseFilter |
| AGPL 合规 | 低 | 全量开源即自动合规;上游代码不做闭源修改 |

---

## 8. 质量与溯源基线

四条不可协商的底线,与 VonishAgent 既有工程哲学同源:

1. **成功声明必须来自实测返回,而非意图**——engines_failed、fetch_status 的存在意义。
2. **禁止静默丢弃**——失败的引擎、失败的 URL、被删的 DOM 节点,全部显式可见。
3. **一切内容可溯源**——engine / fetched_at / url 三件套贯穿搜索到 Evidence Pack。
4. **阈值必须校准后上线**——任何评分阈值在金标测试集验证前只是设计值。

---

## 9. 仓库与发布

建议 monorepo 结构:

```
research-browser/
├── api/            # FastAPI 编排层(自研)
├── purifier/       # 净化流水线(自研)
├── searxng/        # SearXNG 部署配置(settings.yml、docker-compose,不含源码修改)
├── reader/         # P4 阅读器前端(自研)
├── tests/
│   └── golden/     # 金标测试集(页面快照 + 期望正文)
├── LICENSE         # AGPL-3.0
└── README.md
```

发布姿势:LICENSE 先行、README 写清与 SearXNG / Scrapling / trafilatura 的依赖关系与致谢、公开实例与自托管文档分开写。项目名沿用白皮书 "AI Research Browser" 占位,正式命名后全局替换。

---

## 10. 结语

v1 的教训浓缩成一句话:**别在别人已经铺好的路上重新修路,把力气留给只有你能修的那一段。**

v2 的全部架构决策都是这句话的展开——SearXNG 管召回,Scrapling 管抓取,trafilatura 管抽取,而项目真正的身份,藏在把它们串起来的编排层、让每条数据可溯源的 Evidence 体系,和最终那个"安静、纯净、冷白"的阅读界面里。

先通水,再换粗管。这次把水通到底。
