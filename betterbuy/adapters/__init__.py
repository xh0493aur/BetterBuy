"""适配器注册表与统一采集入口。

调用 ``collect(url)`` 即可：自动挑选适配器 -> 查缓存 -> 采集 -> 落库 -> 写采集日志。
"""
from __future__ import annotations

import os

from ..cache import db
from ..http_client import FetchError
from ..models import Product
from ..zero_cost import QuotaExceeded, quota
from .base import Adapter, BaseAdapter, UnsupportedLink
from .generic_html import GenericHtmlAdapter
from .jd import JDAdapter
from .pdd import PDDAdapter
from .steam import SteamAdapter
from .taobao import TaobaoAdapter

__all__ = [
    "ADAPTERS", "collect", "pick_adapter", "supported_platforms",
    "Adapter", "BaseAdapter", "UnsupportedLink",
    "SteamAdapter", "JDAdapter", "TaobaoAdapter", "PDDAdapter", "GenericHtmlAdapter",
]

CACHE_TTL = float(os.environ.get("BETTERBUY_CACHE_TTL", "3600"))

#: 顺序即优先级：专用平台适配器在前，通用兜底在后
ADAPTERS: list[Adapter] = [
    SteamAdapter(),
    JDAdapter(),
    TaobaoAdapter(),
    PDDAdapter(),
    GenericHtmlAdapter(),
]


def pick_adapter(url: str) -> Adapter:
    for adapter in ADAPTERS:
        try:
            if adapter.matches(url):
                return adapter
        except Exception:  # 单个适配器的匹配逻辑不应影响整体
            continue
    raise UnsupportedLink(f"没有适配器能处理这个链接：{url[:90]}")


def supported_platforms() -> list[dict[str, str]]:
    return [
        {"适配器": a.label, "标识": a.key, "采集方式": a.method,
         "覆盖域名": ", ".join(a.domains) if a.domains != ("*",) else "任意 http/https 页面（兜底）"}
        for a in ADAPTERS
    ]


def collect(url: str, *, use_cache: bool = True, ttl: float | None = None,
            force: bool = False) -> Product:
    """采集单个链接。失败抛 FetchError，并写入采集日志。"""
    try:
        adapter = pick_adapter(url)
    except UnsupportedLink as exc:
        raise FetchError(
            "链接格式不对：需要以 http:// 或 https:// 开头的完整商品页地址",
            kind="unsupported",
        ) from exc
    ttl = CACHE_TTL if ttl is None else ttl

    key = None
    if not force:
        try:
            key = adapter.cache_key(url)
        except Exception:
            key = None
    if use_cache and key and not force:
        age = db.cached_age_seconds(key)
        if age is not None and age <= ttl:
            cached = db.load_product(key)
            if cached:
                cached.origin = "cache"
                for source in cached.sources.values():
                    source.cached = True
                note = f"本次结果来自本地缓存（{age:.0f} 秒前采集），未重新请求网络。"
                cached.notes = [note] + [n for n in cached.notes if not n.startswith("本次结果来自本地缓存")]
                db.log_fetch(url, adapter.key, True, "cache", note)
                return cached

    try:
        quota.consume(adapter.key)
    except QuotaExceeded as exc:
        db.log_fetch(url, adapter.key, False, "quota", str(exc))
        raise FetchError(str(exc), kind="quota") from exc

    try:
        product = adapter.fetch(url)
    except FetchError as exc:
        db.log_fetch(url, adapter.key, False, exc.kind, exc.message, exc.http_status)
        raise
    except UnsupportedLink as exc:
        db.log_fetch(url, adapter.key, False, "unsupported", str(exc))
        raise FetchError(str(exc), kind="unsupported") from exc

    try:
        db.save_product(product)
    except Exception:  # 缓存失败不应让采集结果丢失
        pass
    db.log_fetch(url, adapter.key, True, "", product.status, 200)
    return product
