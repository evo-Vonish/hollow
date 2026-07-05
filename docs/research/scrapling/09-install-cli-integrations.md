# 09 · 安装 / CLI / 集成 / MCP 子系统研究笔记

> 研读对象:Scrapling v0.4.10(`/home/user/hollow/vendor/scrapling/`,未修改快照)
> 负责范围:`scrapling/cli.py`、`pyproject.toml`(extras)、`scrapling/integrations/`、`scrapling/core/ai.py`、`docs/cli/`、`docs/ai/`、`docs/integrations/`、README 安装段
> 写给以后维护我们 FastAPI 抓取封装的自己。结论均标注源码出处;源码与文档冲突时以源码为准。

---

## 一、职责概述

这个子系统是 Scrapling 的"外壳层",跟 P2 的核心抓取逻辑(三档升级链)没有直接耦合,但决定了我们**怎么装、怎么把浏览器跑起来、能不能复用官方的并发抓取封装**。四块内容:

- **安装 / extras**(`pyproject.toml`):决定 `pip install` 装什么,哪些 extra 才有 `Fetcher/DynamicFetcher/StealthyFetcher`。
- **CLI**(`scrapling/cli.py`):`scrapling install / shell / extract / mcp` 四组命令。其中 `scrapling install` 是**部署时必须跑的一步**——它下载 Chromium 并装系统依赖。
- **集成**(`scrapling/integrations/scrapy.py`):只有 Scrapy 适配器,与 P2 无关。
- **MCP / AI 封装**(`scrapling/core/ai.py`):`ScraplingMCPServer` 里的 `bulk_get / bulk_fetch / bulk_stealthy_fetch` **正是 P2 想要的"并发 + 会话复用 + 三种引擎统一封装"的参考实现**,重点研读对象。

---

## 二、关键 API / 参数详解

### 2.1 extras 定义(`pyproject.toml:72-96`)

```
version = "0.4.10"                       # 静态版本(pyproject.toml:8)

dependencies (核心, 裸装 pip install scrapling):
    lxml>=6.1.1, cssselect>=1.4.0, orjson>=3.11.8, tld>=0.13.2,
    w3lib>=2.4.1, typing_extensions        # 只有解析引擎,没有任何 fetcher

[optional-dependencies]
fetchers = [
    click>=8.3.0,
    curl_cffi>=0.15.0,                     # Fetcher(静态 HTTP + TLS 指纹)底层
    playwright==1.61.0,                    # ← 精确 pin!DynamicFetcher 底层
    patchright==1.61.1,                    # ← 精确 pin!StealthyFetcher 底层(打补丁的 playwright)
    browserforge>=1.2.4,                   # 指纹生成
    apify-fingerprint-datapoints>=0.13.0,  # 指纹数据
    msgspec>=0.21.1, anyio>=4.13.0, protego>=0.6.2,
]
ai      = [ mcp>=1.27.0, markdownify>=1.2.0, scrapling[fetchers] ]   # MCP server
shell   = [ IPython>=8.37, markdownify>=1.2.0, scrapling[fetchers] ] # 交互 shell + extract 命令
all     = [ scrapling[ai,shell] ]
```

要点:
- **裸 `pip install scrapling` 只装解析器**,`import scrapling.fetchers` 会直接 `ModuleNotFoundError`(README:485 明确写了)。
- `fetchers` 是我们唯一必须的 extra。`ai`/`shell` 都 `include scrapling[fetchers]`,是超集。
- `playwright==1.61.0` 和 `patchright==1.61.1` 是**精确等号 pin**,不是 `>=`。这对浏览器二进制版本匹配至关重要(见问题 4)。
- `requires-python = ">=3.10"`(`pyproject.toml:35`),官方 Docker 用 `python:3.12-slim-trixie`。
- 入口点:`scrapling = "scrapling.cli:main"`(`pyproject.toml`,`[project.scripts]`)。

### 2.2 `scrapling install` 到底干了什么(`cli.py:109-141`)

