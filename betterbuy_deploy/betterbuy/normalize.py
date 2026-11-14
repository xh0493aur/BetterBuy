"""商品信息标准化：清洗、解析、同款判定、去重。

规则全部可解释：不做"猜测式补全"，只做格式归一（全角转半角、去站点后缀、
价格单位换算），无法判断的一律返回 None。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

__all__ = [
    "clean_text", "strip_site_suffix", "parse_price", "parse_int", "parse_percent",
    "guess_brand", "guess_model", "norm_key", "similarity", "same_product",
    "dedupe_products", "CURRENCY_SYMBOL",
]

CURRENCY_SYMBOL = {"CNY": "¥", "RMB": "¥", "USD": "$", "EUR": "€", "JPY": "JP¥", "GBP": "£"}
SYMBOL_CURRENCY = {"¥": "CNY", "￥": "CNY", "$": "USD", "€": "EUR", "£": "GBP"}


def clean_text(value: object, limit: int | None = None) -> str:
    """全角转半角、压缩空白、去掉首尾控制字符。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


#: 标题里常见的站点后缀与购买按钮文案（按顺序反复剥离）
_SITE_NOISE_PATTERNS = [
    re.compile(r"\s*[-_|｜·]\s*(?:京东|天猫|淘宝|拼多多|当当|小米商城|华为商城|官方商城|"
               r"官方旗舰店|旗舰店|商城|官网|Steam|SteamDB)\s*$", re.I),
    re.compile(r"(?:立即购买|马上购买|在线购买|立即抢购|商品详情|商品页|旗舰店|官网)\s*$", re.I),
    re.compile(r"\s*[-_|｜·]\s*$"),
]


def strip_site_suffix(title: str) -> str:
    """去掉标题里的站点后缀，如「REDMI显示器A22 节能版立即购买-小米商城」。"""
    text = clean_text(title)
    for _ in range(6):
        before = text
        for pattern in _SITE_NOISE_PATTERNS:
            text = pattern.sub("", text).strip()
        if text == before:
            break
    return text.strip(" -_|｜·")


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_price(value: object, currency_hint: str | None = None) -> tuple[float | None, str | None]:
    """从文本/数字中解析价格与货币。无法确定货币时返回 (值, None)，绝不默认人民币。"""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return (float(value), (currency_hint or "").upper() or None)
    text = clean_text(value)
    if not text:
        return None, None
    currency = (currency_hint or "").upper() or None
    for symbol, code in SYMBOL_CURRENCY.items():
        if symbol in text:
            currency = currency or code
            break
    if re.search(r"\b(CNY|RMB)\b|人民币", text, re.I):
        currency = currency or "CNY"
    if re.search(r"\bUSD\b|美元", text, re.I):
        currency = currency or "USD"
    match = _NUM.search(text.replace(",", ""))
    if not match:
        return None, currency
    try:
        number = float(match.group(0))
    except ValueError:
        return None, currency
    return (number if number >= 0 else None), currency


def parse_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = clean_text(value).replace(",", "").replace("，", "")
    match = re.search(r"\d+(?:\.\d+)?\s*(万|w)?", text, re.I)
    if not match:
        return None
    try:
        number = float(match.group(0).replace("万", "").replace("w", "").replace("W", ""))
    except ValueError:
        return None
    if match.group(1):
        number *= 10000
    return int(number)


