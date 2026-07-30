# -*- coding: utf-8 -*-
"""PDF 正文抽取(2026-07-30;fetcher static 档接入)。

此前 PDF 走"二进制终端短路"(no_content)——那是 PDF 穿甲修复时的诚实拒绝;
现在有了提取能力:GET 拿到的 PDF 经 pypdf 逐页抽文本,直接进 content 成为可读正文。

铁律(与 extractor/embedder 同款):提取失败/空(扫描件/图片型 PDF/损坏)返回
(None, pages),调用方出 no_content 如实入账,绝不拖垮主流程、绝不回退塞原始字节。
页数与总字符双封顶(config.PDF_*),防百页大部头烧内存烧载荷。
"""
import io

from api import config


def is_pdf(body: bytes) -> bool:
    return body[:5] == b"%PDF-"


def extract_pdf_text(body: bytes) -> tuple[str | None, int]:
    """提取 PDF 文本。返回 (markdown 化文本 | None, 总页数)。

    - 成功: "[PDF · N pages(提取 M 页)]\n\n" 头部 + 逐页文本(页间 \n\n)
    - 空(扫描件/无文本层)/损坏: (None, 页数或0)
    """
    from pypdf import PdfReader  # 延迟导入:pypdf 加载不便宜,只在真 PDF 时付

    try:
        reader = PdfReader(io.BytesIO(body))
        total = len(reader.pages)
        parts: list[str] = []
        chars = 0
        taken = 0
        for page in reader.pages[: config.PDF_PAGES_MAX]:
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                t = ""  # 单页失败不拖垮整档(底线②:丢的是这页,不是全部)
            if t:
                parts.append(t)
                chars += len(t)
                taken += 1
            if chars >= config.PDF_CHARS_MAX:
                break
        if not parts:
            return None, total
        header = f"[PDF · {total} pages"
        if taken < total:
            header += f"(提取 {taken} 页,封顶 {config.PDF_PAGES_MAX} 页/{config.PDF_CHARS_MAX} 字符)"
        header += "]"
        text = header + "\n\n" + "\n\n".join(parts)
        if len(text) > config.PDF_CHARS_MAX:
            text = text[: config.PDF_CHARS_MAX] + "…(truncated)"
        return text, total
    except Exception:
        return None, 0
