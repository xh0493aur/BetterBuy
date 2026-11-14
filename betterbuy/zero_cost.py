"""零成本约束层：成本开关、每日调用配额、公开展示许可。

这一层的目的很单纯——**保证开发者在开发/测试/公开试用阶段的实际支出为 0 元**，
并且这个约束是代码强制的，不是靠自觉：

1. :func:`zero_cost_mode` 默认开启。开启时 :func:`betterbuy.advisor.llm_available`
   一律返回 ``False``，即不会调用任何大模型接口（哪怕是"兼容 OpenAI 的免费端点"，
   也默认当作不可用，除非你显式关掉零成本模式）。
2. :class:`Quota` 给每个外部数据源设了**每日调用次数上限**，超限立刻抛
   :class:`QuotaExceeded`，请求根本发不出去。计数落 SQLite，重启进程不清零（换天清零）。
3. :data:`COST_ITEMS` / :data:`SHOW_POLICY` / :data:`FREE_DEPLOY` 是给界面和文档用的
   事实清单，避免"以为是免费的其实是收费的"。

**关于额度的诚实说明**：``DAILY_LIMITS`` 里的数字是**本项目自设的保守上限**，
不是平台公布的免费额度。各平台真实额度随账号等级、应用审核结果变化，以你控制台的
显示为准。这里取保守值，是为了在最坏情况下也不会产生账单、也不会触发风控。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from .cache import db, default_db_path

__all__ = [
    "QuotaExceeded", "Quota", "quota", "zero_cost_mode", "daily_limit",
    "DAILY_LIMITS", "TOTAL_KEY", "SOURCE_LABELS", "COST_ITEMS", "SHOW_POLICY", "FREE_DEPLOY",
    "quota_rows", "usage_summary",
    "public_demo", "PUBLIC_DEMO_LIMITS", "PUBLIC_DEMO_TOTAL_LIMIT", "total_limit",
]

#: 环境变量：设为 0/false/no 才允许产生费用（默认关闭付费路径）
ENV_ZERO_COST = "BETTERBUY_ZERO_COST"

#: 环境变量：设为 1 表示这是一个"公网演示实例"（任何访客都能打开）
ENV_PUBLIC_DEMO = "BETTERBUY_PUBLIC_DEMO"

#: 每个数据源每日最大外部请求数（本项目自设上限，非平台官方额度）
DAILY_LIMITS: dict[str, int] = {
    "jd": 200,
    "taobao": 200,
    "pdd": 200,
    "steam": 600,
    "generic_html": 300,
}

#: 所有数据源合计的每日上限
DEFAULT_TOTAL_LIMIT = 800

#: 公网演示模式下的上限（``BETTERBUY_PUBLIC_DEMO=1`` 时生效）
#:
#: 公开部署意味着**陌生访客也在花你的额度**。这里的数字不是"平台额度"，
#: 而是"被刷光也不心疼"的量级：即使一整天被人恶意点满，也只是几十次请求，
#: 不会触发风控、不会产生账单，你自己改天再测就行。
PUBLIC_DEMO_LIMITS: dict[str, int] = {
    "jd": 20,
    "taobao": 20,
    "pdd": 20,
    "steam": 100,
    "generic_html": 40,
}

#: 公网演示模式下的合计上限
PUBLIC_DEMO_TOTAL_LIMIT = 120

TOTAL_KEY = "_total"

#: 界面显示名（不要把内部 key 直接丢给用户看）
SOURCE_LABELS: dict[str, str] = {
    "jd": "京东",
    "taobao": "淘宝/天猫",
    "pdd": "拼多多",
    "steam": "Steam",
    "generic_html": "通用网页兜底",
    TOTAL_KEY: "合计（所有来源）",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_quota (
    day      TEXT NOT NULL,
    source   TEXT NOT NULL,
    used     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, source)
);
"""


class QuotaExceeded(RuntimeError):
    """当日调用额度用尽。调用方应转成面向用户的提示，且**不得**重试。"""

    def __init__(self, message: str, source: str = "", used: int = 0, limit: int = 0) -> None:
        super().__init__(message)
        self.source = source
        self.used = used
        self.limit = limit


# ---------------------------------------------------------------- 开关
def _truthy(value: str | None, default: bool = True) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def zero_cost_mode() -> bool:
    """零成本模式是否开启（默认开启）。"""
    return _truthy(os.environ.get(ENV_ZERO_COST), default=True)


def public_demo() -> bool:
    """是否是公网演示实例（默认否）。开启后每日上限会被压到很低。"""
    return _truthy(os.environ.get(ENV_PUBLIC_DEMO), default=False)


