# -*- coding: utf-8 -*-
"""Pure-function unit tests for api/searx_client.py.

Covers reconcile / sanitize_bang / instant_answers / _collect_used_engines / _s.
Network-free: SearchOutcome inputs are hand-built; search() is never called.
"""
from api.searx_client import (
    SearchOutcome,
    _collect_used_engines,
    _s,
    instant_answers,
    reconcile,
    sanitize_bang,
)
from api.models import EngineFailure


# --------------------------------------------------------------------------- #
# _s
# --------------------------------------------------------------------------- #
def test_s_returns_value_for_normal_string():
    assert _s("hello") == "hello"


def test_s_returns_none_for_empty_string():
    assert _s("") is None


def test_s_returns_none_for_literal_None_string():
    assert _s("None") is None


def test_s_returns_none_for_non_str_types():
    assert _s(None) is None
    assert _s(123) is None
    assert _s(0) is None
    assert _s([]) is None
    assert _s({"a": 1}) is None
    assert _s(True) is None


def test_s_preserves_whitespace_only_and_zero_str():
    # only "" and "None" are filtered; " " and "0" survive
    assert _s(" ") == " "
    assert _s("0") == "0"


# --------------------------------------------------------------------------- #
# sanitize_bang
# --------------------------------------------------------------------------- #
def test_sanitize_bang_clean_query_unchanged():
    clean, changed = sanitize_bang("python asyncio tutorial")
    assert clean == "python asyncio tutorial"
    assert changed is False


def test_sanitize_bang_strips_leading_bang():
    clean, changed = sanitize_bang("!g python")
    assert clean == "g python"
    assert changed is True


def test_sanitize_bang_strips_colon_language_override():
    clean, changed = sanitize_bang(":de wikipedia")
    assert clean == "de wikipedia"
    assert changed is True


def test_sanitize_bang_strips_lt_timeout_override():
    clean, changed = sanitize_bang("<3 slowquery")
    assert clean == "3 slowquery"
    assert changed is True


def test_sanitize_bang_strips_only_leading_not_internal():
    # lstrip only removes from the front of each token
    clean, changed = sanitize_bang("a!b c:d")
    assert clean == "a!b c:d"
    assert changed is False


def test_sanitize_bang_strips_mixed_leading_prefixes():
    clean, changed = sanitize_bang("!:<foo")
    assert clean == "foo"
    assert changed is True


def test_sanitize_bang_drops_pure_prefix_token():
    # token that becomes empty after stripping is dropped
    clean, changed = sanitize_bang("!!! realword")
    assert clean == "realword"
    assert changed is True


def test_sanitize_bang_all_tokens_pure_prefix_yields_empty():
    clean, changed = sanitize_bang("!! :: <<")
    assert clean == ""
    assert changed is True


def test_sanitize_bang_whitespace_collapse_is_not_a_change():
    # extra internal/leading whitespace collapses via split()/join but that
    # alone must NOT set changed=True
    clean, changed = sanitize_bang("  hello   world  ")
    assert clean == "hello world"
    assert changed is False


def test_sanitize_bang_empty_input():
    clean, changed = sanitize_bang("")
    assert clean == ""
    assert changed is False


def test_sanitize_bang_change_flag_true_only_when_stripped():
    # a query needing both collapse AND stripping -> changed True due to strip
    clean, changed = sanitize_bang("  !bang  word  ")
    assert clean == "bang word"
    assert changed is True


# --------------------------------------------------------------------------- #
# _collect_used_engines
# --------------------------------------------------------------------------- #
def test_collect_used_from_result_engine_field():
    payload = {"results": [{"engine": "duckduckgo"}]}
    assert _collect_used_engines(payload) == {"duckduckgo"}


def test_collect_used_from_result_engines_list():
    payload = {"results": [{"engines": ["baidu", "sogou"]}]}
    assert _collect_used_engines(payload) == {"baidu", "sogou"}


