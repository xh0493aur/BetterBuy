"""密钥读取：只从环境变量读，绝不出现在网页或日志里。

命名统一为 ``BETTERBUY_<平台>_<字段>``。读取放在函数里而不是模块导入时，
这样用户改了环境变量（或测试里注入）立刻生效，不需要重启进程。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["Credential", "SPECS", "load", "mask"]


@dataclass(frozen=True)
class Credential:
    platform: str
    label: str
    values: dict[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return all(self.values.get(name, "").strip() for name in self.required)

    @property
    def missing(self) -> list[str]:
        return [name for name in self.required if not self.values.get(name, "").strip()]

    def get(self, name: str, default: str = "") -> str:
        return (self.values.get(name) or default).strip()

    def status_text(self) -> str:
        if self.configured:
            return "已配置"
        return "未配置（缺 " + "、".join(self.missing) + "）"


#: 平台 -> (显示名, 必需环境变量, 可选环境变量)
SPECS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "jd": (
        "京东",
        ("BETTERBUY_JD_APP_KEY", "BETTERBUY_JD_APP_SECRET"),
        ("BETTERBUY_JD_SITE_ID", "BETTERBUY_JD_POSITION_ID", "BETTERBUY_JD_ACCESS_TOKEN"),
    ),
    "taobao": (
        "淘宝/天猫",
        ("BETTERBUY_TB_APP_KEY", "BETTERBUY_TB_APP_SECRET", "BETTERBUY_TB_ADZONE_ID"),
        ("BETTERBUY_TB_SESSION",),
    ),
    "pdd": (
        "拼多多",
        ("BETTERBUY_PDD_CLIENT_ID", "BETTERBUY_PDD_CLIENT_SECRET", "BETTERBUY_PDD_PID"),
        ("BETTERBUY_PDD_ACCESS_TOKEN",),
    ),
}


def load(platform: str, environ: dict | None = None) -> Credential:
    """读取某平台凭据。``environ`` 仅用于测试注入。"""
    label, required, optional = SPECS[platform]
    env = os.environ if environ is None else environ
    names = list(required) + list(optional)
    return Credential(
        platform=platform,
        label=label,
        values={name: env.get(name, "") for name in names},
        required=required,
        optional=optional,
    )


def mask(value: str, keep: int = 4) -> str:
    """脱敏展示：只留前 4 位，其余用 * 代替。"""
    value = value or ""
    if not value:
        return "（空）"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


def describe(platform: str, environ: dict | None = None) -> list[dict[str, str]]:
    """给界面用：每个环境变量的名称、是否已设置、脱敏值。"""
    cred = load(platform, environ)
    rows: list[dict[str, str]] = []
    for name in list(cred.required) + list(cred.optional):
        raw = cred.values.get(name, "")
        rows.append({
            "环境变量": name,
            "是否必需": "必需" if name in cred.required else "可选",
            "状态": "已设置" if raw.strip() else "未设置",
            "当前值": mask(raw) if raw.strip() else "—",
        })
    return rows
