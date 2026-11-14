"""跨平台「疑似同款」检索。

必须先说清楚一件事：**没有任何平台的官方接口提供「这件商品在别的平台是哪个 ID」**。
所以这里的做法是「关键词检索 + 本地相似度判定」，结果只能叫**疑似同款**，
不能当作平台官方的同款标识，界面上也这样标注。

判定规则（全部可复现）：
- 品牌都识别出来且不一致 → 直接排除；
- 名称二元组相似度 + 品牌一致加分 + 型号一致加分；
- 两边都有价格时，价差超过 4 倍视为不同商品；
- 分数达到阈值才算疑似同款，宁可漏也不硬凑。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..http_client import FetchError
from ..models import Product
from ..normalize import guess_brand, guess_model, similarity
from ..openapi import ApiError, JDClient, PDDClient, TaobaoClient
from ..adapters.jd import product_from_goods
from ..adapters.pdd import product_from_pdd_goods
from ..adapters.taobao import product_from_tbk_item
from ..openapi.fields import pick

__all__ = ["CrossPlatformResult", "PLATFORM_LABEL", "search_platform",
           "same_product_across", "match_score", "MATCH_THRESHOLD"]

#: 平台标识 -> 中文名（与 Product.platform 对应）
PLATFORM_LABEL = {"京东": "jd", "淘宝": "taobao", "天猫": "taobao", "拼多多": "pdd"}
_LABEL_OF = {"jd": "京东", "taobao": "淘宝/天猫", "pdd": "拼多多"}

MATCH_THRESHOLD = 0.6
_PRICE_RATIO_LIMIT = 4.0


@dataclass
class CrossPlatformResult:
    ok: bool
    note: str
    matches: list[Product] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _keyword_for(product: Product) -> str:
    brand = product.brand or guess_brand(product.name)
    model = product.model or guess_model(product.name)
    if brand and model:
        return f"{brand} {model}"
    if model:
        return model
    if brand:
        return f"{brand} {product.name[:20]}"
    return product.name[:24]


def search_platform(platform: str, keyword: str, *, limit: int = 5) -> list[Product]:
    """在指定平台按关键词搜一批商品（未配置密钥返回空列表，不抛错）。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    try:
        if platform == "jd":
            client = JDClient()
            if not client.available:
                return []
            items = client.goods_query(keyword=keyword, pageIndex=1, pageSize=max(limit, 10))
            return [product_from_goods(item, sku_id=str(pick(item, "skuId") or ""),
                                       url=str(pick(item, "materialUrl", "clickURL") or ""))
                    for item in items]
        if platform == "taobao":
            client = TaobaoClient()
            if not client.available:
                return []
            items = client.material_search(q=keyword, page_size=max(limit, 10))
            return [product_from_tbk_item(item, item_id=str(pick(item, "item_id") or ""),
                                          url=str(pick(item, "item_url", "click_url") or ""))
                    for item in items]
        if platform == "pdd":
            client = PDDClient()
            if not client.available:
                return []
            items = client.goods_search(keyword=keyword, page_size=max(limit, 10))
            return [product_from_pdd_goods(item, goods_id=str(pick(item, "goods_id") or ""),
                                           url=str(pick(item, "goods_url") or ""))
                    for item in items]
    except ApiError as exc:
        raise FetchError(f"{_LABEL_OF.get(platform, platform)} 检索失败：{exc.message}",
                         kind=exc.kind) from exc
    return []


def match_score(base: Product, candidate: Product) -> tuple[float, list[str]]:
    """返回 (分数 0-1, 判定依据)。分数为 0 表示明确排除。"""
    reasons: list[str] = []
    brand_a = (base.brand or guess_brand(base.name)).lower()
    brand_b = (candidate.brand or guess_brand(candidate.name)).lower()
    if brand_a and brand_b and brand_a != brand_b:
        return 0.0, ["品牌不同"]

    score = similarity(base.name, candidate.name)
    reasons.append(f"标题相似度 {score:.2f}")

    if brand_a and brand_a == brand_b:
        score += 0.15
        reasons.append("品牌一致")

    model_a = (base.model or guess_model(base.name)).lower()
    model_b = (candidate.model or guess_model(candidate.name)).lower()
    if model_a and model_b and model_a == model_b:
        score += 0.2
        reasons.append(f"型号一致（{model_a}）")
    elif model_a and model_b and model_a != model_b:
        score -= 0.2
        reasons.append("型号不同")

    price_a, price_b = base.offer.price, candidate.offer.price
    if price_a and price_b:
        ratio = max(price_a, price_b) / max(1e-9, min(price_a, price_b))
        if ratio > _PRICE_RATIO_LIMIT:
            return 0.0, [f"价差过大（{ratio:.1f} 倍），判为不同商品"]
        reasons.append(f"价差 {ratio:.2f} 倍")

    return max(0.0, min(score, 1.0)), reasons


def same_product_across(products: list[Product], *, exclude_platforms: tuple[str, ...] = (),
                        limit: int = 2, per_platform: int = 5) -> CrossPlatformResult:
    """为一组商品在其他平台找疑似同款。"""
    bases = [p for p in products if p.platform in PLATFORM_LABEL]
    if not bases:
        return CrossPlatformResult(
            ok=False,
            note="当前商品不是京东/淘宝/拼多多商品，无法做跨平台疑似同款检索。",
        )

    base = bases[0]
    base_key = PLATFORM_LABEL[base.platform]
    targets = [k for k in ("jd", "taobao", "pdd")
               if k != base_key and _LABEL_OF[k] not in exclude_platforms]

    skipped: list[str] = []
    matches: list[Product] = []
    keyword = _keyword_for(base)

    for platform in targets:
        try:
            candidates = search_platform(platform, keyword, limit=per_platform)
        except FetchError as exc:
            skipped.append(f"{_LABEL_OF[platform]}：{exc.message}")
            continue
        if not candidates:
            skipped.append(f"{_LABEL_OF[platform]}：未配置密钥或搜索无结果")
            continue

        scored = sorted(
            ((match_score(base, cand), cand) for cand in candidates),
            key=lambda pair: pair[0][0], reverse=True,
        )
        best_score, best = scored[0] if scored else (0.0, None)
        if not best or best_score[0] < MATCH_THRESHOLD:
            skipped.append(
                f"{_LABEL_OF[platform]}：搜到 {len(candidates)} 条，但最高相似度 "
                f"{best_score[0]:.2f} 未达阈值 {MATCH_THRESHOLD}，不认定为同款"
            )
            continue

        best.origin = "discovered"
        best.notes.append(
            "跨平台疑似同款：按「品牌 + 型号 + 标题相似度」匹配得出（"
            + "；".join(best_score[1]) + "）。"
            "这不是平台官方的同款标识，请自行核对规格后再决定。"
        )
        matches.append(best)
        if len(matches) >= limit:
            break

    if matches:
        note = (
            f"按关键词「{keyword}」在其他平台找到 {len(matches)} 个疑似同款。"
            "匹配是本地相似度算法得出的，不是平台官方同款标识。"
        )
        return CrossPlatformResult(ok=True, note=note, matches=matches, skipped=skipped)

    note = (
        f"未能找到可信的跨平台同款（关键词：{keyword}）。"
        + ("原因：" + "；".join(skipped) if skipped else "")
        + " 本工具不会为了凑数把不同商品标成同款。"
    )
    return CrossPlatformResult(ok=False, note=note, matches=[], skipped=skipped)
