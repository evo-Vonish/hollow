# DEPLOY-HANDOFF — 把 hollow v0.1.0 部署到 Linux 服务器

> 给接手的 Claude Code(经 `xiaowo` MCP SSH 上目标机)。一屏够用;完整版见
> [docs/design/07-deployment.md](docs/design/07-deployment.md) 与 [README](README.md)。
> **部署对象**:hollow —— 深度研究 API(search/research/fetch 三端点)。tag `v0.1.0`。

## 三步跑起来(在目标 Linux 机上)

```bash
# 1) 拿代码到 /opt/hollow(Linux 直接 clone,不需要 Windows 那套 sparse-checkout)
git clone https://github.com/evo-Vonish/hollow.git /opt/hollow && cd /opt/hollow
git checkout v0.1.0

# 2) 两个 venv + 依赖 + 浏览器档
python3 -m venv .venv-searx && .venv-searx/bin/pip install -r vendor/searxng/requirements.txt tzdata
python3 -m venv .venv-api   && .venv-api/bin/pip install -r requirements.txt
.venv-api/bin/scrapling install                  # 浏览器档(dynamic/stealthy)需要 chromium(或 .venv-api/bin/playwright install chromium)

# 3) 密钥 + systemd + 反代
useradd -r -s /usr/sbin/nologin hollow && chown -R hollow:hollow /opt/hollow
install -Dm600 deploy/gateway.env.example /etc/hollow/gateway.env
sensible-editor /etc/hollow/gateway.env          # 填强 HOLLOW_API_KEY(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
cp deploy/hollow-*.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now hollow-searxng hollow-gateway
curl -s localhost:8080/healthz                   # {"status":"ok","searxng":"ok"}
# nginx: cp deploy/nginx-hollow.conf → sites-available,改域名,certbot 签证书,nginx -s reload
```

## 🔴 标红的坑(工具/服务起不来先查这里)

**`deploy/` 里所有路径硬写成 `/opt/hollow`**(两个 `.service` 的 WorkingDirectory/ExecStart/EnvironmentFile,
以及 `hollow-searxng.service` 的 `PYTHONPATH`)。**不 clone 到 `/opt/hollow` 就必须逐处改路径**——
和 xiaowo 那份"`cwd` 换机必改"是同一个坑。`systemctl status hollow-gateway` 报找不到路径,先看这条。

其余两条硬约束(已在 unit 里设好,别改坏):
- **别设 `HTTP(S)_PROXY`**(生产直连):静态档 DNS-pin 只对直连生效,走代理就失效。
- **`--workers 1`**:在飞限流/上游闸/浏览器闸都是进程内状态,多 worker 会各算各的、闸失效。
- nginx 必须 **`proxy_buffering off`**,否则 `/v1/research` 的 SSE 流被憋住/掐断。

## 只有在这台真机上才能验的三项(本地关不掉的缺口,交给你)

1. **浏览器抓取档**:`.venv-api/bin/scrapling install` 后 → `curl -s localhost:8080/v1/fetch -H 'content-type: application/json'
   -d '{"urls":"<一个 JS 重的 SPA>","mode":"thorough"}'` → 确认 `fetch_status:"ok"`(不是 `no_content`),即 dynamic/stealthy 能渲出正文。
2. **DNS-pin**:确认进程环境无代理 → 抓个公网页 `ok`,即 CURLOPT_RESOLVE 钉生效。
3. **中文引擎**:baidu/sogou/quark 在数据中心 IP 上大概率触发验证码/熔断 → 评估是否从默认集移除(`data/engine_registry.yaml` / `api/config.py DEFAULT_ENGINES`)。

## 心里有数(初代如实告知)

**底线④(阈值校准)未达**:内容闸/rerank 权重/highlight 参数都是起点值,**没按真实数据调过**。
v0.1.0 明确以**"跑真实流量采数据、据此校准"**为目的上线,不是稳定终版。校准用 [`eval/`](eval/) 评测台
(设 `HOLLOW_EVAL_JUDGE_URL` 指向一个 OpenAI 兼容的裁判模型)。召回质量已知短板见 docs/design/08。

## 冒烟验收(上线后)

```bash
.venv-api/bin/pip install -r requirements-dev.txt && .venv-api/bin/pytest          # 309 单元(网络无关)
HOLLOW_TEST_LIVE=1 .venv-api/bin/pytest tests/integration                          # 20 集成(需活网关)
curl -s 'localhost:8080/healthz?deep=1'                                            # 深探:接 SearXNG /stats/errors
# 授权:curl 无 Bearer → 401;带 Authorization: Bearer <key> → 200
```
