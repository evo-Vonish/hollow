# -*- coding: utf-8 -*-
"""SSRF 目的地校验(api/netguard.py)的纯单元测试。

网络无关:所有 DNS(socket.getaddrinfo)一律 monkeypatch,IP 字面量路径本就不触网。
覆盖:IPv4 数字写法绕过、v4/v6 内网/保留段拦截、公网放行、scheme/host/port 边界、
allowlist、主机名双栈解析(pin 生成 / 内网拒绝 / 解析失败放行)、vet_url==check_and_resolve[0]。
"""
import socket

import pytest

from api import netguard


# ----------------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------------
def blocked(url):
    """vet_url 拒绝返回原因串(非 None)= blocked。"""
    return netguard.vet_url(url) is not None


def _ai(*addrs, port=443):
    """构造 getaddrinfo 返回的 addrinfo 元组列表。
    addr 含 ':' 视为 v6,否则 v4;sockaddr 结构与真实 getaddrinfo 一致(code 只取 info[4][0])。"""
    out = []
    for a in addrs:
        if ":" in a:
            out.append((socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, port, 0, 0)))
        else:
            out.append((socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, port)))
    return out


# ============================================================================
# IPv4 数字写法绕过 —— 全部归一到 127.0.0.1 / 内网 → 必须拦截
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://2130706433/",            # 十进制整数 = 127.0.0.1
    "http://0x7f000001/",            # 十六进制整数 = 127.0.0.1
    "http://0177.0.0.1/",            # 八进制点分 = 127.0.0.1
    "http://127.1/",                 # 短式 = 127.0.0.1
    "http://127.0.0.1./",            # 尾点 = 127.0.0.1
    "http://127.0.0.1:8888/x",       # 标准点分 + 端口
])
def test_ipv4_numeric_encodings_blocked(url):
    assert blocked(url), f"{url} 应被识别为环回并拦截"


def test_decimal_bypass_reason_mentions_refused():
    reason = netguard.vet_url("http://2130706433/")
    assert reason is not None
    assert "refused" in reason


# ============================================================================
# IPv4 内网 / 环回 / 链路本地 / 元数据 —— 拦截
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",                          # 环回
    "http://10.0.0.1/",                           # RFC1918
    "http://192.168.1.1/",                        # RFC1918
    "http://172.16.0.1/",                         # RFC1918
    "http://169.254.169.254/latest/meta-data/",   # 云元数据(链路本地)
    "http://0.0.0.0/",                            # unspecified
    "http://255.255.255.255/",                    # 保留/广播
])
def test_ipv4_internal_blocked(url):
    assert blocked(url)


# ============================================================================
# IPv4 公网字面量 —— 放行,且不需 pin(IP 字面量不走 DNS)
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://1.1.1.1/",
    "http://8.8.8.8/",
    "https://93.184.216.34/",
])
def test_ipv4_public_allowed_no_pin(url):
    assert netguard.check_and_resolve(url) == (None, None)


# ============================================================================
# IPv6 字面量:内网拦截
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://[::1]/",                         # 环回
    "http://[fd00::1]:8080/",                # ULA(fc00::/7)
    "http://[fc00::1]/",                     # ULA 下界
    "http://[fe80::1]/",                     # 链路本地
    "http://[::]/",                          # unspecified
    "http://[ff02::1]/",                     # 组播
    "http://[::ffff:127.0.0.1]/",            # v4-mapped 环回
    "http://[::ffff:169.254.169.254]/",      # v4-mapped 元数据
    "http://[::ffff:10.0.0.1]/",             # v4-mapped 私网
])
def test_ipv6_internal_blocked(url):
    assert blocked(url)


# ============================================================================
# IPv6 字面量:公网 / Teredo-类合成段放行(不误杀,底线④校准)
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://[2606:4700:4700::1111]/",   # Cloudflare DNS
    "http://[2001:4860:4860::8888]/",   # Google DNS
])
def test_ipv6_public_allowed_no_pin(url):
    assert netguard.check_and_resolve(url) == (None, None)


