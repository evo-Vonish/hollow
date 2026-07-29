# -*- coding: utf-8 -*-
"""环境配置(docs/design/03 §5)。全部可用环境变量覆盖,默认值面向本地直跑。"""
import os


def _env_str(name: str, default: str) -> str:
    # 空串视同未设置:编排工具(Docker -e X= / systemd Environment=X=)常注入空值
    raw = os.environ.get(name)
    return raw.strip() if raw and raw.strip() else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.strip() else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw and raw.strip() else default


SEARXNG_URL: str = _env_str("SEARXNG_URL", "http://127.0.0.1:8888")

# 可观测性(2026-07-15,生产就绪批):应用日志级别。run_local 会把 stdout/stderr 落成可见日志文件。
LOG_LEVEL: str = _env_str("HOLLOW_LOG_LEVEL", "INFO")

# 查询串长度上限(OpenAPI/契约收尾):防超长 query 浪费上游/被滥用。SearXNG 实际也不吃超长。
QUERY_MAX_LEN: int = _env_int("HOLLOW_QUERY_MAX_LEN", 500)

# OpenAI 兼容层(/v1/*)的 Bearer 鉴权;空 = 不校验(本机开发)。/v0 不受影响。
API_KEY: str = _env_str("HOLLOW_API_KEY", "")

# SearXNG 搜索调用超时(秒)。settings.yml max_request_timeout=15,留余量。
SEARCH_TIMEOUT: float = _env_float("SEARCH_TIMEOUT", 20.0)

# 资源治理(2026-07-15,生产就绪批 #2):防自伤 DoS 与尾延迟放大。
# 同时打向 SearXNG 的搜索数上限:此前零节流,12 并发就把某引擎打进 2min 全局熔断连累后续。
SEARX_MAX_CONCURRENCY: int = _env_int("HOLLOW_SEARX_MAX_CONCURRENCY", 4)
# 同时在飞的"重"端点(research/fetch/v0)上限;超限直接 429(shed load),而非让所有人一起变慢。
MAX_INFLIGHT_HEAVY: int = _env_int("HOLLOW_MAX_INFLIGHT_HEAVY", 8)
# SSE 心跳间隔(秒):事件间隔超过它就发一帧注释心跳,防反代把空闲连接掐断(生产就绪批 #6)。
SSE_HEARTBEAT_SECS: float = _env_float("HOLLOW_SSE_HEARTBEAT_SECS", 15.0)

# TLS 指纹模拟:本地/VPS 直连默认 chrome(2026-07-06 本地实测代理隧道下也可用);
# 特殊网络环境(如 MITM egress 代理)设 HOLLOW_IMPERSONATE=none 关闭。
_imp = _env_str("HOLLOW_IMPERSONATE", "chrome").strip().lower()
IMPERSONATE: str | None = None if _imp in ("", "none", "off") else _imp

FETCH_CONCURRENCY: int = _env_int("FETCH_CONCURRENCY", 5)
# 单请求可指定的并行抓取上限(请求级 concurrency 参数的 le 边界)
REQUEST_CONCURRENCY_MAX: int = 8