def test_collect_used_from_both_engine_and_engines():
    payload = {"results": [{"engine": "bing", "engines": ["bing", "brave"]}]}
    assert _collect_used_engines(payload) == {"bing", "brave"}


def test_collect_used_from_infoboxes_channel():
    payload = {"infoboxes": [{"engine": "wikipedia"}]}
    assert _collect_used_engines(payload) == {"wikipedia"}


def test_collect_used_from_answers_channel():
    payload = {"answers": [{"engine": "wolframalpha"}]}
    assert _collect_used_engines(payload) == {"wolframalpha"}


def test_collect_used_infobox_engines_list_form():
    payload = {"infoboxes": [{"engines": ["wikidata", "wikipedia"]}]}
    assert _collect_used_engines(payload) == {"wikidata", "wikipedia"}


def test_collect_used_empty_payload():
    assert _collect_used_engines({}) == set()


def test_collect_used_handles_none_channels():
    payload = {"results": None, "infoboxes": None, "answers": None}
    assert _collect_used_engines(payload) == set()


def test_collect_used_ignores_falsy_engine():
    payload = {"results": [{"engine": ""}, {"engine": None}]}
    assert _collect_used_engines(payload) == set()


def test_collect_used_across_all_channels():
    payload = {
        "results": [{"engine": "ddg"}, {"engines": ["baidu"]}],
        "infoboxes": [{"engine": "wikipedia"}],
        "answers": [{"engine": "wolframalpha"}],
    }
    assert _collect_used_engines(payload) == {"ddg", "baidu", "wikipedia", "wolframalpha"}


# --------------------------------------------------------------------------- #
# reconcile — the 3-way split (used / failed / no_results)
# --------------------------------------------------------------------------- #
def test_reconcile_engine_with_results_is_used():
    payload = {"results": [{"engine": "ddg"}]}
    used, failed, no_results = reconcile(["ddg"], payload)
    assert used == ["ddg"]
    assert failed == []
    assert no_results == []


def test_reconcile_unresponsive_pair_form_goes_to_failed_with_reason():
    payload = {"unresponsive_engines": [["bing", "timeout"]]}
    used, failed, no_results = reconcile(["bing"], payload)
    assert used == []
    assert failed == [EngineFailure(engine="bing", reason="timeout")]
    assert no_results == []


def test_reconcile_unresponsive_bare_string_form_goes_to_failed():
    payload = {"unresponsive_engines": ["brave"]}
    used, failed, no_results = reconcile(["brave"], payload)
    assert used == []
    assert len(failed) == 1
    assert failed[0].engine == "brave"
    assert failed[0].reason == "unresponsive"
    assert no_results == []


def test_reconcile_requested_but_no_output_no_error_is_no_results():
    # neither produced nor errored -> no_results, NOT failed
    payload = {"results": []}
    used, failed, no_results = reconcile(["mojeek"], payload)
    assert used == []
    assert failed == []
    assert no_results == ["mojeek"]


def test_reconcile_no_results_reason_never_says_silent_failure():
    # 底线②: honest ambiguity, not accusatory "silent failure"
    payload = {"results": []}
    _, failed, no_results = reconcile(["mojeek"], payload)
    assert "mojeek" in no_results
    # no_results entries carry no reason string at all; assert none leaks the phrase
    for f in failed:
        assert "silent failure" not in f.reason.lower()


def test_reconcile_three_way_split_together():
    payload = {
        "results": [{"engine": "ddg"}],
        "unresponsive_engines": [["bing", "CAPTCHA"]],
    }
    requested = ["ddg", "bing", "mojeek"]
    used, failed, no_results = reconcile(requested, payload)
    assert used == ["ddg"]
    assert failed == [EngineFailure(engine="bing", reason="CAPTCHA")]
    assert no_results == ["mojeek"]


def test_reconcile_engine_used_via_engines_list_not_no_results():
    payload = {"results": [{"engines": ["baidu"]}]}
    used, failed, no_results = reconcile(["baidu"], payload)
    assert used == ["baidu"]
    assert no_results == []


