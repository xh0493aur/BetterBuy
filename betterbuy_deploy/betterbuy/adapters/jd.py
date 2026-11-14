"""京东（京东联盟开放接口）适配器。

调用顺序：
1. ``jd.union.open.goods.promotiongoodsinfo.query``（按 skuId 查详情，含价格与评论数）
2. 若为空，退回 ``jd.union.open.goods.query``（goodsReqDTO.skuIds）

**价格单位假设**：京东联盟返回的价格按官方示例是「元」的十进制字符串，
因此这里按元解析，同时把接口原始值原样写进备注，一旦单位不符可以立刻发现。
拼多多的「分」有单独换算，两者不共用逻辑。
"""
from __future__ import annotations

from typing import Any

from ..http_client import FetchError
from ..models import Offer, Product, Rating, Source, now_text
from ..openapi import ApiError, JDClient
from ..openapi.fields import all_keys, pick, to_float, to_int
from .base import UnsupportedLink
from .ecommerce_base import EcommerceAdapter, to_fetch_error

__all__ = ["JDAdapter", "product_from_goods"]

#: 字段说明里展示的接口名
_API_DETAIL = "jd.union.open.goods.promotiongoodsinfo.query"


def product_from_goods(item: dict[str, Any], *, sku_id: str, url: str,
                       used_api: str = _API_DETAIL,
                       fetched_at: str | None = None) -> Product:
    """把京东返回的一条商品映射成 Product。取不到的字段保持空，不猜。"""
    stamp = fetched_at or now_text()
    name = pick(item, "skuName", "title", "goodsName", "wareName") or ""
    price = to_float(pick(item, "priceInfo.lowestPrice", "priceInfo.price", "lowestPrice",
                          "price", "jdPrice", "wlPrice"))
    original = to_float(pick(item, "priceInfo.price", "originalPrice", "marketPrice",
                             "priceInfo.marketPrice"))

    product = Product(
        product_id=f"jd:{sku_id}",
        platform="京东",
        url=url,
        name=str(name).strip(),
        brand=str(pick(item, "brandName", "brandCode") or ""),
        category=_category(item),
        image_url=str(pick(item, "imageUrl", "imgUrl", "logoUrl", "whiteImage",
                           "imageInfo.imageList.0.url") or ""),
    )
    product.fetched_at = stamp
    product.offer = Offer(
        price=price,
        currency="CNY" if price is not None else None,
        original_price=original if original and price and original > price else None,
        availability=str(pick(item, "stockInfo.stockDesc", "availability") or ""),
        source_key="jd",
    )

    # ---- 口碑：京东联盟可能给「好评数/评价总数」，比例由两者相除得出（是计算，不是猜） ----
    good = to_int(pick(item, "goodComments", "goodCommentCount", "comments.goodComments"))
    total = to_int(pick(item, "totalComments", "commentCount", "comments.totalComments"))
    rate: float | None = None
    scope = ""
    if good is not None and total and 0 < good <= total:
        rate = round(good / total * 100, 1)
        scope = "京东好评数/评价总数换算"
    product.rating = Rating(
        positive_rate=rate,
        review_count=total,
        scope=scope,
        source_key="jd",
    )

    # ---- 参数 ----
    specs: dict[str, str] = {}
    shop = pick(item, "shopInfo.shopName", "shopName")
    if shop:
        specs["店铺"] = str(shop)
    owner = str(pick(item, "owner") or "")
    if owner:
        specs["经营模式"] = {"g": "京东自营", "p": "POP 第三方"}.get(owner, owner)
    commission = pick(item, "commissionInfo.commissionShare", "commissionInfo.commission")
    if commission is not None:
        specs["佣金比例"] = f"{to_float(commission)}%"
    if original is not None:
        specs["接口原始价格"] = f"{original}"
    if price is not None and price >= 100000:
        specs["价格单位提示"] = "原始值偏大，可能以「分」为单位，请核对"
    if specs:
        product.specs = specs

    source = Source(adapter="jd_union_api", label="京东联盟官方 API",
                    url=f"https://api.jd.com/routerjson（{used_api}）",
                    method="官方开放接口", fetched_at=stamp, http_status=200)
    product.add_source("jd", source, ["name", "price", "currency", "brand", "category",
                                      "image", "rating", "specs", "url"])
    product.raw_fields = all_keys(item)

    product.notes.append(
        "数据来自京东联盟官方接口，属于推广口径：只有进入联盟推广池的商品才查得到。"
    )
    if rate is None:
        product.notes.append(
            "京东接口未返回可换算的好评率，口碑维度留空，不参与口碑打分。"
        )
    product.recompute_missing()
    return product


def _category(item: dict[str, Any]) -> str:
    names = [
        pick(item, "categoryInfo.cid1Name"),
        pick(item, "categoryInfo.cid2Name"),
        pick(item, "categoryInfo.cid3Name"),
    ]
    text = " > ".join(str(n).strip() for n in names if n)
    return text or str(pick(item, "categoryName", "catName") or "")


class JDAdapter(EcommerceAdapter):
    key = "jd_union_api"
    label = "京东联盟官方 API"
    method = "京东开放接口（jd.union.open.*，需 app_key/app_secret）"
    domains = ("jd.com", "jd.hk", "3.cn")
    platform = "jd"
    accepted = ("jd",)

    def fetch(self, url: str) -> Product:
        try:
            info = self.resolve_info(url)
        except FetchError:
            raise
        except UnsupportedLink as exc:
            raise FetchError(str(exc), kind="unsupported") from exc

        client = JDClient()
        self.guard_credentials(client)
        used = _API_DETAIL
        try:
            items = client.goods_detail(info.item_id)
            if not items:
                used = "jd.union.open.goods.query"
                items = client.goods_query(skuIds=info.item_id)
        except ApiError as exc:
            raise to_fetch_error(exc) from exc

        if not items:
            raise FetchError(
                f"京东联盟接口查询成功，但没有返回 skuId={info.item_id} 的数据。"
                "通常说明该商品没有进入联盟推广池（或已下架）。"
                "本工具不会用页面抓取或估算值代替接口结果。",
                kind="notfound",
            )
        product = product_from_goods(items[0], sku_id=info.item_id, url=info.url, used_api=used)
        return self.finish(product, self.key, self.label)