# ---- 浏览器升级链(2026-07-07 拍板:blocked+failed 触发,三档 static→dynamic→stealthy) ----
# 浏览器是重量级资源:进程级并发闸 + 专用线程池都用这个数
BROWSER_CONCURRENCY: int = _env_int("HOLLOW_BROWSER_CONCURRENCY", 2)
# 单请求可同时占用的浏览器升级数上限:必须 < 全局 BROWSER_CONCURRENCY 才真能防单请求垄断
# (审查 #3:此前默认 2 == 全局 2,防垄断形同虚设)。默认 1:一个请求一次只占一个浏览器槽,
# 第二个槽永远留给并发请求。
REQUEST_BROWSER_CONCURRENCY: int = _env_int("HOLLOW_REQUEST_BROWSER_CONCURRENCY", 1)
# 已知升档无益的强反爬域(2026-07-22 实测:机房 IP 信誉层拦截,浏览器指纹伪装救不回——
# 知乎 403×3 档、B站 412×3、头条系/百度百科空壳×3)。static 失败后对这些域跳过浏览器升级,
# 不白烧浏览器槽与 20-40s 预算;将来若接入住宅代理,可用环境变量清空本名单恢复升级。
ESCALATE_SKIP_DOMAINS: frozenset = frozenset(
    d.strip().lower()
    for d in (os.environ.get("HOLLOW_ESCALATE_SKIP_DOMAINS")
              or "zhihu.com,bilibili.com,toutiao.com,baike.baidu.com").split(",")
    if d.strip()
)
# ---- 延迟治理(2026-07-28 四项,实测驱动:dynamic 冷启动 14.7s、EN/dev 搜索长尾 3s) ----
# ① fetch LRU 缓存:同 URL 短 TTL 不重抓(热门页跨用户秒开);只缓存确定性结果(ok/no_content),
#   瞬时失败(failed/timeout/blocked)不缓存。进程内存放,重启即清。
FETCH_CACHE: bool = _env_bool("HOLLOW_FETCH_CACHE", True)
FETCH_CACHE_TTL: float = _env_float("HOLLOW_FETCH_CACHE_TTL", 300.0)
FETCH_CACHE_MAX: int = _env_int("HOLLOW_FETCH_CACHE_MAX", 512)
# ② 浏览器暖池:常驻 1 个 chromium(headless-shell, CDP 接入),dynamic 档省 2-4s 冷启动;
#   stealthy 低频兜底仍冷启动(常驻两实例内存不划算)。代理/绕行跟随 fetch 的 env 语义。
BROWSER_WARM: bool = _env_bool("HOLLOW_BROWSER_WARM", True)
BROWSER_WARM_PORT: int = _env_int("HOLLOW_BROWSER_WARM_PORT", 9223)
# ③ static 空壳域自适应跳过:同域连续 N 次 static 拿不到正文(no_content/blocked)后,
#   后续请求(升档开启时)跳过 static 直上浏览器,省白等;static 成功即清零。0=关闭。
STATIC_ADAPTIVE_SKIP: int = _env_int("HOLLOW_STATIC_ADAPTIVE_SKIP", 2)
# 浏览器档单 URL 超时(秒;Scrapling 浏览器 API 内部单位是毫秒,换算在 fetcher 里做)。
# dynamic 与 stealthy 同款:本版**不开 solve_cloudflare**(其内部无上限循环不可中断,
# 会导致线程泄漏——审查确认),故所有浏览器操作都受 playwright 自身 timeout 硬约束。
BROWSER_TIMEOUT: float = _env_float("HOLLOW_BROWSER_TIMEOUT", 30.0)
# 进程级抓取并发上限(跨请求共享),同时是专用抓取线程池的大小。
# 闸位与线程一一对应:拿到闸即有线程,wait_for 不会把线程池排队时间误算进抓取超时
# (审查发现:混用默认共享池时,高载下排队会被误判 timeout)。
FETCH_CONCURRENCY_GLOBAL: int = _env_int("FETCH_CONCURRENCY_GLOBAL", 16)
FETCH_TIMEOUT: float = _env_float("FETCH_TIMEOUT", 15.0)
FETCH_TOP_N_DEFAULT: int = 5
FETCH_TOP_N_MAX: int = 20  # 2026-07-06 拍板:8→20(场景多选并集召回更大,时间预算兜底)
FETCH_URLS_MAX: int = _env_int("HOLLOW_FETCH_URLS_MAX", 10)  # /v1/fetch 单次点名 URL 上限

# 页面资产抽取(2026-07-29;api/extractor.py):外链/媒体清单条数封顶。
# 抽取只在 include_links/include_media 显式开启时运行(默认关,不给载荷灌水)。
EXTRACT_LINKS_MAX: int = _env_int("HOLLOW_EXTRACT_LINKS_MAX", 100)
EXTRACT_MEDIA_MAX: int = _env_int("HOLLOW_EXTRACT_MEDIA_MAX", 50)

