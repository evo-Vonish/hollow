# -*- coding: utf-8 -*-
"""结果过滤(2026-07-15,产品差距批 #9):按域名 include/exclude 过滤召回结果。

对齐 Tavily/Exa 的 include_domains / exclude_domains。SearXNG 不统一支持域过滤,故在
hollow 侧对召回结果做后置过滤(**过滤在召回之后**:可能使产出少于 top_n,属预期)。
子域也算命中:host == d 或 host.endswith('.'+d)。
"""
from urllib.parse import urlsplit


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _matches(host: str, domain: str) -> bool:
    d = domain.lower().strip().lstrip(".").rstrip(".")
    return bool(d) and (host == d or host.endswith("." + d))


def filter_by_domain(
    results: list[dict],
    include: list[str] | None,
    exclude: list[str] | None,
) -> list[dict]:
    """include 非空 → 只留命中其一的;exclude → 去掉命中其一的。两者都空则原样返回(零成本)。
    过滤时跳过非 dict / 无 url 条目(下游本也会防御跳过)。"""
    inc = [d for d in (include or []) if isinstance(d, str) and d.strip()]
    exc = [d for d in (exclude or []) if isinstance(d, str) and d.strip()]
    if not inc and not exc:
        return results
    out: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        host = _host(url) if isinstance(url, str) else ""
        if not host:
            continue
        if inc and not any(_matches(host, d) for d in inc):
            continue
        if exc and any(_matches(host, d) for d in exc):
            continue
        out.append(r)
    return out
