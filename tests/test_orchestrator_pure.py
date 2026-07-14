# -*- coding: utf-8 -*-
"""Pure/deterministic helpers in api/orchestrator.py — no network, no fetches.

Covers select_candidates, _published, resolve_mode_fetch, resolve_effective,
_safe_str/_safe_float/_result_engine. Importing orchestrator pulls in
fetcher->scrapling (installed) but we never call fetch_one.
"""
from datetime import datetime, date, timezone

import pytest

from api import config, orchestrator
from api.models import ResearchRequest


# ---------------------------------------------------------------------------
# select_candidates
# ---------------------------------------------------------------------------

def _r(url, **extra):
    d = {"url": url}
    d.update(extra)
    return d


def test_select_happy_path_preserves_order():
    results = [_r("http://a.com/1"), _r("http://b.com/2"), _r("http://c.com/3")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://a.com/1", "http://b.com/2", "http://c.com/3"]


def test_select_caps_at_top_n():
    results = [_r(f"http://a.com/{i}") for i in range(10)]
    got = orchestrator.select_candidates(results, top_n=3)
    assert len(got) == 3
    assert [c["url"] for c in got] == ["http://a.com/0", "http://a.com/1", "http://a.com/2"]


def test_select_top_n_zero_still_yields_one():
    # cap check `len(picked) >= top_n` runs AFTER append, so top_n=0 emits exactly 1
    results = [_r("http://a.com/1"), _r("http://b.com/2")]
    got = orchestrator.select_candidates(results, top_n=0)
    assert [c["url"] for c in got] == ["http://a.com/1"]


def test_select_empty_results_returns_empty():
    assert orchestrator.select_candidates([], top_n=5) == []


def test_select_dedup_by_netloc_and_path():
    results = [_r("http://a.com/x"), _r("http://a.com/x"), _r("http://a.com/y")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://a.com/x", "http://a.com/y"]


def test_select_dedup_is_netloc_case_insensitive():
    results = [_r("http://A.COM/x"), _r("http://a.com/x")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 1
    # first one wins (order preserved)
    assert got[0]["url"] == "http://A.COM/x"


def test_select_dedup_trailing_slash_equivalent():
    results = [_r("http://a.com/path/"), _r("http://a.com/path")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 1


def test_select_root_and_root_slash_dedup():
    # path "" and "/" both rstrip to "" -> same key
    results = [_r("http://a.com"), _r("http://a.com/")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 1


def test_select_path_case_sensitive():
    # only netloc is lowercased; path keeps case, so these are distinct
    results = [_r("http://a.com/X"), _r("http://a.com/x")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 2


def test_select_skips_degenerate():
    results = [_r("http://a.com/1", _degenerate=True), _r("http://b.com/2")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://b.com/2"]


def test_select_degenerate_falsy_kept():
    results = [_r("http://a.com/1", _degenerate=False), _r("http://b.com/2", _degenerate=0)]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 2


def test_select_skips_non_dict_entries():
    results = ["not a dict", 42, None, ["x"], _r("http://a.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://a.com/1"]


def test_select_skips_non_str_url():
    results = [_r(123), _r(None), _r(["http://a.com"]), _r("http://good.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://good.com/1"]


def test_select_skips_empty_url():
    results = [_r(""), _r("http://good.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://good.com/1"]


def test_select_skips_missing_url_key():
    results = [{"title": "no url here"}, _r("http://good.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://good.com/1"]


@pytest.mark.parametrize("scheme_url", [
    "ftp://a.com/file",
    "mailto:someone@a.com",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "data:text/html,hi",
    "//a.com/protocol-relative",
])
def test_select_skips_non_web_schemes(scheme_url):
    results = [_r(scheme_url), _r("http://good.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://good.com/1"]


def test_select_allows_https():
    results = [_r("https://secure.com/x")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["https://secure.com/x"]


@pytest.mark.parametrize("ext", [
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp3", ".mp4", ".avi", ".mkv", ".exe", ".dmg",
])
def test_select_skips_all_skip_extensions(ext):
    results = [_r(f"http://a.com/file{ext}"), _r("http://b.com/page")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://b.com/page"]


def test_select_skip_extension_case_insensitive():
    results = [_r("http://a.com/IMG.JPG"), _r("http://b.com/DOC.PDF"), _r("http://c.com/ok")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://c.com/ok"]


def test_select_extension_check_ignores_query_and_fragment():
    # extension test is on path only; query/fragment don't turn a page into a skip
    results = [_r("http://a.com/page?file=x.pdf"), _r("http://b.com/page#x.jpg")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 2


def test_select_query_does_not_affect_dedup_key():
    # dedup key is (netloc, path); query differing does not distinguish
    results = [_r("http://a.com/p?a=1"), _r("http://a.com/p?a=2")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert len(got) == 1


def test_select_invalid_url_valueerror_skipped():
    # unclosed IPv6 bracket -> urlsplit raises ValueError -> entry skipped, not fatal
    results = [_r("http://[::1"), _r("http://good.com/1")]
    got = orchestrator.select_candidates(results, top_n=10)
    assert [c["url"] for c in got] == ["http://good.com/1"]


def test_select_returns_original_dict_objects():
    original = _r("http://a.com/1", title="hi", score=9)
    got = orchestrator.select_candidates([original], top_n=10)
    assert got[0] is original  # identity preserved, not a copy


# ---------------------------------------------------------------------------
# _published
# ---------------------------------------------------------------------------

def test_published_str_passthrough():
    assert orchestrator._published({"publishedDate": "2024-01-02"}) == "2024-01-02"


def test_published_empty_str_is_none():
    assert orchestrator._published({"publishedDate": ""}) is None


def test_published_missing_key_is_none():
    assert orchestrator._published({}) is None


def test_published_none_is_none():
    assert orchestrator._published({"publishedDate": None}) is None


def test_published_datetime_isoformat():
    dt = datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    assert orchestrator._published({"publishedDate": dt}) == dt.isoformat()


def test_published_date_isoformat():
    d = date(2024, 3, 4)
    assert orchestrator._published({"publishedDate": d}) == "2024-03-04"


def test_published_isoformat_raising_falls_back_to_none():
    class Bad:
        def isoformat(self):
            raise ValueError("boom")
    assert orchestrator._published({"publishedDate": Bad()}) is None


def test_published_non_str_non_datetime_str_coerced():
    # int has no isoformat -> str(v) or None
    assert orchestrator._published({"publishedDate": 12345}) == "12345"


# ---------------------------------------------------------------------------
# resolve_mode_fetch
# ---------------------------------------------------------------------------

def test_resolve_mode_fetch_fast_preset():
    escalate, timeout = orchestrator.resolve_mode_fetch("fast", None, None)
    assert escalate is False
    assert timeout == 8.0


def test_resolve_mode_fetch_balanced_preset():
    escalate, timeout = orchestrator.resolve_mode_fetch("balanced", None, None)
    assert escalate is True
    assert timeout == config.FETCH_TIMEOUT


def test_resolve_mode_fetch_thorough_preset():
    escalate, timeout = orchestrator.resolve_mode_fetch("thorough", None, None)
    assert escalate is True
    assert timeout == 30.0


def test_resolve_mode_fetch_unknown_mode_uses_default():
    got = orchestrator.resolve_mode_fetch("bogus", None, None)
    default = config.MODE_PRESETS[config.DEFAULT_MODE]
    assert got == (default["escalate"], default["timeout"])


def test_resolve_mode_fetch_explicit_timeout_overrides():
    _, timeout = orchestrator.resolve_mode_fetch("fast", 99.0, None)
    assert timeout == 99.0


def test_resolve_mode_fetch_explicit_escalate_overrides():
    escalate, _ = orchestrator.resolve_mode_fetch("fast", None, True)
    assert escalate is True


def test_resolve_mode_fetch_explicit_escalate_false_overrides_balanced():
    # escalate=False must override a preset that is True (None-check, not truthiness)
    escalate, _ = orchestrator.resolve_mode_fetch("balanced", None, False)
    assert escalate is False


def test_resolve_mode_fetch_explicit_timeout_zero_overrides():
    # 0.0 is falsy but only None triggers the preset, so 0.0 must pass through
    _, timeout = orchestrator.resolve_mode_fetch("fast", 0.0, None)
    assert timeout == 0.0


# ---------------------------------------------------------------------------
# resolve_effective
# ---------------------------------------------------------------------------

def _req(**kw):
    kw.setdefault("q", "hello")
    return ResearchRequest(**kw)


def test_resolve_effective_balanced_defaults():
    escalate, timeout, pool = orchestrator.resolve_effective(_req(fetch_top_n=5, mode="balanced"))
    assert escalate is True
    assert timeout == config.FETCH_TIMEOUT
    assert pool == 5  # pool_factor 1


def test_resolve_effective_fast_pool_factor():
    _, _, pool = orchestrator.resolve_effective(_req(fetch_top_n=5, mode="fast"))
    assert pool == 15  # 5 * 3


def test_resolve_effective_fast_pool_capped_at_pool_max():
    # 10 * 3 = 30 > POOL_MAX (24) -> capped
    _, _, pool = orchestrator.resolve_effective(_req(fetch_top_n=10, mode="fast"))
    assert pool == config.POOL_MAX == 24


def test_resolve_effective_thorough_pool_and_preset():
    escalate, timeout, pool = orchestrator.resolve_effective(_req(fetch_top_n=7, mode="thorough"))
    assert escalate is True
    assert timeout == 30.0
    assert pool == 7  # pool_factor 1


def test_resolve_effective_explicit_escalate_overrides_preset():
    escalate, _, _ = orchestrator.resolve_effective(_req(mode="fast", escalate=True))
    assert escalate is True


def test_resolve_effective_explicit_timeout_overrides_preset():
    _, timeout, _ = orchestrator.resolve_effective(_req(mode="fast", fetch_timeout=12.5))
    assert timeout == 12.5


def test_resolve_effective_pool_uses_mode_factor_not_overrides():
    # overriding escalate/timeout must not change pool sizing (mode still drives factor)
    _, _, pool = orchestrator.resolve_effective(
        _req(fetch_top_n=4, mode="fast", escalate=False, fetch_timeout=3.0)
    )
    assert pool == 12  # 4 * 3


# ---------------------------------------------------------------------------
# _safe_str / _safe_float / _result_engine
# ---------------------------------------------------------------------------

def test_safe_str_passes_str():
    assert orchestrator._safe_str("abc") == "abc"
    assert orchestrator._safe_str("") == ""


@pytest.mark.parametrize("v", [None, 1, 2.0, ["x"], {"a": 1}, True])
def test_safe_str_non_str_is_none(v):
    assert orchestrator._safe_str(v) is None


def test_safe_float_from_number_and_numeric_str():
    assert orchestrator._safe_float(3) == 3.0
    assert orchestrator._safe_float("2.5") == 2.5


@pytest.mark.parametrize("v", [None, "abc", "", [1], {}])
def test_safe_float_bad_values_none(v):
    assert orchestrator._safe_float(v) is None


def test_result_engine_prefers_engines_list():
    r = {"engines": ["baidu", "sogou"], "engine": "duckduckgo"}
    assert orchestrator._result_engine(r) == "baidu,sogou"


def test_result_engine_single_engine_when_no_list():
    assert orchestrator._result_engine({"engine": "duckduckgo"}) == "duckduckgo"


def test_result_engine_empty_list_falls_back_to_engine():
    assert orchestrator._result_engine({"engines": [], "engine": "baidu"}) == "baidu"


def test_result_engine_coerces_non_str_list_members():
    assert orchestrator._result_engine({"engines": [1, 2]}) == "1,2"


def test_result_engine_none_when_nothing():
    assert orchestrator._result_engine({}) is None


def test_result_engine_non_str_engine_is_none():
    # engines absent/not-list, engine not a str -> _safe_str -> None
    assert orchestrator._result_engine({"engine": 123}) is None


def test_result_engine_non_list_engines_falls_back_to_engine():
    assert orchestrator._result_engine({"engines": "baidu", "engine": "sogou"}) == "sogou"
