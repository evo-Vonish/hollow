# -*- coding: utf-8 -*-
"""词汇相关性重排(2026-07-14,搜索质量批)。

SearXNG 的融合分 = weight×Σ(1/position),只看位置、不看 query 词是否出现在
标题/正文——撤稿空壳、关键词凑巧命中的离题论文都能拿最高分(审查实锤)。这里在
搜索与抓取之间插一层零成本重排:按 (query, title, snippet) 的词汇相关性排序,
标题命中权重 >> 正文命中,SearXNG 原分仅作兜底 prior。不引入模型/网络,可校准。

中英混合:latin 按词、CJK 按字符 bigram(否则"钠离子电池"匹配不到"钠电池")。
"""
import re

from api import config

# 极小停用词表:最泛的功能/元词,不参与匹配(否则 "paper/best/tutorial" 虚增命中)
_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "vs", "is", "are",
    "how", "what", "why", "with", "best", "paper", "papers", "tutorial", "guide",
    "using", "use", "practices", "practice", "intro", "introduction", "overview",
    "的", "了", "和", "与", "是", "在", "怎么", "如何", "什么",
}
_CJK = "一-鿿㐀-䶿豈-﫿"
_LATIN_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN_RE = re.compile(f"[{_CJK}]+")


def features(text: str | None) -> set[str]:
    """把文本抽成匹配特征集:latin 词(≥2 字符)+ CJK 字符 bigram(单字成 run 时用单字)。"""
    if not text:
        return set()
    t = text.lower()
    feats = {w for w in _LATIN_RE.findall(t) if w not in _STOP}
    for run in _CJK_RUN_RE.findall(t):
        if len(run) == 1:
            feats.add(run)
        else:
            feats.update(run[i:i + 2] for i in range(len(run) - 1))
    return feats


def query_features(query: str) -> set[str]:
    """query 特征;若全是停用词导致为空,退回不去停用词的版本(避免零除/全 0)。"""
    qf = features(query)
    if qf:
        return qf
    t = (query or "").lower()
    raw = set(_LATIN_RE.findall(t))
    for run in _CJK_RUN_RE.findall(t):
        raw.update([run] if len(run) == 1 else (run[i:i + 2] for i in range(len(run) - 1)))
    return raw


def relevance(qf: set[str], title: str | None, snippet: str | None, prior_norm: float) -> float:
    """0~ 的相关分:标题命中率×W_TITLE + 正文命中率×W_SNIPPET + prior_norm×W_PRIOR。
    prior_norm 是 SearXNG 原分在本结果集内归一到 0~1 后的值(仅兜底/tiebreak)。"""
    if not qf:
        return config.RERANK_W_PRIOR * prior_norm
    n = len(qf)
    title_hit = len(qf & features(title)) / n
    snippet_hit = len(qf & features(snippet)) / n
    return (config.RERANK_W_TITLE * title_hit
            + config.RERANK_W_SNIPPET * snippet_hit
            + config.RERANK_W_PRIOR * prior_norm)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rerank(query: str, results: list[dict]) -> list[dict]:
    """给每条 result 附 `_rel`(相关分)与 `_degenerate`(纯噪声标记),按相关分降序稳定排序。
    不修改除这两个私有键外的任何字段;不丢弃任何条目(过滤留给候选选取阶段)。"""
    if not results:
        return results
    qf = query_features(query)
    max_score = max((_num(r.get("score")) for r in results if isinstance(r, dict)), default=0.0)
    for r in results:
        if not isinstance(r, dict):
            continue
        prior_norm = (_num(r.get("score")) / max_score) if max_score > 0 else 0.0
        title = r.get("title")
        snippet = r.get("content")
        rel = relevance(qf, title if isinstance(title, str) else None,
                        snippet if isinstance(snippet, str) else None, prior_norm)
        r["_rel"] = rel
        # 纯噪声:query 有实义特征、但标题零命中且 snippet 空——撤稿空壳这类
        title_hit = bool(qf) and bool(qf & features(title if isinstance(title, str) else None))
        has_snippet = bool(isinstance(snippet, str) and snippet.strip())
        r["_degenerate"] = bool(qf) and not title_hit and not has_snippet
    # 稳定排序:相关分降序(Python sort 稳定,保留同分时的 SearXNG 原序)
    order = sorted(range(len(results)),
                   key=lambda i: (-(results[i].get("_rel", 0.0) if isinstance(results[i], dict) else 0.0)))
    return [results[i] for i in order]
