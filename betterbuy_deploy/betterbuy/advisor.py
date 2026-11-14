"""购买建议（V0.5 雏形）。

两层：
1. **规则版（默认，永远可用）**：解析用户需求中的预算与用途，基于真实字段生成
   最推荐 / 分档替代 / 优缺点 / 购买前确认清单 / 不推荐的原因。
2. **大模型增强（可选）**：只有在用户显式同意、且本机配置了兼容 OpenAI 的接口时才会调用；
   传给模型的只有结构化商品事实，不包含任何个人信息。未配置时自动退回规则版，
   不会假装"AI 已经分析过"。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import requests

from .models import UNKNOWN, Product
from .recommend import Metrics, Recommendation
from .zero_cost import zero_cost_mode

__all__ = ["Advice", "parse_budget", "parse_needs", "rule_advice",
           "llm_available", "llm_config", "llm_advice", "llm_blocked_reason"]

BUDGET_FLOOR_NOTE = "未说明下限时不做下限筛选"


@dataclass
class Advice:
    mode: str = "规则模式"
    summary: str = ""
    best: Product | None = None
    best_reason: str = ""
    alternatives: list[dict] = field(default_factory=list)
    pros_cons: list[dict] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    avoid: list[dict] = field(default_factory=list)
    budget: tuple[float | None, float | None] = (None, None)
    notes: list[str] = field(default_factory=list)


def parse_budget(text: str) -> tuple[float | None, float | None, str]:
    """从自然语言里解析预算，返回 (下限, 上限, 说明)。解析不出返回 (None, None, "")。"""
    if not text:
        return None, None, ""
    text = text.replace("，", ",").replace("～", "-").replace("~", "-")
    # 800-1200元 / 800到1200 / 800至1200
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|到|至)\s*(\d+(?:\.\d+)?)", text)
    if range_match:
        low, high = sorted((float(range_match.group(1)), float(range_match.group(2))))
        return low, high, f"按你给出的区间 {low:g}–{high:g} 元理解"
    under = re.search(r"(?:不超过|最多|预算不超过|低于|小于)\s*(\d+(?:\.\d+)?)", text)
    if under:
        return None, float(under.group(1)), f"按「不超过 {float(under.group(1)):g} 元」理解"
    budget = re.search(r"预算\s*(?:是|为|大概|大约|约)?\s*(\d+(?:\.\d+)?)", text)
    if budget:
        value = float(budget.group(1))
        if "以上" in text or "起" in text:
            return value, None, f"按「{value:g} 元以上」理解"
        return None, value, f"按「预算 {value:g} 元」理解为价格上限"
    money = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块|rmb|人民币)", text, re.I)
    if money:
        value = float(money.group(1))
        return None, value, f"按「预算 {value:g} 元」理解为价格上限"
    return None, None, ""


_NEED_TAGS = {
    "学习": ("学习", "网课", "上课", "作业", "写论文", "编程"),
    "游戏": ("游戏", "打游戏", "联机", "单机", "Steam"),
    "办公": ("办公", "文档", "表格", "开会", "写代码"),
    "便携": ("便携", "轻", "随身", "出差"),
    "性价比": ("性价比", "划算", "便宜", "省钱"),
    "口碑": ("口碑", "评价", "稳定", "不踩坑", "靠谱"),
    "内容量": ("耐玩", "内容", "时长", "深度", "长期"),
}


def parse_needs(text: str) -> list[str]:
    if not text:
        return []
    return [tag for tag, words in _NEED_TAGS.items() if any(w.lower() in text.lower() for w in words)]


def _in_budget(product: Product, low: float | None, high: float | None) -> bool:
    price = product.offer.price
    if price is None:
        return False
    if low is not None and price < low:
        return False
    if high is not None and price > high:
        return False
    return True


def _price(product: Product) -> float | None:
    return product.offer.price


def rule_advice(products: list[Product], rec: Recommendation, need_text: str = "") -> Advice:
    advice = Advice(mode="规则模式")
    if not products:
        advice.summary = "还没有可比商品，无法给出建议。"
        return advice

    low, high, budget_note = parse_budget(need_text)
    advice.budget = (low, high)
    if budget_note:
        advice.notes.append(budget_note + f"；{BUDGET_FLOOR_NOTE}。")
    needs = parse_needs(need_text)
    if needs:
        advice.notes.append("识别到的需求关键词：" + "、".join(needs) + "。")

    candidates = [p for p in products if p.offer.price is not None]
    if not candidates:
        # 有商品但一个价格都没有（接口未返回价格 / 只识别到标题）：
        # 不能进排序，否则后面 ranked[0] 会直接崩。这里给出诚实的降级结论。
        advice.summary = (
            "本次采集到的商品都拿不到可用价格，因此无法判断哪一款更划算，也不做分档推荐。"
            "下面只给出需要你自行确认的清单。"
        )
        advice.notes.append(
            f"共 {len(products)} 个商品，其中 0 个有明确价格；未计算综合分。"
        )
        advice.checklist = [
            "到商品页确认当前价格、运费与是否含税，本工具没有拿到价格字段。",
            "确认商品全称（含版本/容量后缀），避免把不同配置当成同一款。",
            "确认退换货与保修条件。",
        ]
        advice.avoid.append({"商品": "—", "原因": "缺少价格数据，无法判断哪一款更划算。"})
        return advice
    if low is not None or high is not None:
        in_budget = [p for p in candidates if _in_budget(p, low, high)]
        if not in_budget:
            advice.summary = (
                f"在 {high and f'不超过 {high:g} 元' or f'不低于 {low:g} 元'} 的条件下，"
                "本次采集到的商品里没有符合的。下面列出超出最多的差额，供你判断是否调整预算。"
            )
            over = sorted(
                [p for p in candidates if high and (_price(p) or 0) > high],
                key=lambda p: (_price(p) or 0),
            )[:2]
            for product in over:
                advice.avoid.append({
                    "商品": product.name,
                    "原因": f"价格 {_price(product):,.2f} 元，超出预算 {_price(product) - high:,.2f} 元",
                })
            return advice
        candidates = in_budget
        advice.notes.append(f"已按预算筛选，剩余 {len(candidates)} 个候选。")

    metrics = rec.metrics

    def score_of(product: Product) -> float:
        metric = metrics.get(product.product_id)
        return metric.overall if metric and metric.overall is not None else -1.0

    ranked = sorted(candidates, key=score_of, reverse=True)
    best = ranked[0]
    best_metric = metrics.get(best.product_id)
    overall_pick = rec.pick("overall")

    advice.best = best
    if best_metric and best_metric.overall is not None:
        advice.best_reason = (
            f"综合得分 {best_metric.overall:.1f}，是预算内可计算综合分的候选中最高的；"
            f"口碑 {best_metric.smoothed_rate or '—'}、价格 {best.offer.display()}、"
            f"规格 {best_metric.spec_score or '—'}。"
        )
    else:
        advice.best_reason = (
            "该商品的关键指标不足以算出综合分，只是因为其他候选缺数据更严重而排在最前；"
            "请把它当作「信息相对完整」的选择，不要当作结论。"
        )
        advice.notes.append("注意：综合分缺失时本工具不会假装有结论。")

    if overall_pick and overall_pick.product and not overall_pick.empty:
        if overall_pick.product.product_id != best.product_id:
            advice.notes.append(
                f"全部候选的综合冠军是「{overall_pick.product.name}」，"
                "但它不符合你给出的预算条件，因此未作为首选。"
            )

    advice.summary = (
        f"在 {len(candidates)} 个符合条件（含预算筛选）的候选中，建议优先考虑「{best.name}」。"
    )

    # ---- 分档替代 ----
    prices = [_price(p) for p in ranked if _price(p) is not None]
    if len(ranked) < 4:
        # 样本太少时分位数没有意义，直接按综合排序给备选
        for index, product in enumerate(ranked[1:4], start=2):
            metric = metrics.get(product.product_id)
            advice.alternatives.append({
                "档位": f"备选第 {index} 名",
                "商品": product.name,
                "价格": product.offer.display(),
                "口碑": product.rating.display(),
                "综合分": metric.overall if metric else None,
                "理由": f"综合得分 {metric.overall:.1f}，低于首选" if metric and metric.overall is not None
                          else "关键指标不足，未能计算综合分",
                "链接": product.url,
            })
    elif len(prices) >= 2:
        ordered = sorted(prices)
        q1 = ordered[len(ordered) // 3]
        q2 = ordered[(2 * len(ordered)) // 3]
        bands = [("预算优先档", 0, q1), ("均衡档", q1, q2), ("体验优先档", q2, float("inf"))]
        seen: set[str] = set()
        for label, lo, hi in bands:
            pool = [p for p in ranked if _price(p) is not None and lo <= _price(p) <= hi
                    and p.product_id not in seen]
            if not pool:
                continue
            product = pool[0]
            seen.add(product.product_id)
            metric = metrics.get(product.product_id)
            advice.alternatives.append({
                "档位": label,
                "商品": product.name,
                "价格": product.offer.display(),
                "口碑": product.rating.display(),
                "综合分": metric.overall if metric else None,
                "理由": f"该价位段内综合排序第一（{len(pool)} 个候选）",
                "链接": product.url,
            })
    if not advice.alternatives:
        advice.alternatives.append({
            "档位": "—", "商品": "不足 2 个有价格的候选，无法分档", "价格": "",
            "口碑": "", "综合分": None, "理由": "", "链接": "",
        })

    # ---- 优缺点 ----
    for product in ranked[:3]:
        metric = metrics.get(product.product_id)
        if metric is None:
            continue
        pros, cons = [], []
        if metric.smoothed_rate is not None:
            pros.append(f"口碑 {product.rating.positive_rate:.0f}%（平滑后 {metric.smoothed_rate:.0f}）")
        if metric.price_score is not None and metric.price_score >= 99:
            pros.append("组内价格最低")
        if product.rating.score is not None:
            pros.append(f"媒体评分 {product.rating.score:g}/100")
        if product.specs.get("支持平台", "").count("/"):
            pros.append(f"支持多平台（{product.specs['支持平台']}）")
        if (product.rating.review_count or 0) >= 1000:
            pros.append(f"评价样本充足（{product.rating.review_count:,} 条）")

        if (product.rating.review_count or 0) < 50:
            cons.append("评价样本少，口碑可能不稳定")
        if metric.price is not None and metric.price_score is not None and metric.price_score < 60:
            cons.append("价格明显高于组内最低价")
        if product.rating.score is None:
            cons.append("缺少第三方媒体评分")
        if "简体中文" not in product.specs.get("支持语言", "") and product.specs.get("支持语言"):
            cons.append("未列出简体中文支持")
        if product.specs.get("追加内容"):
            cons.append("有付费 DLC，完整体验成本更高")
        if product.missing:
            cons.append("缺失字段：" + "、".join(product.missing[:3]))

        advice.pros_cons.append({
            "商品": product.name,
            "优点": pros or ["在本轮数据中没有可验证的突出优点"],
            "缺点": cons or ["未发现数据可证实的明显缺点"],
        })

    # ---- 购买前确认清单 ----
    checklist = ["下单前再核对一次页面价格、运费与是否含税，本工具记录的是采集时刻的标价。"]
    if any(p.offer.discount_percent for p in ranked):
        discounted = [p for p in ranked if p.offer.discount_percent]
        checklist.append(
            f"有 {len(discounted)} 个商品正在打折（最高 {max(p.offer.discount_percent for p in discounted)}%），"
            "请确认活动结束时间，避免按折扣价做预算后失效。"
        )
    if any(p.specs.get("最低配置") for p in ranked):
        checklist.append("确认自己的设备满足页面列出的最低配置（表格里有原文摘要）。")
    if any(p.specs.get("追加内容") for p in ranked):
        checklist.append("确认是否需要购买 DLC 才等于完整体验，以及 DLC 的额外费用。")
    if any(p.specs.get("支持语言") for p in ranked):
        checklist.append("确认界面/字幕语言是否满足需要（Steam 部分语言仅界面支持）。")
    if any(p.rating.review_count is None for p in ranked):
        checklist.append("部分商品拿不到评价数量，其好评率仅作参考，不要当作整体口碑结论。")
    checklist.append("对你最在意的一项（预算 / 口碑 / 内容量）做最终确认，再决定下单。")
    advice.checklist = checklist

    # ---- 不推荐的原因 ----
    for product in ranked:
        metric = metrics.get(product.product_id)
        if metric is None:
            continue
        reasons = []
        if metric.positive_rate is not None and metric.positive_rate < 70 and (metric.review_count or 0) >= 50:
            reasons.append(f"好评率仅 {metric.positive_rate:.0f}%，评价 {metric.review_count:,} 条，口碑偏弱")
        if product.rating.recent_positive_rate is not None and product.rating.positive_rate is not None \
                and product.rating.recent_positive_rate + 8 < product.rating.positive_rate:
            reasons.append("近 30 天好评率明显低于总体，存在口碑下滑")
        if metric.price is not None and rec.price_stats.get("median_price") \
                and metric.price > rec.price_stats["median_price"] * 1.3 \
                and (metric.value_score or 0) < 40:
            reasons.append("在本组内价格偏高、性价比得分偏低（只说明价格与口碑的组合不划算，不代表质量差）")
        if metric.positive_rate is None and metric.price is not None:
            reasons.append("缺少口碑数据，仅凭价格无法判断是否值得买")
        if reasons:
            advice.avoid.append({"商品": product.name, "原因": "；".join(reasons)})

    if not advice.avoid:
        advice.avoid.append({"商品": "—", "原因": "本次候选中没有发现数据可证实的明显不推荐项（不代表没有风险）。"})
    return advice


# ---------------- 可选大模型增强 ----------------
def llm_config() -> dict[str, str]:
    return {
        "base_url": os.environ.get("BETTERBUY_LLM_BASE_URL", "").rstrip("/"),
        "api_key": os.environ.get("BETTERBUY_LLM_API_KEY", ""),
        "model": os.environ.get("BETTERBUY_LLM_MODEL", ""),
    }


def llm_available() -> bool:
    """大模型是否可用。

    **零成本模式下恒为 False**（默认就是零成本模式）：即使你配置了
    ``BETTERBUY_LLM_*`` 三项，只要没有显式设置 ``BETTERBUY_ZERO_COST=0``，
    这里仍然返回 ``False``，推荐与建议一律走纯规则的 Python 算法，不产生任何费用。
    """
    if zero_cost_mode():
        return False
    cfg = llm_config()
    return bool(cfg["base_url"] and cfg["api_key"] and cfg["model"])


def llm_blocked_reason() -> str:
    """给界面看：为什么大模型路径不可用。"""
    if zero_cost_mode():
        return ("当前为零成本模式，已禁用大模型调用。推荐与购买建议由纯规则的 Python "
                "算法生成，不产生任何 API 费用。如确需模型增强，需显式设置 "
                "BETTERBUY_ZERO_COST=0 并自行承担费用。")
    return "未配置大模型接口（需要 BETTERBUY_LLM_BASE_URL / API_KEY / MODEL 三项）。"


def _facts(products: list[Product], rec: Recommendation) -> list[dict]:
    """只把结构化事实交给模型，避免它凭空编造。"""
    facts = []
    for product in products:
        metric: Metrics | None = rec.metrics.get(product.product_id)
        facts.append({
            "名称": product.name,
            "平台": product.platform,
            "价格": product.offer.display(),
            "货币": product.offer.currency,
            "好评率": product.rating.positive_rate,
            "评价数": product.rating.review_count,
            "媒体评分": product.rating.score,
            "口碑口径": product.rating.scope,
            "类别": product.category,
            "规格": {k: v for k, v in list(product.specs.items())[:6]},
            "缺失字段": product.missing,
            "综合分": metric.overall if metric else None,
        })
    return facts


def llm_advice(products: list[Product], rec: Recommendation, need_text: str,
               *, allow_external: bool = False, timeout: float = 40) -> Advice | None:
    """调用兼容 OpenAI 的接口生成建议。未配置或未同意时返回 None（由调用方回退规则版）。"""
    if not allow_external or not llm_available() or not products:
        return None
    cfg = llm_config()
    payload = {
        "model": cfg["model"],
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是消费决策助手。只能使用用户提供的 JSON 事实回答，不得编造商品、价格、评分或评价。"
                    "如果事实不足，必须说明数据不足。输出严格 JSON，键为："
                    "summary, best(名称或null), best_reason, alternatives([{档位,商品,价格,理由}]),"
                    "checklist([字符串]), avoid([{商品,原因}])。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"用户需求": need_text, "商品事实": _facts(products, rec)},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    try:
        resp = requests.post(
            f"{cfg['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(_strip_code_fence(content))
    except Exception:
        return None

    names = {p.name for p in products}
    best_name = data.get("best")
    best = next((p for p in products if p.name == best_name), None) if best_name in names else None
    # 只保留能对上真实商品名的条目，防止模型编造
    alternatives = [a for a in data.get("alternatives", []) if a.get("商品") in names]
    avoid = [a for a in data.get("avoid", []) if a.get("商品") in names or a.get("商品") == "—"]

    advice = rule_advice(products, rec, need_text)
    advice.mode = "大模型增强"
    advice.best = best or advice.best
    advice.best_reason = data.get("best_reason") or advice.best_reason
    advice.summary = data.get("summary") or advice.summary
    if alternatives:
        advice.alternatives = alternatives
    if data.get("checklist"):
        advice.checklist = list(data["checklist"])[:8]
    if avoid:
        advice.avoid = avoid
    advice.notes.append(
        f"本次建议由外部大模型（{cfg['model']}）生成，商品事实与规则结果仍来自本地采集；"
        "模型输出已在本地校验商品名，对不上真实商品的条目会被丢弃。"
    )
    return advice


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
    return text.strip()
