# 部署指南(场景 B:单机 Linux VPS)—— 2026-07-15

> 目标:把 hollow 从"Windows 本地预览"推到"可对小范围可信用户负责"的单机部署。
> 产物在 `deploy/`:两个 systemd unit、`gateway.env.example`、`nginx-hollow.conf`。
> ⚠️ 上线前务必读「四个必做」——尤其目标环境验证与阈值校准是本项目**从未在 Windows/GFW 之外做过**的。

## v0.1.0 上线前检查清单(逐项打勾再发)

**代码/依赖**
- [ ] `git clone` 目标 commit(打了 `v0.1.0` tag),`pip install -r requirements.txt`(已锁版本,可复现)
- [ ] `pytest` 单元套件绿(网络无关);CI 该 commit 绿

**目标环境冒烟(本项目最大盲区)**
- [ ] 真 Linux 机 `.venv-api/bin/scrapling install`(或 `.venv-api/bin/playwright install chromium`),实抓一个 JS 重 SPA → 确认 dynamic/stealthy 档渲出正文
- [ ] **不设** `HTTP(S)_PROXY`(生产直连)→ 静态档 DNS-pin 生效;`curl localhost:8080/v1/fetch` 抓个公网页 `fetch_status:ok`
- [ ] 中文引擎(baidu/sogou/quark)在数据中心 IP 评估验证码/熔断,决定是否保留在默认集

**安全**
- [ ] `/etc/hollow/gateway.env` 设强 `HOLLOW_API_KEY`(chmod 600);`curl` 无 Bearer → 401
- [ ] **egress 防火墙**:禁 hollow 主机出站到 RFC1918 / `169.254.169.254` / `::1` / `fc00::/7`(netguard 的兜底,补浏览器档/代理残留)
- [ ] nginx TLS + `proxy_buffering off`(否则 SSE 被掐);`--workers 1`(进程内闸的硬约束)

**上线后验证**
- [ ] `curl 'localhost:8080/healthz?deep=1'` → searxng ok、报错引擎数正常
- [ ] 跑一条真 `/v1/research` 流式 → 收到 SSE 事件 + `[DONE]`,`logs/gateway-*.log` 有完整访问日志(时长记到流结束,非 40ms)
- [ ] 并发压一下 → 超 `MAX_INFLIGHT_HEAVY` 见 429(含流式路径,见 `tests/test_middleware.py` 修的那个 bug)

> ⚠️ **底线④ 未达**:阈值(内容闸/rerank 权重/highlight)尚未按真实数据校准。初代**明确以"跑真实流量采数据、据此校准"为目的**上线,
> 不是稳定终版。校准用 `eval/` 评测台(接 GLM 裁判)。召回质量已知短板见 docs/design/08。

## 目录布局(建议 /opt/hollow)

```
/opt/hollow/                 # git clone 于此(vendor/ 也在)
  .venv-api/                 # python -m venv;pip install -r requirements.txt
  .venv-searx/               # python -m venv;pip install -r vendor/searxng/requirements.txt tzdata
  api/ searxng/settings.yml vendor/searxng/ data/ ...
  logs/                      # 运行日志(gitignore)
/etc/hollow/gateway.env      # 机密(HOLLOW_API_KEY),chmod 600,来自 gateway.env.example
```

## 起服:systemd(生产)

```bash
sudo useradd -r -s /usr/sbin/nologin hollow
sudo chown -R hollow:hollow /opt/hollow
sudo install -Dm600 deploy/gateway.env.example /etc/hollow/gateway.env
sudo -e /etc/hollow/gateway.env          # 填入强 HOLLOW_API_KEY
sudo cp deploy/hollow-*.service /etc/systemd/system/   # 按实际路径改 /opt/hollow
sudo systemctl daemon-reload
sudo systemctl enable --now hollow-searxng hollow-gateway
curl -s localhost:8080/healthz            # {"status":"ok","searxng":"ok"}
curl -s 'localhost:8080/healthz?deep=1'   # 含 searxng_error_engines
```

(本地开发仍用 `tools/run_local.sh` 前台双起,日志进终端。)

## 反代 + TLS

`deploy/nginx-hollow.conf`:TLS 终止 + 关键的 **SSE 关缓冲**(`proxy_buffering off`),否则
`/v1/research?stream=true` 的事件流会被 nginx 憋住/超时掐断。certbot 签证书后 reload 即可。

## 四个必做(否则不算"可负责")

1. **锁依赖已做**:`requirements.txt` 已钉版本(2026-07-15)。部署用 `pip install -r requirements.txt`。
2. **目标环境冒烟**(本项目最大盲区,过去只在 Windows+代理+GFW 下测):
   - **浏览器档升级链**:`.venv-api/bin/scrapling install`(或 `.venv-api/bin/playwright install chromium`),
     实抓一个 JS 重的 SPA,确认 dynamic/stealthy 档能渲出正文(此前从未端到端验过)。
   - **DNS-pin**:生产**直连**(别设 HTTP(S)_PROXY),确认静态档 CURLOPT_RESOLVE 钉生效(代理下会失效)。
   - **中文引擎**:baidu/sogou/quark 在数据中心 IP 常触发验证码熔断,评估是否保留在默认集。
3. **阈值校准(底线④,尚未做)**:拿一批真实 query 跑,调 `HOLLOW_MIN_CONTENT_CHARS`(空壳判定)、
   `HOLLOW_RERANK_W_*`(重排权重)、`HOLLOW_HIGHLIGHT_*`(高亮句长/条数)。默认值只是起点。
4. **egress 防火墙(SSRF 兜底)**:应用层 netguard 有已知残留(浏览器档抢先拦截、代理下 rebind)。
   在**网络层**禁止 hollow 主机出站到 RFC1918 / `169.254.169.254` / `::1` / `fc00::/7`,作为可靠兜底。

## 已知边界(场景 C 才需处理)

- **单进程**:`--workers 1` 是硬要求——在飞限流 / 上游闸 / 浏览器闸都是**进程内**状态,多 worker 会各算各的。
  横向扩展需把这些状态挪到共享存储(如 Redis 计数器 + 分布式限流)。
- **无持久化**:无配额强制(只暴露 `usage` 用量)、无结果缓存、无跨重启状态。
- **鉴权**:单一共享 Bearer key,无多租户 / 密钥轮换 / 审计。

## 监控

- `GET /healthz`(浅,存活)/ `GET /healthz?deep=1`(接 SearXNG `/stats/errors`,返回报错引擎数)。
- 日志:`logs/gateway-*.log`(结构化访问日志 + 上游失败 + 引擎失败),`journalctl -u hollow-gateway`。
