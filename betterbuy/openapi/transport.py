"""开放平台的 HTTP 传输层：发请求、识别错误、剥掉外层包装。

三家网关都把业务错误塞在 ``error_response`` 里（HTTP 仍是 200），
所以不能只看状态码。这里统一翻译成 :class:`ApiError`。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

from ..zero_cost import QuotaExceeded, quota
from .errors import ApiError

__all__ = ["Transport", "post", "raise_if_error", "unwrap", "first_json_string"]

TIMEOUT = float(os.environ.get("BETTERBUY_API_TIMEOUT", "15"))

#: 同一平台两次请求之间的最小间隔（秒）。平台侧普遍按 QPS 限流，
#: 这里加一道进程内的"慢一点"保险丝，避免连续点击把配额打光。
MIN_INTERVAL = float(os.environ.get("BETTERBUY_API_MIN_INTERVAL", "0.6"))

_throttle_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _wait_turn(bucket: str) -> None:
    """按平台串行化请求，保证两次调用之间至少间隔 ``MIN_INTERVAL`` 秒。"""
    if MIN_INTERVAL <= 0:
        return
    with _throttle_lock:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _last_call.get(bucket, 0.0))
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call[bucket] = now


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

    同一 ``platform`` 的两次请求之间还会隔开 ``MIN_INTERVAL`` 秒（见 ``_wait_turn``），
    这是给平台侧限流留的余量，不改变配额计数。
    """
    if platform:
        try:
            quota.consume(platform)
        except QuotaExceeded as exc:
            raise ApiError(str(exc), kind="quota") from exc
        _wait_turn(platform)

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
    # 拼多多把真正的原因放在 sub_msg 里（如 60001 的备案说明），别丢掉
    sub_msg = str(err.get("sub_msg") or "").strip()
    if sub_msg and sub_msg not in str(msg):
        msg = f"{msg}：{sub_msg}" if msg != "接口返回错误" else sub_msg
    kind = _classify(code, sub, str(msg))
    raise ApiError(
        f"{platform}接口报错：{msg}（code={code}{'，sub_code=' + sub if sub else ''}）",
        kind=kind, code=code, sub_code=sub,
        http_status=result.status, request_id=str(err.get("request_id") or ""),
    )


#: 官方错误码表（2026-11 核对 open.pinduoduo.com「返回错误码说明」）
_AUTH_CODES = {"10016", "10019", "20004", "20032"}
_PERMISSION_CODES = {"20005", "20031", "30000"}
_QUOTA_CODES = {"52101", "52102", "52103", "70031", "70032"}
#: 50001 + sub_code 60001：关键词搜索未传已备案的 pid/custom_parameters
_AUTHORIZE_SUBCODES = {"60001"}


def _classify(code: str, sub: str, msg: str) -> str:
    # 先按官方错误码判定，命中就不要再靠中文关键词猜
    if sub in _AUTHORIZE_SUBCODES:
        return "authorize"
    if code in _QUOTA_CODES:
        return "quota"
    if code in _PERMISSION_CODES:
        return "permission"
    if code in _AUTH_CODES:
        return "auth"

    text = f"{code} {sub} {msg}".lower()
    if any(k in text for k in ("appkey-not-exists", "invalid app", "invalid app_key",
                               "client下线", "clientid不正确", "签名", "sign")):
        return "auth"
    if any(k in text for k in ("permission", "no permission", "无权限", "未授权", "权限")):
        return "permission"
    if any(k in text for k in ("limit", "频繁", "超过", "quota", "flow", "限流")):
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
