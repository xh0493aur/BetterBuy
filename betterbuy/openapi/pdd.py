"""拼多多（多多进宝 / PDD 开放平台）客户端。

网关与协议（官方文档 open.pinduoduo.com/application/document/browse?idStr=8EC06C399636041E）：
- 网关 ``https://gw-api.pinduoduo.com/api/router``，**只允许 POST**；
- 公共参数 type / client_id / timestamp（秒级 UNIX）/ data_type=JSON / version=V1 / sign；
- 签名：参数按名升序拼 ``k1v1k2v2``，首尾各加 client_secret，MD5 后转大写。

接口口径同样是推广（多多进宝）：
- ``pdd.ddk.goods.search`` 关键词搜索，``page_size`` 官方只接受 **10–100**；
  传了 ``keyword`` 就必须带**已授权备案**的 ``pid``（必要时还有 ``custom_parameters``），
  否则返回 ``error_code=50001, sub_code=60001``；
- ``pdd.ddk.goods.detail`` 新版按 ``goods_sign`` 查（``goods_id`` 已下线）；
- ``pdd.ddk.goods.promotion.url.generate`` 生成官方推广链接，需要 ``pid`` 权限；
- 价格单位为**分**，返回 ``min_group_price`` / ``min_normal_price``；
- 不返回好评率，也不返回评价正文，只有 ``sales_tip``（已售件数文案）。
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Iterable

from .credentials import Credential, load
from .errors import ApiError
from .fields import to_int, pick
from .signing import canon, pdd_sign
from .transport import Transport, post, raise_if_error, unwrap

__all__ = ["GATEWAY", "PDDClient", "METHODS", "PAGE_MIN", "PAGE_MAX", "clamp_page_size",
           "normalize_custom_parameters", "ERROR_CODE_TABLE"]

GATEWAY = "https://gw-api.pinduoduo.com/api/router"

#: 官方错误码对照（2026-11 核对 open.pinduoduo.com「返回错误码说明」）。
#: 只列本项目真会碰到的几类，用于界面展示与命令行自检。
ERROR_CODE_TABLE: list[dict[str, str]] = [
    {"错误码": "10016", "含义": "client 下线或 clientId 不正确", "分类": "凭证错误",
     "怎么处理": "核对 BETTERBUY_PDD_CLIENT_ID，注意别带空格"},
    {"错误码": "10019", "含义": "access_token 已过期", "分类": "凭证错误",
     "怎么处理": "重新授权或刷新 token；不用 token 的接口可删掉该项配置"},
    {"错误码": "20004", "含义": "签名 sign 校验失败", "分类": "凭证错误",
     "怎么处理": "client_secret 是否正确、参数是否按名升序且值原样参与签名"},
    {"错误码": "20031", "含义": "用户没有授权访问此接口", "分类": "权限不足",
     "怎么处理": "在开放平台为应用申请 pdd.ddk.goods.* 权限包"},
    {"错误码": "30000", "含义": "没有调用此 target 接口权限", "分类": "权限不足",
     "怎么处理": "同上，检查应用类型与权限包"},
    {"错误码": "20005", "含义": "IP 无权访问接口", "分类": "权限不足",
     "怎么处理": "把服务器出口 IP 加入开放平台白名单（云端 IP 不固定时不要开白名单）"},
    {"错误码": "50001 / 60001", "含义": "关键词搜索未传已授权备案的 pid/custom_parameters",
     "分类": "需要授权备案",
     "怎么处理": "去多多进宝用转链接口生成备案链接并访问一次，再带同样的 pid 调用"},
    {"错误码": "52101 / 70031", "含义": "接口被限流 / 调用过于频繁", "分类": "限流",
     "怎么处理": "降低频率稍后重试；本项目另有每日上限，超限时请求不会发出"},
    {"错误码": "成功但 goods_list 为空", "含义": "推广池里没有匹配商品",
     "分类": "无结果", "怎么处理": "换关键词；确认该商品确实加入了多多进宝"},
]

METHODS = {
    "goods_search": "pdd.ddk.goods.search",
    "goods_detail": "pdd.ddk.goods.detail",
    "goods_recommend": "pdd.ddk.goods.recommend.get",
    "promotion_url": "pdd.ddk.goods.promotion.url.generate",
}

#: 官方对 ddk 搜索的分页硬约束，超出范围会直接报参数错误
PAGE_MIN = 10
PAGE_MAX = 100


def clamp_page_size(value: Any, default: int = 20) -> int:
    """把 page_size 夹到官方允许的 10–100；非数字用默认值。"""
    number = to_int(value)
    if number is None:
        number = default
    return max(PAGE_MIN, min(PAGE_MAX, number))


def normalize_custom_parameters(raw: str) -> str:
    """把配置里的自定义参数规范成 JSON 字符串（原样是 JSON 就不动）。

    官方要求 ``custom_parameters`` 是 ``{"uid":"..."}`` 形式的 JSON 文本，
    并且必须与 pid 一起完成授权备案；这里只做格式规范，不做任何补全。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    if text[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(parsed, dict):
            return ""
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    # 只填了一个 uid 的简写，包成官方要求的对象
    return json.dumps({"uid": text}, ensure_ascii=False, separators=(",", ":"))


class PDDClient:
    def __init__(self, cred: Credential | None = None,
                 post_fn: Callable[..., Transport] = post) -> None:
        self.cred = cred or load("pdd")
        self._post = post_fn
        self.last: Transport | None = None

    @property
    def available(self) -> bool:
        return self.cred.configured

    @property
    def pid(self) -> str:
        return self.cred.get("BETTERBUY_PDD_PID")

    @property
    def custom_parameters(self) -> str:
        return normalize_custom_parameters(self.cred.get("BETTERBUY_PDD_CUSTOM_PARAMETERS"))

    def call(self, api_type: str, biz: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise ApiError(
                "拼多多接口未配置密钥，无法调用。请设置 " + "、".join(self.cred.missing) + "。",
                kind="no_credential",
            )
        params: dict[str, Any] = {
            "type": api_type,
            "client_id": self.cred.get("BETTERBUY_PDD_CLIENT_ID"),
            "timestamp": str(int(time.time())),
            "data_type": "JSON",
            "version": "V1",
        }
        token = self.cred.get("BETTERBUY_PDD_ACCESS_TOKEN")
        if token:
            params["access_token"] = token
        for key, value in (biz or {}).items():
            if value is None or value == "" or value == []:
                continue
            # 先生成最终文本再签名，保证签名串与请求体完全一致
            params[key] = canon(value)
        params["sign"] = pdd_sign(params, self.cred.get("BETTERBUY_PDD_CLIENT_SECRET"))

        result = self._post(GATEWAY, params, platform="pdd")
        self.last = result
        raise_if_error(result, platform="拼多多")
        return unwrap(result)

    # ---------------- 搜索 ----------------
    def search_page(self, *, keyword: str | None = None, pid: str | None = None,
                    page: int = 1, page_size: int = 20, sort_type: int | None = None,
                    with_coupon: bool | None = None, list_id: str | None = None,
                    cat_id: str | None = None, range_list: Iterable[dict] | None = None,
                    custom_parameters: str | None = None) -> dict[str, Any]:
        """一次搜索，返回 ``goods_list`` / ``total`` / ``search_id`` / 原始返回体。

        与 :meth:`goods_search` 的区别：这里把 ``search_id`` 一起给出来，
        查详情时带上它能提高接口侧的匹配质量（官方建议填写）。
        """
        biz: dict[str, Any] = {
            "pid": pid or self.pid,
            "page": page,
            "page_size": clamp_page_size(page_size),
        }
        if keyword:
            biz["keyword"] = keyword
        if sort_type is not None:
            biz["sort_type"] = sort_type
        if with_coupon is not None:
            biz["with_coupon"] = with_coupon
        if list_id:
            biz["list_id"] = list_id
        if cat_id:
            biz["cat_id"] = cat_id
        if range_list:
            biz["range_list"] = list(range_list)
        custom = normalize_custom_parameters(custom_parameters) if custom_parameters else self.custom_parameters
        if custom:
            biz["custom_parameters"] = custom

        body = self.call(METHODS["goods_search"], biz)
        goods = _as_list(body.get("goods_list"))
        return {
            "goods_list": goods,
            "total": to_int(pick(body, "total_count", "total")),
            "search_id": str(pick(body, "search_id") or ""),
            "raw": body,
        }

    def goods_search(self, *, keyword: str | None = None, pid: str | None = None,
                     page: int = 1, page_size: int = 20, sort_type: int | None = None,
                     with_coupon: bool | None = None,
                     list_id: str | None = None) -> list[dict[str, Any]]:
        """多多进宝商品搜索。keyword 支持商品关键词、goods_id 或拼多多商品链接。"""
        return self.search_page(
            keyword=keyword, pid=pid, page=page, page_size=page_size,
            sort_type=sort_type, with_coupon=with_coupon, list_id=list_id,
        )["goods_list"]

    # ---------------- 详情 ----------------
    def goods_detail(self, *, goods_sign: str | None = None,
                     goods_sign_list: Iterable[str] | None = None,
                     goods_id_list: Iterable[Any] | None = None,
                     pid: str | None = None, search_id: str = "",
                     need_sku_info: bool = False,
                     goods_img_type: int | None = None) -> list[dict[str, Any]]:
        """商品详情。

        新版按 ``goods_sign``（单个或列表）查询，``goods_id`` 已下线；
        为了兼容旧调用方仍接受 ``goods_id_list``（走旧版参数，可能报错）。
        """
        biz: dict[str, Any] = {"pid": pid or self.pid}
        if goods_sign:
            biz["goods_sign"] = goods_sign
        elif goods_sign_list:
            biz["goods_sign_list"] = [str(s) for s in goods_sign_list if s]
        elif goods_id_list:
            biz["goods_id_list"] = [str(g) for g in goods_id_list if g]
        if search_id:
            biz["search_id"] = search_id
        if need_sku_info:
            biz["need_sku_info"] = True
        if goods_img_type is not None:
            biz["goods_img_type"] = goods_img_type
        if self.custom_parameters:
            biz["custom_parameters"] = self.custom_parameters

        body = self.call(METHODS["goods_detail"], biz)
        return _as_list(body.get("goods_detail_list") or body.get("goods_details")
                        or body.get("goods_list"))

    # ---------------- 推广链接 ----------------
    def promotion_url_generate(self, *, goods_sign_list: Iterable[str],
                               pid: str | None = None,
                               custom_parameters: str | None = None,
                               generate_short_url: bool = False,
                               generate_authority_url: bool = False) -> list[dict[str, Any]]:
        """生成官方推广链接（含推广参数），需要 pid 对应的转链权限。

        没有权限时网关会报错，调用方必须如实展示错误，**不要拼接任何链接兜底**。
        """
        signs = [str(s) for s in goods_sign_list if s]
        biz: dict[str, Any] = {"pid": pid or self.pid, "goods_sign_list": signs}
        if generate_short_url:
            biz["generate_short_url"] = True
        if generate_authority_url:
            # 官方备案流程需要它：生成的链接先给用户点一次完成授权
            biz["generate_authority_url"] = True
        custom = normalize_custom_parameters(custom_parameters) if custom_parameters else self.custom_parameters
        if custom:
            biz["custom_parameters"] = custom

        body = self.call(METHODS["promotion_url"], biz)
        return _as_list(body.get("goods_promotion_url_list") or body.get("url_list"))

    # ---------------- 推荐 ----------------
    def goods_recommend(self, *, channel_type: int = 0, limit: int = 20,
                        pid: str | None = None, cat_id: str | None = None,
                        offset: int | None = None) -> list[dict[str, Any]]:
        """商品推荐（按渠道类型取推荐列表）。"""
        biz: dict[str, Any] = {"pid": pid or self.pid, "channel_type": channel_type,
                               "limit": max(1, min(limit, 100))}
        if cat_id:
            biz["cat_id"] = cat_id
        if offset is not None:
            biz["offset"] = offset
        body = self.call(METHODS["goods_recommend"], biz)
        return _as_list(body.get("goods_list"))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []
