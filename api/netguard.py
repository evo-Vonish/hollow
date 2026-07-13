# -*- coding: utf-8 -*-
"""SSRF 目的地校验(2026-07-14,安全批)。

hollow 本质是"按 query 抓任意 URL"的代理,抓取目标可能来自被 SEO 污染 / 攻击者
影响的搜索结果。curl_cffi 的 follow_redirects="safe" 只检查重定向的每一跳、不检查
原始 URL,且在走 localhost 代理时会误杀一切重定向,历史上被翻成 True——反而把 SSRF
防护整个关掉(审查确认)。故 hollow 自己做一道**不依赖代理/curl 配置**的目的地校验:
解析目标主机 → 落在内网/环回/链路本地(含云元数据 169.254.169.254)/保留段则拒绝。

用法:抓取任何 URL 前 vet_url(url);手动跟随重定向时对每一跳 Location 也 vet。
"""
import ipaddress
import os
import socket
from urllib.parse import urlsplit

# 显式放行的内网主机(逗号分隔);默认空。仅用于确有内网抓取需求的可信部署。
_ALLOW = {h.strip().lower() for h in os.environ.get("HOLLOW_ALLOW_INTERNAL_HOSTS", "").split(",") if h.strip()}


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """把 host 归一成 IP —— 覆盖 curl/浏览器接受的一切 IPv4 数字写法:
    标准点分、十进制整数(2130706433)、十六进制(0x7f000001)、八进制(0177.0.0.1)、
    短式(127.1)、IPv6 字面量。识别不出(真域名)返回 None,交给上层 DNS 解析。"""
    try:
        return ipaddress.ip_address(host)  # 标准 v4/v6 字面量
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(host))  # inet_aton:短式/十六/八进制(平台 libc)
    except OSError:
        pass
    try:  # 兜底:纯整数(十进制 / 0x 十六 / 0o 八)
        val = int(host, 0) if host[:2].lower() in ("0x", "0o", "0b") else int(host)
        if 0 <= val <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(val)
    except (ValueError, ipaddress.AddressValueError):
        pass
    return None


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private        # RFC1918 / IPv6 ULA fc00::/7
        or ip.is_loopback    # 127.0.0.0/8, ::1
        or ip.is_link_local  # 169.254.0.0/16(含云元数据 169.254.169.254), fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def vet_url(url: str) -> str | None:
    """校验单个 URL 的目的地。放行返回 None;拒绝返回原因字符串(供 blocked 上报)。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "malformed URL"
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme!r} not allowed (only http/https)"
    host = parts.hostname
    if not host:
        return "no host in URL"
    host = host.rstrip(".")  # 尾点绕过(127.0.0.1. / example.com.)
    host_l = host.lower()
    if host_l in _ALLOW:
        return None
    # IP 字面量(含十进制/十六/八进制/短式等一切 curl 接受的数字写法):直接严格判,不走 DNS
    ip = _as_ip(host)
    if ip is not None:
        return f"refused internal/reserved address {host}" if _ip_blocked(ip) else None
    # 主机名:只解析 IPv4(AF_INET)。不查 IPv6——Windows Teredo 会给公网域名返回合成的
    # 2001::/23 地址(Python 判为 is_private)造成误杀;且这类机器实际走 IPv4 抓取。
    # 残留:纯 IPv6 内网主机名不被拦(次要向量,记入待办)。
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   family=socket.AF_INET, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None  # 解析不了交给抓取层报网络错误,不在这里拦(避免误杀临时 DNS 抖动)
    for info in infos:
        addr = info[4][0]
        try:
            if _ip_blocked(ipaddress.ip_address(addr)):
                return f"refused: {host} resolves to internal/reserved {addr}"
        except ValueError:
            continue
    return None
