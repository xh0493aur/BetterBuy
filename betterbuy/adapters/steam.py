"""Steam 商店适配器。

数据来源（均为公开、无需登录的接口/页面）：
1. ``https://store.steampowered.com/api/appdetails`` —— 官方公开的商品详情接口，
   提供名称、开发商、类型、价格与货币、支持平台、语言、发售日期、Metacritic、评价总数等。
2. ``https://store.steampowered.com/app/<appid>/`` —— 公开商品页，提供好评率与评价等级
   （Steam 页面提供的是「语言维度」的评测汇总，本适配器如实标注口径）。

明确说明：``/appreviews`` 接口在本机网络环境下返回占位页面，无法取得用户评测正文，
因此评价内容一栏标记为缺失，不伪造。
"""
from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup

from ..http_client import FetchError, client
from ..models import UNKNOWN, Offer, Product, Rating, Source, now_text
from ..normalize import clean_text, parse_int, parse_percent
from .base import BaseAdapter

__all__ = ["SteamAdapter", "parse_appid", "product_from_appdetails", "apply_store_rating"]

API = "https://store.steampowered.com/api/appdetails"
APP_PAGE = "https://store.steampowered.com/app/{appid}/"

TYPE_CN = {
    "game": "游戏",
    "dlc": "DLC / 扩展内容",
    "demo": "试玩 Demo",
    "application": "软件",
    "music": "原声音乐",
    "video": "视频",
    "hardware": "硬件",
    "advertising": "广告内容",
}

_APP_RE = re.compile(r"/app/(\d+)", re.I)
_SHORT_RE = re.compile(r"s\.team/a/(\d+)", re.I)
_PROTOCOL_RE = re.compile(r"steam://(?:store|run)/(\d+)", re.I)
_QUERY_RE = re.compile(r"[?&]appid[s]?=(\d+)", re.I)


def parse_appid(url: str) -> int | None:
    """从各种形态的 Steam 链接里取出 appid；取不到返回 None（不猜）。"""
    if not url:
        return None
    text = url.strip()
    for pattern in (_APP_RE, _SHORT_RE, _PROTOCOL_RE, _QUERY_RE):
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    if text.isdigit():
        return int(text)
    return None


def _requirements_text(raw) -> str:
    if isinstance(raw, dict):
        raw = raw.get("minimum") or raw.get("recommended") or ""
    if not isinstance(raw, str) or not raw.strip():
        return ""
    text = BeautifulSoup(unescape(raw), "html.parser").get_text(" ", strip=True)
    # 原文本身就以「最低配置:」开头，去掉重复前缀
    text = re.sub(r"^(最低配置|Minimum)\s*[:：]\s*", "", clean_text(text))
    return clean_text(text, limit=260)


def _platforms_text(platforms) -> str:
    if not isinstance(platforms, dict):
        return ""
    names = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}
    return " / ".join(label for key, label in names.items() if platforms.get(key))


def _languages(data: dict) -> tuple[str, int | None]:
    raw = data.get("supported_languages")
    if not isinstance(raw, str) or not raw.strip():
        return "", None
    text = BeautifulSoup(unescape(raw), "html.parser").get_text(" ", strip=True)
    # 页尾还有一句图例说明，会粘在最后一个语言后面，先切掉
    text = re.split(r"具有(?:完全|完整)音频支持的语言|languages with full audio support", text, maxsplit=1)[0]
    parts = [clean_text(p) for p in re.split(r"[,，]", text) if clean_text(p)]
    # Steam 用 * 标注「仅界面/字幕支持」，这里保留原样说明，不做推断。
    # 保留完整列表：判断「是否支持简体中文」必须看全部语言，截断会误判。
    joined = "、".join(parts)
    if "*" in joined:
        # Steam 的语言表用 * 标注「含完整语音」，无 * 表示仅界面/字幕
        joined += "（* 表示含完整语音）"
    return joined, len(parts) or None


