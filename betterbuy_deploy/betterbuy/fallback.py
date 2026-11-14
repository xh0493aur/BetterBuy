"""缺数据时的降级出口：给出「去平台自己看」的跳转链接。

原则：拿不到可靠数据时，**不猜价格、不猜评分**，但也不能让用户卡住。
所以提供一个明确的动作——把商品名丢给平台的搜索页，用户一点就到。

这些链接全部是平台自己的公开搜索页，不含任何推广参数，也不代表我们采到了数据。
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .models import Product

__all__ = ["SEARCH_URLS", "search_url", "fallback_links", "has_enough_data"]

#: 平台 -> 公开搜索页模板（``{q}`` 会被 URL 编码后的关键词替换）
SEARCH_URLS: dict[str, str] = {
    "jd": "https://search.jd.com/Search?keyword={q}&enc=utf-8",
    "taobao": "https://s.taobao.com/search?q={q}",
    "tmall": "https://list.tmall.com/search_product.htm?q={q}",
    "pdd": "https://mobile.yangkeduo.com/search_result.html?search_key={q}",
    "steam": "https://store.steampowered.com/search/?term={q}",
}

#: 界面显示名
PLATFORM_LABELS: dict[str, str] = {
    "jd": "京东",
    "taobao": "淘宝",
    "tmall": "天猫",
    "pdd": "拼多多",
    "steam": "Steam",
}

#: 平台字段（Product.platform 的中文名）-> 搜索键
_ALIAS = {
    "京东": "jd",
    "淘宝": "taobao",
    "天猫": "tmall",
    "拼多多": "pdd",
    "steam": "steam",
    "Steam": "steam",
}


def search_url(platform: str, keyword: str) -> str:
    """拼一个平台搜索链接；平台不认识就返回空串（不编造链接）。"""
    key = _ALIAS.get(platform, platform.lower())
    template = SEARCH_URLS.get(key)
    if not template:
        return ""
    return template.format(q=quote_plus((keyword or "").strip()))


def has_enough_data(product: Product) -> bool:
    """是否够做一次可靠比较：至少要有价格，且口碑或规格有一项。

    这个判断和推荐算法里的样本门槛是两套东西：这里只决定**要不要提示用户去平台自己看**。
    """
    if product.offer.price is None:
        return False
    has_reputation = (product.rating.positive_rate is not None
                      or product.rating.score is not None)
    has_specs = bool(product.specs)
    return has_reputation or has_specs


def fallback_links(product: Product, *, limit: int = 3) -> list[dict[str, str]]:
    """给一个商品生成「去平台自己核实」的链接列表。

    - 首先给原商品链接（如果存在）；
    - 再给它所在平台的搜索链接；
    - 若数据不足，额外给两三个同类平台的搜索链接，方便用户自己横向看。
    """
    name = (product.name or "").strip()
    links: list[dict[str, str]] = []

    if product.url:
        links.append({"平台": product.platform or "原页面", "说明": "原始商品页", "链接": product.url})

    if name:
        own = search_url(product.platform, name)
        if own:
            links.append({
                "平台": PLATFORM_LABELS.get(_ALIAS.get(product.platform, ""), product.platform),
                "说明": "在该平台搜索同名商品",
                "链接": own,
            })

        if not has_enough_data(product):
            for key in ("jd", "taobao", "steam"):
                if _ALIAS.get(product.platform, "") == key:
                    continue
                links.append({
                    "平台": PLATFORM_LABELS[key],
                    "说明": "换平台搜索（人工核对）",
                    "链接": search_url(key, name),
                })
                if len(links) >= limit + 1:
                    break

    return [link for link in links if link["链接"]][: max(limit, 1) + 1]
