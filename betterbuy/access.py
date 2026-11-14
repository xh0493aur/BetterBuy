"""访问口令门：把带密钥的实例只开给知道口令的人（可选，默认关闭）。

为什么需要：
    仓库公开、应用公网可达时，任何人打开你的 Streamlit 链接点一下「搜索拼多多」，
    消耗的都是**你的** client_id 额度。每日上限（零成本层）能保证不花钱，
    但挡不住"额度被别人先烧完、你自己反而用不了"。
    拼多多侧也可能把异常流量算到你的应用头上。

怎么用：
    在 Streamlit Cloud → Settings → Secrets 里设一行::

        BETTERBUY_ACCESS_CODE = "你自己定的一串口令"

    设了才会启用；不设时本模块**完全不生效**，本地开发和自动化测试的行为不变。

三条设计约束：
    1. 默认关闭——不设环境变量就不拦截，避免"本地跑不起来"。
    2. 常量时间比较——用 :func:`hmac.compare_digest`，不做逐字符短路比较。
    3. 口令本身不落盘、不写日志、不回显——``st.text_input(type="password")``，
       只存 ``st.session_state`` 里的一个布尔位。
"""
from __future__ import annotations

import hmac
import os

__all__ = ["ENV_CODE", "SESSION_KEY", "MIN_LENGTH", "configured", "verify", "mask_hint"]

#: 环境变量名（Streamlit Cloud 里写进 Secrets，会被 secrets_bridge 注入到这里）
ENV_CODE = "BETTERBUY_ACCESS_CODE"

#: session_state 里记录"已通过"的键名
SESSION_KEY = "bb_access_ok"

#: 太短的口令等于没有，低于这个长度会在界面上提示换一个
MIN_LENGTH = 6


def _code() -> str:
    return (os.environ.get(ENV_CODE) or "").strip()


def configured() -> bool:
    """是否启用了访问口令（只有设了非空值才算启用）。"""
    return bool(_code())


def verify(user_input: str | None) -> bool:
    """校验口令。未启用时恒为 True；启用时用常量时间比较。"""
    expected = _code()
    if not expected:
        return True
    given = (user_input or "").strip()
    if not given:
        return False
    return hmac.compare_digest(given, expected)


def mask_hint() -> str:
    """给界面用的一句提示，**不泄露口令内容**，只暴露长度档位。"""
    expected = _code()
    if not expected:
        return "未启用"
    if len(expected) < MIN_LENGTH:
        return f"已启用（口令仅 {len(expected)} 位，建议换长一点）"
    return "已启用"
