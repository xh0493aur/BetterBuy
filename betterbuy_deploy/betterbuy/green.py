"""绿色消费建议：在不劝退必要购买的前提下，给出可执行的省钱/减废路径。

三条硬约束：

1. **不虚构任何量化环境效益。** 不写「减少 X kg 碳排放」「省下 Y 元」这类数字，
   因为本项目没有可靠的生命周期数据来源。全部为定性建议。
2. **不为了环保而阻止必要购买。** 如果该品类属于必需品（例如课程指定的教材、
   已经无法完成作业的电脑），会明确写「这些是省钱路径，不是劝你别买」。
3. **不建议任何违反平台条款的做法。** 例如不会建议共享账号、破解、非官方渠道。

建议按「决策阶梯」组织，顺序固定：先问要不要买 → 再问能不能修 → 能不能升级 →
能不能买二手 → 最后才是买新。这个顺序本身就是最省钱、最减废的顺序。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import Category, get_category

__all__ = ["GreenTip", "GreenAdvice", "build_green", "LADDER", "FAMILY_LABEL"]

LADDER = ["能不能不买", "能不能修", "能不能升级/换配件", "能不能买二手/官翻", "最后才是买新的"]

FAMILY_LABEL = {
    "computer": "电脑设备",
    "peripheral": "电脑外设",
    "daily": "日常用品",
    "electronics": "电子产品",
    "study": "学习用品",
    "entertainment": "数字娱乐",
}

_FAMILY_TIPS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "computer": (
        ("先确认是不是「性能不够」还是「系统变脏」",
         "重装系统、清理启动项、换一块固态硬盘，常能让老机器恢复可用。"
         "这类操作成本低，且不需要买任何新硬件。",
         "开机到能用要多久？换过固态吗？"),
        ("能用外接解决的，不必换整机",
         "屏幕小、接口少、键盘手感差，都可以通过外接显示器/扩展坞/键盘解决，"
         "成本远低于换一台整机。",
         "你不满意的具体是哪一项？能不能单独补？"),
        ("升级内存与硬盘通常比换机划算",
         "多数笔记本支持加内存或换硬盘，这是延长寿命最直接的手段。",
         "你的机型内存是板载还是插槽？硬盘是 SATA 还是 M.2？"),
    ),
    "peripheral": (
        ("先确认是不是线材或接口的问题",
         "外设「不好用」很多时候是线材老化、接口松动或转接头不支持，"
         "换一根线或换个接口就能解决，不需要换整件。",
         "换一根线、换一个接口后还有问题吗？"),
        ("先换易损件再考虑换整件",
         "键帽、耳罩、脚贴、支架这类部件可以单独更换，成本远低于换新。",
         "坏的是外观、手感，还是功能本身？"),
        ("二手外设在成色好时风险较低",
         "外设技术迭代慢、结构简单，主要风险是磨损与坏点，可以当面验。",
         "能否现场验：通电试用、看坏点或异响？"),
        ("按需求选档，不必追参数顶配",
         "外设的性能上限往往超出实际使用场景，多花的钱可能感受不到。",
         "你最常用的功能是什么？它真的需要更高的参数吗？"),
    ),
    "daily": (
        ("先清点，再购买",
         "日常用品最容易重复购买。把已有的同类物品集中放一处，往往会发现根本不用买。",
         "同类物品你现在有几件？分别放在哪？"),
        ("先确认是否真的需要「再一个」",
         "消耗品用完确实要补，但耐用品「再来一个」通常是想换而不是需要换。",
         "上一个是怎么坏的，还是只是旧了？"),
        ("优先选可替换、可补充的形态",
         "能换芯、能补充装、能换配件的东西，长期总成本更低，浪费也更少。",
         "这东西用完是整件丢，还是只换其中一部分？"),
    ),
    "electronics": (
        ("电池是最常见的「假报废」原因",
         "手机、耳机、笔记本的续航下降多为电池老化，换电池可以继续用相当长时间。",
         "电池健康度多少？是否出现过意外关机？"),
        ("先换配件再换整机",
         "耳罩、耳塞套、线材、键帽这类易损件可以单独更换，不必换整机。",
         "变旧的是外观、手感，还是功能本身？"),
        ("买之前先确认可维修性",
         "同价位下，能换电池、有配件供应的型号长期更划算。",
         "这款的电池/屏幕是否可单独更换？官方维修点在哪？"),
    ),
    "study": (
        ("图书馆与二手是第一顺位",
         "教材、参考书的复用率极高，图书馆馆藏与高年级转让通常都能满足。",
         "图书馆是否有该版次？同专业有人愿意转让吗？"),
        ("按学期用量买，不按促销买",
         "大包装单价低，但用不完就是浪费。按实际消耗速度估算更准。",
         "这学期你预计会用掉多少？"),
        ("优先可重复使用的形态",
         "可擦写、可补充、可替换芯的产品，长期成本更低。",
         "这件东西是一次性的还是能反复用？"),
    ),
    "entertainment": (
        ("先看手上还没用完的",
         "已买但没玩完/没看完的内容是最直接的替代品，边际成本为零。",
         "你库里还有几个买了没动的？"),
        ("数字内容可以等折扣周期",
         "数字版不存在缺货，非急需时等折扣是纯粹的省钱。",
         "这款近半年出现过更低价格吗？"),
        ("先确认设备能跑得动",
         "买完发现配置不够是最典型的浪费，且往往无法退款。",
         "你的设备达到推荐配置了吗？"),
    ),
}


#: 某些同族通用建议在特定品类里已经被更具体的条目覆盖，命中就跳过，避免同一句话出现两次。
_FAMILY_SKIP: dict[tuple[str, str], tuple[str, ...]] = {
    ("electronics", "电池是最常见的「假报废」原因"): ("phone", "headphone"),
    ("electronics", "先换配件再换整机"): ("keyboard", "headphone", "mouse"),
    ("peripheral", "先换易损件再考虑换整件"): ("keyboard", "headphone", "mouse"),
    ("peripheral", "二手外设在成色好时风险较低"): ("monitor",),
    ("peripheral", "按需求选档，不必追参数顶配"): ("keyboard",),
    ("computer", "升级内存与硬盘通常比换机划算"): ("laptop",),
    ("computer", "先确认是不是「性能不够」还是「系统变脏」"): ("laptop",),
}


@dataclass
class GreenTip:
    stage: str          # 属于决策阶梯的第几级
    title: str
    detail: str
    check: str


@dataclass
class GreenAdvice:
    items: list[GreenTip] = field(default_factory=list)
    ladder: list[str] = field(default_factory=list)
    essential_note: str = ""
    family: str = ""
    family_label: str = ""
    disclaimers: list[str] = field(default_factory=list)


DISCLAIMERS = (
    "以下建议为定性判断，**不含任何碳排放、节约金额等量化估算**——"
    "本项目没有可靠的生命周期数据来源，因此不给数字。",
    "建议只针对「怎么买更省、更少浪费」，不评价任何具体品牌或商家的好坏。",
    "如果某项确实是必需品（例如课程指定教材、已无法完成作业的设备），"
    "请直接购买，跳到清单核对环节即可。",
)


def build_green(category_key: str, *, essential: bool = False) -> GreenAdvice:
    """按品类生成绿色消费建议。"""
    category: Category = get_category(category_key)
    advice = GreenAdvice(
        ladder=list(LADDER),
        family=category.family,
        family_label=FAMILY_LABEL.get(category.family, "通用"),
        disclaimers=list(DISCLAIMERS),
    )

    # 品类专属建议在前（更具体，更有用）
    for path in sorted(category.green, key=lambda p: p.priority):
        advice.items.append(GreenTip(
            stage="能不能不买" if path.priority == 1 else "能不能修 / 升级 / 买二手",
            title=path.title,
            detail=path.detail,
            check=path.check,
        ))

    # 同族通用建议在后（补充视角，去重）
    existing = {item.title for item in advice.items}
    for title, detail, check in _FAMILY_TIPS.get(category.family, ()):
        if title in existing:
            continue
        if category.key in _FAMILY_SKIP.get((category.family, title), ()):
            continue
        advice.items.append(GreenTip(
            stage="同族通用",
            title=title,
            detail=detail,
            check=check,
        ))

    if essential or category.essential_hint:
        advice.essential_note = (
            category.essential_hint
            or "如果这项确实是必需品，请直接购买，不必因为环保建议而拖延。"
        )
    return advice
