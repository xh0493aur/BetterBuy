"""京东联盟（京东开放平台）客户端。

网关与协议常量来自官方帮助中心 union.jd.com/helpcenter/13246-13312-108188：
- 网关 ``https://api.jd.com/routerjson``，HTTP POST，返回 JSON；
- 公共参数 method / app_key / timestamp / format=json / v=1.0 / sign_method=md5 / sign；
- 业务参数统一放在 ``360buy_param_json`` 里（一个 JSON 字符串）；
- timestamp 格式 ``yyyy-MM-dd HH:mm:ss``，东八区，服务端允许 ±10 分钟。

联盟接口都属于推广口径：只能查到「已进入联盟推广池」的商品，
返回的是推广位可用的价格与佣金信息，不是全站商品库。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .credentials import Credential, load
from .errors import ApiError
from .signing import jd_sign
from .transport import Transport, first_json_string, post, raise_if_error, unwrap

__all__ = ["GATEWAY", "JDClient", "METHODS", "now_gmt8"]

GATEWAY = "https://api.jd.com/routerjson"
_CN_TZ = timezone(timedelta(hours=8))

METHODS = {
    "goods_detail": "jd.union.open.goods.promotiongoodsinfo.query",
    "goods_query": "jd.union.open.goods.query",
    "jingfen": "jd.union.open.goods.jingfen.query",
    "category_goods": "jd.union.open.category.goods.get",
}


def now_gmt8() -> str:
    return datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


class JDClient:
    def __init__(self, cred: Credential | None = None,
                 post_fn: Callable[..., Transport] = post) -> None:
        self.cred = cred or load("jd")
        self._post = post_fn
        self.last: Transport | None = None

    @property
    def available(self) -> bool:
        return self.cred.configured

    # ---------------- 底层 ----------------
    def call(self, method: str, biz: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise ApiError(
                "京东接口未配置密钥，无法调用。请设置 " + "、".join(self.cred.missing) + "。",
                kind="no_credential",
            )
        params: dict[str, Any] = {
            "method": method,
            "app_key": self.cred.get("BETTERBUY_JD_APP_KEY"),
            "timestamp": now_gmt8(),
            "format": "json",
            "v": "1.0",
            "sign_method": "md5",
            "360buy_param_json": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        token = self.cred.get("BETTERBUY_JD_ACCESS_TOKEN")
        if token:
            params["access_token"] = token
        params["sign"] = jd_sign(params, self.cred.get("BETTERBUY_JD_APP_SECRET"))

        result = self._post(GATEWAY, params, platform="jd")
        self.last = result
        raise_if_error(result, platform="京东")
        return first_json_string(unwrap(result), ("queryResult", "getResult", "result"))

    # ---------------- 业务 ----------------
    def goods_detail(self, sku_ids: str) -> list[dict[str, Any]]:
        """jd.union.open.goods.promotiongoodsinfo.query：按 skuId 查详情（含价格与评论数）。"""
        body = self.call(METHODS["goods_detail"], {"skuIds": sku_ids})
        return _as_list(body.get("data") or body.get("resultList") or [])

    def goods_query(self, **goods_req: Any) -> list[dict[str, Any]]:
        """jd.union.open.goods.query：关键词/skuIds 检索联盟商品。"""
        body = self.call(METHODS["goods_query"], {"goodsReqDTO": goods_req})
        return _as_list(body.get("data") or [])

    def jingfen_query(self, **goods_req: Any) -> list[dict[str, Any]]:
        """jd.union.open.goods.jingfen.query：京粉精选商品。"""
        body = self.call(METHODS["jingfen"], {"goodsReq": goods_req})
        return _as_list(body.get("data") or [])

    def category_goods(self, **req: Any) -> list[dict[str, Any]]:
        """jd.union.open.category.goods.get：按类目拉商品。"""
        body = self.call(METHODS["category_goods"], {"req": req})
        return _as_list(body.get("data") or [])


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []
