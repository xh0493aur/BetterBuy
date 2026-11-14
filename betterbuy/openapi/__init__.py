"""四个电商开放平台的接入层（京东 / 淘宝天猫 / 拼多多）。

统一入口：
- :func:`client_for` 取某平台客户端；
- :data:`PLATFORMS` 是能力与权限清单（界面直接展示）；
- :data:`FIELD_MATRIX` 说明每个字段到底能不能拿到。

**重要口径**：这三家的开放接口都是「推广/联盟」口径，不是全站商品库。
它们能给出真实的价格、标题、图片、类目与推广链接，但**都不提供好评率百分比**，
也都不提供评价正文。因此本项目在这些商品上不会编造口碑分数，
而是把缺失如实标为「数据源未提供」，并让推荐算法据此降低置信度。
"""
from __future__ import annotations

from typing import Any

from .credentials import Credential, describe, load
from .errors import ApiError
from .jd import GATEWAY as JD_GATEWAY, JDClient
from .pdd import GATEWAY as PDD_GATEWAY, PDDClient
from .signing import jd_sign, pdd_sign, taobao_sign
from .taobao import GATEWAY as TB_GATEWAY, TaobaoClient

__all__ = [
    "ApiError", "JDClient", "TaobaoClient", "PDDClient",
    "PLATFORMS", "FIELD_MATRIX", "client_for", "load", "describe",
    "platform_of", "credential_rows", "capability_rows", "permission_checklist",
    "jd_sign", "taobao_sign", "pdd_sign",
]

PLATFORMS: list[dict[str, Any]] = [
    {
        "key": "jd",
        "label": "京东",
        "gateway": JD_GATEWAY,
        "qualification": "需要有「京东联盟」推广账号 + 联盟推广位；应用在京东开放平台创建并申请联盟接口权限",
        "env": ("BETTERBUY_JD_APP_KEY", "BETTERBUY_JD_APP_SECRET"),
        "apis": [
            {"method": "jd.union.open.goods.promotiongoodsinfo.query", "use": "按 skuId 查商品详情与价格", "note": "链接识别后首选"},
            {"method": "jd.union.open.goods.query", "use": "关键词/类目检索联盟商品", "note": "找竞品用"},
            {"method": "jd.union.open.goods.jingfen.query", "use": "京粉精选商品", "note": ""},
            {"method": "jd.union.open.category.goods.get", "use": "按类目取商品", "note": ""},
        ],
    },
    {
        "key": "taobao",
        "label": "淘宝/天猫",
        "gateway": TB_GATEWAY,
        "qualification": "需要「淘宝客（淘宝联盟）」推广者身份 + 推广位 adzone_id；应用在淘宝开放平台创建",
        "env": ("BETTERBUY_TB_APP_KEY", "BETTERBUY_TB_APP_SECRET", "BETTERBUY_TB_ADZONE_ID"),
        "apis": [
            {"method": "taobao.tbk.item.info.upgrade.get", "use": "按商品 ID 查详情（简易版）", "note": "链接识别后首选"},
            {"method": "taobao.tbk.item.details.upgrade.get", "use": "按商品 ID 查详情（含 SKU/库存）", "note": ""},
            {"method": "taobao.tbk.dg.material.optional.upgrade", "use": "物料搜索找竞品", "note": "adzone_id 必填"},
        ],
    },
    {
        "key": "pdd",
        "label": "拼多多",
        "gateway": PDD_GATEWAY,
        "qualification": "需要「多多进宝」推广位 PID；应用在拼多多开放平台创建并备案",
        "env": ("BETTERBUY_PDD_CLIENT_ID", "BETTERBUY_PDD_CLIENT_SECRET", "BETTERBUY_PDD_PID"),
        "apis": [
            {"method": "pdd.ddk.goods.search", "use": "关键词/商品链接搜索", "note": "keyword 支持直接传拼多多商品链接；page_size 只接受 10–100"},
            {"method": "pdd.ddk.goods.detail", "use": "商品详情", "note": "新版需 goods_sign"},
            {"method": "pdd.ddk.goods.recommend.get", "use": "商品推荐", "note": ""},
            {"method": "pdd.ddk.goods.promotion.url.generate", "use": "生成官方推广链接", "note": "需转链权限，且 pid 已完成授权备案"},
        ],
    },
]

