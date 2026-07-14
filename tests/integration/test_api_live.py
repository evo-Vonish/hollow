# -*- coding: utf-8 -*-
"""活网关验收测试(集成)。默认跳过;设 HOLLOW_TEST_LIVE=1 且网关在 :8080 才跑。

断言集中在**不变量与契约**(禁止静默丢弃的账目对账、错误封套、字段存在性、SSRF 拦截、
限流 429),不断言具体引擎结果——后者受网络/引擎波动影响,不适合做回归。
本套件把本会话散落 scratchpad 的活测固化成可复现验收。
"""
import asyncio

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE = "http://127.0.0.1:8080"


@pytest.fixture(scope="module")
def client():
    c = httpx.Client(trust_env=False, timeout=120)
    try:
        r = c.get(f"{BASE}/healthz", timeout=5)
        if r.status_code != 200:
            pytest.skip(f"gateway /healthz {r.status_code}")
    except httpx.HTTPError as e:
        pytest.skip(f"gateway unreachable: {e!r}")
    yield c
    c.close()


# ---------- 账目不变量(禁止静默丢弃) ----------

def _fetch_invariants(f: dict) -> bool:
    a = f["requested"] == (f["ok"] + f["failed"] + f["timeout"] + f["blocked"] + f["no_content"])
    b = f["submitted"] == f["requested"] + f["deduped"]
    return a and b


def _research_invariants(items: list, f: dict) -> bool:
    a = f["requested"] == len(items) == (
        f["ok"] + f["failed"] + f["timeout"] + f["blocked"] + f["no_content"])
    b = f["pool"] == f["requested"] + f["cancelled"]
    c = f["ok"] <= f["target"]
    return a and b and c


# ---------- /v1/search ----------

def test_search_shape_and_fields(client):
    r = client.post(f"{BASE}/v1/search", json={"query": "transformer attention",
                                               "scenes": ["academic"]})
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "search" and j["id"].startswith("srch_")
    assert j["page"] == 1 and isinstance(j["usage"], dict)
    assert isinstance(j["search"]["engines_no_results"], list)  # 契约健壮批 #8
    for x in j["results"]:
        assert x["object"] == "search.result"
        assert "published_date" in x and "relevance" in x  # 字段存在(缺则 null,非缺键)


def test_search_unknown_engine_400(client):
    r = client.post(f"{BASE}/v1/search", json={"query": "x", "engines": ["no_such_engine_xyz"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_engine"


def test_search_unknown_scene_400(client):
    r = client.post(f"{BASE}/v1/search", json={"query": "x", "scenes": ["no_such_scene"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unknown_scene"


def test_search_invalid_search_param_maps_400_not_502(client):
    # SearXNG 判定非法的透传参数 → 400 invalid_search_param(不再误报 502)
    r = client.post(f"{BASE}/v1/search", json={"query": "x", "time_range": "bogus_value"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_search_param"


def test_search_domain_filter(client):
    r = client.post(f"{BASE}/v1/search", json={"query": "python programming language",
                                               "include_domains": ["wikipedia.org"]})
    assert r.status_code == 200
    for x in r.json()["results"]:
        h = httpx.URL(x["url"]).host.lower()
        assert h == "wikipedia.org" or h.endswith(".wikipedia.org")


def test_search_pagination_echo(client):
    r = client.post(f"{BASE}/v1/search", json={"query": "machine learning", "page": 2})
    assert r.status_code == 200 and r.json()["page"] == 2


# ---------- /v1/research ----------

def test_research_invariants_and_fields(client):
    r = client.post(f"{BASE}/v1/research", json={"query": "python asyncio event loop",
                                                 "top_n": 2, "mode": "fast"})
    assert r.status_code == 200
    j = r.json()
    assert _research_invariants(j["items"], j["fetch"]), j["fetch"]
    assert j["page"] == 1 and "fetches" in j["usage"]
    for it in j["items"]:
        for k in ("published_date", "highlights", "highlight_scores", "relevance", "rank"):
            assert k in it
        # ok 条目应有 highlights;非 ok 条目 highlights 为空(词汇抽取需正文)
        if it["fetch_status"] == "ok":
            assert len(it["highlights"]) == len(it["highlight_scores"])
        else:
            assert it["highlights"] == []


# ---------- /v1/fetch ----------

def test_fetch_invariants_dedup_ssrf(client):
    u = "https://en.wikipedia.org/wiki/Python_(programming_language)"
    r = client.post(f"{BASE}/v1/fetch", json={
        "urls": [u, u, "http://127.0.0.1:8888/healthz"], "escalate": False, "timeout": 10})
    assert r.status_code == 200
    j = r.json()
    assert _fetch_invariants(j["fetch"]), j["fetch"]
    assert j["fetch"]["deduped"] == 1  # 重复 URL 显式入账
    # 内网目标被 netguard 显式拦截(非静默丢)
    intranet = [it for it in j["items"] if "127.0.0.1" in it["url"]][0]
    assert intranet["fetch_status"] == "blocked" and "SSRF" in (intranet["error"] or "")
    # 条目顺序 == 输入去重后顺序
    assert j["items"][0]["url"] == u


@pytest.mark.parametrize("payload,code", [
    ({"urls": "ftp://example.com/f"}, "invalid_url"),
    ({"urls": "http://example.com:99999/"}, "invalid_url"),
    ({"urls": []}, "invalid_parameter"),
    ({"urls": [f"https://x{i}.example.com/" for i in range(11)]}, "invalid_parameter"),
])
def test_fetch_structural_400(client, payload, code):
    r = client.post(f"{BASE}/v1/fetch", json=payload)
    assert r.status_code == 400 and r.json()["error"]["code"] == code


def test_fetch_ignored_params(client):
    r = client.post(f"{BASE}/v1/fetch", json={"urls": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                                              "mode": "fast", "bogus_param": True})
    assert r.status_code == 200
    assert "bogus_param" in r.json().get("ignored_params", [])


def test_fetch_budget_cut(client):
    # 极小预算:两条来不及完成 → 全 timeout(budget_cut),不变量成立
    urls = ["https://en.wikipedia.org/wiki/Python_(programming_language)",
            "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)"]
    r = client.post(f"{BASE}/v1/fetch", json={"urls": urls, "budget": 0.1})
    assert r.status_code == 200
    j = r.json()
    assert _fetch_invariants(j["fetch"]), j["fetch"]
    assert j["fetch"]["budget_cut"] >= 1
    assert all("budget" in (it["error"] or "") for it in j["items"] if it["fetch_status"] == "timeout")


# ---------- 错误封套(契约健壮批 #5) ----------

def test_404_envelope(client):
    r = client.get(f"{BASE}/no-such-path")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and "detail" not in r.json()


def test_405_envelope(client):
    r = client.get(f"{BASE}/v1/research")  # POST-only
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


# ---------- 资源治理:429 shed-load(资源治理批 #4) ----------

def test_inflight_429_shed_load(client):
    async def fire(n):
        async with httpx.AsyncClient(trust_env=False, timeout=60) as ac:
            async def one():
                r = await ac.post(f"{BASE}/v1/fetch", json={
                    "urls": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                    "mode": "fast"})
                return r.status_code
            return await asyncio.gather(*[one() for _ in range(n)])
    codes = asyncio.run(fire(14))
    assert 429 in codes  # 超在飞上限的被 shed
    assert all(c in (200, 429) for c in codes)
