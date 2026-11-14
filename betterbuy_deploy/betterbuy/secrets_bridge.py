"""把 Streamlit Cloud 的 Secrets 桥接到环境变量（本地运行时是空操作）。

为什么需要这一层：
    Streamlit Community Cloud 只把 Secrets 暴露为 ``st.secrets``，
    不会写入 ``os.environ``；而本项目的适配器、配额模块统一通过
    ``os.environ`` 读取 ``BETTERBUY_*`` 配置。没有这层桥接，
    云端即使填了密钥，适配器仍会认为"没有凭据"。

规则（三条，都很保守）：
    1. 只接受大写后以 ``BETTERBUY_`` 开头的键，其余键一律忽略；
    2. 不覆盖已存在的环境变量（本地 .env / 系统变量优先）；
    3. 任何异常都吞掉——没有 secrets.toml 属于正常情况，不能影响启动。

支持的写法（两种等价）：:

    BETTERBUY_JD_APP_KEY = "xxx"      # 顶层平铺

    [betterbuy]                        # 分表：betterbuy + jd_app_key
    jd_app_key = "xxx"
"""
from __future__ import annotations

import os

PREFIX = "BETTERBUY_"

__all__ = ["apply", "collect"]


def _walk(node, prefix: str = ""):
    """把可能嵌套的 Secrets 结构摊平成 (名字, 值) 序列。"""
    if hasattr(node, "keys"):
        for key in node.keys():
            name = f"{prefix}_{key}" if prefix else str(key)
            yield from _walk(node[key], name)
    else:
        yield prefix, node


def collect(secrets_obj) -> dict[str, str]:
    """从 Secrets 对象里挑出本项目认识的键（纯函数，便于测试）。"""
    found: dict[str, str] = {}
    try:
        pairs = list(_walk(secrets_obj))
    except Exception:  # noqa: BLE001 - Secrets 对象行为随版本变化，一律容错
        return found
    for name, value in pairs:
        key = str(name).upper()
        if not key.startswith(PREFIX):
            continue
        if isinstance(value, bool):
            found[key] = "1" if value else "0"
        elif isinstance(value, (str, int, float)):
            found[key] = str(value).strip()
    return found


def apply(secrets_obj=None) -> list[str]:
    """注入环境变量，返回真正写入的键名列表（用于启动日志／自检）。"""
    if secrets_obj is None:
        try:
            import streamlit as st

            secrets_obj = st.secrets
        except Exception:  # noqa: BLE001 - 没有 secrets 文件或不在 Streamlit 里运行
            return []

    written: list[str] = []
    for key, value in collect(secrets_obj).items():
        if not value or os.environ.get(key):
            continue
        os.environ[key] = value
        written.append(key)
    return written