#: 字段可得性：✅ 有 / ⚠️ 有条件或需更高权限 / ❌ 接口不提供
FIELD_MATRIX: list[dict[str, str]] = [
    {"字段": "商品名称", "京东": "✅", "淘宝/天猫": "✅", "拼多多": "✅",
     "说明": "接口直接给出标题"},
    {"字段": "价格", "京东": "✅", "淘宝/天猫": "✅", "拼多多": "✅",
     "说明": "联盟/推广口径价格；拼多多单位为分，已换算为元"},
    {"字段": "商品图片", "京东": "✅", "淘宝/天猫": "✅", "拼多多": "✅",
     "说明": "主图 URL"},
    {"字段": "品牌", "京东": "⚠️", "淘宝/天猫": "✅", "拼多多": "❌",
     "说明": "京东多为品牌编码，拼多多无独立品牌字段"},
    {"字段": "类目", "京东": "✅", "淘宝/天猫": "✅", "拼多多": "✅",
     "说明": "类目 ID 与名称"},
    {"字段": "型号/版本", "京东": "⚠️", "淘宝/天猫": "⚠️", "拼多多": "⚠️",
     "说明": "需从标题里提取，本项目用规则解析，不保证准确"},
    {"字段": "规格参数", "京东": "⚠️", "淘宝/天猫": "✅", "拼多多": "⚠️",
     "说明": "淘宝详细版有 SKU/属性；京东拼多多字段较少"},
    {"字段": "好评率（百分比）", "京东": "❌", "淘宝/天猫": "❌", "拼多多": "❌",
     "说明": "淘宝只有布尔值 h_good_rate（且需 SA 级权限）；均无具体百分比"},
    {"字段": "评价数量", "京东": "⚠️", "淘宝/天猫": "❌", "拼多多": "❌",
     "说明": "淘宝只给 30 天销量 volume，不能当评价数用"},
    {"字段": "评价正文", "京东": "❌", "淘宝/天猫": "❌", "拼多多": "❌",
     "说明": "三家推广接口都不返回评论内容"},
    {"字段": "购买链接", "京东": "✅", "淘宝/天猫": "✅", "拼多多": "⚠️",
     "说明": "多为推广链接；拼多多部分场景需再调转链接口"},
]

CLIENTS = {"jd": JDClient, "taobao": TaobaoClient, "pdd": PDDClient}


def client_for(platform: str, cred: Credential | None = None):
    """按平台取客户端（未配置密钥也能构造，调用时才报错）。"""
    if platform not in CLIENTS:
        raise ApiError(f"不支持的平台：{platform}", kind="api")
    return CLIENTS[platform](cred)


def platform_of(key: str) -> dict[str, Any] | None:
    return next((p for p in PLATFORMS if p["key"] == key), None)


def credential_rows() -> list[dict[str, str]]:
    """界面用：三个平台的配置状态汇总（细节在各自的展开区里）。"""
    rows: list[dict[str, str]] = []
    for item in PLATFORMS:
        cred = load(item["key"])
        rows.append({
            "平台": item["label"],
            "接口网关": item["gateway"],
            "密钥状态": "已配置" if cred.configured else "未配置",
            "缺少项": "—" if cred.configured else f"{len(cred.missing)} 项",
        })
    return rows


def capability_rows() -> list[dict[str, str]]:
    return [dict(row) for row in FIELD_MATRIX]


def permission_checklist() -> list[dict[str, str]]:
    """需要用户去申请的东西，一条一条列清楚。"""
    return [
        {"平台": "京东", "要做什么": "注册京东联盟并实名认证，创建推广位拿到 siteId / positionId",
         "在哪做": "https://union.jd.com/"},
        {"平台": "京东", "要做什么": "在京东开放平台创建应用，拿到 app_key / app_secret",
         "在哪做": "https://jos.jd.com/"},
        {"平台": "京东", "要做什么": "为应用申请「联盟商品查询」类接口权限（4 个 jd.union.open.* 接口）",
         "在哪做": "京东开放平台 → 应用管理 → 接口权限"},
        {"平台": "淘宝/天猫", "要做什么": "注册阿里妈妈/淘宝联盟推广者，创建推广位拿到 adzone_id",
         "在哪做": "https://pub.alimama.com/"},
        {"平台": "淘宝/天猫", "要做什么": "在淘宝开放平台创建应用，拿到 app_key / app_secret",
         "在哪做": "https://open.taobao.com/"},
        {"平台": "淘宝/天猫", "要做什么": "申请淘宝客 API 权限（物料搜索 + 商品详情）",
         "在哪做": "open.taobao.com → 控制台 → 应用 → 权限管理"},
        {"平台": "淘宝/天猫", "要做什么": "如需用商品链接直接查询，还要申请「消费者比价场景 ID2」权限",
         "在哪做": "同上，需提工单说明用途"},
        {"平台": "拼多多", "要做什么": "注册多多进宝推广者，创建推广位拿到 PID",
         "在哪做": "https://jinbao.pinduoduo.com/"},
        {"平台": "拼多多", "要做什么": "在拼多多开放平台创建应用，拿到 client_id / client_secret，完成备案",
         "在哪做": "https://open.pinduoduo.com/"},
        {"平台": "拼多多", "要做什么": "申请 pdd.ddk.goods.* 接口权限（商品搜索/详情/推荐/转链）",
         "在哪做": "open.pinduoduo.com → 控制台 → 应用详情"},
        {"平台": "拼多多", "要做什么": "对 pid（以及 custom_parameters）做授权备案，否则关键词搜索返回 50001/60001",
         "在哪做": "jinbao.pinduoduo.com → 用转链接口生成备案链接并访问一次"},
    ]
