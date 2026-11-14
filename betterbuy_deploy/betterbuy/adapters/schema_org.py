"""通用 schema.org / JSON-LD 商品解析。

适用于任何在页面里发布标准商品结构化数据的站点（大量自建商城、Shopify、
部分品牌官网）。只读取 ``application/ld+json`` 中的声明，**不**从营销文案里猜价格。
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

from ..normalize import clean_text, parse_int, parse_percent, parse_price

__all__ = ["parse_jsonld_product", "iter_product_nodes", "extract_offers"]


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def iter_product_nodes(obj: Any) -> Iterator[dict]:
    """递归遍历 JSON-LD，产出 @type 含 Product 的节点。"""
    if isinstance(obj, list):
        for item in obj:
            yield from iter_product_nodes(item)
        return
    if not isinstance(obj, dict):
        return
    kinds = _as_list(obj.get("@type"))
    if any(str(k).split("/")[-1].lower() == "product" for k in kinds):
        yield obj
    for key in ("@graph", "mainEntity", "itemListElement", "hasVariant", "isSimilarTo"):
        if key in obj:
            yield from iter_product_nodes(obj[key])


def _name_of(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("legalName") or "")
    if isinstance(value, list) and value:
        return _name_of(value[0])
    return ""


def extract_offers(offer: Any) -> tuple[float | None, str | None, float | None, str]:
    """返回 (现价, 货币, 原价, 在售状态)。多报价时取同币种最低价。"""
    options: list[tuple[float, str | None, float | None]] = []
    for item in _as_list(offer):
        if not isinstance(item, dict):
            continue
        currency = clean_text(item.get("priceCurrency")).upper() or None
        availability = clean_text(item.get("availability"))
        price = None
        for key in ("price", "lowPrice", "highPrice"):
            price, cur = parse_price(item.get(key), currency)
            if price is not None:
                currency = currency or cur
                break
        spec = item.get("priceSpecification")
        if price is None and isinstance(spec, dict):
            price, cur = parse_price(spec.get("price"), spec.get("priceCurrency") or currency)
            currency = currency or cur
        if price is None:
            continue
        original, _ = parse_price(item.get("highPrice"), currency)
        options.append((price, currency, original if original and original > price else None))
    if not options:
        return None, None, None, ""
    currency = options[0][1]
    same = [o for o in options if o[1] == currency] or options
    price, currency, original = min(same, key=lambda x: x[0])
    status = ""
    raw_avail = _as_list(offer)[0] if isinstance(offer, (dict, list)) else None
    if isinstance(raw_avail, dict):
        status = clean_text(raw_avail.get("availability"))
    return price, currency, original, status


def _availability_text(raw: str) -> str:
    if not raw:
        return ""
    low = raw.lower()
    if "instock" in low:
        return "在售"
    if "outofstock" in low or "soldout" in low:
        return "无货"
    if "preorder" in low or "presale" in low:
        return "预售"
    if "discontinued" in low:
        return "已停售"
    return clean_text(raw.split("/")[-1])


def parse_jsonld_product(html: str) -> dict | None:
    """返回第一条可用 Product 节点的标准化结果；没有则返回 None。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for node in iter_product_nodes(data):
            name = _name_of(node.get("name"))
            if not name:
                continue
            price, currency, original, availability = extract_offers(node.get("offers"))
            rating_node = node.get("aggregateRating")
            if isinstance(rating_node, list):
                rating_node = next((x for x in rating_node if isinstance(x, dict)), None)
            score = scale = count = rate = None
            if isinstance(rating_node, dict):
                score, _ = parse_price(rating_node.get("ratingValue"))
                scale, _ = parse_price(rating_node.get("bestRating"))
                count = parse_int(rating_node.get("ratingCount") or rating_node.get("reviewCount"))
                rate = parse_percent(rating_node.get("ratingValue")) if "%" in str(
                    rating_node.get("ratingValue")) else None
            reviews = []
            for item in _as_list(node.get("review"))[:5]:
                if not isinstance(item, dict):
                    continue
                body = clean_text(item.get("reviewBody") or item.get("description"), limit=200)
                author = _name_of(item.get("author"))
                if body:
                    reviews.append({"作者": author or "未署名", "内容": body})
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else ""
            if isinstance(image, dict):
                image = image.get("url") or ""
            brand = _name_of(node.get("brand")) or _name_of(node.get("manufacturer"))
            model = ""
            for key in ("model", "mpn", "sku", "gtin13", "productID"):
                value = node.get(key)
                if isinstance(value, dict):
                    value = value.get("name") or value.get("value")
                if value:
                    model = clean_text(value)
                    break
            return {
                "name": name,
                "brand": brand,
                "model": model,
                "category": clean_text(_as_list(node.get("category"))[0] if _as_list(node.get("category")) else ""),
                "summary": clean_text(node.get("description"), limit=300),
                "image_url": clean_text(str(image)),
                "price": price,
                "currency": currency,
                "original_price": original,
                "availability": _availability_text(availability),
                "score": score,
                "scale": scale,
                "review_count": count,
                "positive_rate": rate,
                "reviews": reviews,
            }
    return None
