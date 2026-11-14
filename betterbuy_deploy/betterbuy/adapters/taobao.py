"""淘宝 / 天猫（淘宝客开放接口）适配器。

调用 ``taobao.tbk.item.info.upgrade.get``（简易版）；若返回为空，
再用 ``taobao.tbk.item.details.upgrade.get``（详细版，含 SKU 与库存）复核一次。

三条硬规则（都由官方文档决定，不是选择）：
1. ``volume`` 是「30 天销量」，**绝不能当评价数**；
2. ``h_good_rate`` 是布尔值（是否高于行业均值），**不能当好评率百分比**；
3. 只有淘宝客推广池里的商品查得到，查不到就如实报空。
"""
from __future__ import annotations

from typing import Any

from ..http_client import FetchError
from ..models import Offer, Product, Rating, Source, now_text
from ..openapi import ApiError, TaobaoClient
from ..openapi.fields import all_keys, pick, pick_path, to_float, to_int
from .ecommerce_base import EcommerceAdapter, to_fetch_error

__all__ = ["TaobaoAdapter", "product_from_tbk_item"]

_SELLER_TYPE = {"0": "淘宝集市", "1": "天猫", "3": "淘宝特价版"}


def product_from_tbk_item(item: dict[str, Any], *, item_id: str, url: str,
                          used_api: str = "taobao.tbk.item.info.upgrade.get",
                          fetched_at: str | None = None) -> Product:
    stamp = fetched_at or now_text()
    basic = item.get("item_basic_info") if isinstance(item.get("item_basic_info"), dict) else {}
    promo = item.get("price_promotion_info") if isinstance(item.get("price_promotion_info"), dict) else {}

    name = pick(basic, "title", "short_title") or pick(item, "title") or ""
    price = to_float(pick(promo, "final_promotion_price", "zk_final_price", "reserve_price")
                     or pick(item, "zk_final_price", "final_promotion_price", "price"))
    original = to_float(pick(promo, "reserve_price") or pick(item, "reserve_price"))
    seller_type = str(pick(basic, "user_type") or "")

    product = Product(
        product_id=f"taobao:{item_id}",
        platform="天猫" if seller_type == "1" else "淘宝",
        url=str(pick(basic, "item_url") or url),
        name=str(name).strip(),
        brand=str(pick(basic, "brand_name") or ""),
        category=_category(basic),
        image_url=str(pick(basic, "pict_url", "white_image") or ""),
    )
    product.fetched_at = stamp
    product.offer = Offer(
        price=price,
        currency="CNY" if price is not None else None,
        original_price=original if original and price and original > price else None,
        availability="有库存" if to_int(pick(basic, "quantity")) not in (None, 0) else "",
        source_key="taobao",
    )

    # ---- 口碑：只有布尔字段，没有百分比，也没有评价数 ----
    good_flag = pick(basic, "h_good_rate")
    label = ""
    if isinstance(good_flag, bool):
        label = "好评率高于行业均值" if good_flag else "好评率未高于行业均值"
    product.rating = Rating(label=label, source_key="taobao")

    specs: dict[str, str] = {}
    shop = pick(basic, "shop_title")
    if shop:
        specs["店铺"] = str(shop)
    if seller_type:
        specs["店铺类型"] = _SELLER_TYPE.get(seller_type, seller_type)
    volume = to_int(pick(basic, "volume"))
    if volume is not None:
        specs["30天销量"] = str(volume)
    annual = pick(basic, "annual_vol")
    if annual:
        specs["年销量"] = str(annual)
    location = pick(basic, "provcity")
    if location:
        specs["发货地"] = str(location)
    post_fee = pick(basic, "free_shipment")
    if isinstance(post_fee, bool):
        specs["包邮"] = "是" if post_fee else "否"
    income = to_float(pick_path(item, "publish_info.income_rate"))
    if income is not None:
        specs["推广收入比例"] = f"{income}%"
    if specs:
        product.specs = specs

    source = Source(adapter="taobao_tbk_api", label="淘宝客开放接口",
                    url=f"https://gw.api.taobao.com/router/rest（{used_api}）",
                    method="官方开放接口", fetched_at=stamp, http_status=200)
    product.add_source("taobao", source, ["name", "price", "currency", "brand", "category",
                                          "image", "rating", "specs", "url"])
    product.raw_fields = all_keys(item)

    product.notes.append(
        "数据来自淘宝客开放接口，属于推广口径：只有淘宝客推广池里的商品查得到。"
    )
    product.notes.append(
        "淘宝客接口不返回好评率百分比（只有「是否高于行业均值」的布尔值），"
        "也不返回评价总数，因此口碑与评价数留空，不参与打分。"
    )
    product.recompute_missing()
    return product


def _category(basic: dict[str, Any]) -> str:
    names = [pick(basic, "level_one_category_name"), pick(basic, "category_name")]
    text = " > ".join(str(n).strip() for n in names if n)
    return text or str(pick(basic, "category_name") or "")


class TaobaoAdapter(EcommerceAdapter):
    key = "taobao_tbk_api"
    label = "淘宝/天猫淘宝客 API"
    method = "淘宝开放平台 TOP（taobao.tbk.*，需 app_key/secret/adzone_id）"
    domains = ("taobao.com", "tmall.com", "tmall.hk", "m.tb.cn")
    platform = "taobao"
    accepted = ("taobao", "tmall")

    def fetch(self, url: str) -> Product:
        info = self.resolve_info(url)
        client = TaobaoClient()
        self.guard_credentials(client)

        used = "taobao.tbk.item.info.upgrade.get"
        try:
            items = client.item_info(info.item_id)
            if not items:
                used = "taobao.tbk.item.details.upgrade.get"
                items = client.item_details(info.item_id)
        except ApiError as exc:
            raise to_fetch_error(exc) from exc

        if not items:
            raise FetchError(
                f"淘宝客接口查询成功，但没有返回商品 ID={info.item_id} 的数据。"
                "常见原因：该商品没有加入淘宝客推广、已下架，或者你的应用缺少"
                "「消费者比价场景 ID2」权限（用商品链接直接查询需要该权限）。"
                "本工具不会用页面抓取或估算值代替接口结果。",
                kind="notfound",
            )
        product = product_from_tbk_item(items[0], item_id=info.item_id, url=info.url, used_api=used)
        return self.finish(product, self.key, self.label)
