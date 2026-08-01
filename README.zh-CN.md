# hollow — AI 研究浏览器 · 深度研究 API

[English](README.md) · **简体中文**

免费开源(AGPL-3.0)的深度研究 API 服务:**多源搜索召回 → 三档升级全文爬取 → 正文净化 → 可溯源 Evidence Pack**。
参考 OpenAI 的信封/错误/SSE 惯例,域模型是 hollow 自己的(不仿制其端点)。

> **状态:生产运行中**。核心 API 面完整,354 单元测试 + 20 集成测试,GitHub Actions 每次 push 全绿;
> 已上线跑真实流量并据此完成了首轮校准(引擎相关性档位、超时矩阵、风控路由)。
> 官方搜索界面:[hollow-browser-front-end](https://github.com/evo-Vonish/hollow-browser-front-end) · 线上 https://hollow.vonish.dev

## 能做什么

| 端点 | 作用 |
|---|---|
| `POST /v1/search` | 纯搜索召回:秒级返回 URL/标题/摘要(+图片缩略图)+ 相关性重排 + 账目,不抓全文 |
| `POST /v1/research` | 搜索 + 并行抓取 + 净化全套(SSE 流式可选);每条带正文/highlights/外链/媒体/可溯源字段 |
| `POST /v1/fetch` | 按 URL 直取:抓取 + 净化,不经搜索;支持 PDF 正文抽取、外链自动展开、正文图片内联 |
| `GET /v1/engines` · `GET /v1/scenes` | 引擎注册表(343 源)/ 场景→引擎集 |

统一 `{error:{message,type,param,code}}` 错误封套 + 正确 HTTP 码;`stream:true` 走**语义化** SSE 事件(非 token delta);
可选 `HOLLOW_API_KEY` Bearer 鉴权(OpenAPI 已声明)。契约详见 [docs/design/04-v1-api.md](docs/design/04-v1-api.md)。

## 内容能力(2026-07 迭代)

**页面资产抽取**(`include_links` / `include_media`,fetch 与 research 双端点)
trafilatura 原生 `output_format='markdown', with_links/media` 管线路径,零额外请求。每条来源带站内/站外分类的外链清单(≤100)与媒体清单(≤50,图片/视频/音频,带 source 标注)。汇总帧显式声明请求字段。

**外链自动展开**(`expand_links` / `expand_depth` / `expand_scope`,仅 fetch)
抓取主 URL 后自动跟进页面外链,产出同构的 `children` 树。四重封顶防爆:单次展开 ≤10 条、深度 ≤3 层、整树 ≤20 节点、整树共享 45s 时间预算;visited 集合(含重定向落点)防循环;展开计数如实进 fetch 账目。

**正文图片两档**(`include_images` / `embed_images`)
- 引用档:正文 Markdown 内联 `![alt](url)` 图片引用(来源结构保真)
- 内联档:小图(≤32KB)直接转 data URI 嵌入正文,阅读零外网请求。四护栏:单图 ≤32KB / 单页 ≤10 张 / 总量 ≤256KB / 单图 5s 超时;SSRF 防护复用 netguard,Content-Type 白名单;**失败一律保留原 URL 引用,绝不丢内容**。

**PDF 正文抽取**
抓取落到 PDF 时自动走 pypdf 提取(页数 ≤30 / 字符 ≤100K 双封顶),头部标注 `[PDF · N pages]`;扫描件/损坏件诚实返回 `no_content`,不伪装成功。arXiv 论文直读已实测(15 页 / 39K 字符)。

**媒体前置**
搜索响应透传引擎的图片字段(`img_src`/`thumbnail`),图片场景 100% 结果带缩略图,客户端可零抓取渲染瀑布流。

## 引擎可靠性(从"团灭"到自愈)

**引擎健康退避**(进程内状态机):单引擎连续失败 3 次自动熔断,从默认集/场景集剔除(**显式点名 `engines=` 豁免**);指数退避 300s×2^n 封顶 3600s,到期半开放探测放行,成功即归零恢复。熔断名单在每次搜索的 `meta.engines_degraded` 如实入账——客户端能如实告诉用户"某引擎暂时不可用、何时重试",而不是假装结果完整。

**风控路由矩阵**:引擎按数据中心实测连通性配置代理——北京直连不通的引擎(duckduckgo/sogou)经东京出口代理,直连快的(360search)保持直连;按引擎配置超时(arxiv 等慢源放宽)。配置即文档,见 `searxng/settings.yml` 注释。

**背景**:SearXNG 对 CAPTCHA 的默认反应是 suspend 引擎 3600s(内存态)——一次触发整小时禁用,多引擎连环触发即成"全站死光"假象。健康退避层把这件事变成可观测、可自愈、可豁免的显式行为。

## 访问层:双池调度与 API key(2026-08-01)

免费 API 公网开放,匿名流量与登录用户**双池对称隔离**(各 4 并发槽):
- **匿名池**(按 IP 识别):自适应公平慢速——池内仅 1 个身份(单人刷取嫌疑)时速率钉死 0.2 req/s;身份 ≥4 个(真实公共流量)池满速 1.6 req/s,中间线性爬升。身份间轮转公平,攻击者挤不掉其他匿名用户;请求只在队列满时拒绝(429 + Retry-After 如实),其余全排队;多 IP 轮换由池级速率兜底——总吞吐恒定,饼切薄而已。
- **登录池**(hkv1_ key):满速,多 key 轮转公平;单 key 独占不惩罚(已认证)。
- 排队账目(`pool` / `queue_wait_ms` / `active_identities`)进每次响应的 `fetch.queue`(底线②③)。

key 器:`POST /v1/admin/keys` 签发(明文仅返回一次,sha256 落盘)、`/revoke` 吊销、`GET` 列表;`X-Admin-Key` 保护,未配置则 404 关闭。**account.vonish.dev 上线后签发迁移 account 侧**,本端点预留远端 introspect 对接位。

## 四条不可协商底线

1. 成功声明必须来自实测返回,而非意图
2. **禁止静默丢弃**(engines_failed / engines_degraded / fetch_status 全显式;集合差集对账兜底)
3. 一切内容可溯源(engine / fetched_at / url / final_url)
4. 阈值必须校准后上线

## 架构:借力基建,自研只做薄差异层

FastAPI 网关 + **SearXNG**(搜索,vendored)+ **Scrapling**(三档抓取 static→dynamic→stealthy)+ **trafilatura**(净化与资产抽取)+ **pypdf**(PDF)。
自研边界:API 编排层、Evidence Pack 组装、相关性重排/highlights(纯词汇,零模型)、引擎健康状态机、外链展开器、图片内联器。不做语言转写/重写引擎适配器。

## 本地快速开始

```bash
git clone https://github.com/evo-Vonish/hollow.git && cd hollow
git checkout main
# 两个 venv(SearXNG 与网关依赖分离)
python -m venv .venv-searx && .venv-searx/bin/pip install -r vendor/searxng/requirements.txt tzdata
python -m venv .venv-api   && .venv-api/bin/pip install -r requirements.txt
# 一键双起(Linux: tools/run_local.sh;Windows: tools\run_local.ps1)
tools/run_local.sh                       # SearXNG :8888 + 网关 :8080
curl -s localhost:8080/healthz           # {"status":"ok","searxng":"ok"}
curl -s localhost:8080/v1/search -H 'content-type: application/json' \
     -d '{"query":"transformer attention","scenes":["academic"]}'
# 深研带资产:外链+媒体+正文图片
curl -s localhost:8080/v1/research -H 'content-type: application/json' \
     -d '{"query":"giant panda","top_n":3,"include_links":true,"include_media":true,"include_images":true}'
# 抓取展开:取页面并跟进外链
curl -s localhost:8080/v1/fetch -H 'content-type: application/json' \
     -d '{"urls":["https://en.wikipedia.org/wiki/Giant_panda"],"expand_links":5,"expand_depth":2}'
```

## 部署(单机 Linux VPS)

产物在 [`deploy/`](deploy/)(systemd×2 + nginx SSE-safe + gateway.env.example)。
**完整步骤与上线前必做清单见 [docs/design/07-deployment.md](docs/design/07-deployment.md)** —— 尤其目标环境冒烟、阈值校准、egress 防火墙三项。
生产参考架构:双机 WireGuard 隧道——网关与 SearXNG 在境内机(北京),反代与前端在境外机(东京),海外出口受限的引擎经隧道代理出东京。

## 测试

```bash
.venv-api/bin/pip install -r requirements-dev.txt
.venv-api/bin/pytest                                   # 341 单元(网络无关,CI 即跑)
HOLLOW_TEST_LIVE=1 .venv-api/bin/pytest tests/integration   # 20 集成(需活网关)
```
CI(GitHub Actions)在每次 push 跑单元套件。召回质量离线评测台见 [`eval/`](eval/)。
**纪律:服务器上新装 pip 包必须同提交同步进 `requirements*.txt`**——手动安装对 CI 不可见。

## 已知边界(如实告知)

- **召回质量**:zh 场景(bilibili/360search/搜狗)良好;部分 EN 查询候选集仍有噪声,持续用 `eval/` 评测台校准中。
- **风控是常态**:baidu 在双端(北京直连/东京代理)都会触发验证码,靠健康退避自动熔断+恢复;被封时显式入账而非静默。
- **单进程**:在飞限流/上游闸/浏览器闸/引擎健康态均为进程内状态,`--workers 1` 是硬约束;横向扩展需挪共享存储(Redis)。
- **鉴权**:单一共享 Bearer key,无多租户/密钥轮换;公网部署建议叠加 Cloudflare 限速。
- **浏览器抓取档**:dynamic/stealthy 依赖 Playwright/Chromium,数据中心环境的指纹对抗是持续工程。

## 许可

AGPL-3.0。
