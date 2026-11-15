"""「智选」编排层：把一次分析需要的所有模块串起来，但不碰界面。

分成这一层的好处：

- **可离线测试。** ``collect_fn`` 与 ``discover_fn`` 都可以注入假实现，
  因此可以在不联网、不依赖密钥的情况下验证整条链路的行为。
- **界面只负责展示。** app.py 拿到 ``WiseResult`` 后按区块渲染，不再做判断逻辑，
  避免「界面上临时加一句 if 就改了结论」这种最容易出错的做法。
- **失败也返回结果。** 采集失败不是异常，而是 ``notes`` 里的一条记录 +
  ``products`` 为空，界面照样能给出检查清单和绿色建议——这正是公益场景需要的：
  没数据的时候也要有用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .advisor import Advice, rule_advice
from .catalog import get_category, key_of_name
from .green import GreenAdvice, build_green
from .intent import Intent, parse_intent, strip_urls
from .models import Product
from .ration import RationResult, analyze
from .recommend import Recommendation, recommend
from .risk import RiskReport, assess

__all__ = ["WiseResult", "extract_urls", "search_keyword", "run", "MAX_LINKS_PER_ANALYSIS"]

#: 一次分析最多处理几个链接。公益版强调少操作，3 个足够比较。
MAX_LINKS_PER_ANALYSIS = 3

#: 关键词搜索要几条。官方对分页的硬限制是 10–100，所以实际最少 10 条。
SEARCH_LIMIT = 10

_URL_RE = re.compile(r"https?://[^\s，。；、]+", re.I)


def extract_urls(text: str) -> list[str]:
    """从输入里抠出链接，去重且保持顺序。"""
    found = [m.rstrip(".,;:)]）") for m in _URL_RE.findall(text or "")]
    return [u for u in dict.fromkeys(found) if u]


@dataclass
class WiseResult:
    query: str = ""
    budget_cap: float = 0.0
    urls: list[str] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    intent: Intent = field(default_factory=Intent)
    category_key: str = "other"
    category_name: str = ""
    category_source: str = "自动识别"     # 自动识别 / 你手动选择
    ration: RationResult = field(default_factory=RationResult)
    green: GreenAdvice = field(default_factory=GreenAdvice)
    risk: RiskReport = field(default_factory=RiskReport)
    rec: Recommendation | None = None
    advice: Advice | None = None
    discovery: Any | None = None
    facts_ok: bool = False
    used_llm: bool = False
    #: 本次实际拿去搜商品的关键词（空字符串 = 没搜）。界面据此说明数据从哪来。
    searched_keyword: str = ""
    #: 搜索失败时的分类（no_credential / permission / quota / empty …），成功为空。
    search_kind: str = ""

    @property
    def priced(self) -> list[Product]:
        return [p for p in self.products if p.offer.price is not None]

    @property
    def has_products(self) -> bool:
        return bool(self.products)


def _facts_ok(products: list[Product]) -> bool:
    """有没有可用于比较的字段：至少一个商品同时有价格和（口碑或规格）。"""
    for product in products:
        if product.offer.price is not None and (product.rating.has_any or product.specs):
            return True
    return False


def _filter_by_budget(products: list[Product], budget_cap: float) -> tuple[list[Product], int]:
    """按预算上限粗筛搜索结果。

    只做「明显超预算」的排除（1.5 倍以上），不排序也不改价格；
    过滤后少于 2 件就放弃过滤，否则用户会看到「搜到了但一件都不显示」。
    """
    if not budget_cap or budget_cap <= 0:
        return products, 0
    ceiling = budget_cap * 1.5
    kept = [p for p in products if p.offer.price is None or p.offer.price <= ceiling]
    if len(kept) < 2:
        return products, 0
    return kept, len(products) - len(kept)


def search_keyword(intent: Intent, text: str) -> str:
    """从需求里提炼一个「能拿去搜商品」的关键词。

    优先级：命中的品类别名 → 品类名 → 输入本身（仅当它看起来就是个商品名）。

    这里刻意保守：像「我想花 800 元买人体工学椅，主要用来学习和打游戏」这种句子，
    整句丢给搜索接口只会搜出一堆无关商品，所以宁可退化成品类词。
    """
    if intent.matched_alias:
        return intent.matched_alias
    if intent.category_key and intent.category_key != "other":
        return intent.category_name
    clean = strip_urls(text).strip("，。！？；,.!?; ")
    # 只在「短、没有分句、没有数字/预算」时才把原话当商品名
    if 2 <= len(clean) <= 20 and not re.search(r"[，。；,;]", clean) and not re.search(r"\d", clean):
        return clean
    return ""


def run(
    query: str,
    *,
    category_key: str = "",
    budget_cap: float = 0.0,
    auto_discover: bool = True,
    max_links: int = MAX_LINKS_PER_ANALYSIS,
    use_cache: bool = True,
    collect_fn: Callable[..., Product] | None = None,
    discover_fn: Callable[..., Any] | None = None,
    search_fn: Callable[..., Any] | None = None,
) -> WiseResult:
    """执行一次完整分析。

    Args:
        query: 用户原始输入（可以同时包含链接与自然语言）。
        category_key: 用户手动指定的品类；为空则自动识别。
        budget_cap: 界面上的「预算上限」，会补进需求文本一起解析。
        collect_fn: 采集函数，默认用真实的 :func:`betterbuy.adapters.collect`。
        discover_fn: 竞品发现函数，默认用 :func:`betterbuy.discovery.discover`。
        search_fn: 关键词搜索函数（默认拼多多官方接口）。**只在用户没贴链接时调用**——
            普通用户只写一个商品名，也应该看到真实商品，而不是只有建议。
            不传或返回失败都不影响后续规则分析。
    """
    result = WiseResult(query=query or "", budget_cap=budget_cap)
    raw = (query or "").strip()
    result.urls = extract_urls(raw)[:max_links]

    # 把界面上的预算上限补进文本，让需求解析能吃到
    text = strip_urls(raw)
    if budget_cap and budget_cap > 0 and not re.search(r"\d", text):
        text = (text + f"，预算不超过 {budget_cap:g} 元").strip("，")
    elif budget_cap and budget_cap > 0 and "预算" not in text:
        text = (text + f"，预算不超过 {budget_cap:g} 元").strip("，")

    intent = parse_intent(text)
    result.intent = intent

    # 品类：用户手选优先，其次自动识别
    if category_key:
        result.category_key = category_key
        result.category_source = "你手动选择"
    else:
        result.category_key = intent.category_key
        result.category_source = "自动识别"
    result.category_name = get_category(result.category_key).name

    # ---------------- 采集 ----------------
    if result.urls:
        if collect_fn is None:
            from .adapters import collect as collect_fn  # 延迟导入，避免测试被网络模块拖慢
        for url in result.urls:
            try:
                product = collect_fn(url, use_cache=use_cache)
                product.origin = "user"
                result.products.append(product)
            except Exception as exc:  # 采集失败是常态，记下来继续
                kind = getattr(exc, "kind", type(exc).__name__)
                message = getattr(exc, "message", str(exc))
                result.notes.append(f"❌ `{url[:60]}` 采集失败（{kind}）：{message[:160]}")

    # ---------------- 关键词搜索（没贴链接时也能看到真实商品） ----------------
    if not result.urls and search_fn is not None:
        keyword = search_keyword(intent, text)
        if keyword:
            result.searched_keyword = keyword
            try:
                found = search_fn(keyword, limit=SEARCH_LIMIT)
            except Exception as exc:  # 搜索失败不该让整页塌掉
                result.notes.append(f"⚠️ 商品搜索未能完成：{type(exc).__name__} {str(exc)[:120]}")
            else:
                ok = bool(getattr(found, "ok", False))
                harvested = list(getattr(found, "products", []) or [])
                if ok and harvested:
                    kept, dropped = _filter_by_budget(harvested, budget_cap)
                    result.products.extend(kept)
                    platform = harvested[0].platform or "电商平台"
                    result.notes.append(
                        f"ℹ️ 按「{keyword}」在{platform}官方开放接口搜到 {len(harvested)} 件真实商品"
                        + (f"，其中 {dropped} 件明显超出预算上限，已略去" if dropped else "")
                        + "（采集时间见下方商品卡片）。"
                    )
                elif ok:
                    result.search_kind = "empty"
                    result.notes.append(
                        f"ℹ️ 按「{keyword}」没有搜到商品，下面只给判断与检查清单，不编造价格。"
                    )
                else:
                    kind = getattr(found, "kind", "") or "api"
                    result.search_kind = kind
                    message = str(getattr(found, "message", found))[:160]
                    if kind == "no_credential":
                        result.notes.append(
                            "ℹ️ 这次没有取到商品数据（服务端电商接口尚未就绪），"
                            "所以只给需求判断与检查清单，不用估算值顶替。"
                        )
                    else:
                        result.notes.append(f"⚠️ 商品搜索未成功：{message}")

    # ---------------- 竞品发现 ----------------
    if result.urls and result.products and auto_discover:
        first = result.products[0]
        if first.platform == "Steam":
            if discover_fn is None:
                from .discovery import discover as discover_fn
            try:
                discovery = discover_fn(
                    first,
                    min_price=None,
                    max_price=budget_cap if budget_cap and budget_cap > 0 else None,
                    limit=3,
                )
                result.discovery = discovery
                if getattr(discovery, "ok", False):
                    if collect_fn is None:
                        from .adapters import collect as collect_fn
                    for candidate in discovery.candidates:
                        if len(result.products) >= max_links + 3:
                            break
                        # Candidate 只有 appid，没有 product_id；这里要自己拼成统一的 id，
                        # 否则去重会抛 AttributeError，整段竞品搜索被外层 except 吞掉。
                        candidate_id = f"steam:{candidate.appid}"
                        if any(p.product_id == candidate_id for p in result.products):
                            continue
                        try:
                            found = collect_fn(candidate.url, use_cache=use_cache)
                            found.origin = "discovered"
                            result.products.append(found)
                        except Exception as exc:
                            message = getattr(exc, "message", str(exc))
                            result.notes.append(f"⚠️ 竞品「{candidate.name}」读取失败：{message[:120]}")
            except Exception as exc:
                result.notes.append(f"⚠️ 竞品搜索未能完成：{type(exc).__name__} {str(exc)[:120]}")
        else:
            result.notes.append(
                f"ℹ️ 自动竞品搜索目前只覆盖 Steam；{first.platform} 的商品不做自动找同类。"
            )

    # ---------------- 分析 ----------------
    result.facts_ok = _facts_ok(result.products)
    result.rec = recommend(result.products) if result.products else None
    result.advice = (
        rule_advice(result.products, result.rec, text) if result.products and result.rec else None
    )
    result.ration = analyze(
        text,
        product_count=len(result.products),
        priced_count=len(result.priced),
        facts_ok=result.facts_ok,
    )
    # 手动指定品类时，用指定品类重算绿色建议与风险规则
    essential = bool(intent.has_problem)
    result.green = build_green(result.category_key, essential=essential)
    result.risk = assess(result.products, category_key=result.category_key)

    # 品类被手动改过时，把判定依据也说清楚，避免用户以为系统自己认错了
    if category_key and category_key != intent.category_key:
        result.notes.append(
            f"ℹ️ 品类按你的选择用「{result.category_name}」，"
            f"自动识别给的是「{get_category(intent.category_key).name}」。"
        )
    return result
