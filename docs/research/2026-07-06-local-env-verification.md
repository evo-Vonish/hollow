# 本地环境验证 · Windows 11 · 2026-07-06

> 承接 `docs/memory/2026-07-05-session-01.md` §四标注的 ★ 项:云沙箱(数据中心 IP + egress 代理)
> 的实测结论切换到本地(中国家用宽带 + 本机代理 127.0.0.1:1088)后逐项重验。
> 本机环境:Windows 11、Python 3.12.10、系统代理开启(ProxyEnable=1,env HTTP_PROXY/HTTPS_PROXY 全局设置)。

## 一、结论速览

| ★ 待重验项 | 沙箱结论 | 本地结论 |
|---|---|---|
| Scrapling 静态档 impersonate | 须 `impersonate=None`(代理重置 TLS 指纹) | **恢复 `impersonate="chrome"`,代理隧道与直连都正常** |
| Scrapling 浏览器档 | Chromium 版本不匹配,推迟 | 见 §五 |
| 引擎可用性 | brave/startpage/mojeek 封数据中心 IP;quark 挂 | **quark/brave/mojeek 复活;被墙引擎须走代理**(§三) |

新增两个 Windows 特有事实(§二)和一个上游快照事实:**经典 `google` 引擎在 vendor 快照中已不存在**,只剩 `google cse`(需密钥)。引擎注册表 `data/engine_registry.yaml` 后续需体现。

## 二、SearXNG 在 Windows 直跑:两个坑 + 修法(vendor 零改动)

1. **`import pwd` 崩溃**:`searx/valkeydb.py:22` 模块级导入 Unix-only 的 `pwd`(仅在 valkey 连接失败的日志路径用到,我们不配 valkey 永远走不到)。修法:`compat/win/pwd.py` 桩模块,PYTHONPATH 前置。
2. **bilibili 引擎加载失败**:`searx/engines/bilibili.py:43` 模块级 `ZoneInfo("Asia/Shanghai")`,Windows 无系统时区库 → `pip install tzdata` 即修复(已加装,修复后 bilibili 返 20 条)。

启动命令(Windows):

```powershell
$env:SEARXNG_SETTINGS_PATH = "F:\Projects\HOLLOW\searxng\settings.yml"
$env:PYTHONPATH = "F:\Projects\HOLLOW\compat\win;F:\Projects\HOLLOW\vendor\searxng"
.venv-searx\Scripts\python.exe -m searx.webapp   # → http://127.0.0.1:8888/healthz
```

venv:`.venv-searx` = `vendor/searxng/requirements.txt` + `tzdata`。

引擎注册对账:343(注册表)− 62(上游 inactive)− 3(加载失败:ahmia/torch 依赖 onion 环境、bilibili 已修复)= 278 = `/config` 实测注册数。✔ 数字全对上。

## 三、引擎可用性矩阵(q=deep learning,engines=单引擎)

| 引擎 | 沙箱(数据中心 IP) | 本地直连 | 本地经代理(all://127.0.0.1:1088) |
|---|---|---|---|
| arxiv | ✅ | ✅ 10 条 | ✅ 10 条 |
| baidu | ✅ | ✅ 9 条 | ✅ 9 条 |
| sogou | ✅ | ✅ 10 条 | ✅ 10 条 |
| quark | ❌ HTTP error | **✅ 9 条(复活)** | ✅ 9 条 |
| bilibili | ✅ | ❌ tzdata → 修复 | ✅ 20 条 |
| wikipedia | ✅ | ❌ timeout(被墙) | ✅ infobox 1 条(该引擎产出走 infoboxes 字段,不是 results) |
| duckduckgo | ✅ | ❌ timeout(被墙) | ✅ 10 条 |
| bing | ✅ | ⚠️ 0 条无报错(疑似空页/验证页,**静默失败案例**) | ✅ 10 条 |
| brave | ❌ too many requests | ❌ timeout | **✅ 20 条(复活)** |
| mojeek | ❌ access denied | ❌ timeout | **✅ 10 条(复活)** |
| startpage | ❌ access denied | ❌ timeout | ❌ parsing error(疑似验证页,双环境全挂,建议留 L2 观察) |
| google | — | **引擎已不存在**(快照中仅剩 google cse,需密钥) | — |

关键判断:

- **代理是本地环境的必选项**:被墙引擎(wikipedia/ddg/brave/mojeek/bing)直连全灭,经代理全部复活。
- **无需引擎级拆分路由**:国内源经 `all://` 代理依然正常(本机代理自带分流规则),SearXNG 配一个全局 proxies 即可。配置模板已注释在 `searxng/settings.yml` 的 outgoing 段。
- `engines=` 参数可激活 enabled=False 的引擎(baidu/quark 默认禁用但点名即用)——网关按场景点名引擎的设计可行。
- bing 直连"0 条无报错"是**静默丢弃的活样本**:证明网关必须做"请求集−结果集−失败集"差集对账(项目底线 2),不能只信 unresponsive_engines。

## 四、Scrapling 静态档(curl_cffi 0.15.0,与沙箱同版)

| 场景 | 结果 |
|---|---|
| arxiv,经代理,impersonate=chrome | ✅ 200,43654B,净化 3190 字符,0.9s |
| wikipedia(被墙),经代理,impersonate=chrome | ✅ 200,1.3MB,净化 121753 字符,11.0s |
| arxiv,直连,impersonate=chrome | ✅ 200,0.4s |
| baidu 搜索结果页,直连,impersonate=chrome | ✅ 200,1.1MB,1.2s |

- **沙箱的 impersonate 限制确认消失**:本机代理是 CONNECT 隧道,TLS 指纹端到端保留,`impersonate="chrome"` 全场景可用。**API 代码按默认 chrome 写**。
- trafilatura 2.1.0 直接吃 `resp.body`(bytes)链路跑通。
- venv:`.venv-api` = `scrapling[fetchers]==0.4.10` + `trafilatura`。
- 静态档走 env 代理(curl_cffi trust_env),清掉 env 即直连——网关后续若要"国内直连/国外走代理"可在请求级控制,当前无必要。

## 五、Scrapling 浏览器档(playwright 1.61.0 + chromium)

`python -m playwright install chromium`(经代理下载)后 DynamicFetcher 直接可用,**沙箱的版本不匹配/代理不通问题在本地不存在**:

| 场景 | 结果 |
|---|---|
| arxiv,headless chromium | ✅ 200,44314B,8.3s |
| wikipedia(被墙),headless chromium | ✅ 200,1.9MB,15.9s(chromium 自动走系统代理) |

- timeout 单位按记忆确认:浏览器档毫秒(本测 60000ms)。
- 隐身档(StealthyFetcher/camoufox)未装未测——MVP 不需要,要用时 `scrapling install` 补装即可(下载量大,到时再说)。

## 六、对 P1 动手的影响

1. `api/searx_client.py` 可直接开写:本地 SearXNG 已在 Windows 跑通,JSON 契约与沙箱实测一致(7 键、unresponsive 序列化、infoboxes 通道)。
2. 默认引擎集建议(待拍板项 #7)有了本地数据:`[duckduckgo, wikipedia, arxiv]` 在"代理开启"前提下全部可用;若要不依赖代理,国内可用集是 `[baidu, sogou, quark, bilibili, arxiv]`。
3. 部署前提写进文档:本地开发需系统代理(或 settings.yml 解开 proxies 注释);无代理时被墙引擎必须从默认集剔除,否则每次请求白等 5s 超时。
