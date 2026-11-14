"""从允许访问的公开商品页提取标准商品数据。不破解验证码、登录或反爬机制。"""
from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BOT_UA = "PersonalProductComparator/1.0 (local educational price comparison)"
TIMEOUT = 12


class ScrapeError(Exception):
    """可展示给用户的网页抓取错误。"""


def check_url(url: str) -> str:
    url = url.strip()
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ScrapeError("请输入完整的 http:// 或 https:// 商品链接")
    hostname = parts.hostname.lower()
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        raise ScrapeError("不允许抓取内网/本地地址")
    try:
        if not ipaddress.ip_address(hostname).is_global:
            raise ScrapeError("不允许抓取内网/本地地址")
    except ValueError:
        pass
    return url


@lru_cache(maxsize=100)
def check_robots(origin: str, path_url: str) -> None:
    """无 robots 文件可继续；明确禁止或检查失败则不抓取。"""
    robots_url = urljoin(origin, "/robots.txt")
    try:
        response = requests.get(
            robots_url, headers={"User-Agent": BOT_UA}, timeout=8,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise ScrapeError(f"无法核对 robots.txt，已停止抓取：{exc}") from exc
    if response.status_code in (404, 410):
        return
    if response.status_code != 200:
        raise ScrapeError(f"robots.txt 返回 {response.status_code}，未确认允许抓取")
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    if not parser.can_fetch(BOT_UA, path_url):
        raise ScrapeError("该网站的 robots.txt 不允许此路径被抓取")


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", "").replace("￥", "").replace("¥", "").replace("$", "")
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    return float(match.group(0)) if match else None


def _integer(value):
    n = _num(value)
    return max(0, int(n)) if n is not None else None


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("text"))
    return ""


