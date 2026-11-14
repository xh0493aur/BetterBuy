"""需求理解：把一句自然语言拆成结构化的购买意图。

**这是规则系统，不是大模型。** 全部靠关键词与正则，没有任何模型推理。
界面上凡是展示本模块结果的地方，都标注为「规则解析」而不是「AI 理解」，
避免把条件判断伪装成模型能力。

能做到的：识别品类、预算、用途、使用频率、是否已有同类物品、是否考虑过维修/二手。
做不到的：真正理解语义。比如「我不想要太吵的」会被识别为提到噪音，但不一定
能推断出「安静是硬需求」。所以解析结果一律展示给用户确认，而不是直接当事实用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .advisor import parse_budget
from .catalog import find_purchase_target, get_category

__all__ = [
    "Signal", "Intent", "parse_intent", "PURPOSE_TAGS",
    "FREQUENCY_HIGH", "FREQUENCY_MEDIUM", "FREQUENCY_LOW", "FREQUENCY_UNKNOWN",
    "strip_urls",
]

_URL_RE = re.compile(r"https?://\S+", re.I)


def strip_urls(text: str) -> str:
    """去掉链接，只留自然语言。链接交给适配器处理，需求解析不该被 URL 干扰。"""
    return _URL_RE.sub(" ", text or "").strip()

FREQUENCY_HIGH = "高频"
FREQUENCY_MEDIUM = "中频"
FREQUENCY_LOW = "低频"
FREQUENCY_UNKNOWN = "未说明"

PURPOSE_TAGS: dict[str, tuple[str, ...]] = {
    "学习": ("学习", "上课", "网课", "作业", "论文", "写论文", "复习", "考研", "考试",
             "自习", "记笔记", "看书", "刷题"),
    "游戏": ("游戏", "打游戏", "开黑", "联机", "单机", "电竞", "steam"),
    "办公": ("办公", "写代码", "编程", "文档", "表格", "开会", "实习", "工作"),
    "设计/剪辑": ("设计", "修图", "剪辑", "剪视频", "建模", "画图", "渲染", "ps", "pr"),
    "通勤/携带": ("通勤", "携带", "随身", "背着", "带去", "宿舍到教室", "地铁", "公交"),
    "宿舍生活": ("宿舍", "寝室", "床上", "桌面", "房间"),
    "运动/健康": ("健身", "运动", "跑步", "久坐", "腰疼", "颈椎", "护眼", "视力"),
    "影音娱乐": ("看剧", "追剧", "听歌", "看电影", "刷视频", "音乐"),
}

_EMOTION_WORDS = ("好想要", "想要", "忍不住", "心动", "种草", "跟风", "别人都有",
                  "同学都有", "颜值", "好看", "一时冲动", "手滑", "剁手", "限量")
_PROMO_WORDS = ("限时", "秒杀", "最后一天", "仅剩", "再不买", "抢购", "预售",
                "前100名", "先到先得", "倒计时", "今日特价", "清仓")
_URGENT_WORDS = ("急用", "明天就要", "这周就要", "马上要用", "来不及了", "急需")
_LOW_FREQ_WORDS = ("偶尔", "很少", "不太会", "可能用得上", "说不定", "应该会",
                   "一周一次", "一个月一次", "假期才")
_HIGH_FREQ_WORDS = ("每天", "天天", "经常", "一直用", "全天", "每节课", "高频")
_MEDIUM_FREQ_WORDS = ("每周", "一周", "周末", "隔几天", "两三天")
_OWNED_WORDS = ("已有", "已经有", "我的", "我现在用", "手上", "原来那台", "旧的那台",
                "旧的", "现有的", "家里有", "室友有", "借得到", "实验室有")
#: 描述「现有物品具体哪里不行」的词。出现这些词时，说明用户不是纯粹被种草，
#: 而是在解决一个真实问题——这时不应该因为「没写用途」就劝他暂缓。
_PROBLEM_WORDS = ("坏", "不灵", "失灵", "卡顿", "卡死", "很卡", "变卡", "掉皮", "掉漆",
                  "老化", "不能用", "开不了机", "掉电", "一天要充", "发热", "发烫",
                  "变慢", "很慢", "太慢", "破了", "裂了", "漏", "异响", "闪屏",
                  "花屏", "断连", "续航不行", "撑不住", "不够用", "太小", "带不动",
                  "跑不动", "装不下", "满了", "没反应", "接触不良", "按键不")
_SWAP_WORDS = ("维修", "修理", "修一下", "换电池", "换屏", "换硬盘", "加内存",
               "升级", "二手", "继续用", "翻新", "配件", "换个轴", "清灰")
_RESEARCH_WORDS = ("比价", "对比", "评测", "测评", "看看评价", "口碑", "等降价",
                   "等打折", "再想想", "问问", "咨询", "查一下")
_COMPAT_WORDS = ("兼容", "接口", "支持吗", "能不能接", "适配", "配置", "参数",
                 "型号", "规格", "尺寸", "功率")


@dataclass
class Signal:
    """一条风险或理性信号。``evidence`` 必须是用户原话里的片段，不能凭空生成。"""

    key: str
    label: str
    evidence: str
    detail: str
    weight: int = 1        # 1=提示 2=需要留意 3=建议暂缓


@dataclass
class Intent:
    raw: str = ""
    category_key: str = "other"
    category_name: str = "其他商品"
    matched_alias: str = ""
    category_basis: str = ""
    budget_low: float | None = None
    budget_high: float | None = None
    budget_note: str = ""
    purposes: list[str] = field(default_factory=list)
    frequency: str = FREQUENCY_UNKNOWN
    frequency_evidence: str = ""
    urgent: bool = False
    owned_mentioned: list[str] = field(default_factory=list)
    owned_devices: list[str] = field(default_factory=list)   # 已有设备的品类 key
    problem_described: list[str] = field(default_factory=list)  # 现有物品的具体问题
    considered_alternative: bool = False
    researched: bool = False
    mentions_compat: bool = False
    signals: list[Signal] = field(default_factory=list)
    rational: list[Signal] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    mode: str = "规则解析"

    @property
    def has_purpose(self) -> bool:
        return bool(self.purposes)

    @property
    def has_budget(self) -> bool:
        return self.budget_low is not None or self.budget_high is not None

    @property
    def has_problem(self) -> bool:
        return bool(self.problem_described)

    @property
    def owns_same_category(self) -> bool:
        """已有物品是否和要买的是同一品类（这才是「可能重复购买」）。"""
        return self.category_key in self.owned_devices

    @property
    def risk_score(self) -> int:
        return sum(s.weight for s in self.signals)

    @property
    def rational_score(self) -> int:
        return len(self.rational)

    def evidence_line(self) -> str:
        """给界面展示的一行「我是怎么理解你的话的」。"""
        bits = [f"品类：{self.category_name}"]
        if self.budget_high is not None and self.budget_low is None:
            bits.append(f"预算：≤ {self.budget_high:g} 元")
        elif self.budget_low is not None and self.budget_high is None:
            bits.append(f"预算：≥ {self.budget_low:g} 元")
        elif self.has_budget:
            bits.append(f"预算：{self.budget_low:g}–{self.budget_high:g} 元")
        else:
            bits.append("预算：未提及")
        bits.append("用途：" + ("、".join(self.purposes) if self.purposes else "未提及"))
        bits.append(f"使用频率：{self.frequency}")
        if self.owned_devices:
            bits.append("已有设备：" + "、".join(get_category(k).name for k in self.owned_devices))
        return " · ".join(bits)


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    body = (text or "").lower()
    return [w for w in words if w.lower() in body]


def _first(text: str, words: tuple[str, ...]) -> str:
    hits = _hits(text, words)
    return hits[0] if hits else ""


def _budget_band_check(intent: Intent, text: str) -> None:
    """预算与品类经验区间的关系。注意：这只影响「结论档位」，不作为冲动判定。"""
    category = get_category(intent.category_key)
    if category.price_high <= 0 or not intent.has_budget:
        return
    low = intent.budget_low
    high = intent.budget_high
    # 用上限判断（用户说「预算 800」时上限就是 800）
    cap = high if high is not None else low
    if cap is None:
        return
    if cap < category.price_low * 0.6:
        intent.signals.append(Signal(
            key="budget_tight",
            label="预算明显低于该品类常见价位",
            evidence=f"你给的预算 {cap:g} 元",
            detail=(
                f"{category.name}的常见价位区间约为 {category.price_low:g}–{category.price_high:g} 元"
                f"（经验参考，非实时价格）。在这个预算下可能只能买到低配或二手，"
                "需要重点确认关键参数是否缩水。这不是冲动消费的判断，只是提醒你先看清配置。"
            ),
            weight=2,
        ))
    elif cap > category.price_high * 2.2:
        intent.signals.append(Signal(
            key="budget_loose",
            label="预算明显高于该品类常见价位",
            evidence=f"你给的预算 {cap:g} 元",
            detail=(
                f"{category.name}的常见价位区间约为 {category.price_low:g}–{category.price_high:g} 元"
                "（经验参考）。更高的预算不一定带来你能用到的提升，建议先确认多花的钱换来了什么。"
            ),
            weight=1,
        ))


def parse_intent(text: str) -> Intent:
    """解析一句购买需求。空输入返回一个带 missing 的空 Intent，绝不猜。"""
    intent = Intent(raw=text or "")
    body = strip_urls(text or "")
    if not body:
        intent.missing = ["需求描述", "品类", "预算", "用途"]
        return intent

    key, alias, owned_devices, basis = find_purchase_target(body)
    intent.category_key = key
    intent.category_name = get_category(key).name
    intent.matched_alias = alias
    intent.category_basis = basis
    intent.owned_devices = owned_devices

    low, high, note = parse_budget(body)
    intent.budget_low, intent.budget_high, intent.budget_note = low, high, note

    for tag, words in PURPOSE_TAGS.items():
        if _hits(body, words):
            intent.purposes.append(tag)

    if _hits(body, _HIGH_FREQ_WORDS):
        intent.frequency = FREQUENCY_HIGH
        intent.frequency_evidence = _first(body, _HIGH_FREQ_WORDS)
    elif _hits(body, _MEDIUM_FREQ_WORDS):
        intent.frequency = FREQUENCY_MEDIUM
        intent.frequency_evidence = _first(body, _MEDIUM_FREQ_WORDS)
    elif _hits(body, _LOW_FREQ_WORDS):
        intent.frequency = FREQUENCY_LOW
        intent.frequency_evidence = _first(body, _LOW_FREQ_WORDS)

    intent.urgent = bool(_hits(body, _URGENT_WORDS))
    intent.owned_mentioned = _hits(body, _OWNED_WORDS)
    intent.problem_described = _hits(body, _PROBLEM_WORDS)
    intent.considered_alternative = bool(_hits(body, _SWAP_WORDS))
    intent.researched = bool(_hits(body, _RESEARCH_WORDS))
    intent.mentions_compat = bool(_hits(body, _COMPAT_WORDS))

    # ---------------- 理性信号（先算，避免只盯着风险） ----------------
    if intent.purposes:
        intent.rational.append(Signal(
            "has_purpose", "说明了用途",
            "、".join(intent.purposes),
            "你明确说了拿它做什么，这是判断「值不值得买」最有用的一条信息。",
        ))
    if intent.has_budget:
        intent.rational.append(Signal(
            "has_budget", "给了预算约束",
            intent.budget_note or f"{low or ''}–{high or ''}",
            "有预算上限的购买更不容易被促销带偏。",
        ))
    if intent.frequency in (FREQUENCY_HIGH, FREQUENCY_MEDIUM):
        intent.rational.append(Signal(
            "frequent_use", "使用频率不低",
            intent.frequency_evidence or intent.frequency,
            "用得越多，单次使用成本越低，买的理由越充分。",
        ))
    if intent.has_problem:
        intent.rational.append(Signal(
            "problem_stated", "说清了现在哪里不行",
            "、".join(intent.problem_described[:3]),
            "你描述的是具体问题，不是单纯的购买欲，这类需求通常更值得认真对待。",
        ))
    if intent.considered_alternative:
        intent.rational.append(Signal(
            "considered_alternative", "已经考虑过维修/二手/升级",
            "、".join(_hits(body, _SWAP_WORDS)[:3]),
            "先想替代方案再决定购买，是最省钱也最环保的顺序。",
        ))
    if intent.researched:
        intent.rational.append(Signal(
            "researched", "提到比价或看评测",
            "、".join(_hits(body, _RESEARCH_WORDS)[:3]),
            "愿意先调研再下单，踩坑概率会明显降低。",
        ))
    if intent.mentions_compat:
        intent.rational.append(Signal(
            "mentions_compat", "关注了参数或兼容性",
            "、".join(_hits(body, _COMPAT_WORDS)[:3]),
            "提前确认兼容性，是避免「买回来用不了」的关键一步。",
        ))

    # ---------------- 风险信号 ----------------
    if not intent.purposes:
        intent.signals.append(Signal(
            "no_purpose", "没有说明用途",
            "（需求里没有提到拿它做什么）",
            "只说了想买什么、没说用来做什么，很难判断这个钱花得值不值。补齐用途后结论会准很多。",
            weight=2,
        ))
    if not intent.has_budget:
        intent.signals.append(Signal(
            "no_budget", "没有给出预算",
            "（需求里没有出现金额）",
            "没有预算约束时，容易被「加一点钱就能更好」带着往上加。",
            weight=1,
        ))

    promo = _hits(body, _PROMO_WORDS)
    if promo:
        intent.signals.append(Signal(
            "promo_pressure", "提到了促销紧迫感话术",
            "、".join(promo),
            "限时、仅剩这类话术会压缩你的决策时间，是典型的冲动触发点。建议先把「要不要买」和「现在买」分开判断。",
            weight=2,
        ))

    emotion = _hits(body, _EMOTION_WORDS)
    if emotion:
        intent.signals.append(Signal(
            "emotion_driven", "出现了情绪化表述",
            "、".join(emotion),
            "想要、种草这类表述说明动因偏感受。不是说不能买，而是建议先补一条「具体解决什么」。",
            weight=2,
        ))

    if intent.frequency == FREQUENCY_LOW:
        intent.signals.append(Signal(
            "low_frequency", "自己预期的使用频率很低",
            intent.frequency_evidence,
            "用得少的东西，单价再低也可能是浪费。可以按「每次使用的成本」重新算一遍。",
            weight=2,
        ))

    if intent.owns_same_category and not intent.considered_alternative and not intent.has_problem:
        intent.signals.append(Signal(
            "owned_unexplained", "已有同类物品，但没说明为什么还需要新的",
            "、".join(intent.owned_mentioned[:3]),
            "已有物品不一定够用，也可能确实该换。请补一句「现有的哪里不够」，否则很容易买了功能重叠的东西。",
            weight=2,
        ))

    _budget_band_check(intent, body)

    # ---------------- 缺失项 ----------------
    if not intent.purposes:
        intent.missing.append("购买用途")
    if not intent.has_budget:
        intent.missing.append("预算")
    if intent.frequency == FREQUENCY_UNKNOWN:
        intent.missing.append("使用频率")
    if intent.category_key == "other":
        intent.missing.append("商品品类（未识别，可在下拉框里手动选）")

    return intent