# ============================================================================
# scheme 拒绝
# ============================================================================
@pytest.mark.parametrize("url,scheme", [
    ("ftp://example.com/", "ftp"),
    ("file:///etc/passwd", "file"),
    ("gopher://example.com/", "gopher"),
])
def test_scheme_rejected(url, scheme):
    reason = netguard.vet_url(url)
    assert reason is not None
    assert scheme in reason and "not allowed" in reason


def test_https_scheme_ok_shape():
    # https + 公网 IP → 放行
    assert netguard.check_and_resolve("https://1.1.1.1/") == (None, None)


# ============================================================================
# no-host
# ============================================================================
def test_no_host():
    reason = netguard.vet_url("http:///path/only")
    assert reason == "no host in URL"


# ============================================================================
# 坏端口 → 干净拒绝("invalid port in URL"),不崩(审查 LOW)
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://example.com:99999/",   # 超范围
    "http://example.com:abc/",     # 非数字
])
def test_bad_port_clean_reject_not_crash(url):
    reason = netguard.vet_url(url)   # 不应抛异常
    assert reason == "invalid port in URL"


# ============================================================================
# allowlist(HOLLOW_ALLOW_INTERNAL_HOSTS)—— patch 模块 _ALLOW 集合
# ============================================================================
def test_allowlist_bypasses_internal_block(monkeypatch):
    # 未放行时内网主机名会走解析;放行后直接 (None, None) 短路,不触 DNS
    monkeypatch.setattr(netguard, "_ALLOW", {"intranet.local"})
    # 即便打桩把 DNS 指向内网,也应因命中 allowlist 而不调用它
    def _boom(*a, **k):
        raise AssertionError("allowlist 命中不应触发 DNS 解析")
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _boom)
    assert netguard.check_and_resolve("http://intranet.local/x") == (None, None)


def test_allowlist_case_insensitive(monkeypatch):
    monkeypatch.setattr(netguard, "_ALLOW", {"intranet.local"})
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应解析")))
    assert netguard.check_and_resolve("http://INTRANET.LOCAL/x") == (None, None)


def test_allowlist_does_not_cover_other_hosts(monkeypatch):
    monkeypatch.setattr(netguard, "_ALLOW", {"intranet.local"})
    monkeypatch.setattr(netguard.socket, "getaddrinfo", lambda *a, **k: _ai("10.1.2.3", port=80))
    reason, pin = netguard.check_and_resolve("http://other.host/x")
    assert reason is not None and pin is None


# ============================================================================
# 主机名双栈解析路径(monkeypatch getaddrinfo,零真实 DNS)
# ============================================================================
def test_hostname_public_resolution_yields_pin(monkeypatch):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("93.184.216.34", port=443))
    reason, pin = netguard.check_and_resolve("https://example.com/wiki/X")
    assert reason is None
    assert pin == "example.com:443:93.184.216.34"


def test_hostname_default_port_http(monkeypatch):
    # http 无显式端口 → 默认 80,pin 端口应为 80
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("93.184.216.34", port=80))
    reason, pin = netguard.check_and_resolve("http://example.com/")
    assert reason is None
    assert pin == "example.com:80:93.184.216.34"


def test_hostname_explicit_port_in_pin(monkeypatch):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("93.184.216.34", port=8443))
    reason, pin = netguard.check_and_resolve("https://example.com:8443/")
    assert reason is None
    assert pin == "example.com:8443:93.184.216.34"


def test_hostname_internal_resolution_blocked(monkeypatch):
    # 主机名解析到内网 IP → 拒绝(SSRF via DNS)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("10.0.0.5", port=443))
    reason, pin = netguard.check_and_resolve("https://evil.example/")
    assert pin is None
    assert reason is not None
    assert "10.0.0.5" in reason and "evil.example" in reason


def test_hostname_metadata_resolution_blocked(monkeypatch):
    # DNS-rebind 到 169.254.169.254 也必须拦(底线②:不静默放过)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("169.254.169.254", port=443))
    reason, pin = netguard.check_and_resolve("https://rebind.example/")
    assert pin is None and reason is not None


