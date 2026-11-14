"""三个开放平台的请求签名。

规则来源（2026-11 核实，均为官方文档或官方帮助中心）：
- 京东联盟：union.jd.com/helpcenter/13246-13312-108188
  参数名与参数值按字母序拼接 → appSecret 夹在两端 → MD5（大写）。
- 淘宝 TOP：open.taobao.com 公共参数页
  与京东同构（secret + 拼接串 + secret → MD5 大写），另支持 hmac-sha256。
- 拼多多：open.pinduoduo.com/application/document/browse?idStr=8EC06C399636041E
  同构，MD5 后必须转大写；timestamp 为秒级 UNIX 时间。

三家算法形式相同但细节不同，因此不合并成一个函数：京东要保留
``360buy_param_json`` 的原始 JSON 文本参与签名，淘宝/拼多多不接受空值参与签名。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping

__all__ = ["canon", "signature_string", "jd_sign", "taobao_sign", "pdd_sign"]


def canon(value: Any) -> str:
    """把参数值转成参与签名、并且与实际发送完全一致的文本。

    布尔必须是小写 ``true``/``false``；数组/对象必须是紧凑 JSON，
    否则签名串和请求体不一致就会验签失败（三家都踩过这个坑）。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def signature_string(params: Mapping[str, Any], *, skip: tuple[str, ...] = ("sign",)) -> str:
    """按参数名升序拼成 ``k1v1k2v2...``，跳过 sign 与空值。"""
    parts: list[str] = []
    for key in sorted(params):
        if key in skip:
            continue
        text = canon(params[key])
        if text == "":
            continue
        parts.append(f"{key}{text}")
    return "".join(parts)


def jd_sign(params: Mapping[str, Any], app_secret: str) -> str:
    """京东：MD5(secret + 拼接串 + secret)，结果大写。"""
    raw = f"{app_secret}{signature_string(params)}{app_secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def taobao_sign(params: Mapping[str, Any], app_secret: str, method: str = "md5") -> str:
    """淘宝：md5 → 大写；hmac-sha256 → 大写。"""
    text = signature_string(params)
    method = (method or "md5").lower()
    if method == "hmac-sha256":
        digest = hmac.new(app_secret.encode("utf-8"), text.encode("utf-8"), hashlib.sha256)
    else:
        digest = hashlib.md5(f"{app_secret}{text}{app_secret}".encode("utf-8"))
    return digest.hexdigest().upper()


def pdd_sign(params: Mapping[str, Any], client_secret: str) -> str:
    """拼多多：MD5(secret + 拼接串 + secret)，结果必须大写。"""
    raw = f"{client_secret}{signature_string(params)}{client_secret}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