def product_from_appdetails(appid: int, data: dict, url: str, source: Source) -> Product:
    """把 appdetails 的 JSON 映射成 Product（纯函数，便于离线测试）。"""
    product = Product(
        product_id=f"steam:{appid}",
        platform="Steam",
        url=url or APP_PAGE.format(appid=appid),
        fetched_at=source.fetched_at,
    )
    product.name = clean_text(data.get("name"))
    developers = data.get("developers") or []
    publishers = data.get("publishers") or []
    product.brand = clean_text(developers[0]) if developers else clean_text(
        publishers[0] if publishers else ""
    )
    genres = [clean_text(g.get("description")) for g in (data.get("genres") or []) if isinstance(g, dict)]
    genres = [g for g in genres if g]
    product.category = " / ".join(genres)
    product.subcategory = genres[0] if genres else ""
    product.summary = clean_text(data.get("short_description"), limit=300)
    product.image_url = data.get("header_image") or data.get("capsule_image") or ""

    kind = clean_text(data.get("type"))
    product.model = f"{TYPE_CN.get(kind, kind or '商品')}（Steam 数字版）"

    # ---- 价格 ----
    price_info = data.get("price_overview") or {}
    if price_info:
        product.offer = Offer(
            price=round(float(price_info.get("final", 0)) / 100, 2)
            if price_info.get("final") is not None else None,
            currency=(price_info.get("currency") or "").upper() or None,
            original_price=round(float(price_info.get("initial", 0)) / 100, 2)
            if price_info.get("initial") else None,
            discount_percent=price_info.get("discount_percent") or 0,
            availability="在售",
            region="CN",
            source_key="price",
        )
    elif data.get("is_free"):
        product.offer = Offer(price=0.0, currency="CNY", availability="免费", region="CN", source_key="price")
    else:
        product.offer = Offer(availability="数据源未给出价格", region="CN", source_key="price")

    # ---- 口碑（评价总数来自官方 API；好评率稍后由商品页补充）----
    recommendations = data.get("recommendations") or {}
    total_reviews = parse_int(recommendations.get("total")) if isinstance(recommendations, dict) else None
    metacritic = data.get("metacritic") or {}
    mc_score = parse_int(metacritic.get("score")) if isinstance(metacritic, dict) else None
    product.rating = Rating(
        review_count=total_reviews,
        score=float(mc_score) if mc_score is not None else None,
        scale=100.0 if mc_score is not None else None,
        source_key="rating",
    )

    # ---- 核心参数 ----
    specs: dict[str, str] = {}
    platforms_text = _platforms_text(data.get("platforms"))
    if platforms_text:
        specs["支持平台"] = platforms_text
    features = [clean_text(c.get("description")) for c in (data.get("categories") or [])
                if isinstance(c, dict) and c.get("description")]
    if features:
        specs["功能特性"] = "、".join(features[:8])
    languages, lang_count = _languages(data)
    if languages:
        specs["支持语言"] = f"{lang_count} 种：{languages}" if lang_count else languages
    release = data.get("release_date") or {}
    if isinstance(release, dict) and release.get("date"):
        note = "（尚未发售）" if release.get("coming_soon") else ""
        specs["发售日期"] = clean_text(release.get("date")) + note
    if mc_score is not None:
        specs["Metacritic"] = f"{mc_score}/100"
    dlc = data.get("dlc") or []
    if dlc:
        specs["追加内容"] = f"{len(dlc)} 个 DLC（可能需额外付费）"
    achievements = data.get("achievements") or {}
    if isinstance(achievements, dict) and achievements.get("total"):
        specs["成就"] = f"{achievements['total']} 项"
    req = _requirements_text(data.get("pc_requirements"))
    if req:
        specs["最低配置"] = req
    if total_reviews:
        # 官方接口给的是全语言评价总数；商品页给的是中文口径，两个数都展示便于核对
        specs["评价总数（全语言）"] = f"{total_reviews:,} 条"
    product.specs = specs

    product.add_source("detail", source, ["name", "brand", "model", "category", "specs",
                                          "image", "summary", "rating_count", "metacritic"])
    product.add_source("price", source, ["price", "currency"])
    product.add_source("rating", source, ["rating"])

    required_age = parse_int(data.get("required_age"))
    if required_age and required_age > 0:
        product.notes.append(f"该商品有年龄限制标记（{required_age}+），部分内容需设置出生日期后才能查看。")
    if dlc:
        product.notes.append("该商品存在 DLC，标注价格仅为本体价格。")
    product.notes.append("价格为 Steam 中国区商店标价（cc=cn），实际结算可能因支付方式与促销变化。")
    product.notes.append(
        "Steam 未向本工具开放用户评测正文，评价内容一栏标记为缺失；"
        "口碑依据为官方汇总的好评率与评价数。"
    )
    return product


_CN_REVIEWS_RE = re.compile(r"(\d+)%\s*为好评")
_RECENT_RE = re.compile(r"过去\s*30\s*天")


