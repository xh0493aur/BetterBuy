"""通用网页适配器（平台无关的兜底方案）。

策略：
1. 先尝试 schema.org / JSON-LD 结构化数据（字段可信度最高）；
2. 再尝试 og:* 与 product:* 元标签；
3. 都没有时只保留标题与图片，并明确告知「该站点未提供可自动识别的商品数据」，
   绝不从"限时 299 元"这类营销文案里猜价格。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..http_client import FetchError, client
from ..models import UNKNOWN, Offer, Product, Rating, Source, now_text
from ..normalize import clean_text, guess_brand, guess_model, parse_price, strip_site_suffix
from .base import BaseAdapter
from .schema_org import parse_jsonld_product

__all__ = ["GenericHtmlAdapter", "open_meta_fields"]

_META_KEYS = {
    "title": (("property", "og:title"), ("name", "twitter:title")),
    "image": (("property", "og:image"), ("name", "twitter:image")),
    "description": (("property", "og:description"), ("name", "description")),
    "site": (("property", "og:site_name"),),
}


def open_meta_fields(html: str) -> dict[str, str]:
    """读取 og/twitter 元标签；没有的键不出现。"""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for key, attrs in _META_KEYS.items():
        for attr, value in attrs:
            tag = soup.find("meta", attrs={attr: value})
            if tag and tag.get("content"):
                out[key] = clean_text(tag["content"], limit=300)
                break
    if "title" not in out and soup.title:
        out["title"] = clean_text(soup.title.get_text(" ", strip=True), limit=200)
    price_meta = soup.find("meta", attrs={"property": re.compile(r"(product|og):price:amount", re.I)})
    if price_meta and price_meta.get("content"):
        out["price"] = price_meta["content"]
    currency_meta = soup.find("meta", attrs={"property": re.compile(r"(product|og):price:currency", re.I)})
    if currency_meta and currency_meta.get("content"):
        out["currency"] = currency_meta["content"]
    return out


class GenericHtmlAdapter(BaseAdapter):
    key = "generic_html"
    label = "通用网页"
    method = "公开页面（schema.org / 元标签）"
    domains = ("*",)

    def matches(self, url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def fetch(self, url: str) -> Product:
        resp = client.get(url)
        html = resp.text
        final_url = resp.url
        host = urlparse(final_url).netloc.lower().removeprefix("www.")
        source = Source(
            adapter=self.key,
            label=f"{host} 公开页面",
            url=final_url,
            method=self.method,
            fetched_at=now_text(),
            http_status=resp.status_code,
        )
        product = Product(
            product_id=f"url:{final_url}",
            platform=host,
            url=final_url,
            fetched_at=source.fetched_at,
        )

        structured = parse_jsonld_product(html)
        meta = open_meta_fields(html)

        if structured:
            product.name = structured["name"]
            product.brand = structured["brand"] or guess_brand(product.name)
            product.model = structured["model"] or guess_model(product.name)
            product.category = structured["category"]
            product.summary = structured["summary"]
            product.image_url = structured["image_url"] or meta.get("image", "")
            product.offer = Offer(
                price=structured["price"],
                currency=structured["currency"],
                original_price=structured["original_price"],
                availability=structured["availability"] or "数据源未说明",
                source_key="price",
            )
            product.rating = Rating(
                positive_rate=structured["positive_rate"],
                review_count=structured["review_count"],
                score=structured["score"],
                scale=structured["scale"],
                source_key="rating",
            )
            product.reviews = structured["reviews"]
            product.add_source("detail", source, ["name", "brand", "model", "category", "specs", "image"])
            product.add_source("price", source, ["price", "currency"])
            product.add_source("rating", source, ["rating"])
            product.status = "已识别（页面结构化数据）"
            if product.offer.price is None:
                product.notes.append(
                    "页面结构化数据中没有价格字段，可能价格由脚本异步加载，本工具不解析脚本生成的价格。"
                )
            product.recompute_missing()
            return product

        # 没有结构化数据：只保留能确证的字段
        product.name = strip_site_suffix(meta.get("title", "")) or UNKNOWN
        product.brand = guess_brand(product.name)
        product.model = guess_model(product.name)
        product.summary = meta.get("description", "")
        product.image_url = meta.get("image", "")
        price, currency = parse_price(meta.get("price"), meta.get("currency"))
        product.offer = Offer(price=price, currency=currency, source_key="price")
        product.rating = Rating(source_key="rating")
        product.add_source("detail", source, ["name", "image"])
        if price is not None:
            product.add_source("price", source, ["price"])
        product.notes.append(
            "该页面没有标准商品结构化数据（schema.org / JSON-LD），只能读取标题与图片；"
            "价格、评分等字段标记为缺失。可改用支持自动识别的平台，或在「手动录入」页补充。"
        )
        product.status = "仅识别到基础信息"
        product.recompute_missing()
        product.mark_missing("价格", "好评率", "评价数量", "商品类别", "品牌", "型号/版本")
        return product
