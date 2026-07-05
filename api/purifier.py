# -*- coding: utf-8 -*-
"""trafilatura 正文净化,失败回退 raw HTML(docs/design/03 §2.4)。

输入必须是 resp.body(bytes) —— trafilatura 自己做编码探测,
别喂 html_content/text(session-01 实测结论)。
"""
import trafilatura


def purify(body: bytes, url: str) -> tuple[str | None, bool, int | None]:
    """返回 (content, purified, word_count)。

    - 净化成功: (markdown 正文, True, 可见文本字符数)
    - 净化失败: (raw HTML 解码串, False, None) —— 回退,不丢内容
    """
    try:
        text = trafilatura.extract(body, url=url, output_format="markdown")
    except Exception:
        text = None
    if text:
        return text, True, len(text)
    return _decode_raw(body), False, None


def raw_html(body: bytes) -> tuple[str | None, bool, int | None]:
    """purify=false 时直接返回 raw HTML。"""
    return _decode_raw(body), False, None


def _decode_raw(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")