def apply_store_rating(product: Product, html: str, source: Source) -> None:
    """从公开商品页补充好评率/评价等级/近期评测。口径如实写入 scope。"""
    soup = BeautifulSoup(html, "html.parser")
    found_lang = False
    found_recent = False
    for row in soup.select(".user_reviews_summary_row"):
        tooltip = unescape(row.get("data-tooltip-html") or "")
        text = row.get_text(" ", strip=True)
        if not tooltip and not text:
            continue
        rate = parse_percent(tooltip) if tooltip else None
        count = None
        count_match = re.search(r"([\d,]{2,})\s*篇用户评测", tooltip) or re.search(r"([\d,]{2,})\s*user reviews", tooltip)
        if count_match:
            count = parse_int(count_match.group(1))
        label_el = row.select_one(".game_review_summary")
        label = clean_text(label_el.get_text(strip=True)) if label_el else ""
        if _RECENT_RE.search(tooltip) and rate is not None and not found_recent:
            product.rating.recent_positive_rate = rate
            product.rating.recent_review_count = count
            found_recent = True
            continue
        if rate is not None and not found_lang:
            product.rating.positive_rate = rate
            if count is not None:
                product.rating.review_count = count
            product.rating.scope = "简体中文评测"
            product.rating.label = label
            found_lang = True
    if found_lang or found_recent:
        product.sources["reviews"] = source
        product.field_sources["rating_rate"] = "reviews"
    else:
        product.notes.append("商品页未提供可解析的好评率（可能评价数量过少），该字段标记为缺失。")


class SteamAdapter(BaseAdapter):
    key = "steam"
    label = "Steam 商店"
    method = "官方公开 API + 公开商品页"
    domains = ("store.steampowered.com", "s.team", "steamcommunity.com")

    def matches(self, url: str) -> bool:
        low = (url or "").lower()
        if "steam" in low and parse_appid(url) is not None:
            return True
        return any(host in low for host in self.domains)

    def cache_key(self, url: str) -> str | None:
        appid = parse_appid(url)
        return f"steam:{appid}" if appid else None

    # ---------- 采集 ----------
    def fetch(self, url: str) -> Product:
        appid = parse_appid(url)
        if appid is None and re.search(r"/(sub|bundle)/\d+", url or "", re.I):
            raise FetchError(
                "这是 Steam 的捆绑包/订阅包链接（/sub/ 或 /bundle/），"
                "当前版本只支持单个商品页（/app/<appid>）。请打开具体的商品页再复制链接。",
                kind="unsupported",
            )
        if appid is None:
            # 可能是短链或非标准链接：先跟随重定向
            final_url, chain = client.resolve(url)
            appid = parse_appid(final_url)
            if appid is None:
                raise FetchError(
                    "这个链接不是可识别的 Steam 商品页（没有 appid）。"
                    f"已跟随 {len(chain) - 1} 次跳转到：{final_url[:90]}",
                    kind="unsupported",
                )
            url = final_url

        api_url = f"{API}?appids={appid}&cc=cn&l=schinese"
        payload = client.get_json(API, params={"appids": appid, "cc": "cn", "l": "schinese"})
        entry = (payload or {}).get(str(appid)) or {}
        if not entry.get("success") or not isinstance(entry.get("data"), dict):
            raise FetchError(
                f"Steam 官方接口未返回 appid={appid} 的详情（可能已下架、区域不可见或输入的是捆绑包/DLC 之外的条目）",
                kind="notfound",
            )
        detail_source = Source(
            adapter=self.key, label="Steam 官方公开 API", url=api_url,
            method=self.method, fetched_at=now_text(),
        )
        product = product_from_appdetails(appid, entry["data"], APP_PAGE.format(appid=appid), detail_source)
        product.url = f"https://store.steampowered.com/app/{appid}/"

        # 好评率来自公开商品页；失败不影响主体识别，但要如实说明
        page_url = f"{APP_PAGE.format(appid=appid)}?cc=cn&l=schinese"
        try:
            resp = client.get(page_url)
            page_source = Source(
                adapter=self.key, label="Steam 公开商品页", url=page_url,
                method="公开页面结构化数据", fetched_at=now_text(),
                http_status=resp.status_code,
            )
            apply_store_rating(product, resp.text, page_source)
        except FetchError as exc:
            product.notes.append(f"好评率未能获取：{exc.message}")

        product.recompute_missing()
        have, total, ratio = product.completeness
        product.status = f"已识别（字段完整度 {have}/{total}）"
        return product
