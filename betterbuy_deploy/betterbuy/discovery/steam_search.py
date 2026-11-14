"""Steam 商店搜索页竞品发现。

访问 ``https://store.steampowered.com/search/`` 的公开搜索结果页（robots.txt 允许），
按「游戏类型 + 价格区间」筛选，解析出真实的候选商品：
appid、名称、价格、好评率、评价数量、评价等级、封面图。

不伪造：搜不到就返回空列表并说明原因。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from ..http_client import FetchError, client
from ..models import Offer, Product, Rating, Source, now_text
from ..normalize import clean_text, parse_int, parse_percent

__all__ = ["Candidate", "DiscoveryResult", "SteamSearchDiscovery", "GENRE_SLUG",
           "parse_search_rows"]

SEARCH_URL = "https://store.steampowered.com/search/"

#: Steam 搜索页使用的类型标识（英文 slug）
GENRE_SLUG = {
    "动作": "Action",
    "冒险": "Adventure",
    "休闲": "Casual",
    "独立": "Indie",
    "大型多人在线": "Massively Multiplayer",
    "竞速": "Racing",
    "角色扮演": "RPG",
    "模拟": "Simulation",
    "体育": "Sports",
    "策略": "Strategy",
    "免费开玩": "Free to Play",
    "抢先体验": "Early Access",
}

_RATE_RE = re.compile(r"(\d+)%\s*(?:为好评|of the .*? are positive|are positive)", re.I)
_COUNT_RE = re.compile(r"([\d,]{2,})\s*(?:篇用户评测|user reviews)")


@dataclass
class Candidate:
    appid: int
    name: str
    price: float | None
    currency: str | None
    positive_rate: float | None
    review_count: int | None
    label: str
    image: str
    url: str

    def to_product(self) -> Product:
        """把搜索结果转成一个"尚未深采"的商品对象，界面可直接展示。"""
        product = Product(
            product_id=f"steam:{self.appid}",
            platform="Steam",
            url=self.url,
            name=self.name,
            image_url=self.image,
            origin="discovered",
            fetched_at=now_text(),
        )
        product.offer = Offer(price=self.price, currency=self.currency,
                              availability="在售" if self.price is not None else "",
                              region="CN", source_key="search")
        product.rating = Rating(positive_rate=self.positive_rate, review_count=self.review_count,
                                label=self.label, scope="简体中文评测", source_key="search")
        product.specs = {}
        source = Source(adapter="steam_search", label="Steam 公开搜索结果",
                        url=SEARCH_URL, method="公开页面结构化数据", fetched_at=now_text())
        product.add_source("search", source, ["name", "price", "rating_count"])
        product.status = "自动发现（待补充参数）"
        product.recompute_missing()
        return product


@dataclass
class DiscoveryResult:
    base_id: str
    ok: bool
    note: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    scanned: int = 0
    query_urls: list[str] = field(default_factory=list)
    genre: str = ""
    price_range: tuple[float | None, float | None] = (None, None)

    @property
    def count(self) -> int:
        return len(self.candidates)


def _parse_rows(html: str, base_appid: int = 0) -> list[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Candidate] = []
    for row in soup.select("a.search_result_row"):
        raw_id = row.get("data-ds-appid") or ""
        match = re.search(r"(\d+)", raw_id)
        if not match:
            continue
        appid = int(match.group(1))
        if appid == base_appid:
            continue
        title_el = row.select_one(".title")
        if not title_el:
            continue
        name = clean_text(title_el.get_text(strip=True))
        if not name:
            continue
        # 打折时 .search_price 里会同时出现被划掉的原价和折后价，必须优先取折后价节点
        price_el = row.select_one(".discount_final_price") or row.select_one(".search_price")
        price_text = clean_text(price_el.get_text(" ", strip=True)) if price_el else ""
        price: float | None = None
        currency: str | None = None
        if price_text:
            if re.search(r"免费|Free", price_text, re.I):
                price, currency = 0.0, "CNY"
            else:
                numbers = re.findall(r"[\d,]+(?:\.\d+)?", price_text)
                # 取最后一个数字：无折扣时也就是唯一那个价格
                if numbers:
                    price = float(numbers[-1].replace(",", ""))
                    currency = "CNY" if ("¥" in price_text or "￥" in price_text) else None
        review_el = row.select_one(".search_review_summary")
        tooltip = review_el.get("data-tooltip-html", "") if review_el else ""
        tooltip = re.sub(r"<br\s*/?>", "|", tooltip)
        rate = None
        count = None
        label = ""
        if tooltip:
            parts = [clean_text(p) for p in tooltip.split("|") if clean_text(p)]
            label = parts[0] if parts else ""
            rate = parse_percent(tooltip)
            count_match = _COUNT_RE.search(tooltip)
            if count_match:
                count = parse_int(count_match.group(1))
        image_el = row.select_one("img")
        image = image_el.get("src", "") if image_el else ""
        out.append(
            Candidate(
                appid=appid,
                name=name,
                price=price,
                currency=currency,
                positive_rate=rate,
                review_count=count,
                label=label,
                image=image,
                url=f"https://store.steampowered.com/app/{appid}/",
            )
        )
    return out


def _score(candidate: Candidate) -> float:
    """排序用：口碑（贝叶斯平滑）× 评价量权重，避免只有几条好评的冷门品排前面。"""
    rate = candidate.positive_rate if candidate.positive_rate is not None else 70.0
    count = candidate.review_count or 0
    smoothed = (rate * count + 70 * 50) / (count + 50)
    return smoothed * (1 + min(math.log10(count + 1), 5) / 20)


class SteamSearchDiscovery:
    key = "steam_search"
    label = "Steam 商店搜索页"
    domains = ("store.steampowered.com",)

    def matches(self, product: Product) -> bool:
        return (product.platform or "").lower() == "steam"

    def search(self, product: Product, *, min_price: float | None = None,
               max_price: float | None = None, limit: int = 8,
               pages: int = 2) -> DiscoveryResult:
        base_appid = 0
        if product.product_id.startswith("steam:"):
            try:
                base_appid = int(product.product_id.split(":", 1)[1])
            except ValueError:
                base_appid = 0

        genres = [g.strip() for g in re.split(r"[/、,，]", product.category or "") if g.strip()]
        genre_cn = next((g for g in genres if g in GENRE_SLUG), "")
        if not genre_cn:
            return DiscoveryResult(
                base_id=product.product_id, ok=False,
                note=(f"该商品的类别「{product.category or '未提供'}」无法映射到搜索页可用的类型，"
                      "因此无法自动发现同类竞品。可以用链接自己补充竞品，系统同样会做对比。"),
            )
        slug = GENRE_SLUG[genre_cn]

        # 价格区间：默认以本品价格为中心 ±50%，用户可覆盖
        base_price = product.offer.price
        if min_price is None and base_price and base_price > 0:
            min_price = round(base_price * 0.5, 2)
        if max_price is None and base_price and base_price > 0:
            max_price = round(base_price * 1.5, 2)

        candidates: dict[int, Candidate] = {}
        scanned = 0
        urls: list[str] = []
        for page in range(1, pages + 1):
            params = {
                "genre": slug, "cc": "cn", "l": "schinese",
                "category1": 998, "page": page, "supportedlang": "schinese",
            }
            if min_price:
                params["minprice"] = int(max(0, min_price))
            if max_price:
                params["maxprice"] = int(max(0, max_price))
            try:
                resp = client.get(SEARCH_URL, params=params)
            except FetchError as exc:
                if not candidates:
                    return DiscoveryResult(
                        base_id=product.product_id, ok=False,
                        note=f"竞品搜索页访问失败：{exc.message}", genre=genre_cn,
                        price_range=(min_price, max_price),
                    )
                break
            urls.append(resp.url)
            rows = _parse_rows(resp.text, base_appid)
            scanned += len(rows)
            for item in rows:
                candidates.setdefault(item.appid, item)
            if len(candidates) >= limit * 3:
                break

        if not candidates:
            return DiscoveryResult(
                base_id=product.product_id, ok=False,
                note=(f"在「{genre_cn}」类型、"
                      f"{'不限' if min_price is None else f'¥{min_price:.0f}'}–"
                      f"{'不限' if max_price is None else f'¥{max_price:.0f}'} 区间内没有搜到其他商品。"
                      "可以放宽预算范围后重试。"),
                query_urls=urls, genre=genre_cn, price_range=(min_price, max_price),
            )

        ranked = sorted(candidates.values(), key=_score, reverse=True)
        if max_price is not None:
            ranked = [c for c in ranked if c.price is None or c.price <= max_price + 0.01]
        if min_price is not None:
            ranked = [c for c in ranked if c.price is None or c.price >= min_price - 0.01]
        picked = ranked[:limit]
        note = (
            f"在 Steam「{genre_cn}」分类、价格 "
            f"{'不限' if min_price is None else f'¥{min_price:.0f}'}–"
            f"{'不限' if max_price is None else f'¥{max_price:.0f}'} 内扫描到 {scanned} 个商品，"
            f"按口碑与评价量排序后取前 {len(picked)} 个作为同类竞品。"
            "这些是「同类型不同商品」，不是同款比价。"
        )
        return DiscoveryResult(
            base_id=product.product_id, ok=True, note=note, candidates=picked,
            scanned=scanned, query_urls=urls, genre=genre_cn,
            price_range=(min_price, max_price),
        )


#: 公开别名，便于测试与复用（解析结果完全来自页面，不做任何补全）
parse_search_rows = _parse_rows