def daily_limit(source: str) -> int:
    """取某个数据源的每日上限。

    优先级：``BETTERBUY_DAILY_LIMIT_<SOURCE>`` 显式指定 > 公网演示上限 > 默认值。
    """
    env_name = f"BETTERBUY_DAILY_LIMIT_{source.upper()}"
    raw = os.environ.get(env_name, "").strip()
    if raw:
        try:
            value = int(raw)
            if value >= 0:
                return value
        except ValueError:
            pass
    if public_demo():
        return PUBLIC_DEMO_LIMITS.get(source, 20)
    return DAILY_LIMITS.get(source, 200)


def total_limit() -> int:
    raw = os.environ.get("BETTERBUY_DAILY_LIMIT_TOTAL", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    if public_demo():
        return PUBLIC_DEMO_TOTAL_LIMIT
    return DEFAULT_TOTAL_LIMIT


# ---------------------------------------------------------------- 配额
class Quota:
    """按「日期 + 数据源」计数的每日配额，存在同一个 SQLite 里。

    计数用的是本地日期，跨天自动从 0 开始（旧日期的行留着不影响判断）。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    def used_today(self, source: str) -> int:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            row = conn.execute(
                "SELECT used FROM api_quota WHERE day=? AND source=?",
                (self._today(), source),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0]) if row else 0

    def remaining(self, source: str) -> int:
        return max(0, daily_limit(source) - self.used_today(source))

    def consume(self, source: str, amount: int = 1) -> int:
        """消耗一次额度并返回该源今日已用量。

        超限时抛 :class:`QuotaExceeded`，**并且不会写库**（不消耗）。
        """
        limit = daily_limit(source)
        used = self.used_today(source)
        total_used = self.used_today(TOTAL_KEY)
        cap = total_limit()

        if used + amount > limit:
            raise QuotaExceeded(
                f"{source} 今日调用已达上限（{used}/{limit} 次）。"
                f"为保持零成本与避免触发平台风控，已停止对外请求；明日自动恢复，"
                f"也可用 BETTERBUY_DAILY_LIMIT_{source.upper()} 调整上限。",
                source=source, used=used, limit=limit,
            )
        if total_used + amount > cap:
            raise QuotaExceeded(
                f"今日所有数据源合计调用已达上限（{total_used}/{cap} 次）。"
                f"为保持零成本，已停止对外请求；明日自动恢复，"
                f"也可用 BETTERBUY_DAILY_LIMIT_TOTAL 调整。",
                source=TOTAL_KEY, used=total_used, limit=cap,
            )

        day = self._today()
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            for key, step in ((source, amount), (TOTAL_KEY, amount)):
                conn.execute(
                    """INSERT INTO api_quota (day, source, used) VALUES (?,?,?)
                       ON CONFLICT(day, source) DO UPDATE SET used = used + ?""",
                    (day, key, step, step),
                )
            conn.commit()
        finally:
            conn.close()
        return used + amount

    def rows(self) -> list[dict[str, Any]]:
        """界面用：每个数据源今天的用量与上限（名称已本地化）。"""
        out: list[dict[str, Any]] = []
        for source in list(DAILY_LIMITS) + [TOTAL_KEY]:
            used = self.used_today(source)
            limit = total_limit() if source == TOTAL_KEY else daily_limit(source)
            out.append({
                "数据源": SOURCE_LABELS.get(source, source),
                "今日已用": used,
                "每日上限": limit,
                "剩余": max(0, limit - used),
                "状态": "已达上限" if used >= limit else "可继续",
            })
        return out

    def reset_today(self, source: str | None = None) -> None:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            if source:
                conn.execute("DELETE FROM api_quota WHERE day=? AND source=?",
                             (self._today(), source))
            else:
                conn.execute("DELETE FROM api_quota WHERE day=?", (self._today(),))
            conn.commit()
        finally:
            conn.close()


def usage_summary() -> dict[str, Any]:
    """一句话概括今日配额状态，给页面顶部横幅用。"""
    rows = quota.rows()
    total = next(r for r in rows if r["数据源"].startswith("合计"))
    blocked = [r["数据源"] for r in rows if r["状态"] == "已达上限" and not r["数据源"].startswith("合计")]
    return {
        "zero_cost": zero_cost_mode(),
        "used": total["今日已用"],
        "limit": total["每日上限"],
        "remaining": total["剩余"],
        "blocked_sources": blocked,
        "exhausted": total["剩余"] <= 0,
    }


def quota_rows() -> list[dict[str, Any]]:
    return quota.rows()


# ---------------------------------------------------------------- 事实清单
#: 成本清单：每一项都写明"要不要花钱"，避免误判
COST_ITEMS: list[dict[str, str]] = [
    {"项目": "Steam 商店公开接口", "费用": "0 元", "说明":
     "store.steampowered.com 的 appdetails 与商品页，公开可访问，无需注册、无需密钥。"},
    {"项目": "Steam 搜索页竞品发现", "费用": "0 元", "说明":
     "公开搜索页，遵守 robots.txt 与限速（默认 1.2 秒/次）。"},
    {"项目": "京东 / 淘宝 / 拼多多开放接口", "费用": "0 元（有额度）", "说明":
     "推广/联盟类接口本身不收费，按账号等级给每日调用量。本项目另设保守上限，避免超限或风控。"},
    {"项目": "本地 SQLite 缓存", "费用": "0 元", "说明":
     "存在项目目录 data/betterbuy.sqlite3，无云端数据库费用。"},
    {"项目": "推荐算法（规则 + 评分）", "费用": "0 元", "说明":
     "纯 Python 计算，不调用任何模型接口。这是默认路径。"},
    {"项目": "Streamlit Community Cloud", "费用": "0 元", "说明":
     "官方免费档：公开仓库 + 1GB 内存 + 社区支持。免费档休眠后重启，本地 SQLite 缓存会丢，需重新采集。"},
    {"项目": "大模型增强（可选）", "费用": "**默认关闭**", "说明":
     "只有设置 BETTERBUY_ZERO_COST=0 且配置了 LLM 三项环境变量才会调用，属付费路径，本项目默认禁止。"},
    {"项目": "付费 API / 付费数据源", "费用": "未使用", "说明":
     "代码里没有任何付费接口调用，也没有自动充值、订阅或分期开通的逻辑。"},
]

#: 展示合规：把接口拿到的数据公开给其他用户看，各平台的限制要点
#: 说明：以下是依据公开的推广者协议/接口文档整理的操作要点，**不构成法律意见**，
#: 实际以你签署的协议原文为准。
SHOW_POLICY: list[dict[str, str]] = [
    {"平台": "Steam", "能否公开展示": "可以（有限度）", "要点":
     "公开商品页的价格/标题/图片可展示并注明来源；不要整页转载评测正文，不要缓存超期数据当实时价。"},
    {"平台": "京东联盟", "能否公开展示": "可以（推广用途）", "要点":
     "允许在推广场景展示商品名/图/价；价格不得自行修改或加价，须带推广链接，"
     "并遵守联盟对价格展示时效的要求（价格变动需及时更新）。"},
    {"平台": "淘宝客", "能否公开展示": "可以（推广用途）", "要点":
     "展示商品信息须使用淘宝客推广链接（含 pid），不得去除推广标识；"
     "不得对价格做二次加工或制造虚假优惠。"},
    {"平台": "多多进宝", "能否公开展示": "可以（推广用途）", "要点":
     "同样限于推广场景，须使用带 PID 的推广链接；不得篡改价格与销量数据。"},
    {"平台": "通用", "能否公开展示": "需自行核对", "要点":
     "本项目的推荐结论属于「基于公开数据的比较结果」，展示时应同时展示数据来源与采集时间，"
     "并说明不构成平台官方评价。"},
]

#: 免费部署方案
FREE_DEPLOY: list[dict[str, str]] = [
    {"方案": "Streamlit Community Cloud", "费用": "0 元", "适用": "公开试用首选",
     "要点": "GitHub 公开仓库 → share.streamlit.io 新建 app → 选 app.py。"
             "密钥用平台的 Secrets 配置（只存在服务端，不进仓库）。"
             "注意：免费档文件系统非持久，重启后 SQLite 缓存丢失；闲置会休眠。"},
    {"方案": "本机 / 局域网运行", "费用": "0 元", "适用": "自己验证、开发调试",
     "要点": "直接 streamlit run app.py。数据留在本地 data/ 目录，最稳。"},
    {"方案": "Hugging Face Spaces (Streamlit SDK)", "费用": "0 元", "适用": "备用公开演示",
     "要点": "免费 CPU 基础档；同样是非持久存储，重启丢缓存。"},
    {"方案": "Cloudflare Tunnel / ngrok 免费档", "费用": "0 元", "适用": "临时给他人看",
     "要点": "本机跑服务 + 临时公网地址；需要本机常开，免费档地址会变。"},
]


#: 进程级默认配额
quota = Quota()

# 让 `Quota` 与 `db` 共用同一个库文件（db 先初始化过表，这里只是保持一致）
_ = db
