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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw and raw.strip() else default


SEARXNG_URL: str = _env_str("SEARXNG_URL", "http://127.0.0.1:8888")

# OpenAI 兼容层(/v1/*)的 Bearer 鉴权;空 = 不校验(本机开发)。/v0 不受影响。
API_KEY: str = _env_str("HOLLOW_API_KEY", "")

# SearXNG 搜索调用超时(秒)。settings.yml max_request_timeout=15,留余量。
SEARCH_TIMEOUT: float = _env_float("SEARCH_TIMEOUT", 20.0)

# TLS 指纹模拟:本地/VPS 直连默认 chrome(2026-07-06 本地实测代理隧道下也可用);
# 特殊网络环境(如 MITM egress 代理)设 HOLLOW_IMPERSONATE=none 关闭。
_imp = _env_str("HOLLOW_IMPERSONATE", "chrome").strip().lower()
IMPERSONATE: str | None = None if _imp in ("", "none", "off") else _imp

FETCH_CONCURRENCY: int = _env_int("FETCH_CONCURRENCY", 5)
# 进程级抓取并发上限(跨请求共享),同时是专用抓取线程池的大小。
# 闸位与线程一一对应:拿到闸即有线程,wait_for 不会把线程池排队时间误算进抓取超时
# (审查发现:混用默认共享池时,高载下排队会被误判 timeout)。
FETCH_CONCURRENCY_GLOBAL: int = _env_int("FETCH_CONCURRENCY_GLOBAL", 16)
FETCH_TIMEOUT: float = _env_float("FETCH_TIMEOUT", 15.0)
FETCH_TOP_N_DEFAULT: int = 5
FETCH_TOP_N_MAX: int = 8

# 抓取代理:显式配置优先,否则跟随系统环境变量(curl_cffi trust_env 同款语义)
FETCH_PROXY: str | None = (
    os.environ.get("HOLLOW_FETCH_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or None
)

# 重定向策略(2026-07-06 实测,docs/research/2026-07-06-local-env-verification.md):
# curl_cffi 的 "safe" 模式(SSRF 防护)检查的是"这一跳要连接的 IP"——经 localhost
# 代理时每一跳连的都是 127.0.0.1,导致一切重定向被误杀(https->https 也死)。
# 走代理 → True(代理即网络边界);直连 → "safe"(真实 SSRF 防护,网关抓任意 URL)。
_fr = os.environ.get("HOLLOW_FOLLOW_REDIRECTS", "").strip().lower()
if _fr == "all":
    FOLLOW_REDIRECTS: bool | str = True
elif _fr == "safe":
    FOLLOW_REDIRECTS = "safe"
else:
    FOLLOW_REDIRECTS = True if FETCH_PROXY else "safe"

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