def test_reconcile_infobox_channel_counts_toward_used():
    # wikipedia produces only an infobox -> must count as used, not no_results
    payload = {"infoboxes": [{"engine": "wikipedia"}]}
    used, failed, no_results = reconcile(["wikipedia"], payload)
    assert used == ["wikipedia"]
    assert no_results == []


def test_reconcile_answers_channel_counts_toward_used():
    payload = {"answers": [{"engine": "wolframalpha"}]}
    used, failed, no_results = reconcile(["wolframalpha"], payload)
    assert used == ["wolframalpha"]
    assert no_results == []


def test_reconcile_failed_wins_over_no_results():
    # an engine in unresponsive is excluded from no_results even if no output
    payload = {"unresponsive_engines": [["bing", "err"]]}
    used, failed, no_results = reconcile(["bing"], payload)
    assert no_results == []
    assert [f.engine for f in failed] == ["bing"]


def test_reconcile_outputs_are_sorted():
    payload = {
        "results": [{"engine": "zeta"}, {"engine": "alpha"}],
        "unresponsive_engines": [["yankee", "x"], ["bravo", "y"]],
    }
    requested = ["zeta", "alpha", "yankee", "bravo", "whiskey", "delta"]
    used, failed, no_results = reconcile(requested, payload)
    assert used == sorted(used)
    assert [f.engine for f in failed] == ["bravo", "yankee"]
    assert no_results == sorted(no_results)
    assert no_results == ["delta", "whiskey"]


def test_reconcile_no_double_counting_used_engine_never_in_failed():
    #底线: an engine that both produced and errored still not classified twice
    # into no_results; it stays in used and (if listed) failed, never no_results
    payload = {
        "results": [{"engine": "flaky"}],
        "unresponsive_engines": [["flaky", "partial timeout"]],
    }
    used, failed, no_results = reconcile(["flaky"], payload)
    assert "flaky" in used
    assert [f.engine for f in failed] == ["flaky"]
    assert no_results == []


def test_reconcile_empty_requested():
    used, failed, no_results = reconcile([], {"results": [{"engine": "ddg"}]})
    assert no_results == []
    assert used == ["ddg"]


def test_reconcile_missing_unresponsive_key():
    used, failed, no_results = reconcile(["ddg"], {"results": [{"engine": "ddg"}]})
    assert failed == []


def test_reconcile_unresponsive_pair_stringifies_non_str_entries():
    # entry values coerced to str
    payload = {"unresponsive_engines": [[123, 456]]}
    used, failed, no_results = reconcile(["123"], payload)
    assert failed[0].engine == "123"
    assert failed[0].reason == "456"


def test_reconcile_accounts_for_every_requested_engine():
    # 底线②: no requested engine silently vanishes — union covers all requested
    payload = {
        "results": [{"engine": "a"}],
        "unresponsive_engines": [["b", "err"]],
    }
    requested = ["a", "b", "c"]
    used, failed, no_results = reconcile(requested, payload)
    accounted = set(used) | {f.engine for f in failed} | set(no_results)
    assert set(requested) <= accounted


# --------------------------------------------------------------------------- #
# instant_answers — infobox channel
# --------------------------------------------------------------------------- #
def _outcome(**kw):
    return SearchOutcome(**kw)


def test_instant_answers_empty_outcome():
    assert instant_answers(_outcome()) == []


def test_instant_answers_infobox_full_normalization():
    ob = _outcome(infoboxes=[{
        "infobox": "Python",
        "content": "A programming language",
        "id": "https://en.wikipedia.org/wiki/Python",
        "img_src": "https://img/py.png",
        "engine": "wikipedia",
    }])
    out = instant_answers(ob)
    assert out == [{
        "object": "answer", "type": "infobox",
        "title": "Python",
        "content": "A programming language",
        "url": "https://en.wikipedia.org/wiki/Python",
        "img_src": "https://img/py.png",
        "engine": "wikipedia",
    }]