def test_hostname_dualstack_v4_preferred_in_pin(monkeypatch):
    # 同时返回 v6 + v4 公网 → pin 优先选 v4(--resolve 无歧义)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("2606:4700:4700::1111", "93.184.216.34", port=443))
    reason, pin = netguard.check_and_resolve("https://example.com/")
    assert reason is None
    assert pin == "example.com:443:93.184.216.34"


def test_hostname_v6only_pin_bracketed(monkeypatch):
    # 仅 v6 公网 → pin 用方括号包裹
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("2606:4700:4700::1111", port=443))
    reason, pin = netguard.check_and_resolve("https://example.com/")
    assert reason is None
    assert pin == "example.com:443:[2606:4700:4700::1111]"


def test_hostname_dualstack_internal_v6_blocks_even_with_public_v4(monkeypatch):
    # 审查 #3:AAAA 是内网 v6、A 是公网 v4 → 任一内网即拒(双栈全校验)
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("fd00::1", "93.184.216.34", port=443))
    reason, pin = netguard.check_and_resolve("https://mixed.example/")
    assert pin is None and reason is not None
    assert "fd00::1" in reason


def test_hostname_gaierror_allows_no_pin(monkeypatch):
    # 解析失败(gaierror)→ 放行且无 pin,交抓取层报网络错误(不误杀 DNS 抖动)
    def _fail(*a, **k):
        raise socket.gaierror("Name or service not known")
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _fail)
    assert netguard.check_and_resolve("https://nxdomain.example/") == (None, None)


def test_hostname_idna_unicodeerror_allows_no_pin(monkeypatch):
    def _fail(*a, **k):
        raise UnicodeError("IDNA encode failed")
    monkeypatch.setattr(netguard.socket, "getaddrinfo", _fail)
    assert netguard.check_and_resolve("https://xn--broken.example/") == (None, None)


def test_hostname_empty_resolution_allows_no_pin(monkeypatch):
    # getaddrinfo 返回空 / 全部无法解析成 ip_address → vetted 空 → (None, None)
    monkeypatch.setattr(netguard.socket, "getaddrinfo", lambda *a, **k: [])
    assert netguard.check_and_resolve("https://empty.example/") == (None, None)


def test_hostname_unparseable_addr_skipped(monkeypatch):
    # info[4][0] 非法 → 该条跳过(不崩);剩余公网 v4 仍生成 pin
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("not-an-ip", 443)),
    ] + _ai("93.184.216.34", port=443)
    monkeypatch.setattr(netguard.socket, "getaddrinfo", lambda *a, **k: infos)
    reason, pin = netguard.check_and_resolve("https://example.com/")
    assert reason is None
    assert pin == "example.com:443:93.184.216.34"


# ============================================================================
# malformed URL → "malformed URL"(urlsplit 抛 ValueError 的极端情形)
# ============================================================================
def test_malformed_url_reason(monkeypatch):
    monkeypatch.setattr(netguard, "urlsplit",
                        lambda u: (_ for _ in ()).throw(ValueError("bad")))
    assert netguard.vet_url("http://whatever/") == "malformed URL"


# ============================================================================
# 契约:vet_url(u) 恒等于 check_and_resolve(u)[0]
# ============================================================================
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://1.1.1.1/",
    "ftp://example.com/",
    "http:///nohost",
    "http://example.com:99999/",
    "http://[::1]/",
    "http://[2606:4700:4700::1111]/",
    "http://2130706433/",
])
def test_vet_url_equals_check_and_resolve_reason(url):
    assert netguard.vet_url(url) == netguard.check_and_resolve(url)[0]


def test_vet_url_equals_reason_for_resolved_host(monkeypatch):
    monkeypatch.setattr(netguard.socket, "getaddrinfo",
                        lambda *a, **k: _ai("93.184.216.34", port=443))
    url = "https://example.com/"
    assert netguard.vet_url(url) == netguard.check_and_resolve(url)[0]
