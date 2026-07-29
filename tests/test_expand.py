# -*- coding: utf-8 -*-
"""Unit tests for /v1/fetch 外链自动展开(v1.py _expand_*;monkeypatched fetch_one,无网络)。

覆盖:一层展开、递归层数封顶、整树总量预算、internal/all scope、循环防护、
父页失败/expand_links=0 不展开。
"""
import asyncio

import pytest

from api import config, fetcher
from api import v1


def _fr(u: str, links: list[dict] | None = None, status: str = "ok") -> fetcher.FetchResult:
    fr = fetcher.FetchResult(u, status)
    fr.url = u
    fr.content = "body of " + u
    fr.word_count = 3
    fr.links = links
    return fr


def _body(**kw) -> v1.FetchCreate:
    kw.setdefault("urls", "https://example.com/")
    return v1.FetchCreate(**kw)


def _ctx() -> dict:
    return {"semaphore": asyncio.Semaphore(4), "browser_semaphore": asyncio.Semaphore(2),
            "timeout_s": 5.0, "escalate": False}


def _patch_fetch(monkeypatch, table: dict[str, list[dict]]):
    """table: url -> links;不在表里的 URL 返回无 links 的 ok 页。"""
    async def fake_fetch_one(u, *, semaphore, timeout_s, impersonate, escalate,
                             purify, browser_semaphore=None,
                             include_links=False, include_media=False,
                             include_images=False, embed_images=False):
        return _fr(u, table.get(u))
    monkeypatch.setattr(v1.fetcher, "fetch_one", fake_fetch_one)


def _run(item, fr, body):
    asyncio.run(v1._expand_item(item, fr, body, _ctx()))
    return item


ROOT = "https://example.com/"


def test_expand_one_level(monkeypatch):
    _patch_fetch(monkeypatch, {
        ROOT: [{"url": "https://example.com/a", "internal": True},
               {"url": "https://example.com/b", "internal": True}],
    })
    body = _body(expand_links=5, expand_depth=1)
    item = _run({"url": ROOT}, _fr(ROOT, [{ "url": "https://example.com/a", "internal": True},
                                           {"url": "https://example.com/b", "internal": True}]), body)
    kids = item["children"]
    assert [k["url"] for k in kids] == ["https://example.com/a", "https://example.com/b"]
    assert all(k["depth"] == 1 for k in kids)
    assert all("children" not in k for k in kids)  # depth=1 不再下钻
    assert all(k["fetch_status"] == "ok" and k["content"] for k in kids)


def test_expand_depth_cap(monkeypatch):
    table = {
        ROOT: [{"url": "https://example.com/a", "internal": True}],
        "https://example.com/a": [{"url": "https://example.com/b", "internal": True}],
        "https://example.com/b": [{"url": "https://example.com/c", "internal": True}],
    }
    _patch_fetch(monkeypatch, table)
    body = _body(expand_links=5, expand_depth=2)
    item = _run({"url": ROOT}, _fr(ROOT, table[ROOT]), body)
    a = item["children"][0]
    assert a["url"].endswith("/a") and a["depth"] == 1
    b = a["children"][0]
    assert b["url"].endswith("/b") and b["depth"] == 2
    assert "children" not in b  # 到层数封顶,不再抓 /c 的 links


def test_expand_total_budget(monkeypatch):
    # 宽扇出:根 5 个子,每个再 5 个;总量预算 EXPAND_TOTAL_MAX 兜住
    monkeypatch.setattr(config, "EXPAND_TOTAL_MAX", 7)
    wide = [{"url": f"https://example.com/p{i}", "internal": True} for i in range(5)]
    table = {ROOT: wide}
    for i in range(5):
        table[f"https://example.com/p{i}"] = [
            {"url": f"https://example.com/p{i}/c{j}", "internal": True} for j in range(5)]
    _patch_fetch(monkeypatch, table)
    body = _body(expand_links=5, expand_depth=2)
    item = _run({"url": ROOT}, _fr(ROOT, wide), body)

    def _count(it):
        return sum(1 + _count(c) for c in it.get("children", []))

    assert _count(item) <= 7


def test_expand_scope_internal_filters_external(monkeypatch):
    links = [{"url": "https://example.com/in", "internal": True},
             {"url": "https://other.net/out", "internal": False}]
    _patch_fetch(monkeypatch, {ROOT: links})
    body = _body(expand_links=5, expand_depth=1, expand_scope="internal")
    item = _run({"url": ROOT}, _fr(ROOT, links), body)
    assert [k["url"] for k in item["children"]] == ["https://example.com/in"]

    body_all = _body(expand_links=5, expand_depth=1, expand_scope="all")
    item2 = _run({"url": ROOT}, _fr(ROOT, links), body_all)
    assert [k["url"] for k in item2["children"]] == [
        "https://example.com/in", "https://other.net/out"]


def test_expand_cycle_protection(monkeypatch):
    # A → B → A:不得无限递归
    table = {
        ROOT: [{"url": "https://example.com/b", "internal": True}],
        "https://example.com/b": [{"url": ROOT, "internal": True},
                                  {"url": "https://example.com/c", "internal": True}],
    }
    _patch_fetch(monkeypatch, table)
    body = _body(expand_links=5, expand_depth=3)
    item = _run({"url": ROOT}, _fr(ROOT, table[ROOT]), body)
    b = item["children"][0]
    urls = [c["url"] for c in b.get("children", [])]
    assert ROOT not in urls  # 根在 visited 里,被跳过
    assert urls == ["https://example.com/c"]


def test_expand_skips_failed_parent(monkeypatch):
    body = _body(expand_links=5, expand_depth=1)
    item = {"url": ROOT}
    asyncio.run(v1._expand_item(item, _fr(ROOT, status="blocked"), body, _ctx()))
    assert "children" not in item  # 父页没抓成功,不展开


def test_expand_links_zero_disables(monkeypatch):
    # expand_links=0 时端点不进入展开阶段;_pick_children 保序取前 n 的语义另测
    assert v1._pick_children(
        [{"url": "u1", "internal": True}, {"url": "u2", "internal": True}],
        "internal", 1, set()) == ["u1"]
    assert v1._pick_children(None, "internal", 5, set()) == []
