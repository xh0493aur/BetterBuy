"""淘宝/天猫（淘宝开放平台 TOP）客户端。

网关与协议（2026-11 核实）：
- 正式网关 ``https://gw.api.taobao.com/router/rest``（``eco.taobao.com/router/rest``
  同样可用，作为备用地址）；
- 公共参数 method / app_key / timestamp / format=json / v=2.0 / sign_method / sign；
- 返回体外壳为 ``<method 点换下划线>_response``，错误为 ``error_response``。

三个接口都是**淘宝客（淘宝联盟）口径**，不是淘宝商家的商品库：
- ``taobao.tbk.dg.material.optional.upgrade`` 物料搜索，``adzone_id``（推广位）必填；
- ``taobao.tbk.item.info.upgrade.get`` 商品详情简易版；
- ``taobao.tbk.item.details.upgrade.get`` 商品详情详细版（含 SKU/库存）。

关键限制（官方文档原文）：
- ``q`` 参数不支持直接传入淘宝联盟链接；
- 含数字 ID 的链接需要「消费者比价场景 ID2」权限；
- 好评率只有布尔值 ``h_good_rate``（是否高于行业均值），且属 SA 级字段，
  需联系商务/运营小二申请——**拿不到具体好评百分比**。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .credentials import Credential, load
from .errors import ApiError
from .signing import taobao_sign
from .transport import Transport, post, raise_if_error, unwrap

__all__ = ["GATEWAY", "GATEWAY_FALLBACK", "TaobaoClient", "METHODS"]

GATEWAY = "https://gw.api.taobao.com/router/rest"
GATEWAY_FALLBACK = "https://eco.taobao.com/router/rest"
_CN_TZ = timezone(timedelta(hours=8))

METHODS = {
    "material_search": "taobao.tbk.dg.material.optional.upgrade",
    "item_info": "taobao.tbk.item.info.upgrade.get",
    "item_details": "taobao.tbk.item.details.upgrade.get",
}


class TaobaoClient:
    def __init__(self, cred: Credential | None = None,
                 post_fn: Callable[..., Transport] = post,
                 gateway: str = GATEWAY) -> None:
        self.cred = cred or load("taobao")
        self._post = post_fn
        self.gateway = gateway
        self.last: Transport | None = None

    @property
    def available(self) -> bool:
        return self.cred.configured

    @property
    def adzone_id(self) -> str:
        return self.cred.get("BETTERBUY_TB_ADZONE_ID")

    # ---------------- 底层 ----------------
    def call(self, method: str, biz: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise ApiError(
                "淘宝接口未配置密钥，无法调用。请设置 " + "、".join(self.cred.missing) + "。",
                kind="no_credential",
            )
        params: dict[str, Any] = {
            "method": method,
            "app_key": self.cred.get("BETTERBUY_TB_APP_KEY"),
            "timestamp": datetime.now(_CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "2.0",
            "sign_method": "md5",
        }
        session = self.cred.get("BETTERBUY_TB_SESSION")
        if session:
            params["session"] = session
        for key, value in (biz or {}).items():
            if value is None or value == "":
                continue
            params[key] = value
        params["sign"] = taobao_sign(params, self.cred.get("BETTERBUY_TB_APP_SECRET"))

        result = self._post(self.gateway, params, platform="taobao")
        self.last = result
        raise_if_error(result, platform="淘宝")
        return unwrap(result)

    # ---------------- 业务 ----------------
    def material_search(self, *, q: str | None = None, cat: str | None = None,
                        page_no: int = 1, page_size: int = 20,
                        start_price: float | None = None,
                        end_price: float | None = None,
                        sort: str | None = None,
                        has_coupon: bool | None = None) -> list[dict[str, Any]]:
        """物料搜索（按关键词/类目找淘宝客商品）。adzone_id 必填。"""
        biz: dict[str, Any] = {
            "adzone_id": self.adzone_id,
            "page_no": page_no,
            "page_size": max(1, min(page_size, 100)),
        }
        if q:
            biz["q"] = q
        if cat:
            biz["cat"] = cat
        if start_price is not None:
            biz["start_price"] = start_price
        if end_price is not None:
            biz["end_price"] = end_price
        if sort:
            biz["sort"] = sort
        if has_coupon is not None:
            biz["has_coupon"] = has_coupon

        body = self.call(METHODS["material_search"], biz)
        return _find_list(body, ("result_list", "map_data"), ("result_list",), ("results",))

    def item_info(self, item_id: str) -> list[dict[str, Any]]:
        """商品详情简易版。item_id 可为数字 ID，最多 20 个用逗号分隔。"""
        body = self.call(METHODS["item_info"], {"item_id": item_id, "biz_scene_id": "1"})
        return _find_list(body, ("results", "tbk_item_detail"), ("results",))

    def item_details(self, item_id: str) -> list[dict[str, Any]]:
        """商品详情详细版（含 SKU 与库存）。"""
        body = self.call(METHODS["item_details"], {"item_id": item_id, "biz_scene_id": "1"})
        return _find_list(body, ("results", "tbk_item_detail"), ("results",))


def _find_list(body: dict[str, Any], *paths: tuple[str, ...]) -> list[dict[str, Any]]:
    """按候选路径找列表；找不到就返回空列表，绝不猜。"""
    for path in paths:
        node: Any = body
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list):
            return [item for item in node if isinstance(item, dict)]
        # 不同版本会把列表再包一层，如 results: {"tbk_item_detail": [...]}
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []
