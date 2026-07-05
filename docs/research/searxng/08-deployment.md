# 08 · 容器化部署（container/ + docs/admin/installation-docker）

> 快照:vendor/searxng @ commit a643858(未修改)。
> 研读范围:`container/`(docker-compose.yml / dist.dockerfile / builder.dockerfile / entrypoint.sh / settings.template.yml / .env.example)、`docs/admin/installation-docker.rst`、`docs/admin/installation-granian.rst`、`settings_server.rst` / `settings_valkey.rst`。
> 本笔记服务于 P1:我们把 **原版 SearXNG 用官方镜像独立部署,只调它的 JSON API**。所以这里的核心不是"怎么改源码",而是"怎么把官方镜像正确跑起来 + 让 JSON API 可用 + 内网只暴露给 FastAPI"。

---

## 职责概述

`container/` 是官方发布的容器化制品,包含:

1. **两阶段构建**:`builder.dockerfile`(装依赖、预编译、压缩静态资源)→ `dist.dockerfile`(拷贝产物到运行基础镜像 `searxng/base:searxng`)。基础镜像本身(`docker.io/searxng/base:*`)不在本快照内。
2. **运行入口** `entrypoint.sh`:处理卷权限 → 首次渲染 `settings.yml` 模板 + 生成随机 `secret_key` → 用 **Granian**(不是 uwsgi)启动 WSGI app。
3. **编排** `docker-compose.yml`:两个服务 `core`(SearXNG)+ `valkey`,加两个具名卷。
4. **配置模板** `settings.template.yml`:极简,只开 `use_default_settings: true` + `image_proxy: true`,secret_key 占位。

关键认知(对我们最重要):**当前版本的 web server 是 Granian,不是 uwsgi。** uwsgi 只在裸机安装(installation-uwsgi)里用;容器里 entrypoint 直接 `exec granian`(entrypoint.sh:117)。`installation-granian.rst:21` 明确写着 "Granian will be the future replacement for uwsgi ... only officially supported in the installation container"。所以任务描述里问的"uwsgi worker 数"在容器场景下其实是 **Granian worker 数**。

---

## 架构与关键流程

### 运行时拓扑(官方 compose)

```
        docker compose 默认网络(名字 searxng_default,compose 顶层 name: searxng)
   ┌────────────────────────────────────────────────────────────┐
   │  core (searxng-core)                valkey (searxng-valkey)  │
   │  image searxng/searxng:${VER}       image valkey/valkey:9-a  │
   │  Granian :: :8080  ──(可选)──────▶  valkey-server :6379      │
   │  vol ./core-config → /etc/searxng   vol valkey-data → /data  │
   │  vol core-data → /var/cache/searxng                          │
   └──────────────┬─────────────────────────────────────────────┘
                  │ ports: [HOST:]8080:8080
                  ▼
         宿主机(默认 0.0.0.0:8080)
```

注意:**compose 里 core 与 valkey 之间没有 `depends_on`、没有显式 network、没有 healthcheck**(逐行读过 docker-compose.yml 全 29 行确认)。两者靠 compose 默认网络互通,主机名就是服务名 `valkey` / 容器名 `searxng-valkey`。

### 容器启动流程(entrypoint.sh)

1. 打印版本(entrypoint.sh:99-101,`$__SEARXNG_VERSION`)。
2. `volume_handler` 对配置目录和数据目录各跑一次(entrypoint.sh:104-105):
   - `check_directory` 确认目录存在(不存在 → exit 127)。
   - `setup_ownership`(entrypoint.sh:35-70):`stat` 取属主,若不是 `searxng:searxng` 且 `FORCE_OWNERSHIP`(默认 true,entrypoint.sh:55)且当前是 root → `chown -R searxng:searxng`;否则只打 WARNING,不改。
3. `setup`(entrypoint.sh:80-97):若 `$__SEARXNG_CONFIG_PATH/settings.yml` **不存在**,从 `/usr/local/searxng/settings.template.yml` 拷一份(`cp -pfT`),再用 `sed` 把 `ultrasecretkey` 替换成 `head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9'` 生成的随机串(entrypoint.sh:93)。**已存在则完全不动**(所以我们挂载自己的 settings.yml 会被原样保留)。
4. root 才做:`update-ca-certificates`(entrypoint.sh:110-112)。
5. `export GRANIAN_PORT="${SEARXNG_PORT:-$GRANIAN_PORT}"`(entrypoint.sh:115)—— 把 `SEARXNG_PORT` 别名成 Granian 端口。
6. `exec /usr/local/searxng/.venv/bin/granian searx.webapp:app`(entrypoint.sh:117)。

---

## 逐问题详解

### 问题 1:官方 docker-compose.yml 服务拓扑

**服务 A — `core`(docker-compose.yml:7-16)**

| 项 | 值 | 出处 |
|---|---|---|
| container_name | `searxng-core` | 行 8 |
| image | `docker.io/searxng/searxng:${SEARXNG_VERSION:-latest}` | 行 9 |
| restart | `always` | 行 10 |
| ports | `${SEARXNG_HOST:+${SEARXNG_HOST}:}${SEARXNG_PORT:-8080}:${SEARXNG_PORT:-8080}` | 行 12 |
| env_file | `./.env` | 行 13 |
| volumes | `./core-config/:/etc/searxng/:Z` 和 `core-data:/var/cache/searxng/` | 行 15-16 |

端口映射表达式解读:`SEARXNG_HOST` 设了就作为主机侧监听地址前缀(如 `127.0.0.1:8080:8080`),没设就是 `8080:8080`(即 `0.0.0.0`)。两侧端口都取 `SEARXNG_PORT`,默认 8080。`:Z` 是 SELinux 标签(Podman/RHEL 系需要)。

**服务 B — `valkey`(docker-compose.yml:18-24)**

| 项 | 值 | 出处 |
|---|---|---|
| container_name | `searxng-valkey` | 行 19 |
| image | `docker.io/valkey/valkey:9-alpine` | 行 20 |
| command | `valkey-server --save 30 1 --loglevel warning` | 行 21 |
| restart | `always` | 行 22 |
| volumes | `valkey-data:/data/` | 行 23 |

Valkey 是 Redis 的开源分叉;`--save 30 1` = 30 秒内有 1 次写就落盘 RDB。valkey 未映射端口到宿主(仅容器网络内 6379 可达,docs installation-docker.rst:152 显示 `6379/tcp`)。

**具名卷**:`core-data`、`valkey-data`(docker-compose.yml:26-28)。

**健康检查**:官方 compose **没有** `healthcheck` 段。但应用层有 `/healthz` 端点(webapp.py:597-599,返回纯文本 `OK`),我们可以自己加 healthcheck 探它。

**关键坑**:compose 里起了 valkey,**但默认 SearXNG 并不连它**。默认 `valkey.url: false`(settings.yml:125)、`server.limiter: false`(settings.yml:97),`.env.example` 也没设 `SEARXNG_VALKEY_URL`。也就是说 **开箱即用时 valkey 是"部署了但闲置"** —— 只有开启 limiter(bot 防护)等需要缓存的功能时才用得上,而 limiter 明确 "requires a Valkey database"(settings_server.rst:39,searx.limiter.rst:9)。

### 问题 2:entrypoint.sh 做了什么

见上文"容器启动流程"。归纳三件事:

1. **卷权限矫正**:确保 `/etc/searxng` 和 `/var/cache/searxng` 属于 `searxng:searxng`(UID/GID 977,见 dist.dockerfile:7-9 的 `--chown=977:977`),默认强制 `chown -R`。
2. **首启配置渲染 + 密钥生成**:仅当 settings.yml 不存在时,从模板拷贝并把 `ultrasecretkey` 换成随机值(entrypoint.sh:84-94)。
3. **启动 Granian**:`granian searx.webapp:app`,端口由 `SEARXNG_PORT`→`GRANIAN_PORT` 别名(entrypoint.sh:115-117)。

### 问题 3:镜像名与 tag 策略、如何固定版本

- **镜像名**:`docker.io/searxng/searxng`(compose 行 9)。官方镜像同时镜像到 **DockerHub**(`hub.docker.com/r/searxng/searxng`)和 **GHCR**(`ghcr.io/searxng/searxng`,installation-docker.rst:10-11)。文档提醒 DockerHub 对匿名拉取有速率限制,可改用 GHCR(installation-docker.rst:62-70)。
- **Tag 策略**:默认 `${SEARXNG_VERSION:-latest}`。可固定的具体 tag 形如 `2026.3.25-541c6c3cb`(.env.example:8)或 `2026.6.19-93f66bfb4`(installation-docker.rst:287),即 **`日期-短commit哈希`** 的滚动发布(SearXNG 是 rolling release,无语义化版本)。
- **如何固定版本**:在 `.env` 里设 `SEARXNG_VERSION=2026.x.x-xxxxxxxxx`(取代 `latest`)。升级流程(installation-docker.rst:137-143):`docker compose down && docker compose pull && docker compose up -d`。

### 问题 4:需要持久化哪些卷

官方 "Volumes" 段(installation-docker.rst:232-240)明确两个:

1. **`/etc/searxng`** —— 配置(`settings.yml`、`limiter.toml`、`favicons.toml` 等)。compose 用 bind mount `./core-config/`(行 15),`__SEARXNG_CONFIG_PATH`。
2. **`/var/cache/searxng`** —— 持久数据(如 `faviconcache.db`)。compose 用具名卷 `core-data`(行 16),`__SEARXNG_DATA_PATH`。

Dockerfile 里对这两个路径声明了 `VOLUME`(dist.dockerfile:39-40)。
第三个(可选):**valkey 的 `/data`** —— 只有真用 valkey(limiter 等)才需要持久化,否则丢了也无所谓(缓存性质)。

### 问题 5:适配我们场景的部署草稿 + 注意事项

我们的场景:单机 VPS + Caddy 反代 + **SearXNG 仅内网暴露给 FastAPI 网关**。要点:

- **不对外暴露 SearXNG 端口**,只让同一 compose/docker 网络里的 FastAPI 容器访问;或绑 `127.0.0.1`。
- **必须开 JSON 输出**:默认 `search.formats: [html]`(settings.yml:85-86),**不含 json**,直接调 `/search?format=json` 会被拒。这是 P1 的头号前置条件。
- limiter 默认关(settings.yml:97)。内网可信调用方,**建议保持 limiter 关闭**,这样可以 **不部署 valkey**,简化到单容器。若以后要开 limiter 再加回 valkey。
- Granian 容器内绑 `GRANIAN_HOST="::"`(dist.dockerfile:31),即容器内监听所有接口;`server.bind_address`(settings.yml 默认 127.0.0.1)对 Granian **无效**,别在那儿改。对外是否可达完全由 compose `ports` 决定。

草稿(不部署 valkey、SearXNG 不发布端口、与网关同网络):

```yaml
# docker-compose.yml(我们的部署,原版镜像一行源码不改)
name: hollow

services:
  searxng:
    container_name: hollow-searxng
    image: docker.io/searxng/searxng:2026.x.x-xxxxxxxxx  # 固定 tag,别用 latest
    restart: unless-stopped
    # 关键:不写 ports:,不发布到宿主。仅同网络内的 gateway 能访问 http://searxng:8080
    expose:
      - "8080"
    volumes:
      - ./core-config/:/etc/searxng/:Z          # 放我们改过的 settings.yml(开 json)
      - searxng-cache:/var/cache/searxng/
    environment:
      - SEARXNG_BASE_URL=http://searxng:8080/    # 供内部生成链接;不确定是否必需(见待确认)
      # - SEARXNG_LIMITER=false                  # 默认已 false
      # 如需限制 Granian 并发,可加 GRANIAN_WORKERS / GRANIAN_BLOCKING_THREADS
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/healthz"]  # /healthz 见 webapp.py:597
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

  gateway:                                       # 我们的 FastAPI 网关
    build: ./gateway
    depends_on:
      searxng:
        condition: service_healthy
    # 只有 gateway 由 Caddy 反代对外;searxng 永不对外
    expose:
      - "8000"

volumes:
  searxng-cache:
```

`core-config/settings.yml` 里必须至少覆盖(基于 `use_default_settings: true`):

```yaml
use_default_settings: true
server:
  secret_key: "<自己生成的强随机>"   # 挂载了自己的文件后 entrypoint 不会再帮你随机化
  limiter: false
search:
  formats:
    - html
    - json                          # ★ P1 必需,否则 format=json 被拒
```

