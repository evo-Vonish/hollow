# -*- coding: utf-8 -*-
"""api/rerank.py 单元测试:词汇特征抽取 + 相关性重排 + 高亮句抽取。

纯函数模块,不联网、不起服务;权重/阈值全从 api.config 读取(不硬编码,便于校准跟随)。
覆盖:features / query_features / relevance / rerank / highlights,含四条底线相关不变式
(不静默丢弃条目、可溯源附字段、显式空集而非缺失)。
"""
from api import config, rerank


# --------------------------------------------------------------------------- #
# features()
# --------------------------------------------------------------------------- #
def test_features_none_returns_empty_set():
    assert rerank.features(None) == set()


def test_features_empty_string_returns_empty_set():
    assert rerank.features("") == set()


def test_features_latin_needs_two_chars():
    # 单字符 latin 词(len 1)不被 [a-z0-9]{2,} 命中
    assert rerank.features("a I b") == set()


def test_features_strips_latin_stopwords():
    # best/practices/for 是停用词,仅 asyncio 保留
    assert rerank.features("Best Practices for asyncio") == {"asyncio"}


def test_features_lowercases_latin():
    assert rerank.features("ASYNCIO Loop") == {"asyncio", "loop"}


def test_features_keeps_alnum_tokens():
    assert rerank.features("gpt4 model v2") == {"gpt4", "model", "v2"}


def test_features_single_cjk_char_kept_as_is():
    # 单字成 run 时用单字(否则无 bigram 可产)
    assert rerank.features("锂") == {"锂"}


def test_features_cjk_bigrams():
    # 钠离子电池 -> 相邻二字 bigram
    assert rerank.features("钠离子电池") == {"钠离", "离子", "子电", "电池"}


def test_features_cjk_two_char_run_single_bigram():
    assert rerank.features("电池") == {"电池"}


def test_features_mixed_latin_and_cjk():
    assert rerank.features("钠 battery") == {"钠", "battery"}


def test_features_cjk_single_char_not_stopword_filtered():
    # CJK 单字不过停用词表(仅 latin 词过滤);"和" 在 _STOP 中仍被保留
    assert rerank.features("和") == {"和"}


# --------------------------------------------------------------------------- #
# query_features()
# --------------------------------------------------------------------------- #
def test_query_features_normal_returns_features():
    assert rerank.query_features("python asyncio") == {"python", "asyncio"}


def test_query_features_all_stopword_falls_back_to_raw():
    # 全停用词 -> features 为空 -> 退回不去停用词的原始 latin 集(避免零除/全 0)
    assert rerank.query_features("best paper") == {"best", "paper"}


def test_query_features_fallback_keeps_cjk_bigrams():
    # 退化路径也保留 CJK bigram 语义(此处只有停用汉字)
    qf = rerank.query_features("的了")
    # "的了" 是二字 run -> bigram "的了";features 路径本就非空,不会走 fallback
    assert qf == {"的了"}


def test_query_features_empty_query_returns_empty():
    assert rerank.query_features("") == set()


def test_query_features_none_returns_empty():
    assert rerank.query_features(None) == set()


# --------------------------------------------------------------------------- #
# relevance()
# --------------------------------------------------------------------------- #
def test_relevance_empty_qf_uses_only_prior():
    # qf 空 -> 只剩 prior 兜底项
    assert rerank.relevance(set(), "asyncio", "asyncio", 1.0) == config.RERANK_W_PRIOR


def test_relevance_title_hit_scores_w_title():
    qf = {"asyncio"}
    assert rerank.relevance(qf, "asyncio", None, 0.0) == config.RERANK_W_TITLE


def test_relevance_snippet_hit_scores_w_snippet():
    qf = {"asyncio"}
    assert rerank.relevance(qf, None, "asyncio", 0.0) == config.RERANK_W_SNIPPET


def test_relevance_title_weight_dominates_snippet():
    # 底线相关:标题命中远重于正文命中
    qf = {"asyncio"}
    title_only = rerank.relevance(qf, "asyncio", None, 0.0)
    snippet_only = rerank.relevance(qf, None, "asyncio", 0.0)
    assert title_only > snippet_only