```python
def install(force):
    if force or not __PACKAGE_DIR__.joinpath(".scrapling_dependencies_installed").exists():
        __Execute([python_executable, "-m", "playwright", "install", "chromium"], ...)       # 下载 Chromium 二进制
        __Execute([python_executable, "-m", "playwright", "install-deps", "chromium"], ...)   # 装系统 .so 依赖(apt)
        from tld.utils import update_tld_names
        update_tld_names(fail_silently=True)                                                  # 更新 TLD 列表
        __PACKAGE_DIR__.joinpath(".scrapling_dependencies_installed").touch()                 # 落一个哨兵文件
    else:
        print("The dependencies are already installed")
```

- **只下 `chromium`**,不下 firefox/webkit(尽管 docs/cli/overview.md 说 "downloads all browsers",这是文档夸大,以源码为准——只有 chromium)。
- `playwright install-deps chromium`(`cli.py:125-133`)内部会跑 `apt-get install`,**需要 root / sudo,且只支持 Debian/Ubuntu 系**。
- 幂等靠哨兵文件 `scrapling/.scrapling_dependencies_installed`(`cli.py:120,139`)。`--force` 跳过检查重装(`cli.py:110-118`)。
- **坑**:哨兵文件写在 **site-packages 里的包目录**(`__PACKAGE_DIR__ = Path(__file__).parent`,`cli.py:21`)。如果容器里用只读文件系统或多阶段构建换了层,哨兵可能丢失或不可写。
- 代码级等价调用(README:499-504):`from scrapling.cli import install; install([], standalone_mode=False)`。

### 2.3 CLI 命令树(`cli.py:659-669`)

`main`(group)挂了 4 个:

| 命令 | 出处 | 作用 | extra 需求 |
|------|------|------|-----------|
| `scrapling install [-f/--force]` | `cli.py:109` | 下 Chromium + 系统依赖 | fetchers |
| `scrapling shell [-c code] [-L level]` | `cli.py:173` | IPython 交互抓取台 | shell |
| `scrapling extract {get,post,put,delete,fetch,stealthy_fetch}` | `cli.py:199,361-656` | 命令行抓取存文件 | shell(用到 markdownify/Convertor) |
| `scrapling mcp [--http] [--host] [--port] [--executable-path]` | `cli.py:144-170` | 起 MCP server | ai |

`extract` 子命令与 P2 的对应关系:
- `extract get/post/put/delete` → `Fetcher.<method>`(`cli.py:352-358`,静态 HTTP)
- `extract fetch` → `DynamicFetcher.fetch`(`cli.py:583-585`)
- `extract stealthy_fetch` → `StealthyFetcher.fetch`(`cli.py:654-656`)

浏览器类命令的默认值(`_common_browser_options`,`cli.py:266-334`):`--timeout` 默认 **30000 毫秒**、`--headless` 默认 True、`--network-idle` 默认 False、`--disable-resources` 默认 False、`--block-ads` 默认 False。
`stealthy_fetch` 独有开关(`cli.py:591-606`):`--solve-cloudflare/--no-solve-cloudflare` **默认 False**、`--block-webrtc` 默认 False、`--allow-webgl` 默认 True、`--hide-canvas` 默认 False。

> 注意:静态 HTTP 命令的 `--timeout` 单位是**秒**(默认 30,`cli.py:252`),浏览器命令的 `--timeout` 单位是**毫秒**(默认 30000,`cli.py:301-305`)。别混。

### 2.4 MCP / AI 封装(`scrapling/core/ai.py`)—— P2 最该抄的部分

`ScraplingMCPServer`(`ai.py:109`)对外暴露 10 个工具,内部实现就是对三种 fetcher 的**并发批量封装**,和 P2 网关要做的事高度重合:

- `bulk_get`(`ai.py:417-494`):`async with FetcherSession() as session:` → `[session.get(url, ...) for url in urls]` → `await gather(*tasks)`。静态 HTTP 并发。
- `bulk_fetch`(`ai.py:581-683`):无 session 时 `async with AsyncDynamicSession(..., max_pages=len(urls), block_ads=True, ...)` → `[session.fetch(url) for url in urls]` → `gather`。
- `bulk_stealthy_fetch`(`ai.py:785-902`):`async with AsyncStealthySession(..., solve_cloudflare=..., block_ads=True, ...)` → `gather`。
- `get/fetch/stealthy_fetch`(单 URL)只是 `bulk_*(urls=[url])[0]` 的薄包装(`ai.py:394-415, 555-579, 754-783`)。
- 会话管理 `open_session/close_session/list_sessions`(`ai.py:137-278`):把 `AsyncDynamicSession/AsyncStealthySession` 存进 `self._sessions` dict 复用,`_get_session` 会校验 `session._is_alive`(`ai.py:123-135`)。

