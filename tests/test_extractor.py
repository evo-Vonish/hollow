# -*- coding: utf-8 -*-
"""Unit tests for api/extractor.py (page asset extraction: outbound links / media).

Pure functions, no network. Fixture HTML covers:
- 相对 URL 绝对化(含 <base href>)、fragment 去重、非 http(s) 协议滤除
- internal/external 分类(同 host 与子域)
- img(src/data-src/srcset)、1x1 追踪像素、video(poster)、audio、<source>、iframe 嵌入
- og:/twitter: meta(高置信 source="meta"、与 tag 去重)
- 条数封顶
"""
from api.extractor import extract_assets

HTML = b"""<!doctype html>
<html><head>
  <base href="https://example.com/articles/">
  <meta property="og:image" content="https://cdn.example.com/hero.jpg">
  <meta property="og:video" content="https://cdn.example.com/trailer.mp4">
</head><body>
  <a href="/about">About us</a>
  <a href="https://example.com/about#team">About(dup, fragment)</a>
  <a href="https://sub.example.com/deep">Subdomain</a>
  <a href="https://othersite.net/x">External</a>
  <a href="mailto:a@b.c">mail</a>
  <a href="javascript:void(0)">js</a>
  <a href="#frag">anchor</a>
  <a href="  ">blank</a>
  <img src="pic1.jpg" alt="Pic One">
  <img data-src="/lazy.png" alt="Lazy">
  <img srcset="small.jpg 480w, big.jpg 1024w" alt="Srcset">
  <img src="https://cdn.example.com/hero.jpg" alt="dup of og:image">
  <img src="https://t.example.com/px.gif" width="1" height="1" alt="tracker">
  <video poster="/cover.jpg"><source src="movie.mp4" type="video/mp4"></video>
  <audio src="/pod.ogg"></audio>
  <iframe src="https://player.example.com/embed/123" title="Player"></iframe>
</body></html>"""

BASE = "https://example.com/articles/page-1"


def test_links_absolutize_dedup_classify():
    links, _ = extract_assets(HTML, BASE, True, False, 100, 50)
    urls = [l["url"] for l in links]
    # <base href> 生效:相对 URL 以 /articles/ 为基准
    assert urls[0] == "https://example.com/about"
    # fragment 去重:#team 变体不再出现
    assert urls.count("https://example.com/about") == 1
    assert "https://sub.example.com/deep" in urls
    assert "https://othersite.net/x" in urls
    # mailto/javascript/纯锚点/空白一律滤除;#team 变体被 fragment 去重 → 共 3 条
    assert all(u.startswith("http") for u in urls)
    assert len(urls) == 3
    by = {l["url"]: l for l in links}
    assert by["https://example.com/about"]["internal"] is True
    assert by["https://sub.example.com/deep"]["internal"] is True   # 子域算 internal
    assert by["https://othersite.net/x"]["internal"] is False
    assert by["https://example.com/about"]["text"] == "About us"


def test_media_all_sources_dedup():
    _, media = extract_assets(HTML, BASE, False, True, 100, 50)
    by_url = {m["url"]: m for m in media}
    # img:src / data-src / srcset 首候选
    assert "https://example.com/articles/pic1.jpg" in by_url
    assert "https://example.com/lazy.png" in by_url
    assert "https://example.com/articles/small.jpg" in by_url
    # og:image 与 <img> 重复 → 只出现一次(tag 先,文档序)
    assert list(m["url"] for m in media).count("https://cdn.example.com/hero.jpg") == 1
    assert by_url["https://cdn.example.com/hero.jpg"]["source"] == "tag"
    # video poster 与 <source>
    assert "https://example.com/cover.jpg" in by_url
    assert by_url["https://example.com/articles/movie.mp4"]["type"] == "video"
    assert by_url["https://example.com/pod.ogg"]["type"] == "audio"
    assert by_url["https://player.example.com/embed/123"]["type"] == "embed"
    # og:video(meta 源)
    assert by_url["https://cdn.example.com/trailer.mp4"]["source"] == "meta"
    # 1x1 追踪像素被丢弃
    assert "https://t.example.com/px.gif" not in by_url


def test_caps_respected():
    links, media = extract_assets(HTML, BASE, True, True, 2, 3)
    assert len(links) == 2
    assert len(media) == 3


def test_unrequested_side_is_none():
    links, media = extract_assets(HTML, BASE, True, False, 100, 50)
    assert links is not None and media is None
    links, media = extract_assets(HTML, BASE, False, False, 100, 50)
    assert links is None and media is None


def test_non_html_returns_empty_not_crash():
    links, media = extract_assets(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, BASE, True, True, 100, 50)
    assert links == [] and media == []


def test_no_base_tag_falls_back_to_page_url():
    html = b'<html><body><a href="rel/page">x</a></body></html>'
    links, _ = extract_assets(html, "https://a.com/dir/index.html", True, False, 100, 50)
    assert links[0]["url"] == "https://a.com/dir/rel/page"
