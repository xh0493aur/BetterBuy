"""商品链接识别：判断链接属于哪个平台，并尽量提取商品 ID。

设计原则：
- **不联网也能判断**（域名 + 路径 + 查询参数），短链接只标记出来，展开另走一步；
- 识别不出商品 ID 时返回空字符串，由上层决定是报错还是先展开短链；
- 不做"猜"：`item.jd.com/100012043978.html` 里的数字就是 skuId，
  但 `u.jd.com/abc` 这种短链在展开前没有任何商品信息，就如实标记 ``is_short``。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

__all__ = ["LinkInfo", "PLATFORM_LABELS", "detect", "normalize_url", "SHORT_HOSTS"]

PLATFORM_LABELS = {
    "jd": "京东",
    "taobao": "淘宝",
    "tmall": "天猫",
    "pdd": "拼多多",
    "steam": "Steam",
    "unknown": "未识别",
}

#: 这些域名是短链，必须先联网展开才知道指向哪个商品
SHORT_HOSTS = {
    "u.jd.com", "3.cn", "jd.cn",
    "m.tb.cn", "e.tb.cn", "s.click.taobao.com",
    "p.pinduoduo.com", "mobile.yangkeduo.com",
    "s.team",
}

_JD_HOSTS = ("item.jd.com", "item.m.jd.com", "npcitem.jd.hk", "item.jd.hk",
             "item.jd.com.hk", "mall.jd.com", "jd.com", "jd.hk", "3.cn")
_TB_HOSTS = ("item.taobao.com", "detail.tmall.com", "detail.tmall.hk",
             "chaoshi.detail.tmall.com", "world.taobao.com", "item.taobao.hk",
             "taobao.com", "tmall.com", "tmall.hk", "m.tb.cn", "e.tb.cn",
             "s.click.taobao.com")
_PDD_HOSTS = ("mobile.yangkeduo.com", "yangkeduo.com", "pinduoduo.com",
              "p.pinduoduo.com", "mobile.pinduoduo.com")
_STEAM_HOSTS = ("store.steampowered.com", "steampowered.com", "s.team")

_RE_JD_ITEM = [
    re.compile(r"/(\d{5,})\.html"),
    re.compile(r"/product/(\d{5,})"),
]
_RE_TB_ID = re.compile(r"\d{6,}")
_RE_PDD_GOODS = re.compile(r"goods(?:_id)?[=/](\d{5,})")
_RE_STEAM_APP = [
    re.compile(r"/app/(\d+)"),
    re.compile(r"/a/(\d+)"),
    re.compile(r"store/(\d+)"),
]


@dataclass(frozen=True)
class LinkInfo:
    platform: str          # jd / taobao / tmall / pdd / steam / unknown
    label: str
    item_id: str = ""
    url: str = ""
    is_short: bool = False
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.platform != "unknown"

    @property
    def product_id(self) -> str:
        """给 Product.product_id 用的稳定标识。"""
        if not self.ok:
            return ""
        if self.item_id:
            return f"{self.platform}:{self.item_id}"
        return f"{self.platform}:{_slug(self.url)}"


def _slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", text)[-24:] or "unknown"


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _host(url: str) -> str:
    try:
        return (urlparse(normalize_url(url)).hostname or "").lower()
    except ValueError:
        return ""


def _in(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _query(url: str) -> dict[str, str]:
    try:
        raw = parse_qs(urlparse(normalize_url(url)).query)
    except ValueError:
        return {}
    return {k: v[0] for k, v in raw.items() if v}


def detect(url: str) -> LinkInfo:
    """判断平台并提取商品 ID（纯本地解析，不联网）。"""
    original = (url or "").strip()
    url = normalize_url(original)
    if not url:
        return LinkInfo("unknown", PLATFORM_LABELS["unknown"], url="", note="链接为空")

    host = _host(url)
    if not host:
        return LinkInfo("unknown", PLATFORM_LABELS["unknown"], url=url, note="无法解析域名")

    # ---------------- Steam ----------------
    if _in(host, _STEAM_HOSTS):
        path = urlparse(url).path
        for pattern in _RE_STEAM_APP:
            match = pattern.search(path)
            if match:
                return LinkInfo("steam", PLATFORM_LABELS["steam"], match.group(1), url)
        q = _query(url)
        if q.get("appid", "").isdigit():
            return LinkInfo("steam", PLATFORM_LABELS["steam"], q["appid"], url)
        return LinkInfo("steam", PLATFORM_LABELS["steam"], "", url, is_short=host == "s.team",
                        note="Steam 页面里没找到 appid")

    # ---------------- 京东 ----------------
    if _in(host, _JD_HOSTS):
        path = urlparse(url).path
        for pattern in _RE_JD_ITEM:
            match = pattern.search(path)
            if match:
                return LinkInfo("jd", PLATFORM_LABELS["jd"], match.group(1), url)
        q = _query(url)
        for key in ("sku", "skuId", "wareId", "id"):
            value = q.get(key, "")
            if value.isdigit() and len(value) >= 5:
                return LinkInfo("jd", PLATFORM_LABELS["jd"], value, url)
        short = host in SHORT_HOSTS
        return LinkInfo("jd", PLATFORM_LABELS["jd"], "", url, is_short=short,
                        note="京东短链，需要联网展开后才能拿到 skuId" if short else "没找到 skuId")

    # ---------------- 拼多多 ----------------
    if _in(host, _PDD_HOSTS):
        url_full = url
        match = _RE_PDD_GOODS.search(url_full)
        if match:
            return LinkInfo("pdd", PLATFORM_LABELS["pdd"], match.group(1), url)
        q = _query(url)
        for key in ("goods_id", "goodsId", "gid"):
            value = q.get(key, "")
            if value.isdigit() and len(value) >= 5:
                return LinkInfo("pdd", PLATFORM_LABELS["pdd"], value, url)
        short = host in SHORT_HOSTS
        return LinkInfo("pdd", PLATFORM_LABELS["pdd"], "", url, is_short=short,
                        note="拼多多短链，需要联网展开" if short else "没找到 goods_id")

    # ---------------- 淘宝 / 天猫 ----------------
    if _in(host, _TB_HOSTS):
        q = _query(url)
        for key in ("id", "itemId", "item_id"):
            value = q.get(key, "")
            if _RE_TB_ID.fullmatch(value):
                platform = "tmall" if "tmall" in host else "taobao"
                return LinkInfo(platform, PLATFORM_LABELS[platform], value, url)
        path = urlparse(url).path.strip("/")
        match = re.search(r"/item/(\d{6,})", "/" + path)
        if match:
            platform = "tmall" if "tmall" in host else "taobao"
            return LinkInfo(platform, PLATFORM_LABELS[platform], match.group(1), url)
        # 短链：m.tb.cn/xxxx 这类，路径里没有商品 ID
        short = host in SHORT_HOSTS or not path.startswith("item")
        return LinkInfo("taobao", PLATFORM_LABELS["taobao"], "", url,
                        is_short=short,
                        note="淘宝短链/口令链接，需要联网展开后才能拿到商品 ID")

    return LinkInfo("unknown", PLATFORM_LABELS["unknown"], "", url,
                    note=f"暂不支持这个域名：{host}")
