# -*- coding: utf-8 -*-
"""Unit tests for api/pdf_extract.py(PDF 正文抽取;reportlab 内存生成夹具,无网络)。

覆盖:多页提取+头部标注、页数封顶、字符封顶、空 PDF(无文本层)诚实 None、
损坏字节诚实 None、is_pdf 魔数。
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from api import config
from api import pdf_extract


def _make_pdf(texts: list[str]) -> bytes:
    """每元素一页的 PDF;空字符串 = 无文本层页。"""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for t in texts:
        if t:
            c.drawString(72, 720, t)
        c.showPage()
    c.save()
    return buf.getvalue()


def test_is_pdf_magic():
    assert pdf_extract.is_pdf(_make_pdf(["hi"]))
    assert not pdf_extract.is_pdf(b"<html></html>")
    assert not pdf_extract.is_pdf(b"")


def test_extract_multipage_with_header():
    body = _make_pdf(["Page one content", "Page two content", "Page three content"])
    text, pages = pdf_extract.extract_pdf_text(body)
    assert pages == 3
    assert text.startswith("[PDF · 3 pages]")
    assert "Page one content" in text and "Page three content" in text


def test_extract_page_cap(monkeypatch):
    monkeypatch.setattr(config, "PDF_PAGES_MAX", 2)
    body = _make_pdf([f"Page {i} unique text" for i in range(5)])
    text, pages = pdf_extract.extract_pdf_text(body)
    assert pages == 5
    assert "提取" in text and "封顶" in text  # 头部如实标注截断
    assert "Page 0 unique" in text and "Page 4 unique" not in text


def test_extract_char_cap(monkeypatch):
    monkeypatch.setattr(config, "PDF_CHARS_MAX", 200)
    body = _make_pdf(["x" * 150, "y" * 150, "z" * 150])
    text, _ = pdf_extract.extract_pdf_text(body)
    assert len(text) <= 200 + 20  # 截断 + 省略标注余量
    assert "truncated" in text or text.endswith("…")


def test_empty_pdf_honest_none():
    body = _make_pdf(["", ""])  # 两页全无文本层(模拟扫描件)
    text, pages = pdf_extract.extract_pdf_text(body)
    assert text is None and pages == 2


def test_corrupted_honest_none():
    text, pages = pdf_extract.extract_pdf_text(b"%PDF-1.4 broken garbage not a real pdf")
    assert text is None and pages == 0
