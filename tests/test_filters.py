# -*- coding: utf-8 -*-
"""Unit tests for api/filters.py (domain include/exclude post-filter).

Pure functions, no network. Asserts real behavior read from the source.
"""
from api.filters import filter_by_domain


def _r(url):
    return {"url": url, "title": "t"}


# --- both empty -> identity ------------------------------------------------

def test_both_empty_returns_input_unchanged_identity():
    results = [_r("https://a.com"), _r("https://b.com")]
    out = filter_by_domain(results, None, None)
    assert out is results  # zero-cost: same object


def test_both_empty_lists_returns_identity():
    results = [_r("https://a.com")]
    assert filter_by_domain(results, [], []) is results


def test_whitespace_only_domains_treated_as_empty_identity():
    results = [_r("https://a.com")]
    # entries that are blank/whitespace get filtered out -> inc/exc empty -> identity
    assert filter_by_domain(results, ["   "], ["\t"]) is results


# --- include: host==d or subdomain ----------------------------------------

def test_include_keeps_exact_host():
    results = [_r("https://wikipedia.org/x")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert out == results


def test_include_keeps_subdomain():
    results = [_r("https://en.wikipedia.org/wiki/Foo")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert len(out) == 1


def test_include_drops_non_matching():
    results = [_r("https://en.wikipedia.org/x"), _r("https://example.com/y")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert [r["url"] for r in out] == ["https://en.wikipedia.org/x"]


def test_include_multiple_domains_or_semantics():
    results = [_r("https://a.com"), _r("https://b.net"), _r("https://c.org")]
    out = filter_by_domain(results, ["a.com", "c.org"], None)
    assert [r["url"] for r in out] == ["https://a.com", "https://c.org"]


# --- lookalike suffix must NOT match --------------------------------------

def test_bare_domain_does_not_match_lookalike_prefix_suffix():
    # "evil-wikipedia.org" must NOT match include ["wikipedia.org"]
    results = [_r("https://evil-wikipedia.org/x")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert out == []


def test_notwikipedia_does_not_match():
    results = [_r("https://notwikipedia.org/x")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert out == []


def test_suffix_boundary_requires_dot():
    # only a real subdomain label boundary counts
    results = [_r("https://sub.wikipedia.org"), _r("https://xwikipedia.org")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert [r["url"] for r in out] == ["https://sub.wikipedia.org"]


# --- exclude ---------------------------------------------------------------

def test_exclude_drops_matches():
    results = [_r("https://spam.com/x"), _r("https://good.com/y")]
    out = filter_by_domain(results, None, ["spam.com"])
    assert [r["url"] for r in out] == ["https://good.com/y"]


def test_exclude_drops_subdomain():
    results = [_r("https://ads.spam.com/x"), _r("https://good.com")]
    out = filter_by_domain(results, None, ["spam.com"])
    assert [r["url"] for r in out] == ["https://good.com"]


def test_exclude_lookalike_not_dropped():
    results = [_r("https://notspam.com/x")]
    out = filter_by_domain(results, None, ["spam.com"])
    assert len(out) == 1


# --- include + exclude combined -------------------------------------------

def test_include_and_exclude_combined():
    results = [
        _r("https://en.wikipedia.org/a"),      # included, not excluded -> keep
        _r("https://bad.wikipedia.org/b"),     # included but excluded -> drop
        _r("https://example.com/c"),           # not included -> drop
    ]
    out = filter_by_domain(results, ["wikipedia.org"], ["bad.wikipedia.org"])
    assert [r["url"] for r in out] == ["https://en.wikipedia.org/a"]


def test_exclude_takes_precedence_when_also_included():
    # a domain both included and excluded: exclude wins (dropped)
    results = [_r("https://x.com")]
    out = filter_by_domain(results, ["x.com"], ["x.com"])
    assert out == []


# --- case-insensitivity ----------------------------------------------------

def test_case_insensitive_host_and_domain_arg():
    results = [_r("https://EN.Wikipedia.ORG/x")]
    out = filter_by_domain(results, ["WikiPedia.Org"], None)
    assert len(out) == 1


def test_case_insensitive_exclude():
    results = [_r("https://Spam.COM")]
    out = filter_by_domain(results, None, ["spam.com"])
    assert out == []


# --- leading/trailing dot tolerated ---------------------------------------

def test_leading_dot_in_domain_arg_tolerated():
    results = [_r("https://en.wikipedia.org")]
    out = filter_by_domain(results, [".wikipedia.org"], None)
    assert len(out) == 1


def test_trailing_dot_in_domain_arg_tolerated():
    results = [_r("https://en.wikipedia.org")]
    out = filter_by_domain(results, ["wikipedia.org."], None)
    assert len(out) == 1


def test_both_dots_in_domain_arg_tolerated():
    results = [_r("https://wikipedia.org")]
    out = filter_by_domain(results, [".wikipedia.org."], None)
    assert len(out) == 1


def test_fqdn_trailing_dot_in_url_host_normalized():
    # urlsplit host keeps trailing dot; _host rstrips it
    results = [_r("https://wikipedia.org./x")]
    out = filter_by_domain(results, ["wikipedia.org"], None)
    assert len(out) == 1


# --- non-dict / missing / non-str url skipped -----------------------------

def test_non_dict_entries_skipped_when_filtering():
    results = [_r("https://a.com"), "not a dict", 42, None, ["x"]]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


def test_entry_missing_url_skipped():
    results = [{"title": "no url"}, _r("https://a.com")]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


def test_entry_non_str_url_skipped():
    results = [{"url": 123}, {"url": None}, _r("https://a.com")]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


def test_url_with_no_host_skipped():
    # e.g. relative path or scheme-less string yields empty host
    results = [_r("/relative/path"), _r("not-a-url"), _r("https://a.com")]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


def test_empty_url_string_skipped():
    results = [_r(""), _r("https://a.com")]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


# --- malformed urls don't crash -------------------------------------------

def test_malformed_urls_do_not_crash():
    # bracketed/invalid IPv6 etc. can raise ValueError in urlsplit -> _host returns ""
    bad = [
        _r("http://[::1"),          # unterminated bracket
        _r("http://[invalid]:port"),
        _r("http://exa mple.com"),  # space in host
        _r("://noscheme"),
    ]
    results = bad + [_r("https://a.com")]
    # exclude filter forces host computation on every entry; must not raise
    out = filter_by_domain(results, None, ["z.com"])
    assert _r("https://a.com") in out


def test_malformed_url_with_include_only_keeps_valid():
    results = [_r("http://[::1"), _r("https://a.com")]
    out = filter_by_domain(results, ["a.com"], None)
    assert out == [_r("https://a.com")]


# --- non-matching filter yields explicit empty list (nothing silently kept)-

def test_no_matches_returns_empty_list_not_none():
    results = [_r("https://a.com")]
    out = filter_by_domain(results, ["nope.com"], None)
    assert out == []
    assert isinstance(out, list)


def test_empty_results_with_active_filter_returns_empty():
    out = filter_by_domain([], ["a.com"], None)
    assert out == []


# --- non-str domain entries in include/exclude ignored ---------------------

def test_non_str_domain_entries_ignored():
    results = [_r("https://a.com")]
    # 123 and None are not str -> filtered out; only "a.com" remains as include
    out = filter_by_domain(results, [123, None, "a.com"], None)
    assert len(out) == 1


def test_all_non_str_include_becomes_identity():
    results = [_r("https://a.com")]
    # include has only non-str/blank -> inc empty; exc empty -> identity
    assert filter_by_domain(results, [123, None], None) is results


# --- traceability: filtering preserves entry objects unchanged -------------

def test_kept_entries_are_same_objects():
    a = _r("https://a.com")
    b = _r("https://b.com")
    out = filter_by_domain([a, b], ["a.com"], None)
    assert out[0] is a  # object identity preserved (traceability)
