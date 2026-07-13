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


_ULA6 = ipaddress.ip_network("fc00::/7")  # IPv6 唯一本地地址(ULA)


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # IPv4-mapped IPv6(::ffff:127.0.0.1)按其内嵌 v4 判(否则映射写法可绕过)
    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return _ip_blocked(mapped)
        # v6 校准(底线④):只拦**明确内网**类别,刻意不用 is_private/is_reserved 全量——
        # 否则 Windows Teredo 给公网域名返回的合成 2001::/23(Python 判 is_private/reserved)会误杀。
        return (
            ip.is_loopback       # ::1
            or ip.is_link_local  # fe80::/10
            or ip.is_unspecified  # ::
            or ip.is_multicast
            or ip in _ULA6       # fc00::/7(含 fd00::/8,内网 IPv6 主力)
        )
    return (
        ip.is_private        # RFC1918
        or ip.is_loopback    # 127.0.0.0/8
        or ip.is_link_local  # 169.254.0.0/16(含云元数据 169.254.169.254)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_and_resolve(url: str) -> tuple[str | None, str | None]:
    """校验目的地 + 解析出 DNS-pin 用的 CURLOPT_RESOLVE 条目。返回 (reason, pin):
      reason 非 None            → 拒绝(blocked,原因串);
      reason None、pin 非 None  → 放行且需把 curl 钉到 pin(格式 'host:port:ip',v6 加括号);
      reason None、pin None     → 放行且无需钉(IP 字面量本就不走 DNS;或解析不了交抓取层报错)。
    pin 存在时钉住"vet 时解析到的那个 IP",关掉 vet→连接之间的 DNS-rebind 窗口(审查 #2)。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "malformed URL", None
    if parts.scheme not in ("http", "https"):
        return f"scheme {parts.scheme!r} not allowed (only http/https)", None
    host = parts.hostname
    if not host:
        return "no host in URL", None
    host = host.rstrip(".")  # 尾点绕过(127.0.0.1. / example.com.)
    if host.lower() in _ALLOW:
        return None, None
    # IP 字面量(含十进制/十六/八进制/短式等一切 curl 接受的数字写法):直接严格判,不走 DNS/不需钉
    ip = _as_ip(host)
    if ip is not None:
        return (f"refused internal/reserved address {host}" if _ip_blocked(ip) else None), None
    # 主机名:解析**双栈**(AF_UNSPEC),A/AAAA 全部校验(审查 #3:只发 AAAA 的内网 IPv6
    # 主机原先漏网)。Teredo 误杀由 _ip_blocked 的 v6 校准处理(不再靠 AF_INET-only 规避)。
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "invalid port in URL", None  # 坏端口干净拒绝,不让 ValueError 冒泡成崩溃(审查 LOW)
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return None, None  # 解析不了/无法 IDNA 编码:交抓取层报网络错误,不在这里崩(避免误杀 DNS 抖动)
    vetted: list[str] = []
    for info in infos:
        addr = info[4][0]
        try:
            if _ip_blocked(ipaddress.ip_address(addr)):
                return f"refused: {host} resolves to internal/reserved {addr}", None
        except ValueError:
            continue
        vetted.append(addr)
    if not vetted:
        return None, None
    # 钉到已校验 IP:优先 v4(--resolve 无歧义),否则 v6 加括号。curl 连该 IP,SNI/证书仍用原主机名。
    v4 = [a for a in vetted if ":" not in a]
    pick = v4[0] if v4 else vetted[0]
    pin_addr = pick if ":" not in pick else f"[{pick}]"
    return None, f"{host}:{port}:{pin_addr}"


def vet_url(url: str) -> str | None:
    """只校验、不解析钉 IP(浏览器档初始/落地复校用):放行 None,拒绝返回原因串。"""
    return check_and_resolve(url)[0]
