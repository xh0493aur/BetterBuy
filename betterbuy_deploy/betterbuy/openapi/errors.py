"""开放平台接口异常。

``kind`` 用于界面分类展示，取值：
- ``no_credential`` 未配置密钥
- ``auth``         密钥/授权无效（app_key 不存在、token 过期等）
- ``permission``   已登录但无该接口权限（需申请）
- ``quota``        限流/超额
- ``api``          接口返回业务错误
- ``http``         网络层状态码异常
- ``network``      连接失败/超时
- ``parse``        返回结构无法识别
"""
from __future__ import annotations

__all__ = ["ApiError"]


class ApiError(Exception):
    def __init__(self, message: str, kind: str = "api", code: str = "",
                 sub_code: str = "", http_status: int | None = None,
                 request_id: str = ""):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.code = str(code or "")
        self.sub_code = sub_code or ""
        self.http_status = http_status
        self.request_id = request_id

    def __str__(self) -> str:  # pragma: no cover - 调试用
        bits = [self.kind, self.code, self.sub_code]
        return f"[{'/'.join(b for b in bits if b)}] {self.message}"