def parse_percent(value: object) -> float | None:
    """解析 0-100 的百分比；同时兼容 0-1 的小数比例。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = clean_text(value)
        if not text:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if match:
            number = float(match.group(1))
        else:
            parsed, _ = parse_price(text)
            if parsed is None:
                return None
            number = parsed
    if number <= 1.0 and "%" not in str(value):
        number *= 100
    if 0 <= number <= 100:
        return round(number, 1)
    return None


_BRAND_HINTS = [
    "Apple", "苹果", "小米", "Xiaomi", "Redmi", "REDMI", "华为", "HUAWEI", "荣耀", "HONOR",
    "联想", "Lenovo", "戴尔", "Dell", "惠普", "HP", "华硕", "ASUS", "宏碁", "Acer",
    "索尼", "Sony", "三星", "Samsung", "OPPO", "vivo", "一加", "OnePlus", "魅族",
    "罗技", "Logitech", "雷蛇", "Razer", "樱桃", "CHERRY", "戴森", "Dyson",
    "宜家", "IKEA", "耐克", "Nike", "阿迪达斯", "Adidas", "优衣库", "UNIQLO",
    "美的", "Midea", "格力", "GREE", "海尔", "Haier", "西门子", "Siemens", "松下", "Panasonic",
    "Anker", "ANKER", "贝尔金", "Belkin", "绿联", "UGREEN", "倍思", "Baseus",
    "英特尔", "Intel", "AMD", "英伟达", "NVIDIA", "高通", "Qualcomm",
]


def guess_brand(*texts: str) -> str:
    """从名称/描述里识别品牌；识别不到返回空字符串（不猜）。"""
    for text in texts:
        if not text:
            continue
        head = clean_text(text)[:40]
        for brand in _BRAND_HINTS:
            if brand.lower() in head.lower():
                return brand
    return ""


# 注意：中文紧邻英文/数字时 \b 不成立，因此统一用「非字母数字」的后行断言
_MODEL_PATTERNS = [
    r"(?<![A-Za-z])(?:iPhone|iPad|MacBook|Galaxy|ThinkPad|MateBook|RedmiBook|MagicBook)\s?[\w\s]{0,12}",
    r"(?<![A-Za-z0-9])[A-Z]{1,6}[- ]?\d{2,5}[A-Za-z]{0,4}(?![0-9])",   # 例如 A22 / GTX1660 / RTX4070
    r"第[一二三四五六七八九十\d]+代",
    r"(?:Pro|Max|Plus|Ultra|Air|Mini|Lite|SE|青春版|标准版|尊享版|典藏版|豪华版)",
    r"\d{1,4}\s?(?:GB|TB|寸|英寸|cm|ml)",
]


def guess_model(name: str) -> str:
    """抽取型号/版本片段；找不到就返回空字符串。"""
    text = clean_text(name)
    if not text:
        return ""
    found: list[str] = []
    for pattern in _MODEL_PATTERNS:
        for match in re.findall(pattern, text, flags=re.I):
            token = clean_text(match)
            if token and token.lower() not in {x.lower() for x in found}:
                found.append(token)
    # 去掉被更长片段包含的冗余项（例如 "iPhone 16 Pro Max" 里再拆出的 "Pro"）
    trimmed = [t for t in found if not any(t != other and t.lower() in other.lower() for other in found)]
    return " / ".join(trimmed[:4])


_PUNCT = re.compile(r"[\s\-_·•,，.。:：;；!！?？/\\|｜()（）\[\]【】<>《》\"'“”‘’+*]+")


def norm_key(text: str) -> str:
    """用于去重/匹配的归一化键：全角转半角、去标点与空白、转小写。"""
    return _PUNCT.sub("", clean_text(text).lower())


def similarity(a: str, b: str) -> float:
    """基于字符二元组的 Dice 相似度，0-1。短字符串也能给出稳定结果。"""
    ka, kb = norm_key(a), norm_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    if len(ka) < 2 or len(kb) < 2:
        return 0.0
    grams_a = {ka[i: i + 2] for i in range(len(ka) - 1)}
    grams_b = {kb[i: i + 2] for i in range(len(kb) - 1)}
    overlap = len(grams_a & grams_b)
    return round(2 * overlap / (len(grams_a) + len(grams_b)), 3)


def same_product(left, right, threshold: float = 0.86) -> bool:
    """判断两个 Product 是否为同款（同平台同 id，或名称+品牌高度一致）。"""
    if getattr(left, "product_id", None) and left.product_id == getattr(right, "product_id", None):
        return True
    if left.platform != getattr(right, "platform", None):
        return False
    if left.brand and right.brand and left.brand.lower() != right.brand.lower():
        return False
    return similarity(left.name, right.name) >= threshold


def dedupe_products(products: Iterable) -> list:
    """按 product_id 去重，保留最后一次出现的对象。"""
    seen: dict[str, object] = {}
    for item in products:
        seen[item.product_id] = item
    return list(seen.values())
