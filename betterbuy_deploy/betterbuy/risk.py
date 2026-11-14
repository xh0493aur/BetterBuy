"""购买风险提示：只报「数据能证明的」，不确定的一律标「待核实」。

设计上的关键取舍：

- **不判定质量好坏。** 本项目没有质检数据、没有返修率，任何「这东西质量差」的结论
  都是编的。因此风险项只说「有什么需要你确认」，不说「这商品有问题」。
- **不确定 = 待核实。** 拿不到用户设备信息时，输出的是「需要确认的接口清单」，
  而不是「不兼容」。
- **每条风险都要有依据。** ``evidence`` 必须来自真实字段（价格、评价数、采集时间、
  接口名），不允许写「据经验」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .catalog import get_category
from .models import Product
from .normalize import norm_key, similarity

__all__ = ["RiskItem", "RiskReport", "assess", "LEVEL_HIGH", "LEVEL_MEDIUM",
           "LEVEL_LOW", "LEVEL_TODO", "LEVEL_OK"]

LEVEL_HIGH = "高"
LEVEL_MEDIUM = "中"
LEVEL_LOW = "低"
LEVEL_TODO = "待核实"
LEVEL_OK = "已核实"

#: 价格超过这个小时数就提示「可能已变动」
STALE_HOURS_WARN = 6
#: 超过这个小时数直接判定「已过期」
STALE_HOURS_EXPIRE = 48
#: 评价数低于这个值视为「样本不足」
MIN_REVIEWS_USEFUL = 50


@dataclass
class RiskItem:
    kind: str
    label: str
    level: str
    detail: str
    evidence: str
    action: str
    product: str = ""


@dataclass
class RiskReport:
    items: list[RiskItem] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.level] = out.get(item.level, 0) + 1
        return out

    @property
    def has_high(self) -> bool:
        return any(item.level == LEVEL_HIGH for item in self.items)

    @property
    def todo_count(self) -> int:
        return sum(1 for item in self.items if item.level == LEVEL_TODO)

    def by_level(self, *levels: str) -> list[RiskItem]:
        return [i for i in self.items if i.level in levels]


def _age_hours(fetched_at: str) -> float | None:
    try:
        then = datetime.strptime((fetched_at or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return (datetime.now() - then).total_seconds() / 3600


def _check_price_staleness(products: list[Product], report: RiskReport) -> None:
    worst: tuple[float, Product] | None = None
    unknown: list[Product] = []
    for product in products:
        age = _age_hours(product.fetched_at)
        if age is None:
            unknown.append(product)
            continue
        if worst is None or age > worst[0]:
            worst = (age, product)

    if worst and worst[0] >= STALE_HOURS_EXPIRE:
        age, product = worst
        report.items.append(RiskItem(
            kind="price_stale",
            label="价格信息已过期",
            level=LEVEL_HIGH,
            detail=f"最近一次采集在 {age:.0f} 小时前，价格很可能已经变化。"
                   f"促销结束后价格可能上浮，据此做预算可能失准。",
            evidence=f"「{product.name}」采集时间 {product.fetched_at}",
            action="打开商品页重新确认当前价格，或点「重新分析」刷新数据。",
            product=product.name,
        ))
    elif worst and worst[0] >= STALE_HOURS_WARN:
        age, product = worst
        report.items.append(RiskItem(
            kind="price_aged",
            label="价格可能已变动",
            level=LEVEL_MEDIUM,
            detail=f"最近一次采集在 {age:.0f} 小时前。短期内价格波动通常不大，但仍建议下单前核对。",
            evidence=f"「{product.name}」采集时间 {product.fetched_at}",
            action="下单前在商品页再确认一次价格。",
            product=product.name,
        ))
    if unknown:
        report.items.append(RiskItem(
            kind="price_time_unknown",
            label="无法确认价格采集时间",
            level=LEVEL_TODO,
            detail=f"有 {len(unknown)} 个商品缺少可靠的采集时间戳，无法判断价格新鲜度。",
            evidence="采集时间字段为空或格式异常",
            action="以商品页当前显示为准。",
        ))


def _check_reviews(products: list[Product], report: RiskReport) -> None:
    for product in products:
        count = product.rating.review_count
        rate = product.rating.positive_rate
        if count is None and rate is None:
            report.items.append(RiskItem(
                kind="no_review",
                label="缺乏有效评价数据",
                level=LEVEL_TODO,
                detail="该商品没有任何可用的口碑字段（既无好评率也无评价数），"
                       "因此无法判断真实使用体验。这不代表商品不好，只代表数据不足。",
                evidence=f"「{product.name}」好评率与评价数均为空",
                action="到商品页或第三方评测看看真实用户反馈，重点看差评里重复出现的问题。",
                product=product.name,
            ))
        elif count is not None and count < MIN_REVIEWS_USEFUL:
            report.items.append(RiskItem(
                kind="few_reviews",
                label="评价样本偏少",
                level=LEVEL_TODO,
                detail=f"只有 {count} 条评价，低于 {MIN_REVIEWS_USEFUL} 条的参考线。"
                       "样本太小时好评率波动很大，不能当作稳定口碑。",
                evidence=f"「{product.name}」评价数 {count}",
                action="多看几条具体评价内容，判断好评是否集中在「发货快」这类与商品无关的点上。",
                product=product.name,
            ))


def _check_sources(products: list[Product], report: RiskReport) -> None:
    weak = [p for p in products if not p.sources or "price" not in p.field_sources]
    if not weak:
        report.items.append(RiskItem(
            kind="source_ok",
            label="数据来源可追溯",
            level=LEVEL_OK,
            detail="每个商品的价格与关键字段都记录了来源接口、地址与采集时间。",
            evidence="来源记录完整",
            action="可在「数据来源」区块逐字段查看。",
        ))
        return
    names = "、".join(p.name or p.url for p in weak[:3])
    report.items.append(RiskItem(
        kind="source_unclear",
        label="商品数据来源不明确",
        level=LEVEL_MEDIUM,
        detail=f"{len(weak)} 个商品的关键字段缺少来源记录，无法核对数据是从哪来的。"
               "来源不明的价格不应作为决策依据。",
        evidence=f"缺少来源记录：{names}",
        action="优先采信记录完整的商品；对来源不明的条目，直接到商品页核对。",
        product=names,
    ))


def _check_model_consistency(products: list[Product], report: RiskReport) -> None:
    """型号/配置是否一致——这是「买错」最常见的两个原因。"""
    real = [p for p in products if p.name]
    if not real:
        report.items.append(RiskItem(
            kind="model_unknown",
            label="商品型号待核实",
            level=LEVEL_TODO,
            detail="本次没有识别出任何商品名称与型号，无法核对是否为同一款。",
            evidence="商品名称为空",
            action="粘贴具体商品页链接，或手动确认商品全称（含后缀）。",
        ))
        return

    # 1) 同款判定：标题很像但型号写法不同 → 很可能是不同配置
    pairs: list[tuple[Product, Product, float]] = []
    for i in range(len(real)):
        for j in range(i + 1, len(real)):
            sim = similarity(real[i].name, real[j].name)
            if sim >= 0.6:
                pairs.append((real[i], real[j], sim))

    conflicts = [
        (a, b, s) for a, b, s in pairs
        if (a.model or "").strip().lower() != (b.model or "").strip().lower()
    ]
    if conflicts:
        a, b, _ = conflicts[0]
        report.items.append(RiskItem(
            kind="model_mismatch",
            label="疑似同款但型号不一致",
            level=LEVEL_HIGH,
            detail=(
                f"「{a.name}」与「{b.name}」名称高度相似，但型号写法不同"
                f"（{a.model or '未提供'} vs {b.model or '未提供'}）。"
                "差一个字母往往就是不同代次或不同配置，价格没有可比性。"
            ),
            evidence=f"标题相似度 {_pct(conflicts[0][2])}，型号不同",
            action="以商品页的完整型号（含后缀、容量、版本）为准逐个核对，不要只看主标题。",
        ))

    # 2) 型号缺失
    no_model = [p for p in real if not (p.model or "").strip()]
    if no_model:
        report.items.append(RiskItem(
            kind="model_missing",
            label="商品型号未提供",
            level=LEVEL_TODO,
            detail=f"{len(no_model)} 个商品没有可识别的型号/版本信息，"
                   "无法确认它与你想要的配置是否一致。",
            evidence="型号字段为空：" + "、".join(p.name[:16] for p in no_model[:3]),
            action="到商品页确认完整型号，特别留意「标准版/增强版/2024 款」这类后缀。",
        ))

    # 3) 规格值冲突
    values: dict[str, dict[str, list[str]]] = {}
    for product in real:
        for key, value in (product.specs or {}).items():
            text = str(value).strip()
            if not text or len(text) > 40:
                continue
            values.setdefault(key, {}).setdefault(text, []).append(product.name)
    for key, buckets in values.items():
        if len(buckets) < 2:
            continue
        owners = sum(len(v) for v in buckets.values())
        if owners < 2:
            continue
        sample = "；".join(f"{k}（{v[0][:14]}）" for k, v in list(buckets.items())[:3])
        report.items.append(RiskItem(
            kind="spec_diff",
            label="规格/配置不一致",
            level=LEVEL_MEDIUM,
            detail=f"「{key}」在不同商品上的取值不同，说明它们不是同一配置。"
                   "不同配置的价格不能直接相减比较。",
            evidence=sample,
            action="先确认你要的是哪个配置，再在同一配置内比价。",
        ))
        if len(report.items) > 12:
            break


def _check_compat(products: list[Product], category_key: str, report: RiskReport) -> None:
    """兼容性：拿不到用户设备信息时，只给「必须确认的清单」，不判定不兼容。"""
    category = get_category(category_key)
    if not category.compat:
        report.checked.append("该品类没有已收录的兼容性规则，未做自动检查。")
        return

    fatal = [r for r in category.compat if r.fatal_if_missing]
    normal = [r for r in category.compat if not r.fatal_if_missing]
    for rule in fatal:
        report.items.append(RiskItem(
            kind="compat_todo",
            label="设备兼容性待确认（不满足会直接影响使用）",
            level=LEVEL_TODO,
            detail=f"{rule.need}。{rule.why}。本项目拿不到你的设备型号，"
                   "因此只能提醒你确认，不能替你判定是否兼容。",
            evidence="需要你提供设备信息才能判定",
            action="在设备说明书或系统设置里查一下，再对照商品页的接口/规格说明。",
        ))
    if normal:
        report.items.append(RiskItem(
            kind="compat_soft",
            label="还有需要确认的兼容性项",
            level=LEVEL_LOW,
            detail="；".join(f"{r.need}（{r.why}）" for r in normal),
            evidence=f"共 {len(normal)} 项",
            action="逐条对照商品页参数，拿不准就优先选支持退换的渠道。",
        ))
    report.checked.append(f"已检查 {len(category.compat)} 条该品类的兼容性规则。")


def _check_after_sales(products: list[Product], report: RiskReport) -> None:
    platforms = {p.platform for p in products if p.platform}
    if not platforms:
        report.items.append(RiskItem(
            kind="after_sales",
            label="售后与维修条件待核实",
            level=LEVEL_TODO,
            detail="本次没有可确认的平台信息，无法给出售后判断。",
            evidence="平台字段为空",
            action="下单前确认退换货期限、保修范围与维修网点。",
        ))
        return

    if platforms == {"Steam"}:
        report.items.append(RiskItem(
            kind="after_sales",
            label="退款条件需按下单时的官方条款确认",
            level=LEVEL_TODO,
            detail="Steam 的退款政策通常以「购买时长 + 游玩时长」为条件，"
                   "具体门槛以官网当前条款为准。本项目不替你判定是否可退。",
            evidence="平台：Steam",
            action="购买前在商店页底部查看退款政策原文。",
        ))
    elif platforms & {"京东", "淘宝", "天猫", "拼多多"}:
        report.items.append(RiskItem(
            kind="after_sales",
            label="售后与维修条件待核实",
            level=LEVEL_TODO,
            detail="电商平台的保修与退换由商家与平台规则共同决定，"
                   "推广接口不返回这些信息，因此无法自动核实。",
            evidence=f"平台：{'、'.join(sorted(platforms))}",
            action="下单前确认：是否支持七天无理由、保修几年、是否全国联保、维修点在哪。",
        ))
    else:
        report.items.append(RiskItem(
            kind="after_sales",
            label="售后与维修条件待核实",
            level=LEVEL_TODO,
            detail=f"平台「{'、'.join(sorted(platforms))}」的售后条款未被本项目收录。",
            evidence="未收录该平台的售后规则",
            action="到平台帮助中心查看退换货与保修政策。",
        ))


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def assess(products: list[Product], *, category_key: str = "other") -> RiskReport:
    """对一批商品做七类风险检查。没有商品时也会给出「数据不足」的结论。"""
    report = RiskReport()
    report.checked = [
        "型号与配置是否一致",
        "规格/容量是否相同",
        "与你的设备是否兼容",
        "评价数据是否有效",
        "价格是否过期",
        "数据来源是否可追溯",
        "售后与维修条件",
    ]

    if not products:
        report.items.append(RiskItem(
            kind="no_data",
            label="没有商品数据，无法核对这件具体商品",
            level=LEVEL_TODO,
            detail="本次没有采集到任何商品，因此型号、配置、评价、价格、来源这几类"
                   "都无法针对具体商品核对。但兼容性与售后这两类只取决于品类，"
                   "下面仍然给出你需要确认的内容。",
            evidence="商品数量 0",
            action="粘贴商品链接，或按下面的清单自行核对。",
        ))
        _check_compat(products, category_key, report)
        _check_after_sales(products, report)
        report.note = ("没有商品数据时不输出「无风险」结论，只输出针对该品类的确认清单。"
                       f"共 {report.todo_count} 项待核实。")
        return report

    _check_model_consistency(products, report)
    _check_reviews(products, report)
    _check_price_staleness(products, report)
    _check_sources(products, report)
    _check_compat(products, category_key, report)
    _check_after_sales(products, report)

    normalised = {norm_key(p.name) for p in products if p.name}
    if len(normalised) == 1 and len(products) > 1:
        report.items.append(RiskItem(
            kind="same_name",
            label="多个链接指向同一款商品",
            level=LEVEL_LOW,
            detail="识别出的商品名称完全相同，可能是重复粘贴，也可能确实同款不同渠道。",
            evidence=f"{len(products)} 个商品，去重后 {len(normalised)} 个名称",
            action="确认是否想比较不同渠道的价格；否则删掉重复链接即可。",
        ))

    high = [i for i in report.items if i.level == LEVEL_HIGH]
    todo = [i for i in report.items if i.level == LEVEL_TODO]
    if high:
        report.note = f"发现 {len(high)} 项需要优先处理的高风险项，另有 {len(todo)} 项待核实。"
    elif todo:
        report.note = f"没有发现确定性风险，但有 {len(todo)} 项需要你确认后才能判断。"
    else:
        report.note = "七类检查里没有发现需要提示的问题（这不代表商品没问题，只代表数据层面没发现异常）。"
    return report
