"""分类推荐算法（不依赖任何外部 AI，全部来自真实字段）。

五个类别：
- 性价比最优：平滑好评率 × （组内最低价 ÷ 本品价）
- 规格/内容最优：媒体评分 + 内容规模（语言数、功能特性数）——游戏类没有硬件性能，
  这里用可量化的"内容体量"近似，界面会明确说明
- 预算友好：口碑达到基本线（默认 75%）的前提下价格最低
- 口碑较好：贝叶斯平滑后的好评率（评价越少越保守）
- 综合最推荐：四维加权（默认 性价比 30% / 口碑 35% / 规格 25% / 价格 10%）

硬性规则：
- 同一货币才比较；混合货币不换算，只比较数量最多的那一组并提示。
- 有效样本少于 2 个、或关键字段缺失时，该类别**不给冠军**，返回原因。
- 任何维度都不允许只看价格或只看评价数量就下结论。
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Iterable

from .models import UNKNOWN, Product

__all__ = ["Metrics", "Pick", "Recommendation", "recommend", "compute_metrics",
           "DEFAULT_WEIGHTS", "PICK_ORDER"]

#: 综合推荐默认权重（界面高级选项可调）
DEFAULT_WEIGHTS = {"value": 30, "reputation": 35, "spec": 25, "price": 10}

PICK_ORDER = ["value", "spec", "budget", "reputation", "overall"]
PICK_TITLES = {
    "value": "性价比最优",
    "spec": "规格/内容最优",
    "budget": "预算友好",
    "reputation": "口碑较好",
    "overall": "综合最推荐",
}

#: 权重项的中文名（用于向用户展示加权公式）
WEIGHT_LABELS = {"value": "性价比", "reputation": "口碑", "spec": "规格", "price": "价格"}

MIN_SAMPLE = 2            # 少于 2 个可比样本不评选
MIN_REVIEWS_RELIABLE = 50  # 评价少于该数量时口碑置信度下降
BUDGET_QUALITY_FLOOR = 75.0

#: 贝叶斯平滑先验：假设一个"平均商品"是 70% 好评、有 50 条评价
PRIOR_RATE = 70.0
PRIOR_WEIGHT = 50.0


@dataclass
class Metrics:
    """单个商品的可比指标（None 表示数据不足，绝不填 0）。"""

    product_id: str
    price: float | None = None
    currency: str | None = None
    positive_rate: float | None = None
    smoothed_rate: float | None = None
    review_count: int | None = None
    spec_score: float | None = None
    value_score: float | None = None
    price_score: float | None = None
    overall: float | None = None
    completeness: float = 0.0
    gaps: list[str] = field(default_factory=list)

    @property
    def has_price(self) -> bool:
        return self.price is not None


@dataclass
class Pick:
    key: str
    title: str
    basis: str
    product: Product | None = None
    score: float | None = None
    reason: str = ""
    advantages: list[str] = field(default_factory=list)
    drawbacks: list[str] = field(default_factory=list)
    audience: str = ""
    data_gaps: list[str] = field(default_factory=list)
    confidence: str = "低"
    empty_note: str = ""

    @property
    def empty(self) -> bool:
        return self.product is None


@dataclass
class Recommendation:
    currency: str = ""
    metrics: dict[str, Metrics] = field(default_factory=dict)
    picks: list[Pick] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    price_stats: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    products: list[Product] = field(default_factory=list, repr=False)

    def pick(self, key: str) -> Pick | None:
        return next((p for p in self.picks if p.key == key), None)

    def ranked(self) -> list[tuple[Product, Metrics]]:
        items = [(p, self.metrics.get(p.product_id)) for p in self.products]
        items = [(p, m) for p, m in items if m is not None]
        return sorted(items, key=lambda x: (x[1].overall is None, -(x[1].overall or 0)))


def _smooth(rate: float | None, count: int | None) -> float | None:
    if rate is None:
        return None
    n = count if count is not None else 0
    return round((rate * n + PRIOR_RATE * PRIOR_WEIGHT) / (n + PRIOR_WEIGHT), 2)


def _spec_score(product: Product) -> tuple[float | None, list[str]]:
    """规格/内容得分：媒体评分 60% + 语言覆盖 20% + 功能特性 20%。缺项按权重重新分配。"""
    gaps: list[str] = []
    parts: list[tuple[float, float]] = []  # (得分, 权重)

    if product.rating.score is not None and product.rating.scale:
        parts.append((100 * product.rating.score / product.rating.scale, 0.6))
    else:
        gaps.append("缺少媒体评分（Metacritic），规格维度仅由内容规模推断")

    lang_text = product.specs.get("支持语言", "")
    lang_count = None
    if lang_text:
        digits = "".join(ch if ch.isdigit() else " " for ch in lang_text.split("：")[0]).split()
        if digits:
            try:
                lang_count = int(digits[0])
            except ValueError:
                lang_count = None
    if lang_count:
        parts.append((min(lang_count, 20) / 20 * 100, 0.2))
    else:
        gaps.append("未取得支持语言数量")

    features = product.specs.get("功能特性", "")
    if features:
        count = len([x for x in features.replace("、", ",").split(",") if x.strip()])
        parts.append((min(count, 12) / 12 * 100, 0.2))
    else:
        gaps.append("未取得功能特性列表")

    if len(parts) < 2:
        return None, gaps
    total_weight = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / total_weight
    return round(score, 1), gaps


def compute_metrics(products: Iterable[Product], weights: dict[str, float] | None = None) -> dict[str, Metrics]:
    products = list(products)
    weights = weights or DEFAULT_WEIGHTS
    prices = [p.offer.price for p in products if p.offer.price is not None and p.offer.price > 0]
    min_price = min(prices) if prices else None

    out: dict[str, Metrics] = {}
    for product in products:
        have, total, completeness = product.completeness
        metrics = Metrics(
            product_id=product.product_id,
            price=product.offer.price,
            currency=(product.offer.currency or "").upper() or None,
            positive_rate=product.rating.positive_rate,
            review_count=product.rating.review_count,
            completeness=completeness,
        )
        metrics.smoothed_rate = _smooth(product.rating.positive_rate, product.rating.review_count)
        metrics.spec_score, spec_gaps = _spec_score(product)
        metrics.gaps = list(spec_gaps)

        if metrics.price is not None and min_price and metrics.price > 0:
            metrics.price_score = round(100 * min_price / metrics.price, 1)
        if metrics.smoothed_rate is not None and metrics.price_score is not None:
            metrics.value_score = round(metrics.smoothed_rate * metrics.price_score / 100, 1)
        if metrics.smoothed_rate is not None and metrics.review_count is not None \
                and metrics.review_count < MIN_REVIEWS_RELIABLE:
            metrics.gaps.append(f"评价仅 {metrics.review_count} 条，口碑置信度较低")
        if metrics.positive_rate is None:
            metrics.gaps.append("数据源未提供好评率")

        components = {
            "value": (metrics.value_score, weights.get("value", 0)),
            "reputation": (metrics.smoothed_rate, weights.get("reputation", 0)),
            "spec": (metrics.spec_score, weights.get("spec", 0)),
            "price": (metrics.price_score, weights.get("price", 0)),
        }
        available = [(k, v, w) for k, (v, w) in components.items() if v is not None and w > 0]
        weight_sum = sum(w for _, _, w in available)
        weight_total = sum(w for _, (_, w) in components.items() if w > 0)
        # 必需维度的权重要覆盖 60% 以上，否则不给综合分（避免只靠单一指标夺冠）
        if available and weight_total and weight_sum / weight_total >= 0.6:
            metrics.overall = round(sum(v * w for _, v, w in available) / weight_sum, 1)
        else:
            metrics.gaps.append("关键指标缺失，无法计算综合分")
        out[product.product_id] = metrics
    return out


def _money(value: float | None, currency: str = "") -> str:
    if value is None:
        return UNKNOWN
    symbol = {"CNY": "¥", "USD": "$", "EUR": "€", "JPY": "JP¥"}.get(currency, "")
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {currency}".strip()


def _drawbacks(product: Product, metrics: Metrics, peers: list[Product],
               median_price: float | None) -> list[str]:
    """缺点与购买风险：只写能由数据证实的项。"""
    out: list[str] = []
    rating = product.rating
    if rating.review_count is None:
        out.append("评价数量未知，口碑无法判断稳定性")
    elif rating.review_count < MIN_REVIEWS_RELIABLE:
        out.append(f"评价仅 {rating.review_count} 条，样本太少，好评率可能波动")
    if rating.recent_positive_rate is not None and rating.positive_rate is not None \
            and rating.recent_positive_rate + 5 < rating.positive_rate:
        out.append(
            f"近 30 天好评率 {rating.recent_positive_rate:.0f}%，低于总体 {rating.positive_rate:.0f}%，近期口碑下滑"
        )
    if rating.positive_rate is None:
        out.append("数据源未提供好评率，无法评估口碑")
    langs = product.specs.get("支持语言", "")
    if langs and "简体中文" not in langs:
        out.append("页面未列出简体中文支持，中文体验需自行确认")
    platforms = product.specs.get("支持平台", "")
    if platforms and "/" not in platforms:
        out.append(f"仅支持 {platforms}，换设备后可能无法继续使用")
    if product.specs.get("追加内容"):
        out.append(product.specs["追加内容"] + "，完整体验可能需要额外付费")
    if metrics.price is not None and median_price and metrics.price > median_price * 1.15:
        ratio = metrics.price / median_price
        out.append(
            f"价格是同类中位数（{_money(median_price, metrics.currency or '')}）的 {ratio:.1f} 倍"
        )
    if product.rating.score is None:
        out.append("缺少第三方媒体评分，规格判断依赖内容规模")
    if any("年龄限制" in n for n in product.notes):
        out.append("有年龄限制/内容分级标记，购买前需确认适用性")
    if not out:
        out.append("现有数据里没有可证实的明显缺点；这不等于没有风险，仍需核对退款政策与实际体验")
    return out[:6]


def _advantages(product: Product, metrics: Metrics, peers: list[Product],
                stats: dict[str, float], currency: str) -> list[str]:
    out: list[str] = []
    others = [p for p in peers if p.product_id != product.product_id]
    if metrics.price is not None and stats.get("min_price") is not None \
            and abs(metrics.price - stats["min_price"]) < 0.01 and len(others) >= 1:
        out.append(f"本次对比中价格最低（{_money(metrics.price, currency)}）")
    if metrics.smoothed_rate is not None:
        peer_rates = [_smooth(p.rating.positive_rate, p.rating.review_count) for p in others]
        peer_rates = [r for r in peer_rates if r is not None]
        if peer_rates and metrics.smoothed_rate >= max(peer_rates):
            out.append(f"平滑后口碑最高（{metrics.smoothed_rate:.0f} 分），高于其他 {len(peer_rates)} 个候选")
    if metrics.review_count is not None:
        peer_counts = [p.rating.review_count for p in others if p.rating.review_count]
        if peer_counts and metrics.review_count >= max(peer_counts):
            out.append(f"评价样本最充分（{metrics.review_count:,} 条），口碑更可信")
    if product.rating.score is not None:
        peer_scores = [p.rating.score for p in others if p.rating.score is not None]
        if peer_scores and product.rating.score >= max(peer_scores):
            out.append(f"媒体评分最高（{product.rating.score:g}/{product.rating.scale:g}）")
    if product.offer.discount_percent:
        out.append(f"当前有 {product.offer.discount_percent}% 折扣")
    platforms = product.specs.get("支持平台", "")
    if "/" in platforms:
        out.append(f"支持多平台（{platforms}）")
    if not out:
        out.append("在本次对比的候选中没有突出的单项优势")
    return out[:5]


def _confidence(product: Product, metrics: Metrics) -> str:
    have, total, ratio = product.completeness
    count = metrics.review_count or 0
    if ratio >= 0.8 and count >= 500:
        return "高"
    if ratio >= 0.6 and count >= MIN_REVIEWS_RELIABLE:
        return "中"
    return "低"


def _audience(key: str, product: Product, metrics: Metrics, stats: dict[str, float]) -> str:
    price = metrics.price
    mid = stats.get("median_price")
    tags: list[str] = []
    if key == "value":
        tags.append("希望用更少的钱拿到接近顶级体验的人")
    elif key == "spec":
        tags.append("更看重内容深度与完整体验、预算相对宽松的人")
    elif key == "budget":
        tags.append("预算优先、想先入门再升级的人")
    elif key == "reputation":
        tags.append("不想踩坑、最在意稳定性与口碑的人")
    else:
        tags.append("想一次买对、不愿花太多时间研究的人")
    if price is not None and mid:
        if price <= mid * 0.85:
            tags.append("对价格敏感")
        elif price >= mid * 1.15:
            tags.append("愿意为品质多付一些")
    if (metrics.review_count or 0) >= 5000:
        tags.append("偏好大众验证过的选择")
    return "；".join(tags) + "。"


def _pick_of(key: str, product: Product, score: float, peers: list[Product],
             metrics: Metrics, stats: dict[str, float], basis: str,
             reason: str) -> Pick:
    currency = metrics.currency or stats.get("currency", "")
    return Pick(
        key=key,
        title=PICK_TITLES[key],
        basis=basis,
        product=product,
        score=round(score, 1),
        reason=reason,
        advantages=_advantages(product, metrics, peers, stats, currency),
        drawbacks=_drawbacks(product, metrics, peers, stats.get("median_price")),
        audience=_audience(key, product, metrics, stats),
        data_gaps=list(dict.fromkeys(metrics.gaps + [f"缺失：{m}" for m in product.missing[:4]])),
        confidence=_confidence(product, metrics),
    )


def recommend(products: Iterable[Product], weights: dict[str, float] | None = None) -> Recommendation:
    products = [p for p in products if p is not None]
    weights = {k: float(v) for k, v in (weights or DEFAULT_WEIGHTS).items()}
    result = Recommendation(weights=weights)
    result.products = list(products)

    if not products:
        result.notes.append("没有可比商品。")
        for key in PICK_ORDER:
            result.picks.append(Pick(key=key, title=PICK_TITLES[key], basis="", empty_note="还没有可比商品"))
        return result

    # 货币分组：不换算汇率，只比较数量最多的一组
    by_currency: dict[str, list[Product]] = {}
    for product in products:
        code = (product.offer.currency or "").upper()
        by_currency.setdefault(code, []).append(product)
    currency = max(by_currency, key=lambda c: len(by_currency[c])) if by_currency else ""
    if "" in by_currency and len(by_currency[""]) > len(by_currency.get(currency, [])):
        currency = ""
    group = by_currency.get(currency, [])
    if len(by_currency) > 1:
        names = "、".join(f"{c or '货币未知'}({len(v)})" for c, v in by_currency.items())
        result.notes.append(
            f"检测到多种货币（{names}），本工具不做汇率换算，只比较数量最多的那一组"
            f"（{currency or '货币未知'}，{len(group)} 个）。"
        )
    result.currency = currency
    if currency == "" and sum(1 for p in group if p.offer.price is not None) >= 2:
        result.notes.append(
            "参与比较的商品货币未知，价格对比仅供参考；请先确认各商品的实际币种，"
            "本工具不会默认按人民币处理。"
        )

    prices = [p.offer.price for p in group if p.offer.price is not None and p.offer.price > 0]
    stats: dict[str, float] = {"currency": currency} if currency else {}
    if prices:
        stats.update({
            "min_price": min(prices),
            "max_price": max(prices),
            "median_price": round(statistics.median(prices), 2),
            "mean_price": round(statistics.fmean(prices), 2),
        })
    result.price_stats = stats

    result.metrics = compute_metrics(group, weights)
    result.products = list(group)
    peers = group

    # ---- 口碑较好 ----
    rated = [(p, result.metrics[p.product_id]) for p in peers
             if result.metrics[p.product_id].smoothed_rate is not None]
    reliable = [(p, m) for p, m in rated
                if (m.review_count or 0) >= MIN_REVIEWS_RELIABLE]
    pool = reliable or rated
    if len(pool) >= MIN_SAMPLE:
        product, metrics = max(pool, key=lambda x: x[1].smoothed_rate or 0)
        count_note = "" if reliable else "（所有候选评价数都少于 50，可信度受限）"
        result.picks.append(_pick_of(
            "reputation", product, metrics.smoothed_rate or 0, peers, metrics, stats,
            "贝叶斯平滑好评率 =（好评率×评价数 + 70×50）÷（评价数+50），评价少会被拉向平均",
            f"好评率 {metrics.positive_rate:.0f}%、{metrics.review_count or 0:,} 条评价，"
            f"平滑后 {metrics.smoothed_rate:.1f} 分，在 {len(pool)} 个有口碑数据的候选中最高{count_note}。",
        ))
    else:
        result.picks.append(Pick(
            key="reputation", title=PICK_TITLES["reputation"], basis="贝叶斯平滑好评率",
            empty_note=(f"只有 {len(pool)} 个候选拿到了好评率数据，"
                        f"少于 {MIN_SAMPLE} 个样本不足以下结论，因此不评选口碑冠军。"),
        ))

    # ---- 性价比最优 ----
    valued = [(p, result.metrics[p.product_id]) for p in peers
              if result.metrics[p.product_id].value_score is not None]
    if len(valued) >= MIN_SAMPLE:
        product, metrics = max(valued, key=lambda x: x[1].value_score or 0)
        result.picks.append(_pick_of(
            "value", product, metrics.value_score or 0, peers, metrics, stats,
            "性价比分 = 平滑好评率 ×（组内最低价 ÷ 本品价）",
            f"口碑 {metrics.smoothed_rate:.1f} 分，价格 {_money(metrics.price, currency)}"
            f"（组内最低 {_money(stats.get('min_price'), currency)}），"
            f"性价比得分 {metrics.value_score:.1f}，为 {len(valued)} 个候选中的最高值。",
        ))
    else:
        result.picks.append(Pick(
            key="value", title=PICK_TITLES["value"], basis="好评率 × 相对价格系数",
            empty_note="同时具备「价格」和「好评率」的候选少于 2 个，无法计算性价比，因此不评选。",
        ))

    # ---- 规格/内容最优 ----
    specced = [(p, result.metrics[p.product_id]) for p in peers
               if result.metrics[p.product_id].spec_score is not None]
    if len(specced) >= MIN_SAMPLE:
        product, metrics = max(specced, key=lambda x: x[1].spec_score or 0)
        result.picks.append(_pick_of(
            "spec", product, metrics.spec_score or 0, peers, metrics, stats,
            "规格分 = 媒体评分 60% + 支持语言 20% + 功能特性 20%（游戏类无硬件性能，用内容体量近似）",
            f"规格/内容得分 {metrics.spec_score:.1f}"
            + (f"（Metacritic {product.rating.score:g}/100）" if product.rating.score else "")
            + f"，在 {len(specced)} 个可评估的候选中最高。",
        ))
    else:
        result.picks.append(Pick(
            key="spec", title=PICK_TITLES["spec"], basis="媒体评分 + 内容规模",
            empty_note=f"只有 {len(specced)} 个候选能算出规格分（需要媒体评分或内容规模数据），样本不足，不评选。",
        ))

    # ---- 预算友好 ----
    priced = [(p, result.metrics[p.product_id]) for p in peers
              if result.metrics[p.product_id].price is not None]
    qualified = [(p, m) for p, m in priced if (m.smoothed_rate or 0) >= BUDGET_QUALITY_FLOOR]
    if len(priced) >= MIN_SAMPLE:
        pool_b = qualified or priced
        product, metrics = min(pool_b, key=lambda x: x[1].price or 0)
        floor_note = (
            f"在口碑 ≥ {BUDGET_QUALITY_FLOOR:.0f} 分的 {len(qualified)} 个候选里价格最低"
            if qualified else
            "没有候选达到默认口碑底线，退化为「全部候选中价格最低」，请谨慎参考"
        )
        result.picks.append(_pick_of(
            "budget", product, metrics.price or 0, peers, metrics, stats,
            f"先筛掉口碑低于 {BUDGET_QUALITY_FLOOR:.0f} 分的候选，再取价格最低者",
            f"{floor_note}，价格 {_money(metrics.price, currency)}，"
            f"口碑 {metrics.smoothed_rate:.0f} 分。" if metrics.smoothed_rate is not None
            else f"{floor_note}，价格 {_money(metrics.price, currency)}。",
        ))
    else:
        result.picks.append(Pick(
            key="budget", title=PICK_TITLES["budget"], basis="口碑达标后的最低价",
            empty_note="拿到价格的候选少于 2 个，无法做预算比较。",
        ))

    # ---- 综合最推荐 ----
    overalled = [(p, result.metrics[p.product_id]) for p in peers
                 if result.metrics[p.product_id].overall is not None]
    if len(overalled) >= MIN_SAMPLE:
        product, metrics = max(overalled, key=lambda x: x[1].overall or 0)
        weight_text = " / ".join(
            f"{WEIGHT_LABELS[k]} {int(weights[k])}%" for k in ("value", "reputation", "spec", "price")
        )
        result.picks.append(_pick_of(
            "overall", product, metrics.overall or 0, peers, metrics, stats,
            f"加权综合分（{weight_text}），缺失维度按剩余权重重新归一",
            f"综合得分 {metrics.overall:.1f}：性价比 {metrics.value_score or '—'}、"
            f"口碑 {metrics.smoothed_rate or '—'}、规格 {metrics.spec_score or '—'}、"
            f"价格分 {metrics.price_score or '—'}，在 {len(overalled)} 个候选中最优。",
        ))
    else:
        result.picks.append(Pick(
            key="overall", title=PICK_TITLES["overall"], basis="四维加权",
            empty_note=f"只有 {len(overalled)} 个候选的关键指标足以计算综合分，样本不足，不评选综合冠军。",
        ))

    # 保持稳定顺序
    order = {k: i for i, k in enumerate(PICK_ORDER)}
    result.picks.sort(key=lambda p: order.get(p.key, 99))
    return result