**内存 / worker 注意事项**:
- 官方基础镜像未在本快照内,`dist.dockerfile` **没有设 `GRANIAN_WORKERS`**,所以 worker 数取 Granian 自身默认(通常为 1)。已设的相关参数只有 `GRANIAN_BLOCKING_THREADS="4"`、`GRANIAN_WORKERS_KILL_TIMEOUT="30s"`、`GRANIAN_BLOCKING_THREADS_IDLE_TIMEOUT="5m"`(dist.dockerfile:34-36)。
- 官方 **强烈建议不要改 worker 数**:installation-granian.rst:42-45 "It's not advised to modify the amount of workers, expect increased resource usage and potential issues with botdetection";installation-docker 的环境变量说明也把 `$GRANIAN_*` 指向 Granian 配置。SearXNG 大量用协程做并发外呼引擎,单 worker + 多 blocking thread 已够,盲目加 worker 会翻倍内存并可能破坏 botdetection 的令牌一致性。
- 小 VPS 上 SearXNG 单容器常驻内存量级为几十~一百多 MB(镜像 dist 层约 265 MB,base 约 143 MB,见 installation-docker.rst:287-291);不开 valkey 可再省一个容器。**结论:单机 VPS 默认单 worker 别动,内存主要吃在并发引擎抓取上,受 count/engines 数量影响。**

---

## 对 P1 的影响与行动建议

1. **部署形态**:用官方 `docker.io/searxng/searxng` 镜像,固定 `日期-短哈希` tag(禁用 `latest`),与 FastAPI 网关放同一 compose 网络;SearXNG **不写 `ports:` / 不发布宿主端口**,网关经 `http://searxng:8080` 内部访问。只有网关经 Caddy 对外。
2. **开 JSON 是硬前置**:在挂载的 `settings.yml` 里把 `search.formats` 加上 `json`(默认只有 html,settings.yml:85-86),否则 P1 的 `POST /v0/search` 底层拿不到 JSON。
3. **可以不部署 valkey**:limiter 默认 false 且我们是内网可信调用,砍掉 valkey 服务简化为单容器;待将来要 bot 防护/缓存再加。
4. **secret_key 自管**:一旦我们挂自己的 settings.yml,entrypoint 的随机化逻辑不触发(只在文件不存在时跑,entrypoint.sh:84),必须自己填强随机 secret_key。
5. **healthcheck 用 `/healthz`**:官方 compose 没带,但应用有 `/healthz`(webapp.py:597-599),我们在自己的 compose 里加,并让网关 `depends_on: condition: service_healthy`,避免网关比 SearXNG 先就绪。
6. **worker 不要动**:保持 Granian 默认(单 worker + `GRANIAN_BLOCKING_THREADS=4`)。若压测发现并发不足,优先调 blocking threads,谨慎加 worker(会掉 botdetection、翻内存)。
7. **method 默认 POST**:`server.method: "POST"`(settings.yml)。我们从后端直接构造请求,GET/POST 都行;若走 GET 注意 URL 长度,必要时在 settings 设 `method: "GET"`。
8. **升级纪律**:固定 tag + `down/pull/up` 流程;升级前 review 新版 `docker-compose.yml` / `.env.example` 差异(官方在 installation-docker.rst:122-135 明确提醒模板会变)。

---

## 待实测确认的问题

1. **`GRANIAN_WORKERS` 的实际默认值**:本快照的 `dist.dockerfile` 没设它,基础镜像 `searxng/base:searxng` 不在快照内,无法从源码确证默认 worker 数(推断为 Granian 官方默认 1,但需 `docker inspect` 或容器内 `env | grep GRANIAN` 实测)。
2. **`__SEARXNG_CONFIG_PATH` / `__SEARXNG_DATA_PATH` 的确切取值**:注释说"defined in base images"(dist.dockerfile:38),源码里查不到;从 compose 挂载和文档推断为 `/etc/searxng` 与 `/var/cache/searxng`,需容器内实测确认。
3. **内网调用是否会被 limiter/botdetection 拦**:limiter 默认关时应无影响;但若误开 `public_instance: true` 或 limiter,内部 FastAPI 的请求(无浏览器 cookie/link_token)可能被判为 bot。需实测确认我们的调用路径不触发 botdetection。
4. **`SEARXNG_BASE_URL` 是否为 JSON API 必需**:纯 JSON 消费(我们只取结构化字段)可能不需要正确 base_url;但缩略图/图片代理、分页 URL 等会受影响。需实测 `format=json` 在 `base_url: false` 下是否报错或产出坏链接。
5. **JSON API 是否还需额外放开(如 limiter 对 format 的限制)**:确认仅 `search.formats` 加 `json` 就够,还是 public_instance 模式下 JSON 会被额外封禁(部分版本对公共实例禁 json)。需在我们固定的镜像 tag 上实测。
6. **健康探针镜像内是否有 `wget`/`curl`**:草稿里的 healthcheck 依赖容器内有 `wget`;base 镜像是否自带需实测,否则改用 Granian/内置探测方式。
