"""统一商品数据模型。

设计要点：
1. 每个数据片段都挂在 :class:`Source` 上，记录「采集适配器 / 访问方式 / 原始地址 / 采集时间」，
   因此界面上可以逐字段说明来源与更新时间。
2. 缺失的字段不填默认值、不猜，统一进入 ``missing`` 列表，界面显示「数据源未提供」。
3. ``to_legacy_row()`` 可以把新模型转成旧版对比表的一行，从而复用 scoring.py。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable

__all__ = ["UNKNOWN", "Source", "Offer", "Rating", "Product", "now_text"]

#: 统一的缺失占位文案。界面上不出现空字符串，避免让用户误以为是 0 或免费。
UNKNOWN = "数据源未提供"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Source:
    """一次采集的数据来源。"""

    adapter: str          # 采集适配器标识，如 steam_official_api
    label: str            # 人类可读名称，如 "Steam 官方公开 API"
    url: str              # 实际访问的地址（可能是接口地址）
    method: str = "公开接口"   # 公开接口 / 公开页面结构化数据 / 页面元标签
    fetched_at: str = field(default_factory=now_text)
    http_status: int | None = None
    cached: bool = False  # 是否直接来自本地缓存

    def describe(self) -> str:
        suffix = "（本地缓存）" if self.cached else ""
        return f"{self.label} · {self.fetched_at}{suffix}"


@dataclass
class Offer:
    """价格信息。货币未知时保持 None，不做汇率换算。"""

    price: float | None = None
    currency: str | None = None
    original_price: float | None = None
    discount_percent: int | None = None
    availability: str = ""
    region: str = ""
    source_key: str = ""

    @property
    def is_known(self) -> bool:
        return self.price is not None and bool(self.currency)

    @property
    def discount_text(self) -> str:
        if self.discount_percent:
            return f"-{self.discount_percent}%"
        return ""

    def display(self) -> str:
        if self.price is None:
            return UNKNOWN
        if self.price == 0:
            return "免费"
        symbol = {"CNY": "¥", "USD": "$", "EUR": "€", "JPY": "JP¥", "GBP": "£"}.get(
            (self.currency or "").upper(), ""
        )
        text = f"{symbol}{self.price:,.2f}" if symbol else f"{self.price:,.2f} {self.currency or ''}".strip()
        if self.original_price and self.original_price > self.price:
            text += f"（原价 {symbol}{self.original_price:,.2f}）"
        return text


@dataclass
class Rating:
    """口碑信息。``positive_rate`` 只在数据源明确给出好评比例时才有值。"""

    positive_rate: float | None = None      # 0-100，数据源明确给出的好评比例
    review_count: int | None = None         # 评价总数
    label: str = ""                          # 数据源给出的评价等级，如「好评如潮」
    scope: str = ""                           # 好评率的统计口径，如「简体中文评测」
    recent_positive_rate: float | None = None
    recent_review_count: int | None = None
    score: float | None = None               # 第三方评分，如 Metacritic
    scale: float | None = None
    source_key: str = ""

    @property
    def has_any(self) -> bool:
        return any(
            v is not None
            for v in (self.positive_rate, self.review_count, self.score, self.recent_positive_rate)
        )

    def display(self) -> str:
        bits: list[str] = []
        if self.positive_rate is not None:
            bits.append(f"{self.positive_rate:.0f}% 好评")
        if self.review_count is not None:
            bits.append(f"{self.review_count:,} 条评价")
        text = " · ".join(bits)
        if text and self.scope:
            text += f"（{self.scope}口径）"
        if self.score is not None and self.scale:
            text = (text + " · " if text else "") + f"媒体评分 {self.score:g}/{self.scale:g}"
        return text or UNKNOWN


@dataclass
class Product:
    """一个被识别出来的商品（含竞品）。"""

    product_id: str                  # 稳定标识，如 steam:413150
    platform: str                    # 平台名称，如 Steam
    url: str                         # 规范化后的商品页地址
    name: str = ""
    brand: str = ""
    model: str = ""
    category: str = ""
    subcategory: str = ""
    summary: str = ""
    image_url: str = ""
    specs: dict[str, str] = field(default_factory=dict)

    offer: Offer = field(default_factory=Offer)
    rating: Rating = field(default_factory=Rating)
    reviews: list[dict[str, Any]] = field(default_factory=list)

    sources: dict[str, Source] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)   # 字段名 -> sources 键
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)                # 采集说明/降级原因
    raw_fields: list[str] = field(default_factory=list)           # 接口返回的原始字段路径（排查用）
    origin: str = "user"            # user=用户提供 / discovered=自动发现 / cache=缓存
    fetched_at: str = field(default_factory=now_text)
    status: str = ""                # 一句话采集状态

    # ---------- 便捷方法 ----------
    def add_source(self, key: str, source: Source, fields: Iterable[str]) -> None:
        self.sources[key] = source
        for name in fields:
            self.field_sources[name] = key

    def source_of(self, name: str) -> Source | None:
        key = self.field_sources.get(name)
        return self.sources.get(key) if key else None

    def source_text(self, name: str) -> str:
        src = self.source_of(name)
        if not src:
            return "来源未记录"
        return f"{src.label} · {src.fetched_at}"

    def mark_missing(self, *names: str) -> None:
        for name in names:
            if name not in self.missing:
                self.missing.append(name)

    def recompute_missing(self) -> None:
        """根据当前字段值重新计算缺失项（界面直接展示这份清单）。"""
        self.missing = []
        checks = {
            "商品名称": self.name,
            "品牌": self.brand,
            "型号/版本": self.model,
            "商品类别": self.category,
            "价格": None if self.offer.price is None else "ok",
            "货币": self.offer.currency,
            "好评率": None if self.rating.positive_rate is None else "ok",
            "评价数量": None if self.rating.review_count is None else "ok",
            "商品图片": self.image_url,
            "核心参数": self.specs or None,
            "评价内容": self.reviews or None,
        }
        for label, value in checks.items():
            if value in (None, "", {}, []):
                self.missing.append(label)

    @property
    def completeness(self) -> tuple[int, int, float]:
        """返回 (已有字段数, 总字段数, 完整度 0-1)。"""
        keys = ["商品名称", "品牌", "型号/版本", "商品类别", "价格", "货币",
                "好评率", "评价数量", "商品图片", "核心参数"]
        total = len(keys)
        have = total - len([m for m in self.missing if m in keys])
        return have, total, round(have / total, 3)

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Product":
        data = dict(data or {})
        offer = data.pop("offer", {}) or {}
        rating = data.pop("rating", {}) or {}
        sources_raw = data.pop("sources", {}) or {}
        product = cls(**data)
        product.offer = Offer(**offer)
        product.rating = Rating(**rating)
        product.sources = {k: Source(**v) for k, v in sources_raw.items()}
        return product

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    # ---------- 兼容旧表格 ----------
    def to_legacy_row(self) -> dict[str, Any]:
        """转成旧版对比表的列结构，复用 scoring.py 的评分逻辑。"""
        spec_bits = [f"{k}：{v}" for k, v in list(self.specs.items())[:6]]
        return {
            "商品": self.name or self.url,
            "平台": self.platform,
            "链接": self.url,
            "价格": self.offer.price,
            "币种": (self.offer.currency or "").upper(),
            "评分": self.rating.score,
            "满分": self.rating.scale,
            "评价数": self.rating.review_count,
            "好评率%": self.rating.positive_rate,
            "实用度(1-10)": None,
            "核心参数/备注": "；".join(spec_bits),
            "来源": self.source_text("price") if self.offer.price is not None else "数据源未提供",
            "采集状态": self.status or "已识别",
            "采集时间": self.fetched_at,
        }

    def short(self, limit: int = 60) -> str:
        text = self.name or self.url
        return text if len(text) <= limit else text[: limit - 1] + "…"