def test_relevance_partial_hit_ratio():
    # 命中率是 |qf∩feats| / |qf|
    qf = {"event", "loop"}
    # 标题只命中一半
    assert rerank.relevance(qf, "event driven", None, 0.0) == config.RERANK_W_TITLE * 0.5


def test_relevance_prior_adds_tiebreak():
    qf = {"asyncio"}
    base = rerank.relevance(qf, "asyncio", None, 0.0)
    withprior = rerank.relevance(qf, "asyncio", None, 1.0)
    assert withprior == base + config.RERANK_W_PRIOR * 1.0


def test_relevance_no_hits_only_prior_component():
    qf = {"asyncio"}
    assert rerank.relevance(qf, "cooking", "recipes", 1.0) == config.RERANK_W_PRIOR * 1.0


# --------------------------------------------------------------------------- #
# rerank()
# --------------------------------------------------------------------------- #
def test_rerank_empty_returns_same_object():
    empty = []
    assert rerank.rerank("q", empty) is empty


def test_rerank_attaches_rel_and_degenerate():
    results = [{"title": "asyncio guide", "content": "loop details", "score": 1.0}]
    out = rerank.rerank("asyncio", results)
    assert "_rel" in out[0] and "_degenerate" in out[0]
    assert isinstance(out[0]["_rel"], float)
    assert out[0]["_degenerate"] is False


def test_rerank_orders_title_hit_above_snippet_only():
    # 底线相关:标题命中的条目排在只有正文命中的之前
    results = [
        {"title": "cooking", "content": "asyncio tutorial", "score": 5.0},   # snippet-only
        {"title": "asyncio deep dive", "content": "", "score": 1.0},          # title hit
    ]
    out = rerank.rerank("asyncio", results)
    assert out[0]["title"] == "asyncio deep dive"
    assert out[1]["title"] == "cooking"


def test_rerank_sorted_desc_by_rel():
    results = [
        {"title": "cooking", "content": "asyncio", "score": 1.0},
        {"title": "asyncio", "content": "asyncio", "score": 1.0},
    ]
    out = rerank.rerank("asyncio", results)
    rels = [r["_rel"] for r in out]
    assert rels == sorted(rels, reverse=True)


def test_rerank_stable_sort_preserves_original_order_on_ties():
    # 三条相关分相同 -> 保留 SearXNG 原序(稳定排序,底线:不乱序丢信息)
    results = [
        {"id": "a", "title": "asyncio one", "content": ""},
        {"id": "b", "title": "asyncio two", "content": ""},
        {"id": "c", "title": "asyncio three", "content": ""},
    ]
    out = rerank.rerank("asyncio", results)
    assert [r["_rel"] for r in out] == [config.RERANK_W_TITLE] * 3
    assert [r["id"] for r in out] == ["a", "b", "c"]


def test_rerank_prior_breaks_ties_by_searxng_score():
    # 词汇分相同,SearXNG 原分高者靠前(prior 兜底 tiebreak)
    results = [
        {"id": "low", "title": "asyncio", "content": "", "score": 5.0},
        {"id": "high", "title": "asyncio", "content": "", "score": 10.0},
    ]
    out = rerank.rerank("asyncio", results)
    assert [r["id"] for r in out] == ["high", "low"]


def test_rerank_marks_withdrawn_shell_degenerate():
    # 撤稿空壳:query 有实义特征、标题零命中、snippet 空 -> _degenerate True
    results = [{"title": "This paper has been withdrawn", "content": "", "score": 9.0}]
    out = rerank.rerank("sodium battery recycling", results)
    assert out[0]["_degenerate"] is True


def test_rerank_not_degenerate_when_title_hits():
    results = [{"title": "sodium battery basics", "content": "", "score": 1.0}]
    out = rerank.rerank("sodium battery recycling", results)
    assert out[0]["_degenerate"] is False