# 词汇重排权重(2026-07-14,搜索质量批;api/rerank.py)。标题命中远重于正文,
# SearXNG 原分只当兜底 prior。⚠️ 待校准(底线④):这组是起点,需按真实 query 调。
RERANK_W_TITLE: float = _env_float("HOLLOW_RERANK_W_TITLE", 3.0)
RERANK_W_SNIPPET: float = _env_float("HOLLOW_RERANK_W_SNIPPET", 1.0)
RERANK_W_PRIOR: float = _env_float("HOLLOW_RERANK_W_PRIOR", 0.5)

# highlights(2026-07-15,对齐 Exa):从净化正文抽 query 最相关的句子。纯词汇打分、无模型。
# ⚠️ 待校准(底线④):句长阈值与条数是起点,按真实正文调。
HIGHLIGHTS_MAX: int = _env_int("HOLLOW_HIGHLIGHTS_MAX", 3)           # 每条 item 最多几句高亮
HIGHLIGHT_MIN_CHARS: int = _env_int("HOLLOW_HIGHLIGHT_MIN_CHARS", 20)   # 太短的句(标题/碎片)不选
HIGHLIGHT_MAX_CHARS: int = _env_int("HOLLOW_HIGHLIGHT_MAX_CHARS", 400)  # 太长的段(未断开)不选

# 内容闸门(2026-07-07):净化出的正文低于此字符数 = 空壳/反爬页/无正文 → no_content。
# "成功"从"HTTP 2xx"重定义为"真拿到正文":no_content 不算 ok、不占结果位、触发升级链渲染。
# ⚠️ 阈值需校准后上线(底线4):200 是起点——空壳提取为空(远低于),真文章通常 500+,
# B站边栏/词典短条目在 200~500 之间是灰区,先从严还是从宽待实测调。
MIN_CONTENT_CHARS: int = _env_int("HOLLOW_MIN_CONTENT_CHARS", 200)

# ---- 抓取模式(2026-07-07 拍板:一根旋钮 速度/广度 ↔ 质量/难度) ----
# mode 是"预设":给 候选池倍数 / 升级链 / 单URL超时 设默认;显式传 escalate/timeout 仍覆盖。
# - fast    广度/速度:超召回(池 = top_n×3),先到先得凑够 top_n 条 ok 就砍其余,不升级,短超时
# - balanced默认(= 2026-07-07 前的行为):池 = top_n,爬完每条,升级链兜底,中超时
# - thorough质量/难度:池 = top_n,死磕每条,升级链全开,长超时
MODE_PRESETS: dict[str, dict] = {
    "fast":     {"pool_factor": 3, "escalate": False, "timeout": 8.0},
    "balanced": {"pool_factor": 1, "escalate": True,  "timeout": FETCH_TIMEOUT},
    "thorough": {"pool_factor": 1, "escalate": True,  "timeout": 30.0},
}
DEFAULT_MODE: str = "balanced"
POOL_MAX: int = 24  # 候选池封顶(fast 模式 top_n×factor 的上限)

# 抓取出站代理由 curl_cffi 的 trust_env(默认)从环境变量 HTTPS_PROXY/HTTP_PROXY 自动读取,
# 无需在此显式配置。(旧的 FETCH_PROXY 是死代码 + 旧的 FOLLOW_REDIRECTS 会被代理env静默关掉
# SSRF 防护——安全批已移除;重定向改由 fetcher 手动逐跳 + netguard 校验,见 api/netguard.py。)

# 安全批(2026-07-14):抓取目的地与体量硬约束
MAX_REDIRECTS: int = _env_int("HOLLOW_MAX_REDIRECTS", 5)          # 手动跟随的最大重定向跳数
MAX_FETCH_BYTES: int = _env_int("HOLLOW_MAX_FETCH_BYTES", 20_000_000)  # 单条响应体上限(20MB)

# 默认引擎集(2026-07-06 拍板:国际+国内"都来")。
# 前提:本地开发时系统代理开启,否则被墙引擎(duckduckgo/wikipedia)会白等超时,
# 见 docs/research/2026-07-06-local-env-verification.md §三。
DEFAULT_ENGINES: list[str] = [
    "duckduckgo",
    "wikipedia",
    "arxiv",
    "baidu",
    "sogou",
    "quark",
]
