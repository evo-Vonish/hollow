# -*- coding: utf-8 -*-
"""trafilatura 正文净化 + 内容闸门(docs/design/03 §2.4、design/05)。

输入必须是 resp.body(bytes) —— trafilatura 自己做编码探测,
别喂 html_content/text(session-01 实测结论)。
"""
import trafilatura


def extract_gated(
    body: bytes, url: str, min_chars: int, include_images: bool = False
) -> tuple[str, str | None, bool, int | None]:
    """净化 + 内容闸门。返回 (status, content, purified, word_count)。

    - 正文达阈值: ("ok", markdown 正文, True, 字符数)
    - 空壳/反爬页/无正文(提取为空或不足 min_chars): ("no_content", None, False, 提取到的字符数或 None)
      —— 不当"成功"、不回退塞 raw HTML(那正是要拦的空壳),error 由调用方标注。

    include_images: 在 markdown 正文原位置保留图片引用(![alt](绝对 URL),
    trafilatura 2.x 原生支持,2026-07-29 实测)——外链资源夹到正文的第一层。
    """
    try:
        text = trafilatura.extract(body, url=url, output_format="markdown",
                                   include_images=include_images)
    except Exception:
        text = None
    if text and len(text) >= min_chars:
        return "ok", text, True, len(text)
    return "no_content", None, False, (len(text) if text else None)


def raw_html(body: bytes) -> tuple[str, str | None, bool, int | None]:
    """purify=false:用户要 raw HTML,不走内容闸门(status 恒 ok)。"""
    return "ok", _decode_raw(body), False, None


def _decode_raw(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")
