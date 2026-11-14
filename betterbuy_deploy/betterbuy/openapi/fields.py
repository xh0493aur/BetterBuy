"""从接口返回体里安全取值的小工具。

三家接口的字段名在不同版本/不同权限等级下会变，所以这里只做**候选键名匹配**，
取不到就是取不到，返回空值让上层标记「数据源未提供」，绝不填默认值。
"""
from __future__ import annotations

from typing import Any, Iterable

__all__ = ["pick", "pick_path", "to_float", "to_int", "fen_to_yuan", "all_keys"]


def pick(data: dict[str, Any], *names: str) -> Any:
    """按候选键名取第一个非空值，支持一层嵌套（如 priceInfo.lowestPrice）。"""
    if not isinstance(data, dict):
        return None
    for name in names:
        if "." in name:
            node: Any = data
            for part in name.split("."):
                if not isinstance(node, dict):
                    node = None
                    break
                node = node.get(part)
            if node not in (None, "", [], {}):
                return node
        else:
            value = data.get(name)
            if value not in (None, "", [], {}):
                return value
    return None


def pick_path(data: Any, *paths: Iterable[str]) -> Any:
    """按多个路径尝试，返回第一个命中的值。"""
    for path in paths:
        parts = [p for p in str(path).split(".") if p]
        node: Any = data
        ok = True
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                ok = False
                break
        if ok and node not in (None, "", [], {}):
            return node
    return None


def to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        return None
    try:
        return int(number)
    except (ValueError, OverflowError):
        return None


def fen_to_yuan(value: Any) -> float | None:
    """拼多多价格单位是分。"""
    number = to_float(value)
    return None if number is None else round(number / 100, 2)


def all_keys(data: Any, prefix: str = "", depth: int = 2) -> list[str]:
    """列出返回体里的字段路径，用于「原始字段」排查面板。"""
    keys: list[str] = []
    if depth < 0 or not isinstance(data, dict):
        return keys
    for key, value in data.items():
        path = f"{prefix}{key}"
        keys.append(path)
        if isinstance(value, dict):
            keys.extend(all_keys(value, prefix=path + ".", depth=depth - 1))
    return keys
