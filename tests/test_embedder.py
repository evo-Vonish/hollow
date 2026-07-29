# -*- coding: utf-8 -*-
"""Unit tests for api/embedder.py(正文小图 data URI 内联;全 mock,无网络)。

覆盖:成功内联、Content-Length/流式中段超限、坏 CT、SSRF、下载异常、
张数配额、总字节配额、无引用原样。铁律:任何失败保留原 URL 引用。
"""
import base64

import pytest

from api import config
from api import embedder


class _FakeResp:
    def __init__(self, status=200, ct="image/png", chunks=(b"\x89PNG-data",),
                 cl: int | None = None):
        self.status_code = status
        self.headers = {"Content-Type": ct}
        if cl is not None:
            self.headers["Content-Length"] = str(cl)
        self._chunks = chunks

    # 故意不提供 __enter__/__exit__:curl_cffi 真实 Response 不支持 with 协议
    # (2026-07-29 线上实测 TypeError)——mock 同样裸用,防"with r"式回归。
    def close(self):
        pass

    def iter_content(self, n):
        return iter(self._chunks)


def _patch(monkeypatch, table: dict[str, _FakeResp]):
    """table: url -> _FakeResp;不在表里的 URL 抛异常(模拟网络失败)。"""
    monkeypatch.setattr(embedder.netguard, "vet_url", lambda u: None)
    def fake_get(url, **kw):
        r = table.get(url)
        if r is None:
            raise RuntimeError("boom")
        return r
    monkeypatch.setattr(embedder.creq, "get", fake_get)


MD = "para\n\n![A pic](https://cdn.ex.com/a.png)\n\nmid\n\n![B](https://cdn.ex.com/b.png)\n"


def test_embed_success(monkeypatch):
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(chunks=(b"tiny",))})
    out, stats = embedder.embed_markdown_images(MD)
    want = "data:image/png;base64," + base64.b64encode(b"tiny").decode()
    assert want in out
    assert "https://cdn.ex.com/a.png" not in out
    assert "https://cdn.ex.com/b.png" in out  # 失败的保留原引用
    assert stats["candidates"] == 2 and stats["embedded"] == 1
    assert stats["skipped_fetch_failed"] == 1


def test_embed_content_length_too_large(monkeypatch):
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(cl=999_999),
                         "https://cdn.ex.com/b.png": _FakeResp()})
    out, stats = embedder.embed_markdown_images(MD)
    assert "https://cdn.ex.com/a.png" in out  # 头即超限:不下载,保留引用
    assert stats["skipped_too_large"] == 1 and stats["embedded"] == 1


def test_embed_stream_overflow(monkeypatch):
    big = b"x" * (config.EMBED_IMAGE_BYTES + 1)
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(chunks=(big,)),
                         "https://cdn.ex.com/b.png": _FakeResp()})
    out, stats = embedder.embed_markdown_images(MD)
    assert "https://cdn.ex.com/a.png" in out
    assert stats["skipped_too_large"] == 1


def test_embed_bad_ct_and_ssrf(monkeypatch):
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(ct="text/html"),
                         "https://cdn.ex.com/b.png": _FakeResp()})
    monkeypatch.setattr(embedder.netguard, "vet_url",
                        lambda u: "private ip" if "b.png" in u else None)
    out, stats = embedder.embed_markdown_images(MD)
    assert stats["skipped_bad_ct"] == 1 and stats["skipped_ssrf"] == 1
    assert "https://cdn.ex.com/a.png" in out and "https://cdn.ex.com/b.png" in out


def test_embed_count_quota(monkeypatch):
    monkeypatch.setattr(config, "EMBED_IMAGES_MAX", 1)
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(),
                         "https://cdn.ex.com/b.png": _FakeResp()})
    out, stats = embedder.embed_markdown_images(MD)
    assert stats["embedded"] == 1 and stats["skipped_quota"] == 1
    assert "https://cdn.ex.com/b.png" in out  # 第二张配额耗尽,保留引用


def test_embed_total_bytes_quota(monkeypatch):
    monkeypatch.setattr(config, "EMBED_TOTAL_BYTES", 8)  # 第一张的 b64 就爆总量
    _patch(monkeypatch, {"https://cdn.ex.com/a.png": _FakeResp(chunks=(b"12345678",)),
                         "https://cdn.ex.com/b.png": _FakeResp()})
    out, stats = embedder.embed_markdown_images(MD)
    assert stats["embedded"] == 1 and stats["skipped_quota"] == 1


def test_embed_no_refs_passthrough():
    out, stats = embedder.embed_markdown_images("plain text, no images")
    assert out == "plain text, no images" and stats["candidates"] == 0