def test_instant_answers_infobox_url_falls_back_to_url_field():
    ob = _outcome(infoboxes=[{"infobox": "X", "url": "https://u"}])
    out = instant_answers(ob)
    assert out[0]["url"] == "https://u"


def test_instant_answers_infobox_url_falls_back_to_urls_first_entry():
    ob = _outcome(infoboxes=[{
        "infobox": "X",
        "urls": [{"url": "https://first"}, {"url": "https://second"}],
    }])
    out = instant_answers(ob)
    assert out[0]["url"] == "https://first"


def test_instant_answers_infobox_id_preferred_over_urls():
    ob = _outcome(infoboxes=[{
        "infobox": "X",
        "id": "https://from-id",
        "urls": [{"url": "https://from-urls"}],
    }])
    out = instant_answers(ob)
    assert out[0]["url"] == "https://from-id"


def test_instant_answers_infobox_url_none_when_no_source():
    ob = _outcome(infoboxes=[{"infobox": "X"}])
    out = instant_answers(ob)
    assert out[0]["url"] is None


def test_instant_answers_infobox_urls_non_dict_entry_ignored():
    ob = _outcome(infoboxes=[{"infobox": "X", "urls": ["not-a-dict"]}])
    out = instant_answers(ob)
    assert out[0]["url"] is None


def test_instant_answers_infobox_skips_non_dict():
    ob = _outcome(infoboxes=["garbage", None, 123])
    assert instant_answers(ob) == []


def test_instant_answers_infobox_none_fields_normalized():
    # empty/"None" strings normalize to None via _s
    ob = _outcome(infoboxes=[{
        "infobox": "", "content": "None", "engine": None, "img_src": "",
    }])
    out = instant_answers(ob)
    assert out[0]["title"] is None
    assert out[0]["content"] is None
    assert out[0]["engine"] is None
    assert out[0]["img_src"] is None


# --------------------------------------------------------------------------- #
# instant_answers — answer channel
# --------------------------------------------------------------------------- #
def test_instant_answers_answer_dict_form():
    ob = _outcome(answers=[{
        "answer": "42", "url": "https://ans", "engine": "wolframalpha",
    }])
    out = instant_answers(ob)
    assert out == [{
        "object": "answer", "type": "answer", "title": None,
        "content": "42", "url": "https://ans",
        "img_src": None, "engine": "wolframalpha",
    }]


def test_instant_answers_answer_legacy_bare_string():
    ob = _outcome(answers=["just a string answer"])
    out = instant_answers(ob)
    assert out == [{
        "object": "answer", "type": "answer", "title": None,
        "content": "just a string answer", "url": None,
        "img_src": None, "engine": None,
    }]


def test_instant_answers_answer_bare_non_string_stringified():
    ob = _outcome(answers=[42])
    out = instant_answers(ob)
    assert out[0]["content"] == "42"


def test_instant_answers_answer_falsy_bare_dropped():
    # empty string / 0 / None are falsy -> dropped by the `elif a:` guard
    ob = _outcome(answers=["", 0, None])
    assert instant_answers(ob) == []


def test_instant_answers_answer_dict_none_fields():
    ob = _outcome(answers=[{"answer": "None", "url": "", "engine": "None"}])
    out = instant_answers(ob)
    assert out[0]["content"] is None
    assert out[0]["url"] is None
    assert out[0]["engine"] is None


def test_instant_answers_combines_infobox_and_answer_order():
    ob = _outcome(
        infoboxes=[{"infobox": "IB"}],
        answers=[{"answer": "A"}],
    )
    out = instant_answers(ob)
    assert len(out) == 2
    assert out[0]["type"] == "infobox"
    assert out[1]["type"] == "answer"


def test_instant_answers_every_entry_tagged_object_answer():
    # traceability 底线③: everything carries the object tag
    ob = _outcome(
        infoboxes=[{"infobox": "A"}, {"infobox": "B"}],
        answers=[{"answer": "C"}, "D"],
    )
    out = instant_answers(ob)
    assert all(item["object"] == "answer" for item in out)
    assert len(out) == 4
