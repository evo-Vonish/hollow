# hollow — AI 研究浏览器 · 深度研究 API

免费开源(AGPL-3.0)的深度研究 API 服务:**多源搜索召回 → 三档升级全文爬取 → 正文净化 → 可溯源 Evidence Pack**。
参考 OpenAI 的信封/错误/SSE 惯例,域模型是 hollow 自己的(不仿制其端点)。

> **状态:v0.1.0 初代**。核心 API 面完整、有测试安全网与部署产物;但**阈值尚未按真实数据校准**(底线④),
> 召回质量在部分 EN 查询上有已知短板——见下方「已知边界」。初代定位是"上服务器、跑真实流量、据此校准",不是"稳定终版"。

## 能做什么

| 端点 | 作用 |
|---|---|
| `POST /v1/search` | 纯搜索召回:秒级返回 URL/标题/摘要 + 相关性重排 + 账目,不抓全文 |
| `POST /v1/research` | 搜索 + 并行抓取 + 净化全套(SSE 流式可选);每条带正文/highlights/可溯源字段 |
| `POST /v1/fetch` | 按 URL 直取:抓取 + 净化,不经搜索(vonish 两跳集成:search 拿 URL → fetch 取正文) |
| `GET /v1/engines` · `GET /v1/scenes` | 引擎注册表(343 源)/ 场景→引擎集 |

统一 `{error:{message,type,param,code}}` 错误封套 + 正确 HTTP 码;`stream:true` 走**语义化** SSE 事件(非 token delta);
可选 `HOLLOW_API_KEY` Bearer 鉴权(OpenAPI 已声明)。契约详见 [docs/design/04-v1-api.md](docs/design/04-v1-api.md)。

## 四条不可协商底线

1. 成功声明必须来自实测返回,而非意图
2. **禁止静默丢弃**(engines_failed / fetch_status 全显式;集合差集对账兜底)
3. 一切内容可溯源(engine / fetched_at / url / final_url)
4. 阈值必须校准后上线 ⚠️(v0.1.0 尚未做,见「已知边界」)

## 架构:借力基建,自研只做薄差异层

FastAPI 网关 + **SearXNG**(搜索,vendored)+ **Scrapling**(三档抓取 static→dynamic→stealthy)+ **trafilatura**(净化)。
自研边界只有三样:API 编排层、Evidence Pack 组装、相关性重排/highlights(纯词汇,零模型)。不做语言转写/重写引擎适配器(那是 v1 的死法)。

## 本地快速开始

```bash
git clone https://github.com/evo-Vonish/hollow.git && cd hollow
git checkout claude/new-project-setup-1dzma1
# 两个 venv(SearXNG 与网关依赖分离)
python -m venv .venv-searx && .venv-searx/bin/pip install -r vendor/searxng/requirements.txt tzdata
python -m venv .venv-api   && .venv-api/bin/pip install -r requirements.txt
# 一键双起(Linux: tools/run_local.sh;Windows: tools\run_local.ps1)
tools/run_local.sh                       # SearXNG :8888 + 网关 :8080
curl -s localhost:8080/healthz           # {"status":"ok","searxng":"ok"}
curl -s localhost:8080/v1/search -H 'content-type: application/json' \
     -d '{"query":"transformer attention","scenes":["academic"]}'
```

## 部署(单机 Linux VPS)

产物在 [`deploy/`](deploy/)(systemd×2 + nginx SSE-safe + gateway.env.example)。
**完整步骤与上线前必做清单见 [docs/design/07-deployment.md](docs/design/07-deployment.md)** —— 尤其目标环境冒烟、阈值校准、egress 防火墙三项。

## 测试

```bash
.venv-api/bin/pip install -r requirements-dev.txt
.venv-api/bin/pytest                                   # 309 单元(网络无关,CI 即跑)
HOLLOW_TEST_LIVE=1 .venv-api/bin/pytest tests/integration   # 20 集成(需活网关)
```
CI(GitHub Actions)在每次 push 跑单元套件。召回质量离线评测台见 [`eval/`](eval/)。

## 已知边界(初代如实告知)

- **阈值未校准(底线④)**:`MIN_CONTENT_CHARS` / rerank 权重 / highlight 参数都是起点值,未按真实 query 调过。
  上服务器跑真实流量 + 用 `eval/` 评测台校准,是初代之后的头号事项。
- **召回质量**:部分 EN 查询候选集被关键词命中的噪声污染(见 [docs/design/08](docs/design/08-recall-quality-baseline.md));zh(bilibili)召回良好。
- **目标环境未全验**:此前只在 Windows + 家用代理 + GFW 下测过;浏览器抓取档(dynamic/stealthy)未在 Linux 数据中心端到端验证。
- **单进程**:在飞限流/上游闸/浏览器闸均为进程内状态,`--workers 1` 是硬约束;横向扩展需挪共享存储(Redis)。
- **鉴权**:单一共享 Bearer key,无多租户/密钥轮换。

## 许可

AGPL-3.0。
