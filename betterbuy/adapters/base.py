"""采集适配器基类与注册表。

每个平台一个适配器文件，避免全部抓取逻辑堆在一个文件里。
新增平台只需实现 ``Adapter`` 并在 ``adapters/__init__.py`` 注册。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Product

__all__ = ["Adapter", "UnsupportedLink"]


class UnsupportedLink(Exception):
    """该链接不属于本适配器可处理的平台。"""


@runtime_checkable
class Adapter(Protocol):
    #: 适配器标识（写入数据库与来源说明）
    key: str
    #: 人类可读名称
    label: str
    #: 采集方式说明
    method: str
    #: 覆盖的域名（用于展示支持范围）
    domains: tuple[str, ...]

    def matches(self, url: str) -> bool:
        """是否能处理这个链接。"""

    def cache_key(self, url: str) -> str | None:
        """不需要联网就能算出的稳定缓存键；算不出返回 None。"""

    def fetch(self, url: str) -> Product:
        """采集并返回标准化商品；失败抛 FetchError。"""


class BaseAdapter:
    key = "base"
    label = "基础适配器"
    method = "公开页面"
    domains: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:  # pragma: no cover - 子类覆盖
        return False

    def cache_key(self, url: str) -> str | None:
        return None

    def fetch(self, url: str) -> Product:  # pragma: no cover - 子类覆盖
        raise NotImplementedError
