"""对外网访问目标做安全校验：只允许公网 http/https，禁止内网与本地地址。

独立的轻量模块，避免和旧的 scraper.py 互相依赖。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["UnsafeUrl", "check_url"]


class UnsafeUrl(Exception):
    """目标地址不允许访问（可展示给用户）。"""


def _is_bad_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not ip.is_global


def _resolve_and_check(hostname: str) -> None:
    """解析域名并确认解析结果都是公网地址（防止 DNS 指向内网）。"""
    if hostname == "localhost" or hostname.endswith((".local", ".internal", ".localhost")):
        raise UnsafeUrl("不允许访问内网或本地地址")
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        # 解析失败交给后续请求层报网络错误，这里不阻断
        return
    for info in infos:
        address = info[4][0]
        if _is_bad_ip(address):
            raise UnsafeUrl(f"域名 {hostname} 解析到内网地址，已拒绝访问")


def check_url(url: str) -> str:
    """校验并返回去空格后的 URL；不合法时抛 UnsafeUrl。"""
    url = (url or "").strip()
    if not url:
        raise UnsafeUrl("链接为空")
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1].strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl("请输入以 http:// 或 https:// 开头的商品链接")
    if not parts.hostname:
        raise UnsafeUrl("链接缺少域名")
    hostname = parts.hostname.lower()
    if _is_bad_ip(hostname) or hostname == "localhost":
        raise UnsafeUrl("不允许访问内网或本地地址")
    if hostname.endswith((".local", ".internal", ".localhost")):
        raise UnsafeUrl("不允许访问内网或本地地址")
    _resolve_and_check(hostname)
    return url