**并发是怎么控的**(关键):Scrapling 的浏览器会话不是靠外部信号量,而是靠 `max_pages` 参数在 `PagePool` 里限并发页数:
- `PagePool`(`_page.py:44-61`):超过 `max_pages` 时 **直接 `raise RuntimeError(f"Maximum page limit ({self.max_pages}) reached")`**,不是排队等待。
- 配置层 `max_pages` **默认 1**(`_validators.py:62`)。MCP 的 `bulk_fetch` 里显式设 `max_pages=len(urls)`(`ai.py:668`),即"有几个 URL 开几个页"。
- 结论:**Scrapling 自己不排队、不限内存**。P2 想把浏览器实例池压到 2-3、想要"超过就等"而不是"超过就抛",必须**在我们网关侧自己加 `asyncio.Semaphore`**——不能指望 `max_pages`。

`_translate_response`(`ai.py:77-92`)会把 `Response` 交给 `Convertor._extract_content` 转 markdown/html/text。**P2 不要用这一步**——我们要的是 raw HTML 交给 P3 的 trafilatura。直接取 `response.html_content` / `response.body`,绕开 Convertor。

### 2.5 Scrapy 集成(`integrations/scrapy.py`)

`scrapling_response` 装饰器 + `convert_response`,把 Scrapy 的 Response 转成 Scrapling 的 Response。**与 P2 完全无关**,我们不用 Scrapy。唯一可借鉴点:`convert_response`(`scrapy.py:24-56`)演示了如何**手工构造一个 `scrapling.engines.toolbelt.custom.Response`**(传 url/content/status/reason/cookies/headers/request_headers/encoding/method/meta)。如果 P2 要为失败 URL 造占位 Response 对象,这是构造签名的参考;但更简单的做法是我们自己定义 Pydantic 出参模型,不必复用它的 Response。

---

## 三、逐问题解答

### 问题 1:安装方式与 extras——`pip install "scrapling[fetchers]"` 装了什么?`scrapling install` 干了什么?

- `pip install "scrapling[fetchers]"` 在核心解析依赖之上,额外装:`click`、`curl_cffi`、**`playwright==1.61.0`**、**`patchright==1.61.1`**、`browserforge`、`apify-fingerprint-datapoints`、`msgspec`、`anyio`、`protego`(`pyproject.toml:73-83`)。这一步**只装 Python 包,不下浏览器二进制**。
- `scrapling install`(`cli.py:119`)才下浏览器:跑 `playwright install chromium`(下 Chromium 二进制)+ `playwright install-deps chromium`(apt 装系统 `.so`)+ 更新 TLD 名单,然后落哨兵文件。**只装 chromium,不装 firefox/webkit**(源码 `cli.py:122`,与 docs 措辞"all browsers"不符,以源码为准)。
- 对 P2:我们需要三种 fetcher(静态 + 动态 + 隐身),`[fetchers]` 就够了。**不需要 `[ai]` 或 `[shell]`**,除非想复用 MCP 的封装或 `extract` CLI。

### 问题 2:无 GUI Linux VPS 部署浏览器的依赖与坑