def test_rerank_not_degenerate_when_snippet_present():
    # 标题零命中但 snippet 非空 -> 不算纯噪声
    results = [{"title": "unrelated headline", "content": "some real body text here", "score": 1.0}]
    out = rerank.rerank("sodium battery recycling", results)
    assert out[0]["_degenerate"] is False


def test_rerank_not_degenerate_when_query_has_no_features():
    # query 无实义特征 -> 不标记 degenerate(qf 为空前置条件)
    results = [{"title": "This paper has been withdrawn", "content": "", "score": 1.0}]
    out = rerank.rerank("", results)
    assert out[0]["_degenerate"] is False


def test_rerank_whitespace_only_snippet_is_degenerate():
    # snippet 仅空白 = 无正文,视同空
    results = [{"title": "withdrawn shell", "content": "   \n  ", "score": 1.0}]
    out = rerank.rerank("sodium battery recycling", results)
    assert out[0]["_degenerate"] is True


def test_rerank_does_not_drop_entries():
    # 底线:不静默丢弃任何条目
    results = [
        {"title": "asyncio", "content": "", "score": 1.0},
        {"title": "cooking", "content": "", "score": 1.0},
        {"title": "gardening", "content": "", "score": 1.0},
    ]
    out = rerank.rerank("asyncio", results)
    assert len(out) == len(results)


def test_rerank_tolerates_non_dict_entries():
    # 非 dict 条目不崩、不丢
    results = ["a bare string", {"title": "asyncio", "content": "", "score": 1.0}, 42]
    out = rerank.rerank("asyncio", results)
    assert len(out) == 3
    # dict 条目拿到最高 _rel,排在最前
    assert isinstance(out[0], dict) and out[0]["title"] == "asyncio"
    assert "a bare string" in out and 42 in out


def test_rerank_non_string_title_content_ignored():
    # title/content 非 str -> 当作 None,不崩;无词汇命中 -> 仅剩 prior 项
    # 单条 score=1 -> max_score=1 -> prior_norm=1 -> _rel = W_PRIOR*1
    results = [{"title": 123, "content": ["x"], "score": 1.0}]
    out = rerank.rerank("asyncio", results)
    assert out[0]["_rel"] == config.RERANK_W_PRIOR


def test_rerank_missing_score_treated_as_zero():
    # 无 score 字段 -> _num -> 0 -> max_score 0 -> prior_norm 0
    results = [{"title": "cooking", "content": ""}]
    out = rerank.rerank("asyncio", results)
    assert out[0]["_rel"] == 0.0


def test_rerank_does_not_mutate_other_fields():
    # 只附 _rel/_degenerate,不动其它字段(可溯源:原字段保持)
    results = [{"title": "asyncio", "content": "loop", "url": "http://x", "engine": "arxiv", "score": 2.0}]
    out = rerank.rerank("asyncio", results)
    r = out[0]
    assert r["url"] == "http://x" and r["engine"] == "arxiv"
    assert r["title"] == "asyncio" and r["content"] == "loop"


# --------------------------------------------------------------------------- #
# highlights()
# --------------------------------------------------------------------------- #
def test_highlights_empty_text_returns_empty_list():
    assert rerank.highlights("x", "") == []


def test_highlights_none_text_returns_empty_list():
    assert rerank.highlights("x", None) == []


def test_highlights_empty_query_returns_empty_list():
    assert rerank.highlights("", "some long enough sentence here about the world") == []


def test_highlights_no_overlap_returns_empty_list():
    out = rerank.highlights(
        "quantum physics",
        "This sentence is about gardening and flowers only, nothing else here at all.",
    )
    assert out == []


def test_highlights_english_picks_relevant_rejects_offtopic():
    text = (
        "This page is about cooking recipes and has nothing to do with programming. "
        "The asyncio event loop is the core of every asyncio application and runs tasks. "
        "Buy our newsletter today for great deals. "
        "You should never block the asyncio event loop with synchronous calls; use await instead. "
        "Contact us at the footer."
    )
    out = rerank.highlights("python asyncio event loop", text, k=2)
    assert len(out) == 2
    assert "event loop" in out[0][0].lower()
    assert all("cooking" not in s.lower() for s, _ in out)


