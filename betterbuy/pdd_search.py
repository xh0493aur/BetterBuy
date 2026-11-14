"""拼多多关键词搜索：把 ``pdd.ddk.goods.search`` 的返回接进统一比较流程。

这一层只做三件事，不做任何"看起来好看"的加工：
1. 调接口、把失败翻译成**能据此行动**的分类（凭证 / 权限 / 备案 / 限流 / 无结果）；
2. 把返回体映射成统一的 :class:`~betterbuy.models.Product`，缺什么标什么；
3. 需要时再调一次详情接口补字段、或调转链接口拿**官方**推广链接。

价格口径写在 ``Offer.terms`` 里（拼团价 / 券后估算 / 不含运费），
因为拼多多返回的是拼团价，直接和各平台比价会有偏差。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .adapters.pdd import product_from_pdd_goods
from .models import Product, Source
from .openapi import ApiError, PDDClient
from .openapi.fields import fen_to_yuan, pick
from .openapi.pdd import METHODS, clamp_page_size

__all__ = ["SearchResult", "KIND_LABEL", "KIND_HINT", "search", "attach_links",
           "explain_error"]

#: 面向用户的失败分类
KIND_LABEL: dict[str, str] = {
    "input": "输入需要调整",
    "no_credential": "未配置密钥",
    "auth": "凭证或签名无效",
    "permission": "接口权限不足",
    "authorize": "需要先做授权备案",
    "quota": "限流或超出每日上限",
    "network": "网络不通或超时",
    "http": "网关返回异常",
    "api": "接口业务错误",
    "empty": "没有匹配的商品",
}

KIND_HINT: dict[str, str] = {
    "input": "改一下输入框里的内容就行，这一条不算接口错误，也没有发出任何请求。",
    "no_credential": (
        "没读到拼多多密钥。需要 BETTERBUY_PDD_CLIENT_ID、BETTERBUY_PDD_CLIENT_SECRET、"
        "BETTERBUY_PDD_PID 三项；云端请在 Streamlit → Settings → Secrets 里填，不要写进代码。"
    ),
    "auth": (
        "凭证无效或签名不一致（常见错误码 10016 client_id 不正确、20004 签名校验失败）。"
        "检查 client_id/client_secret 是否复制完整、首尾有没有空格；"
        "timestamp 必须是秒级 UNIX 时间。"
    ),
    "permission": (
        "应用没有这个接口的权限（20031 用户没有授权访问此接口 / 30000 无接口权限），"
        "或服务器 IP 不在白名单（20005）。到拼多多开放平台为应用申请 pdd.ddk.goods.* 权限包。"
    ),
    "authorize": (
        "密钥和签名是对的，但推广位（pid）还没在多多进宝完成「授权备案」，所以关键词搜索被拒（50001/60001）。"
        "按顺序做四步：① 用与 client_id 同一个账号登录 jinbao.pinduoduo.com；"
        "② 推广位管理里确认这个 pid 存在且状态正常（形如 1234567_98765432）；"
        "③ 用该 pid 生成一条推广链接并在浏览器里打开一次，完成备案；"
        "④ 若 Secrets 里配了 BETTERBUY_PDD_CUSTOM_PARAMETERS，它必须与备案时一致，拿不准就先清空。"
    ),
    "quota": (
        "触发平台限流（52101 接口被限流 / 70031 调用过于频繁），或达到本项目自设的每日上限。"
        "等一会儿再试；本项目超限时不会发出请求，也不会产生费用。"
    ),
    "network": "请求没能到达网关：检查网络、代理、DNS，或稍后重试。",
    "api": "接口返回了业务错误，原始信息在下面，按错误码对照表处理。",
    "empty": (
        "接口调用成功但没有商品。常见原因：该关键词在多多进宝推广池里没有货、"
        "pid 与备案参数不匹配、或关键词太偏。换一个更常见的词再试。"
    ),
}


@dataclass
class SearchResult:
    """一次关键词搜索的结果（含失败信息，UI 直接照着渲染即可）。"""

    ok: bool
    keyword: str
    products: list[Product] = field(default_factory=list)
    total: int | None = None
    search_id: str = ""
    kind: str = ""
    message: str = ""
    hint: str = ""
    code: str = ""
    sub_code: str = ""
    api_calls: int = 0
    used_api: str = METHODS["goods_search"]

    @property
    def kind_label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind or "—")


def explain_error(exc: ApiError) -> tuple[str, str]:
    """把 ApiError 翻译成 (分类中文, 可执行建议)。"""
    kind = exc.kind if exc.kind in KIND_LABEL else "api"
    hint = KIND_HINT.get(kind, "")
    if exc.kind == "no_credential":
        hint = exc.message + " " + hint
    return KIND_LABEL[kind], hint


def search(keyword: str, *, limit: int = 10, sort_type: int | None = None,
           with_coupon: bool | None = None, detail_count: int = 0,
           client: PDDClient | None = None) -> SearchResult:
    """关键词搜索。

    :param limit: 返回条数，会被夹到官方的 10–100
    :param sort_type: 0=综合，1=销量（其他值官方文档未明确，不猜）
    :param detail_count: 额外读前 N 条详情（每条一次调用，会消耗配额）
    """
    text = (keyword or "").strip()
    if not text:
        return SearchResult(ok=False, keyword="", kind="input",
                            message="关键词是空的。", hint="填一个商品关键词，例如「机械键盘 87 键」。")
    if "http://" in text or "https://" in text:
        return SearchResult(
            ok=False, keyword=text, kind="input",
            message="这看起来是一个商品链接，不是关键词。",
            hint="贴链接请用「② 自动识别与智能比价」里的链接输入框；"
                 "本输入框走的是关键词搜索接口。",
        )

    client = client or PDDClient()
    if not client.available:
        missing = "、".join(client.cred.missing) or "BETTERBUY_PDD_CLIENT_ID / CLIENT_SECRET / PID"
        return SearchResult(
            ok=False, keyword=text, kind="no_credential",
            message=f"拼多多接口未配置密钥，缺少：{missing}",
            hint=KIND_HINT["no_credential"],
        )

    api_calls = 0
    try:
        page = client.search_page(keyword=text, page_size=clamp_page_size(limit),
                                  sort_type=sort_type, with_coupon=with_coupon)
        api_calls += 1
    except ApiError as exc:
        label, hint = explain_error(exc)
        return SearchResult(ok=False, keyword=text, kind=exc.kind, message=exc.message,
                            hint=hint, code=exc.code, sub_code=exc.sub_code,
                            api_calls=api_calls)

    goods = page.get("goods_list") or []
    if not goods:
        return SearchResult(ok=False, keyword=text, kind="empty",
                            message=f"「{text}」在多多进宝推广池里没有搜到商品。",
                            hint=KIND_HINT["empty"], total=page.get("total"),
                            search_id=page.get("search_id", ""), api_calls=api_calls)

    products: list[Product] = []
    seen: set[str] = set()
    for item in goods:
        goods_id = str(pick(item, "goods_id") or "")
        product = product_from_pdd_goods(item, goods_id=goods_id, url="")
        if product.product_id in seen:
            continue
        seen.add(product.product_id)
        product.origin = "search"
        product.status = "拼多多关键词搜索结果"
        products.append(product)

    if detail_count > 0:
        api_calls += _enrich_details(client, products, detail_count, page.get("search_id", ""))

    return SearchResult(ok=True, keyword=text, products=products, total=page.get("total"),
                        search_id=page.get("search_id", ""), api_calls=api_calls,
                        message=f"搜到 {len(products)} 件商品（接口共 {page.get('total') or '未返回'} 条）。")


def _enrich_details(client: PDDClient, products: list[Product], count: int,
                    search_id: str) -> int:
    """给前 N 条补详情字段。失败只记录，不影响整体结果。"""
    calls = 0
    for product in products[:max(0, count)]:
        sign = product.specs.get("goods_sign", "")
        if not sign:
            continue
        try:
            items = client.goods_detail(goods_sign=sign, search_id=search_id)
            calls += 1
        except ApiError as exc:
            product.notes.append(f"详情接口未取到（{exc.kind}）：{exc.message[:120]}")
            continue
        if items:
            # 只补空字段，不覆盖搜索接口已经给出的值
            _merge_detail(product, items[0], api=METHODS["goods_detail"])
    return calls


def _merge_detail(product: Product, item: dict[str, Any], *, api: str = "") -> None:
    if not product.image_url:
        url = pick(item, "goods_image_url", "goods_thumbnail_url")
        if url:
            product.image_url = str(url)
    if not product.category:
        cat = pick(item, "opt_name", "category_name")
        if cat:
            product.category = str(cat)
    desc = str(pick(item, "goods_desc") or "").strip()
    if desc and not product.summary:
        product.summary = desc
    specs = dict(product.specs)
    shop = pick(item, "mall_name")
    if shop and "店铺" not in specs:
        specs["店铺"] = str(shop)
    coupon = fen_to_yuan(pick(item, "coupon_discount"))
    if coupon and "优惠券" not in specs:
        specs["优惠券"] = f"{coupon:g} 元"
    if specs != product.specs:
        product.specs = specs
    product.recompute_missing()
    product.notes.append(f"部分字段来自 {api}（按 goods_sign 查询）。")


def attach_links(products: Iterable[Product], *, client: PDDClient | None = None,
                 limit: int = 10) -> tuple[int, str]:
    """调官方转链接口，为商品补上**真实**推广链接。

    返回 ``(成功条数, 说明文字)``。没有权限、没配置 pid、接口报错时
    一律如实返回 0 条 + 原因，**不拼接任何替代链接**。
    """
    items = list(products)
    signs = [p.specs.get("goods_sign", "") for p in items if p.specs.get("goods_sign")]
    if not signs:
        return 0, "这些商品没有 goods_sign，官方转链接口无法使用。"
    if len(signs) > limit:
        signs = signs[:limit]

    client = client or PDDClient()
    if not client.available:
        return 0, "未配置拼多多密钥，无法生成推广链接。"
    try:
        urls = client.promotion_url_generate(goods_sign_list=signs)
    except ApiError as exc:
        label, hint = explain_error(exc)
        return 0, f"转链接口失败（{label}）：{exc.message} {hint}"

    mapping: dict[str, str] = {}
    for item in urls:
        sign = str(pick(item, "goods_sign") or "")
        url = pick(item, "mobile_url", "mobile_short_url", "we_app_web_view_url",
                   "short_url", "url")
        if sign and url:
            mapping[sign] = str(url)

    filled = 0
    for product in items:
        url = mapping.get(product.specs.get("goods_sign", ""))
        if not url:
            continue
        product.url = url
        product.add_source("pdd_link", _link_source(), ["url"])
        product.notes.append(
            "链接由官方 pdd.ddk.goods.promotion.url.generate 生成，含推广参数；"
            "同一商品在不同账号/推广位下链接可能不同。"
        )
        filled += 1
    if not filled:
        return 0, "接口调用成功，但返回里没有可用链接（可能该商品已下架或禁止转链）。"
    return filled, f"已为 {filled}/{len(signs)} 个商品补上官方推广链接。"


def _link_source() -> Source:
    return Source(adapter="pdd_ddk_api", label="拼多多多多进宝开放接口",
                  url=f"https://gw-api.pinduoduo.com/api/router（{METHODS['promotion_url']}）",
                  method="官方开放接口")
