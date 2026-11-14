"""竞品发现模块。

两条真实数据链路，互不替代：

1. **同类竞品**（同平台，按类型 + 价格区间）
   - Steam：商店公开搜索页，robots 允许；
   - 京东/淘宝/拼多多：各自开放接口的关键词检索（需密钥）。

2. **跨平台疑似同款**（不同平台，按品牌 + 型号 + 标题相似度）
   - 见 :mod:`betterbuy.discovery.ecommerce`，结果只能叫「疑似同款」，
     因为没有任何平台提供官方的跨平台同款标识。

任何一步拿不到结果都会返回明确的不可用原因，绝不用随机商品或虚构数据填充。
"""
from __future__ import annotations

from ..models import Product
from .ecommerce import CrossPlatformResult, same_product_across, search_platform
from .steam_search import Candidate, DiscoveryResult, SteamSearchDiscovery

__all__ = [
    "Candidate", "DiscoveryResult", "SteamSearchDiscovery", "CrossPlatformResult",
    "search_platform", "DISCOVERY_SOURCES", "discover", "find_same_product_elsewhere",
    "ECOMMERCE_PLATFORMS",
]

DISCOVERY_SOURCES = [SteamSearchDiscovery()]

#: 这三个平台的竞品检索走开放接口，不走公开页面
ECOMMERCE_PLATFORMS = {"京东", "淘宝", "天猫", "拼多多"}


def discover(product: Product, *, min_price: float | None = None, max_price: float | None = None,
             limit: int = 8) -> DiscoveryResult:
    """为给定商品自动寻找同类竞品。找不到时返回 ok=False 与原因。"""
    for source in DISCOVERY_SOURCES:
        if source.matches(product):
            return source.search(product, min_price=min_price, max_price=max_price, limit=limit)

    if product.platform in ECOMMERCE_PLATFORMS:
        from .ecommerce import PLATFORM_LABEL
        platform_key = PLATFORM_LABEL.get(product.platform, "")
        return DiscoveryResult(
            base_id=product.product_id,
            ok=False,
            note=(
                f"「{product.platform}」的同类竞品检索走开放接口关键词搜索，"
                "请用下方「跨平台疑似同款」区块查看；本区块只做同平台同类推荐。"
                if platform_key else
                f"当前没有可用于 {product.platform} 的竞品发现数据源。"
            ),
            candidates=[],
        )

    return DiscoveryResult(
        base_id=product.product_id,
        ok=False,
        note=f"当前没有可用于 {product.platform} 的竞品发现数据源。"
             "已保留扩展接口，但不会用随机商品或虚构数据填充竞品列表。",
        candidates=[],
    )


def find_same_product_elsewhere(product: Product, *, limit: int = 3) -> tuple[list[Product], str]:
    """跨平台疑似同款。返回 (商品列表, 说明)。"""
    if product.platform in ECOMMERCE_PLATFORMS:
        result = same_product_across([product], limit=limit)
        return result.matches, result.note
    return [], (
        f"「{product.platform}」商品不在京东/淘宝/拼多多商品体系里，"
        "没有跨平台同款可比。跨平台同款检索只对电商平台的商品生效。"
    )