def _product_nodes(obj):
    if isinstance(obj, list):
        for item in obj:
            yield from _product_nodes(item)
    elif isinstance(obj, dict):
        kinds = obj.get("@type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if any(str(kind).split("/")[-1].lower() == "product" for kind in kinds):
            yield obj
        for key in ("@graph", "mainEntity", "itemListElement", "hasVariant"):
            if key in obj:
                yield from _product_nodes(obj[key])


def _meta(soup, *, names=(), props=(), itemprops=()):
    for attr, vals in (("property", props), ("name", names), ("itemprop", itemprops)):
        for value in vals:
            tag = soup.find("meta", attrs={attr: value})
            if tag and tag.get("content"):
                return tag["content"].strip()
    for value in itemprops:
        tag = soup.find(attrs={"itemprop": value})
        if tag:
            return tag.get("content") or tag.get_text(" ", strip=True)
    return None


def _extract_offer(offer):
    if isinstance(offer, list):
        options = [_extract_offer(x) for x in offer]
        options = [x for x in options if x[0] is not None]
        if not options:
            return None, None
        # 不混合不同货币；多报价时选同一币种的最低公开标价。
        currency = options[0][1]
        same_currency = [x for x in options if x[1] == currency]
        return min(same_currency, key=lambda x: x[0])
    if not isinstance(offer, dict):
        return None, None
    value = offer.get("price")
    if value is None:
        value = offer.get("lowPrice")
    if value is None and isinstance(offer.get("priceSpecification"), dict):
        value = offer["priceSpecification"].get("price")
    currency = offer.get("priceCurrency")
    if not currency and isinstance(offer.get("priceSpecification"), dict):
        currency = offer["priceSpecification"].get("priceCurrency")
    return _num(value), _text(currency).upper() or None


def parse_product_html(html: str, url: str) -> dict:
    """只读结构化商品数据或明确的产品价格元标签，不猜测营销文案中的价格。"""
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "商品": "", "平台": urlparse(url).netloc.lower().removeprefix("www."),
        "链接": url, "价格": None, "币种": "", "评分": None, "满分": None,
        "评价数": None, "好评率%": None, "实用度(1-10)": None,
        "核心参数/备注": "", "来源": "网页提取", "采集状态": "", "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    candidates = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
            candidates.extend(_product_nodes(data))
        except (ValueError, TypeError):
            continue
    for product in candidates:
        name = _text(product.get("name"))
        if name:
            result["商品"] = name
        price, currency = _extract_offer(product.get("offers"))
        if price is not None and price > 0:
            result["价格"] = price
        if currency:
            result["币种"] = currency
        rating = product.get("aggregateRating")
        if isinstance(rating, list):
            rating = next((x for x in rating if isinstance(x, dict)), None)
        if isinstance(rating, dict):
            score = _num(rating.get("ratingValue"))
            best = _num(rating.get("bestRating"))
            if best is None and score is not None and 0 < score <= 5:
                best = 5.0  # 常见的 5 分制；无法确认时仍可手动调整
            if score is not None and best is not None and best > 0 and 0 <= score <= best:
                result["评分"] = score
                result["满分"] = best
            count = _integer(rating.get("ratingCount") or rating.get("reviewCount"))
            if count is not None:
                result["评价数"] = count
        if result["商品"] and result["价格"] is not None:
            break

    if not result["商品"]:
        result["商品"] = (_meta(soup, props=("og:title",), names=("twitter:title",)) or
                        (soup.title.get_text(" ", strip=True) if soup.title else "未识别商品名称"))[:180]
    if result["价格"] is None:
        val = _meta(soup,
                    props=("product:price:amount", "og:price:amount"),
                    names=("product:price:amount", "twitter:data1"),
                    itemprops=("price",))
        parsed = _num(val)
        if parsed is not None and parsed > 0:
            result["价格"] = parsed
    if not result["币种"]:
        result["币种"] = (_meta(soup, props=("product:price:currency", "og:price:currency"),
                                   itemprops=("priceCurrency",)) or "").upper()
    if result["价格"] is not None and not result["币种"]:
        # 不擅自将无币种价格理解成人民币，留待手工确认。
        result["采集状态"] = "已抓取价格，但币种未知，需补录"
    missing = []
    if result["价格"] is None:
        missing.append("价格")
    if result["评分"] is None:
        missing.append("评分")
    if missing:
        result["采集状态"] = "待补录：" + "、".join(missing)
    elif not result["采集状态"]:
        result["采集状态"] = "已提取；请核对优惠券与规格"
    return result


def scrape(url: str, browser: bool = False) -> dict:
    url = check_url(url)
    check_robots(_origin(url), url)
    if browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ScrapeError("浏览器模式未安装：pip install playwright && playwright install chromium") from exc
        try:
            with sync_playwright() as p:
                instance = p.chromium.launch(headless=True)
                try:
                    page = instance.new_page(user_agent=BOT_UA, locale="zh-CN")
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    if resp and resp.status in (401, 403, 429):
                        raise ScrapeError(f"网页返回 {resp.status}，不尝试绕过限制")
                    if resp and resp.status >= 400:
                        raise ScrapeError(f"网页返回 {resp.status}")
                    page.wait_for_timeout(1000)
                    final_url = check_url(page.url)
                    check_robots(_origin(final_url), final_url)
                    html = page.content()
                finally:
                    instance.close()
        except ScrapeError:
            raise
        except Exception as exc:
            raise ScrapeError(f"浏览器抓取失败：{str(exc)[:180]}") from exc
    else:
        try:
            response = requests.get(url, headers={"User-Agent": BOT_UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
                                    timeout=TIMEOUT, allow_redirects=True)
            if response.status_code in (401, 403, 429):
                raise ScrapeError(f"网页返回 {response.status_code}，不尝试绕过限制")
            response.raise_for_status()
            final_url = check_url(response.url)
            check_robots(_origin(final_url), final_url)
            html = response.text
        except ScrapeError:
            raise
        except requests.RequestException as exc:
            raise ScrapeError(f"网络访问失败：{str(exc)[:160]}") from exc
    product = parse_product_html(html, final_url)
    return product
