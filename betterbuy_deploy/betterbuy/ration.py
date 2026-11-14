"""AI 理性消费分析（规则引擎）。

输出三档结论，与需求文档一致：

- **推荐购买**：需求明确、预算可接受、没有实质性风险信号，且**有真实商品数据支撑**。
- **建议进一步比较**：需求本身可能成立，但缺价格/参数/兼容性信息，现在下结论不可靠。
- **建议暂缓购买**：需求尚不明确，或存在多条叠加的冲动信号，建议先确认使用频率与已有物品。

三条不可违反的约束（都有测试钉住）：

1. **不因价格高低判定冲动消费。** 价格只影响「预算是否匹配」这一项，
   而且措辞是「预算偏低/偏高」，不是「你冲动了」。
2. **没有商品数据就不给「推荐购买」。** 缺少可比价格时最多到「进一步比较」，
   并明确说明缺什么。
3. **每条结论都能回溯到具体依据。** ``trace`` 字段记录每条规则是否命中及原因，
   界面上的「查看分析依据」直接展示这份记录，不存在「黑箱结论」。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import PRICE_BAND_NOTE, Category, get_category
from .intent import (FREQUENCY_HIGH, FREQUENCY_LOW, FREQUENCY_MEDIUM, Intent,
                     parse_intent)

__all__ = ["BudgetCheck", "RationResult", "analyze", "VERDICT_BUY",
           "VERDICT_COMPARE", "VERDICT_WAIT", "MODE_LABEL"]

VERDICT_BUY = "推荐购买"
VERDICT_COMPARE = "建议进一步比较"
VERDICT_WAIT = "建议暂缓购买"

#: 明确告诉用户这是什么技术。不允许写成「AI 大模型分析」。
MODE_LABEL = "规则引擎（关键词 + 判定表，非大模型）"

#: 判定阈值，集中放在这里便于测试与调参说明。
WAIT_RISK_SCORE = 5          # 风险信号累计到这个分数就建议暂缓
COMPARE_RISK_SCORE = 3       # 到这个分数就至少要到「进一步比较」


@dataclass
class BudgetCheck:
    level: str = "未提供"       # 合理 / 偏低 / 偏高 / 未提供
    text: str = ""
    band: tuple[float, float] = (0.0, 0.0)


@dataclass
class RationResult:
    verdict: str = VERDICT_COMPARE
    verdict_key: str = "compare"
    confidence: str = "低"
    headline: str = ""
    reasons: list[str] = field(default_factory=list)
    budget_check: BudgetCheck = field(default_factory=BudgetCheck)
    focus: list[dict] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    intent: Intent = field(default_factory=Intent)
    data_gaps: list[str] = field(default_factory=list)
    disclaimer: str = MODE_LABEL


def _focus_specs(category: Category, intent: Intent, limit: int = 5) -> list[dict]:
    """挑出该品类最该关注的参数，并按用户用途调整**顺序**。

    只排序、不额外打标签：同一品类下「哪些参数重要」是客观的，
    「哪条对你最重要」则由用途决定，用顺序表达就够了。
    """
    purpose_text = " ".join(intent.purposes)

    def weight(index: int, hint) -> int:
        score = 100 - index
        if "游戏" in purpose_text and any(k in hint.name for k in ("刷新率", "延迟", "散热", "显卡", "重量")):
            score += 30
        if ("设计" in purpose_text or "剪辑" in purpose_text) and any(
                k in hint.name for k in ("色域", "分辨率", "显色", "屏幕")):
            score += 30
        if "学习" in purpose_text and any(k in hint.name for k in ("续航", "重量", "照度", "坐深")):
            score += 20
        if "办公" in purpose_text and any(k in hint.name for k in ("面板", "接口", "支架", "分辨率")):
            score += 20
        if "通勤" in purpose_text and any(k in hint.name for k in ("重量", "便携", "尺寸")):
            score += 25
        return score

    ranked = sorted(
        ((weight(i, h), h) for i, h in enumerate(category.specs)),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [
        {
            "参数": hint.name,
            "为什么重要": hint.why,
            "常见坑": hint.watch_out or "—",
        }
        for score, hint in ranked[:limit]
    ]


def _build_checks(category: Category, intent: Intent) -> list[str]:
    checks: list[str] = []
    if intent.owned_devices:
        owned = "、".join(get_category(k).name for k in intent.owned_devices)
        checks.append(f"你现有的设备是「{owned}」，下面这些确认项请对照它逐条核对。")
    for rule in category.compat:
        prefix = "必须先确认（不满足会直接影响使用）：" if rule.fatal_if_missing else "需要确认："
        checks.append(f"{prefix}{rule.need} —— {rule.why}")
    # 参数的常见坑已经在「这类商品该重点看什么」表里列过，这里不重复，
    # 清单只放「需要动手确认的动作」。
    if intent.category_key == "other":
        checks.append("该品类暂未收录专门规则，请自行确认核心参数与兼容性，或换一个更具体的说法。")
    checks.append("在商品页核对型号全称（含后缀）与版本，避免买到上一代或不同配置。")
    checks.append("确认退换货与保修条件：能否七天无理由、保修是否覆盖你所在地区。")
    return checks


def _budget_check(intent: Intent, category: Category) -> BudgetCheck:
    if not intent.has_budget or category.price_high <= 0:
        return BudgetCheck("未提供", "你没有给出预算，因此无法判断预算是否匹配。", 
                           (category.price_low, category.price_high))
    cap = intent.budget_high if intent.budget_high is not None else intent.budget_low
    band = (category.price_low, category.price_high)
    if cap is None:
        return BudgetCheck("未提供", "", band)
    if cap < category.price_low * 0.6:
        return BudgetCheck(
            "偏低",
            f"你给的预算 {cap:g} 元低于该品类常见区间下限 {category.price_low:g} 元。"
            "不代表买不到能用的，但很可能要在关键参数上让步，或者考虑成色好的二手。",
            band,
        )
    if cap > category.price_high * 2.2:
        return BudgetCheck(
            "偏高",
            f"你给的预算 {cap:g} 元明显高于该品类常见区间上限 {category.price_high:g} 元。"
            "更高的预算未必对应你能感知到的提升，建议先明确「多花的钱买到了什么」。",
            band,
        )
    return BudgetCheck(
        "合理",
        f"你给的预算 {cap:g} 元落在该品类常见区间 {category.price_low:g}–{category.price_high:g} 元内。",
        band,
    )


def analyze(text: str, *, product_count: int = 0, priced_count: int = 0,
            facts_ok: bool = False) -> RationResult:
    """分析一次购买意图。

    Args:
        text: 用户的原始描述（链接会被忽略，只解析自然语言部分）。
        product_count: 本次采集到的真实商品数量（0 表示没有商品数据）。
        priced_count: 其中有明确价格的商品数量。
        facts_ok: 是否有可用于比较的字段（价格 + 至少一项规格或口碑）。
    """
    intent = parse_intent(text)
    category = get_category(intent.category_key)
    result = RationResult(intent=intent)

    # 没有自然语言内容（空输入，或只粘了一个链接）时不出结论，只提示怎么写。
    from .intent import strip_urls
    if not strip_urls(text or "").strip() and product_count == 0:
        result.verdict = ""
        result.verdict_key = "empty"
        result.confidence = "无"
        result.headline = "还没有可以分析的需求描述。"
        result.reasons = [
            "写一句「想买什么 + 用来做什么 + 大概多少预算」，例如："
            "「想花 800 元买人体工学椅，每天在宿舍学习用」。"
        ]
        result.data_gaps = ["需求描述"]
        result.disclaimer = MODE_LABEL
        return result
    result.budget_check = _budget_check(intent, category)
    result.focus = _focus_specs(category, intent)
    result.checks = _build_checks(category, intent)
    result.data_gaps = list(intent.missing)

    trace: list[dict] = []

    def rule(name: str, hit: bool, detail: str) -> bool:
        trace.append({"规则": name, "命中": hit, "说明": detail})
        return hit

    has_purpose = rule("是否说明了购买用途", intent.has_purpose,
                       "、".join(intent.purposes) if intent.purposes else "需求里没有出现用途描述")
    has_budget = rule("是否给出预算", intent.has_budget,
                      intent.budget_note or "需求里没有出现金额")
    freq_ok = rule("使用频率是否支持购买", intent.frequency in (FREQUENCY_HIGH, FREQUENCY_MEDIUM),
                   intent.frequency + (f"（依据：{intent.frequency_evidence}）" if intent.frequency_evidence else ""))
    owned = rule("是否已有同品类物品", intent.owns_same_category,
                 "、".join(get_category(k).name for k in intent.owned_devices)
                 if intent.owned_devices else "未提及已有同品类物品")
    problem = rule("是否说清了现有物品的具体问题", intent.has_problem,
                   "、".join(intent.problem_described[:3]) if intent.has_problem
                   else "没有描述现有物品哪里不行")
    alt = rule("是否已经考虑过维修/二手/升级", intent.considered_alternative,
               "已考虑" if intent.considered_alternative else "未提及替代方案")
    risk = intent.risk_score
    risk_hit = rule(f"风险信号累计分（≥{COMPARE_RISK_SCORE} 需留意，≥{WAIT_RISK_SCORE} 建议暂缓）",
                    risk >= COMPARE_RISK_SCORE, f"当前 {risk} 分，共 {len(intent.signals)} 条信号")
    data_hit = rule("是否有真实商品数据支撑结论", facts_ok,
                    f"已采集 {product_count} 个商品，其中 {priced_count} 个有明确价格"
                    if product_count else "本次没有采集到任何商品数据")
    band = result.budget_check.level
    band_hit = rule("预算是否落在该品类常见区间", band == "合理", f"预算判断：{band}")

    # ---------------- 决策 ----------------
    # 1) 暂缓：需求本身不成立，或风险信号叠加
    wait_reasons: list[str] = []
    if not has_purpose and not has_budget and not problem and not facts_ok:
        wait_reasons.append(
            "你目前只说了想买什么，既没有说用途、也没有给预算，更没有说现在哪里不够用——"
            "这种情况下任何「推荐」都是猜的。补上一句具体用途，结论会完全不一样。"
        )
    if owned and not has_purpose and not problem:
        wait_reasons.append("你提到了同类物品，但没说明现在这个哪里不够用——"
                            "这种情况下新增购买很容易和现有物品功能重叠。")
    if owned and intent.frequency == FREQUENCY_LOW and not problem:
        wait_reasons.append(f"你自己预期的使用频率是「{intent.frequency}」，"
                            "而手上已经有同类物品，先确认现有物品是否真的不够用更稳妥。")
    if risk >= WAIT_RISK_SCORE:
        top = "、".join(s.label for s in intent.signals[:3])
        wait_reasons.append(f"累计 {risk} 分的风险信号（{top}），建议先补齐信息再决定。")
    promo = next((s for s in intent.signals if s.key == "promo_pressure"), None)
    emotion = next((s for s in intent.signals if s.key == "emotion_driven"), None)
    if promo and emotion:
        wait_reasons.append("同时出现了促销紧迫感话术和情绪化表述，"
                            "这是最典型的冲动触发组合，建议至少隔一天再决定——"
                            "如果一天后还想要，那多半是真的需要。")

    # 2) 进一步比较：需求成立但数据不足 / 需要确认
    compare_reasons: list[str] = []
    if not facts_ok:
        if product_count == 0:
            compare_reasons.append("这次没有拿到任何商品的可比数据（价格、参数、口碑），"
                                   "因此无法判断「买哪一款」，只能先帮你把需求和检查清单理清。")
        else:
            compare_reasons.append(f"采集到 {product_count} 个商品，但只有 {priced_count} 个有明确价格，"
                                   "字段不足以做可靠比较。")
    if band in ("偏低", "偏高"):
        compare_reasons.append(f"预算判断为「{band}」：{result.budget_check.text}")
    if not has_budget and facts_ok:
        compare_reasons.append("没有预算约束时，很难判断采集到的价格算不算划算。")
    if not has_purpose and facts_ok:
        compare_reasons.append(
            "这次拿到了商品的价格与参数，但这些数据只能回答「这个商品本身怎么样」，"
            "回答不了「它是否适合你」——因为你还没说用途和大概多久用一次。补上一句，结论会准很多。"
        )
    if problem and category.green:
        cheap_fix = sorted(category.green, key=lambda p: p.priority)[0]
        compare_reasons.append(
            f"你提到的问题是「{intent.problem_described[0]}」，它有可能不用换新就能解决："
            f"{cheap_fix.title}。先花几分钟确认这一步，再决定要不要买新的。"
        )
    if COMPARE_RISK_SCORE <= risk < WAIT_RISK_SCORE:
        compare_reasons.append("有 " + "、".join(s.label for s in intent.signals[:3]) +
                               " 等信号，建议先确认清楚再下单。")
    # 兼容性不进结论，只进检查清单：本项目拿不到用户设备型号，
    # 用「不知道是否兼容」去否决一次购买是不诚实的。

    # 3) 推荐购买
    buy_reasons: list[str] = []
    if has_purpose and has_budget and band == "合理" and risk < COMPARE_RISK_SCORE and facts_ok:
        buy_reasons.append("用途明确、预算落在合理区间、没有实质性风险信号，且本次有真实商品数据支撑。")

    # ---------------- 汇总 ----------------
    if wait_reasons:
        result.verdict, result.verdict_key = VERDICT_WAIT, "wait"
        result.reasons = wait_reasons
        result.headline = "建议先别急着下单，把下面几件事确认清楚。"
    elif compare_reasons:
        result.verdict, result.verdict_key = VERDICT_COMPARE, "compare"
        result.reasons = compare_reasons
        if has_purpose and band in ("合理", "未提供"):
            result.headline = "你的需求本身是站得住的，但还缺数据或确认项，现在下单有踩坑空间。"
        elif problem:
            result.headline = ("你说的问题是具体的，这个需求站得住；但还没有可比价格，"
                               "先按清单确认，再决定是修还是换。")
        else:
            result.headline = "信息还不齐，先补齐再判断更稳妥。"
    else:
        result.verdict, result.verdict_key = VERDICT_BUY, "buy"
        result.reasons = buy_reasons or ["需求、预算与数据三者一致，且没有发现风险信号。"]
        result.headline = "这笔购买在需求与预算上都说得通，可以按清单挑一款下手。"

    # ---------------- 置信度 ----------------
    score = sum([has_purpose, has_budget, freq_ok, facts_ok, band == "合理"])
    result.confidence = "高" if score >= 4 else ("中" if score >= 2 else "低")

    # ---------------- 其他满足方式（只取优先级最高的两条） ----------------
    for path in sorted(category.green, key=lambda p: p.priority)[:2]:
        result.alternatives.append({
            "做法": path.title,
            "说明": path.detail,
            "自查": path.check,
        })

    if category.essential_hint:
        trace.append({"规则": "该品类的必要性判断", "命中": True, "说明": category.essential_hint})
    trace.append({"规则": "价格是否被用于判定冲动消费", "命中": False,
                  "说明": "否。价格只参与「预算是否匹配」，不参与冲动判定。"})
    trace.append({"规则": "价格区间性质", "命中": False, "说明": PRICE_BAND_NOTE})
    result.trace = trace
    return result
