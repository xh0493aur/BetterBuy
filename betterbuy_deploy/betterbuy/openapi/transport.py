"""开放平台的 HTTP 传输层：发请求、识别错误、剥掉外层包装。

三家网关都把业务错误塞在 ``error_response`` 里（HTTP 仍是 200），
所以不能只看状态码。这里统一翻译成 :class:`ApiError`。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import requests

from ..zero_cost import QuotaExceeded, quota
from .errors import ApiError

__all__ = ["Transport", "post", "raise_if_error", "unwrap", "first_json_string"]

TIMEOUT = float(os.environ.get("BETTERBUY_API_TIMEOUT", "15"))


@dataclass
class Transport:
    """一次调用的结果，便于界面展示原始返回。"""

    url: str
    status: int
    payload: dict[str, Any]
    body_text: str = ""

    @property
    def request_id(self) -> str:
        err = self.payload.get("error_response") or {}
        return str(err.get("request_id") or "")


def post(url: str, data: dict[str, Any], *, timeout: float | None = None,
         platform: str = "") -> Transport:
    """POST 表单。业务错误不在这里抛，交给 :func:`raise_if_error` 统一处理。

    传了 ``platform`` 就先扣当日配额：额度用尽时请求**不会发出**，
    直接抛 ``kind="quota"`` 的错误。这是零成本约束的落地位置——
    只要走这个函数，就没有办法绕过每日上限。
    """
    if platform:
        try:
            quota.consume(platform)
        except QuotaExceeded as exc:
            raise ApiError(str(exc), kind="quota") from exc

    try:
        resp = requests.post(url, data=data, timeout=timeout or TIMEOUT)
    except requests.Timeout as exc:
        raise ApiError(f"接口请求超时（{timeout or TIMEOUT:.0f} 秒）：{url}", kind="network") from exc
    except requests.RequestException as exc:
        raise ApiError(f"接口连接失败：{type(exc).__name__} {str(exc)[:120]}", kind="network") from exc

    text = resp.text or ""
    try:
        payload = resp.json()
    except ValueError:
        snippet = text.strip().replace("\n", " ")[:200]
        raise ApiError(
            f"接口返回的不是 JSON（HTTP {resp.status_code}）：{snippet}",
            kind="http", http_status=resp.status_code,
        ) from None
    if not isinstance(payload, dict):
        payload = {"_raw": payload}
    return Transport(url=url, status=resp.status_code, payload=payload, body_text=text)


def raise_if_error(result: Transport, *, platform: str) -> None:
    """把 error_response 翻译成带 kind 的 ApiError。"""
    err = result.payload.get("error_response")
    if not err:
        return
    code = str(err.get("code") or err.get("error_code") or "")
    sub = str(err.get("sub_code") or err.get("sub_msg") or "")
    msg = (
        err.get("msg")
        or err.get("zh_desc")
        or err.get("error_msg")
        or err.get("en_desc")
        or "接口返回错误"
    )
    kind = _classify(code, sub, str(msg))
    raise ApiError(
        f"{platform}接口报错：{msg}（code={code}{'，sub_code=' + sub if sub else ''}）",
        kind=kind, code=code, sub_code=sub,
        http_status=result.status, request_id=str(err.get("request_id") or ""),
    )


def _classify(code: str, sub: str, msg: str) -> str:
    text = f"{code} {sub} {msg}".lower()
    if any(k in text for k in ("appkey-not-exists", "invalid app", "invalid app_key",
                               "client下线", "clientid不正确", "签名", "sign")):
        return "auth"
    if any(k in text for k in ("permission", "no permission", "无权限", "未授权", "权限")):
        return "permission"
    if any(k in text for k in ("limit", "频繁", "超过", "quota", "flow")):
        return "quota"
    return "api"


def unwrap(result: Transport, *, suffix: tuple[str, ...] = ("_response", "_responce")) -> dict[str, Any]:
    """剥掉 ``xxx_response`` 外壳，返回真正的业务体。"""
    for key, value in result.payload.items():
        if key.endswith(suffix) and isinstance(value, dict):
            return value
    # 有的网关直接把业务体放在顶层
    return result.payload


def first_json_string(body: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    """京东把结果塞在字符串里（如 ``queryResult``），需要二次解析。"""
    for name in names:
        value = body.get(name)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except ValueError:
                continue
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        if isinstance(value, dict):
            return value
    return {}