- **必须跑 `scrapling install`**(或等价的 `playwright install chromium` + `playwright install-deps chromium`),否则 `DynamicFetcher`/`StealthyFetcher` 在启动浏览器时报缺 `.so`。
- `playwright install-deps chromium`(`cli.py:125-133`)**要 root 且只认 Debian/Ubuntu 的 apt**。非 Debian 系(Alpine 等)会失败——官方 Dockerfile 就是用 `python:3.12-slim-trixie`(Debian)才能 `apt-get update && playwright install-deps`(`vendor/scrapling/Dockerfile:29-33`)。**别用 Alpine 基础镜像**(musl + 无 apt,playwright chromium 不支持)。
- headless:所有浏览器命令 `--headless` 默认 True(`cli.py:316-320`),无 GUI VPS 直接可用,不需要 xvfb。
- 内存:`StealthyFetcher`(patchright + 指纹)每实例数百 MB,与硬约束一致。Scrapling **不做内存/并发保护**(见 2.4),必须靠我们的信号量 + 实例池上限。
- 哨兵文件坑:`.scrapling_dependencies_installed` 写在 site-packages 包目录(`cli.py:120`)。容器里若浏览器缓存目录(`~/.cache/ms-playwright` 或 `PLAYWRIGHT_BROWSERS_PATH`)与哨兵所在层不一致,可能出现"哨兵在但二进制没了"→ `install` 误判已装。**构建时一次装好、别依赖运行时哨兵**最稳。
- `solve_cloudflare=True` 会把 timeout 强制抬到 ≥60000ms(`_validators.py:153-155`),VPS 上要给足内存和超时预算。

### 问题 3:CLI 提供哪些命令

见 2.3 表格。四组:`install`(装浏览器)、`shell`(IPython 台)、`extract`(命令行抓取存文件,6 个子命令)、`mcp`(起 MCP server)。P2 运行期**几乎不用 CLI**——只在**构建镜像时用一次 `scrapling install`**。`extract` 命令可作本地手工调试抓取的工具,但生产走 Python API。

### 问题 4:版本固定建议

- **pin `scrapling==0.4.10`**(与本快照一致,`pyproject.toml:8`)。Scrapling 处于 `Development Status :: 4 - Beta`(`pyproject.toml:38`),小版本间 API 可能动,精确 pin 最安全。
- 关键:`[fetchers]` 里 `playwright==1.61.0`、`patchright==1.61.1` 是 Scrapling **精确 pin** 的(`pyproject.toml:76-77`)。**浏览器二进制版本与 playwright 库版本强绑定**——`playwright install chromium` 下的 Chromium 修订号由 playwright 库版本决定。所以:
  - 不要单独升/降 playwright,让它跟着 `scrapling` 的 pin 走。
  - `scrapling install` 必须在 `pip install "scrapling[fetchers]==0.4.10"` **之后**跑,保证二进制匹配库。
  - 升级 Scrapling 时(改 0.4.10 → 新版)**必须重跑 `scrapling install --force`**,否则可能出现库版本升了、Chromium 二进制没换的错配。
- 建议在我们的依赖清单里写死:`scrapling[fetchers]==0.4.10`(见第四节)。

### 问题 5:MCP server / AI 集成是什么?有无可复用的抓取封装?