def test_highlights_scores_descending():
    text = (
        "The asyncio event loop is the core of every asyncio application and runs tasks. "
        "You should never block the asyncio event loop with synchronous calls; use await instead."
    )
    out = rerank.highlights("python asyncio event loop", text, k=2)
    scores = [sc for _, sc in out]
    assert scores == sorted(scores, reverse=True)


def test_highlights_chinese_cjk_bigram_match():
    text = (
        "本文介绍钠离子电池的回收技术。"
        "今天天气不错适合出门。"
        "钠电池的正极材料回收是关键环节,湿法冶金可提取有价金属。"
        "点击订阅我们的频道。"
    )
    out = rerank.highlights("钠离子电池回收", text, k=2)
    assert len(out) >= 1
    assert any("回收" in s for s, _ in out)
    assert all("天气" not in s for s, _ in out)


def test_highlights_respects_k():
    text = (
        "The event loop runs tasks. The asyncio task awaits. The await keyword yields. "
        "The loop schedules coroutines. Tasks wrap coroutines nicely here."
    )
    out = rerank.highlights("event loop asyncio task await", text, k=2)
    assert len(out) == 2


def test_highlights_default_k_from_config():
    # k=None -> 用 config.HIGHLIGHTS_MAX 封顶
    sent = "The asyncio event loop schedules and runs the coroutine tasks here nicely. "
    # 造 5 个各含命中的不同句
    variants = [
        "The asyncio event loop schedules coroutine tasks here nicely for you. ",
        "An asyncio event loop runs many coroutine tasks concurrently right now. ",
        "This asyncio event loop object drives the whole coroutine task system. ",
        "Every asyncio event loop can run thousands of coroutine tasks smoothly. ",
        "The asyncio event loop dispatches queued coroutine tasks in order today. ",
    ]
    out = rerank.highlights("asyncio event loop task", "".join(variants))
    assert len(out) == config.HIGHLIGHTS_MAX


def test_highlights_filters_too_short_sentences():
    # 短于 HIGHLIGHT_MIN_CHARS 的句(即便命中)被过滤
    short = "asyncio."  # < 20 chars but hits
    long = " The asyncio framework provides an event loop for concurrency here today."
    out = rerank.highlights("asyncio", short + long)
    assert len(out) == 1
    assert "framework" in out[0][0]


def test_highlights_filters_too_long_sentences():
    # 超过 HIGHLIGHT_MAX_CHARS 的未断句段(即便命中)被过滤
    huge = ("asyncio " * ((config.HIGHLIGHT_MAX_CHARS // 8) + 20)).strip() + ". "
    assert len(huge) > config.HIGHLIGHT_MAX_CHARS
    good = "The asyncio loop is short enough to pass the length gate here."
    out = rerank.highlights("asyncio loop", huge + good)
    # 长段被过滤 -> 只剩 good;且没有任何超长句入选
    assert all(len(s) <= config.HIGHLIGHT_MAX_CHARS for s, _ in out)
    assert any("short enough" in s for s, _ in out)


def test_highlights_dedups_repeated_sentences():
    # 重复样板句只计一次
    one = "The asyncio event loop runs the queued coroutine tasks here nicely. "
    other = "Something else entirely different about gardening and flowers today. "
    out = rerank.highlights("asyncio event loop task", one + one + other)
    picked = [s for s, _ in out]
    assert picked.count(one.strip()) == 1
    # gardening 无命中,不入选 -> 结果恰好 1 条
    assert len(out) == 1


def test_highlights_returns_tuples_of_sentence_and_score():
    text = "The asyncio event loop runs the queued coroutine tasks here nicely today."
    out = rerank.highlights("asyncio event loop", text)
    assert len(out) == 1
    s, sc = out[0]
    assert isinstance(s, str) and isinstance(sc, float) and sc > 0
