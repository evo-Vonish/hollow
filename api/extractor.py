# -*- coding: utf-8 -*-
"""页面资产抽取(外链/媒体)——与净化正交:同一份 HTML,另出两组结构化清单。

设计(2026-07-29 拍板,补"爬虫抽不出外链/资源"的能力缺口):
- 只对 HTTP ok 的 HTML 响应运行(在 _gate 内,static/浏览器双档共用);二进制闸门之前已短路;
- 相对 URL 以**最终落点**(重定向后)绝对化;尊重 <base href>;
- 去重保序(文档序,meta 最后),条数封顶(config.EXTRACT_LINKS_MAX / EXTRACT_MEDIA_MAX);
- 链接分类 internal(同 host 或子域)/external;剥离 fragment 去重;
- 媒体来源:img(src/data-src/srcset/lazy 系)、video(src/poster)、audio、<source>、
  iframe 嵌入,以及 og:/twitter: meta(高置信,标 source="meta");
- 1x1 追踪像素直接丢弃;data:/javascript: 等非 http(s) 一律滤除。
"""
from urllib.parse import urljoin, urlsplit, urldefrag

import lxml.html

_SKIP_PREFIXES = ("javascript:", "mailto:", "tel:", "data:", "ftp:", "file:")

# og:/twitter: meta → (媒体类型)
_META_MAP = {
    "og:image": "image", "og:image:secure_url": "image", "og:image:url": "image",
    "og:image:alt": None,  # 附属描述,不单独成条
    "twitter:image": "image", "twitter:image:src": "image",
    "og:video": "video", "og:video:url": "video", "og:video:secure_url": "video",
    "twitter:player": "video", "twitter:player:stream": "video",
    "og:audio": "audio",
}


def _abs(base: str, raw: str | None) -> str | None:
    """相对→绝对 + 协议过滤;返回 None 表示不可用。"""
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.lower().startswith(_SKIP_PREFIXES):
        return None
    absu = urljoin(base, raw)
    if not absu.startswith(("http://", "https://")):
        return None
    return absu


def _first_of_srcset(srcset: str | None) -> str | None:
    """srcset 取第一个候选 URL(描述符空格前)。"""
    if not srcset:
        return None
    first = srcset.split(",", 1)[0].strip()
    return first.split(" ")[0] if first else None


def extract_assets(
    body: bytes,
    base_url: str,
    want_links: bool,
    want_media: bool,
    links_max: int,
    media_max: int,
) -> tuple[list[dict] | None, list[dict] | None]:
    """从 HTML 字节流抽取外链与媒体清单。

    返回 (links, media);未请求的一侧返回 None(调用方据此省略字段)。
    解析失败/非 HTML:已请求侧返回 [](诚实空,不炸主流程)。
    """
    if not (want_links or want_media):
        return None, None
    try:
        doc = lxml.html.fromstring(body)
    except Exception:
        return ([] if want_links else None, [] if want_media else None)

    # <base href> 改变全文档相对 URL 的基准
    base_el = doc.find(".//base[@href]")
    if base_el is not None:
        base_url = urljoin(base_url, base_el.get("href", ""))

    links: list[dict] | None = None
    if want_links:
        links = []
        seen: set[str] = set()
        base_host = (urlsplit(base_url).hostname or "").lower()
        for el in doc.iter("a"):
            absu = _abs(base_url, el.get("href"))
            if absu is None:
                continue
            absu = urldefrag(absu)[0]
            if absu in seen:
                continue
            seen.add(absu)
            host = (urlsplit(absu).hostname or "").lower()
            internal = bool(base_host) and (host == base_host or host.endswith("." + base_host))
            text = " ".join(el.text_content().split())[:120] or None
            links.append({"url": absu, "text": text, "internal": internal})
            if len(links) >= links_max:
                break

    media: list[dict] | None = None
    if want_media:
        media = []
        mseen: set[str] = set()

        def add(raw: str | None, mtype: str, alt: str | None = None, source: str = "tag") -> None:
            if len(media) >= media_max:
                return
            absu = _abs(base_url, raw)
            if absu is None:
                return
            key = urldefrag(absu)[0]
            if key in mseen:
                return
            mseen.add(key)
            entry = {"url": key, "type": mtype, "source": source}
            if alt:
                entry["alt"] = alt[:160]
            media.append(entry)

        for el in doc.iter("img"):
            # 1x1 追踪像素:只在其明确声明尺寸时丢弃
            if (el.get("width") or "").strip() == "1" and (el.get("height") or "").strip() == "1":
                continue
            cand = (
                el.get("src")
                or el.get("data-src")
                or el.get("data-original")
                or el.get("data-lazy-src")
                or _first_of_srcset(el.get("srcset") or el.get("data-srcset"))
            )
            add(cand, "image", el.get("alt"))

        for el in doc.iter("video"):
            add(el.get("src"), "video")
            if el.get("poster"):
                add(el.get("poster"), "image", "poster")

        for el in doc.iter("audio"):
            add(el.get("src"), "audio")

        for el in doc.iter("source"):
            parent = el.getparent()
            ptag = parent.tag if parent is not None else ""
            mtype = {"video": "video", "audio": "audio", "picture": "image"}.get(ptag)
            if mtype:
                add(el.get("src") or _first_of_srcset(el.get("srcset")), mtype)

        for el in doc.iter("iframe"):
            add(el.get("src") or el.get("data-src"), "embed", el.get("title"))

        # og:/twitter: meta(高置信,放在文档序媒体之后;URL 去重自然滤掉重复)
        for el in doc.iter("meta"):
            prop = (el.get("property") or el.get("name") or "").strip().lower()
            mtype = _META_MAP.get(prop)
            if mtype:
                add(el.get("content"), mtype, None, "meta")

    return links, media