- **是什么**(P2 不用):`scrapling mcp` 起一个 FastMCP server(`ai.py:904-934`),把抓取能力暴露成 10 个 MCP 工具给 Claude 等 AI agent 用。stdio 或 streamable-http 传输(`cli.py:144-170`)。跟我们要做的 FastAPI 网关是**平行方案**,不是我们要的形态(它面向 LLM 工具调用,不是 HTTP JSON API)。
- **可复用的封装**(重点):`ai.py` 里的 `bulk_get / bulk_fetch / bulk_stealthy_fetch`(`ai.py:417, 581, 785)`是**三种引擎 + asyncio.gather 并发 + 会话复用**的现成范例。P2 的 `/v0/fetch` 本质就是"给一批 URL,按引擎并发抓、逐个返回结果",这三个方法就是模板。**但要注意它的取舍与 P2 不同**:
  1. 它用 `max_pages=len(urls)` 让所有 URL 同时开页(`ai.py:668`),**没有并发上限、没有信号量、超限直接 RuntimeError**——与 P2 硬约束冲突,不能照抄这一点。
  2. 它 `gather` 后不做 per-URL 超时 kill,一个 URL 卡住会拖到整体 timeout。
  3. 它把结果转成 markdown(`_translate_response`),P2 要 raw HTML,得绕开。
- 结论:**抄它的"async session + gather + block_ads=True"结构,但并发控制、per-URL 超时、失败占位、raw HTML 提取全部自己写**。

---

## 四、对 P2 的影响与行动建议

### 4.1 依赖清单(`requirements.txt` / `pyproject`)

```
# 只需要 fetchers,不需要 ai/shell(除非要复用 MCP 封装或 extract CLI)
scrapling[fetchers]==0.4.10
# 传递依赖已含 playwright==1.61.0 / patchright==1.61.1,不要自己再 pin playwright
```

### 4.2 Dockerfile 推荐写法(参考官方 `vendor/scrapling/Dockerfile`,改成我们网关的)

```dockerfile
# 必须 Debian 系(playwright install-deps 只认 apt);别用 Alpine
FROM python:3.12-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 把浏览器装到固定路径,避免运行时找不到 / 跨层丢失
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

WORKDIR /app

# 1) 先装 Python 依赖(利于层缓存)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt   # 含 scrapling[fetchers]==0.4.10 + 我们的 fastapi/uvicorn 等

# 2) 装 Chromium 二进制 + 系统依赖(构建期一次装好,别留给运行时哨兵)
#    等价于 `scrapling install`,但拆开写更透明、可控
RUN python -m playwright install-deps chromium \
    && python -m playwright install chromium

# 3) 拷我们的网关源码
COPY . .

# 4) 起 FastAPI 网关(不是 scrapling mcp)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

要点:
- 用 `python -m playwright install[-deps] chromium` 而非 `scrapling install`,好处是**不落哨兵文件、构建期确定性强、日志清晰**;`scrapling install` 的额外动作只是 `update_tld_names`(可选,`Fetcher` 用 tld 判域名,建议保留:构建期加一句 `python -c "from tld.utils import update_tld_names; update_tld_names(fail_silently=True)"`)。
- 设 `PLAYWRIGHT_BROWSERS_PATH` 固定浏览器路径,避免多阶段构建 / 只读根丢二进制。
- **只装 chromium**,和 Scrapling 内部一致,省空间。

### 4.3 P2 抓取封装的骨架(抄 MCP 结构,补齐 Scrapling 缺的三件事)

```python
import asyncio
from datetime import datetime, timezone
from scrapling.fetchers import Fetcher, AsyncDynamicSession, AsyncStealthySession

# 硬约束:浏览器实例池上限 2-3,自己用信号量控,不靠 max_pages
BROWSER_SEM = asyncio.Semaphore(2)          # StealthyFetcher 数百 MB/实例
PER_URL_TIMEOUT = 30.0                        # 秒;solve_cloudflare 时需 >=60(见 _validators.py:154)

async def fetch_one(url: str) -> dict:
    fetched_at = datetime.now(timezone.utc).isoformat()
    # 第 1 档:静态 HTTP(curl_cffi,轻量,无需信号量占浏览器名额)
    try:
        resp = await asyncio.wait_for(
            Fetcher.async_get(url, stealthy_headers=True, timeout=int(PER_URL_TIMEOUT)),
            timeout=PER_URL_TIMEOUT,
        )
        html = resp.html_content            # 取 raw HTML 交给 P3 trafilatura,别用 Convertor
        if resp.status == 200 and _long_enough(html):
            return _ok(url, html, "fetcher", resp.status, fetched_at)
        blocked = _looks_blocked(resp)      # Cloudflare/403/429 → 直接跳隐身档
    except asyncio.TimeoutError:
        return _timeout(url, "fetcher", fetched_at)
    except Exception:
        blocked = False

    # 第 2/3 档:浏览器,过信号量 + per-URL 超时
    async with BROWSER_SEM:
        Session = AsyncStealthySession if blocked else AsyncDynamicSession
        engine = "stealthy" if blocked else "dynamic"
        try:
            async with Session(max_pages=1, block_ads=True, headless=True,
                               solve_cloudflare=blocked) as s:
                page = await asyncio.wait_for(s.fetch(url), timeout=PER_URL_TIMEOUT)
                return _ok(url, page.html_content, engine, page.status, fetched_at)
        except asyncio.TimeoutError:
            return _timeout(url, engine, fetched_at)      # wait_for 取消任务,session 退出 __aexit__ 回收浏览器
        except Exception as e:
            return _failed(url, engine, str(e), fetched_at)

async def fetch_batch(urls: list[str]) -> list[dict]:
    # 逐 URL 独立、失败不影响他人;gather 收集全部占位结果,禁止静默丢弃
    return await asyncio.gather(*(fetch_one(u) for u in urls))
```

> 注意:上面 `Fetcher.async_get` / `page.html_content` / `resp.status` / `AsyncStealthySession.fetch` 的**确切签名与属性名不属于本子系统研读范围**(属于 fetcher 子系统 03/04/05 号笔记),这里按 MCP `ai.py` 的调用姿势推断,**落地前必须以 fetcher 子系统的结论为准**。本笔记确定的是:(a) 用信号量而非 max_pages 控并发;(b) 用 `asyncio.wait_for` 做 per-URL 超时,靠 `async with Session` 的 `__aexit__` 回收浏览器;(c) 取 raw HTML 绕开 Convertor;(d) `block_ads=True` 抄 MCP。

### 4.4 关键取舍清单

| Scrapling 默认 / 行为 | P2 该怎么做 | 出处 |
|----|----|----|
| `max_pages` 默认 1,超限抛 RuntimeError,不排队 | 网关侧加 `asyncio.Semaphore(2~3)`,不靠 max_pages 排队 | `_validators.py:62`, `_page.py:60-61` |
| MCP `bulk_fetch` 设 `max_pages=len(urls)` 全开 | 不照抄;单会话 `max_pages=1` + 信号量控实例数 | `ai.py:668` |
| `solve_cloudflare` 默认 False | 第 3 档隐身遇 CF 才置 True;注意会强制 timeout≥60s | `_validators.py:148,154`; `cli.py:596`; `ai.py:711` |
| `block_ads` 默认 False,MCP 里置 True | 抄 MCP:浏览器档 `block_ads=True` 省流量省内存 | `ai.py:667,882` |
| MCP `_translate_response` 转 markdown | 绕开,直接取 raw HTML 给 P3 | `ai.py:77-92` |
| `scrapling install` 落哨兵、含 apt | 构建期直接 `playwright install[-deps] chromium`,别依赖运行时哨兵 | `cli.py:119-139` |

---

## 五、待实测 / 待跨子系统确认的问题

1. `Fetcher` 是否有 `async_get`、异步签名和返回对象上 raw HTML 的确切属性名(`.html_content`? `.body`? `.content`?)——属 fetcher 子系统,落地前核对。
2. `AsyncStealthySession.fetch` / `AsyncDynamicSession.fetch` 单次 fetch 的确切参数(是否支持传 per-call timeout、`solve_cloudflare` 能否在 `fetch()` 而非构造器上传)——MCP 里 session 复用时是在 `fetch()` 上传 `solve_cloudflare`(`ai.py:868`),而无 session 时在构造器传(`ai.py:895`),两处不一致,需确认哪条路径适合 P2。
3. `asyncio.wait_for` 取消 `session.fetch` 后,`async with AsyncStealthySession` 的 `__aexit__` 能否**可靠 kill 底层 Chromium 进程**、有没有僵尸进程残留——硬约束"超时杀进程回收"的核心,**必须实测**(起 N 个会卡的 URL,看 `ps`/内存是否回落)。
4. `scrapling install` 只装 chromium,但 `StealthyFetcher` 底层是 `patchright`(打补丁的 playwright)——`patchright` 用的是不是 `playwright install chromium` 下的同一份 Chromium 二进制?若 patchright 需要单独的 `patchright install chromium`,官方 `install` 命令没跑这一步,**需实测隐身档能否直接启动**。(源码 `cli.py:122` 只跑了 `playwright ... chromium`,未见 patchright install。)
5. 非 Debian 宿主 / K8s 无 apt 环境下的部署路径(官方强绑 Debian + apt),若我们的目标 VPS 不是 Debian 系需另找方案。
6. `.scrapling_dependencies_installed` 哨兵写在 site-packages,只读根文件系统容器里 `scrapling install` 是否会因无法 touch 而报错——若用我们推荐的"直接 playwright install"则规避,但若有代码路径调用了 `scrapling.cli.install` 需注意。
