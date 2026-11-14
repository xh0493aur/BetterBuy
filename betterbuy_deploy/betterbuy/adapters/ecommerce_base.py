"""电商开放平台适配器的公共部分。

三个平台适配器（jd / taobao / pdd）都继承 :class:`EcommerceAdapter`，
各自只负责「调哪个接口、怎么把返回体映射成 Product」。
公共的链接展开、密钥检查、错误翻译放在这里，避免三份重复实现走偏。
"""
from __future__ import annotations

from ..http_client import FetchError, client as http
from ..linkdetect import LinkInfo, detect
from ..models import Product
from ..openapi import ApiError
from .base import BaseAdapter

__all__ = ["EcommerceAdapter", "to_fetch_error"]

#: ApiError.kind -> FetchError.kind（界面按 kind 给不同颜色的提示）
_KIND_MAP = {
    "no_credential": "no_credential",
    "auth": "auth",
    "permission": "permission",
    "quota": "quota",
    "network": "network",
    "http": "http",
    "parse": "parse",
}


def to_fetch_error(exc: ApiError) -> FetchError:
    return FetchError(exc.message, kind=_KIND_MAP.get(exc.kind, "api"),
                      http_status=exc.http_status)


class EcommerceAdapter(BaseAdapter):
    """电商平台适配器基类。"""

    platform = ""            # jd / taobao / pdd
    accepted: tuple[str, ...] = ()

    # ---------------- 匹配与缓存 ----------------
    def matches(self, url: str) -> bool:
        return detect(url).platform in self.accepted

    def cache_key(self, url: str) -> str | None:
        info = detect(url)
        if info.item_id:
            return f"{self.platform}:{info.item_id}"
        return None

    # ---------------- 公共步骤 ----------------
    def resolve_info(self, url: str) -> LinkInfo:
        """解析链接；短链或缺少 ID 时联网展开一次。"""
        info = detect(url)
        if info.item_id and not info.is_short:
            return info
        if not info.ok:
            raise FetchError(info.note or "无法识别这个链接", kind="unsupported")

        final_url, _chain = http.resolve(info.url)
        resolved = detect(final_url)
        if resolved.ok and resolved.item_id:
            return resolved
        raise FetchError(
            f"展开短链后仍未拿到商品 ID（最终地址：{final_url[:90]}）。"
            "如果是 App 内复制的口令文本，请改用浏览器里的商品页地址。",
            kind="unsupported",
        )

    def guard_credentials(self, client) -> None:
        if not client.available:
            raise FetchError(
                f"{client.cred.label}接口未配置密钥，无法采集。"
                f"请在后端环境变量里设置：{'、'.join(client.cred.missing)}。"
                "配置前本项目不会用任何估算值或示例数据代替真实商品。",
                kind="no_credential",
            )

    def finish(self, product: Product, adapter_key: str, source_label: str) -> Product:
        """统一的收尾：补缺失清单与状态文案。"""
        product.recompute_missing()
        have, total, ratio = product.completeness
        product.status = f"已通过{source_label}采集（字段完整度 {have}/{total}）"
        if product.missing:
            product.notes.append(
                "以下字段该平台接口未提供，已如实标注：" + "、".join(product.missing)
            )
        return product
