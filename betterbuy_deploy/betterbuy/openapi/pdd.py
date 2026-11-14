"""拼多多（多多进宝 / PDD 开放平台）客户端。

网关与协议（官方文档 open.pinduoduo.com/application/document/browse?idStr=8EC06C399636041E）：
- 网关 ``https://gw-api.pinduoduo.com/api/router``，**只允许 POST**；
- 公共参数 type / client_id / timestamp（秒级 UNIX）/ data_type=JSON / version=V1 / sign；
- 签名：参数按名升序拼 ``k1v1k2v2``，首尾各加 client_secret，MD5 后转大写。

接口口径同样是推广（多多进宝）：
- ``pdd.ddk.goods.search`` 必填 ``pid``（推广位），``keyword`` 可直接传商品链接；
- 价格单位为**分**，返回 ``min_group_price`` / ``min_normal_price``；
- 不返回好评率，也不返回评价正文，只有 ``sales_tip``（已售件数文案）。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .credentials import Credential, load
from .errors import ApiError
from .signing import canon, pdd_sign
from .transport import Transport, post, raise_if_error, unwrap

__all__ = ["GATEWAY", "PDDClient", "METHODS"]

GATEWAY = "https://gw-api.pinduoduo.com/api/router"

METHODS = {
    "goods_search": "pdd.ddk.goods.search",
    "goods_detail": "pdd.ddk.goods.detail",
    "goods_recommend": "pdd.ddk.goods.recommend.get",
}


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
            if value is None or value == "":
                continue
            # 先生成最终文本再签名，保证签名串与请求体完全一致
            params[key] = canon(value)
        params["sign"] = pdd_sign(params, self.cred.get("BETTERBUY_PDD_CLIENT_SECRET"))

        result = self._post(GATEWAY, params, platform="pdd")
        self.last = result
        raise_if_error(result, platform="拼多多")
        return unwrap(result)

    # ---------------- 业务 ----------------
    def goods_search(self, *, keyword: str | None = None, pid: str | None = None,
                     page: int = 1, page_size: int = 20, sort_type: int | None = None,
                     with_coupon: bool | None = None,
                     list_id: str | None = None) -> list[dict[str, Any]]:
        """多多进宝商品搜索。keyword 支持商品关键词、goods_id 或拼多多商品链接。"""
        biz: dict[str, Any] = {
            "pid": pid or self.pid,
            "page": page,
            "page_size": max(1, min(page_size, 100)),
        }
        if keyword:
            biz["keyword"] = keyword
        if sort_type is not None:
            biz["sort_type"] = sort_type
        if with_coupon is not None:
            biz["with_coupon"] = with_coupon
        if list_id:
            biz["list_id"] = list_id

        body = self.call(METHODS["goods_search"], biz)
        return _as_list(body.get("goods_list"))

    def goods_detail(self, **kwargs: Any) -> list[dict[str, Any]]:
        """商品详情。新旧版参数不同：goods_id_list 或 goods_sign_list，按需传入。"""
        body = self.call(METHODS["goods_detail"], dict(kwargs))
        return _as_list(body.get("goods_detail_list") or body.get("goods_list"))

    def goods_recommend(self, *, channel_type: int = 0, limit: int = 20,
                        pid: str | None = None) -> list[dict[str, Any]]:
        """商品推荐（按渠道类型取推荐列表）。"""
        biz = {"pid": pid or self.pid, "channel_type": channel_type,
               "limit": max(1, min(limit, 100))}
        body = self.call(METHODS["goods_recommend"], biz)
        return _as_list(body.get("goods_list"))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []
