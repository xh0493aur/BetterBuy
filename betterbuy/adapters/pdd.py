"""拼多多（多多进宝开放接口）适配器。

取数方式：``pdd.ddk.goods.search`` 的 ``keyword`` 支持直接传商品链接
（官方参数说明原文：「可支持 goods_id、拼多多链接、进宝长链/短链」），
所以粘贴商品页链接即可查询，不需要先换 goods_sign。

价格单位是**分**，统一除以 100；拼多多接口不返回好评率与评价正文。
"""
from __future__ import annotations

from typing import Any

from ..http_client import FetchError
from ..models import Offer, Product, Rating, Source, now_text
from ..openapi import ApiError, PDDClient
from ..openapi.fields import all_keys, fen_to_yuan, pick, to_float, to_int
from .ecommerce_base import EcommerceAdapter, to_fetch_error

__all__ = ["PDDAdapter", "product_from_pdd_goods"]


def product_from_pdd_goods(item: dict[str, Any], *, goods_id: str, url: str = "",
                           used_api: str = "pdd.ddk.goods.search",
                           fetched_at: str | None = None) -> Product:
    """把多多进宝返回的一条商品映射成统一 Product。

    ``url`` 允许为空：搜索接口本身**不返回商品链接**，只有转链接口
    （``pdd.ddk.goods.promotion.url.generate``）才有。宁可留空并说明，
    也不去拼一个看起来像商品页的地址。
    """
    stamp = fetched_at or now_text()
    name = pick(item, "goods_name", "goods_desc") or ""
    price = fen_to_yuan(pick(item, "min_group_price", "min_on_sale_group_price"))
    original = fen_to_yuan(pick(item, "min_normal_price", "min_on_sale_normal_price"))

    product = Product(
        product_id=f"pdd:{goods_id or pick(item, 'goods_sign') or ''}",
        platform="拼多多",
        url=url,
        name=str(name).strip(),
        brand="",                        # 拼多多接口没有品牌字段，留空由标题提取
        category=str(pick(item, "opt_name") or ""),
        image_url=str(pick(item, "goods_thumbnail_url", "goods_image_url") or ""),
    )
    product.fetched_at = stamp

    coupon = fen_to_yuan(pick(item, "coupon_discount"))
    coupon_min = fen_to_yuan(pick(item, "coupon_min_order_amount"))
    product.offer = Offer(
        price=price,
        currency="CNY" if price is not None else None,
        original_price=original if original and price and original > price else None,
        source_key="pdd",
        terms=_price_terms(price, coupon, coupon_min),
    )
    # 拼多多不提供好评率/评价数，口碑对象保持空，界面会显示「数据源未提供」
    product.rating = Rating(source_key="pdd")

    specs: dict[str, str] = {}
    shop = pick(item, "mall_name")
    if shop:
        specs["店铺"] = str(shop)
    sales = pick(item, "sales_tip")
    if sales:
        specs["已售"] = str(sales)
    if coupon:
        specs["优惠券"] = f"{coupon:g} 元" + (f"（满 {coupon_min:g} 元可用）" if coupon_min else "")
        if price is not None:
            specs["券后参考价"] = f"约 {max(price - coupon, 0):.2f} 元"
    rate = to_float(pick(item, "promotion_rate"))
    if rate is not None:
        specs["佣金比例"] = f"{rate / 10:g}%"      # 千分比 -> 百分比
    sign = pick(item, "goods_sign")
    if sign:
        specs["goods_sign"] = str(sign)
    desc = str(pick(item, "goods_desc") or "").strip()
    if desc and desc != product.name:
        product.summary = desc
    if specs:
        product.specs = specs

    source = Source(adapter="pdd_ddk_api", label="拼多多多多进宝开放接口",
                    url=f"https://gw-api.pinduoduo.com/api/router（{used_api}）",
                    method="官方开放接口", fetched_at=stamp, http_status=200)
    product.add_source("pdd", source, ["name", "price", "currency", "category",
                                       "image", "specs", "url"])
    product.raw_fields = all_keys(item)

    product.notes.append(
        "数据来自多多进宝开放接口，属于推广口径：只有进入多多进宝推广池的商品查得到。"
    )
    product.notes.append(
        "拼多多接口不返回好评率、评价总数与评价正文，这三项一律留空，不参与打分。"
    )
    if price is not None:
        product.notes.append(
            "价格为接口返回的拼团价（单位原为分，已换算为元）；券后价是「拼团价减券面额」的"
            "估算，未考虑用券门槛、限购与运费。"
        )
    if not url:
        product.notes.append(
            "搜索接口不返回商品链接，本页不拼接任何“看起来像”的购买地址；"
            "需要可点击的官方推广链接时，请用「生成官方推广链接」（需转链权限）。"
        )
    product.recompute_missing()
    return product


def _price_terms(price: float | None, coupon: float | None, coupon_min: float | None) -> str:
    """价格口径：说清楚是拼团价还是单买价、有没有算券、有没有算运费。"""
    if price is None:
        return "接口未返回价格，口径无法确认"
    bits = ["拼团价"]
    if coupon:
        after = max(price - coupon, 0)
        bits.append(f"券后约 ¥{after:.2f}" + (f"（满 ¥{coupon_min:g} 可用）" if coupon_min else ""))
    else:
        bits.append("未含优惠券")
    bits.append("不含运费（接口未返回运费）")
    return " · ".join(bits)


class PDDAdapter(EcommerceAdapter):
    key = "pdd_ddk_api"
    label = "拼多多多多进宝 API"
    method = "拼多多开放平台（pdd.ddk.*，需 client_id/secret/pid）"
    domains = ("yangkeduo.com", "pinduoduo.com")
    platform = "pdd"
    accepted = ("pdd",)

    def fetch(self, url: str) -> Product:
        info = self.resolve_info(url)
        client = PDDClient()
        self.guard_credentials(client)

        goods_id = info.item_id
        try:
            items = client.goods_search(keyword=info.url, page_size=20)
            if not items and goods_id:
                items = client.goods_search(keyword=goods_id, page_size=20)
        except ApiError as exc:
            raise to_fetch_error(exc) from exc

        if not items:
            raise FetchError(
                f"多多进宝接口查询成功，但没有返回商品（goods_id={goods_id or '未知'}）。"
                "常见原因：该商品没有加入多多进宝推广、已下架，或链接是 App 口令。"
                "本工具不会用页面抓取或估算值代替接口结果。",
                kind="notfound",
            )

        chosen = _match_by_id(items, goods_id) or items[0]
        resolved_id = str(pick(chosen, "goods_id") or goods_id or "")
        product = product_from_pdd_goods(chosen, goods_id=resolved_id, url=info.url)
        if not any(pick(item, "goods_id") and str(pick(item, "goods_id")) == goods_id
                   for item in items) and goods_id:
            product.notes.append(
                "接口未返回与该链接 goods_id 完全一致的商品，展示的是搜索结果中最接近的一条，"
                "请核对标题与价格是否为你想要的那款。"
            )
        return self.finish(product, self.key, self.label)


def _match_by_id(items: list[dict[str, Any]], goods_id: str) -> dict[str, Any] | None:
    if not goods_id:
        return None
    for item in items:
        if str(pick(item, "goods_id") or "") == goods_id:
            return item
    return None
