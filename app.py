"""BetterBuy 智选 —— 面向大学生的 AI 理性消费与绿色消费助手。

运行：streamlit run app.py

页面结构：
    ① 智选 · 理性消费分析（公益版核心：链接/商品名/需求三种输入 → 三档结论 + 绿色建议 + 风险提示）
    ② 自动识别与智能比价（V0.2–V0.5 的比价与推荐能力）
    ③ 手动录入与自定义权重（保留旧版本的对比与评分能力）
    ④ 数据与缓存（SQLite 状态、价格历史、采集日志）
    ⑤ 电商平台接入（京东/淘宝/拼多多 官方接口状态与字段矩阵）
    ⑥ 零成本与配额（调用上限、成本清单、免费部署）
    ⑦ 说明与限制（能力边界，避免误以为"全平台全自动"）
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from betterbuy.adapters import collect, supported_platforms
from betterbuy.advisor import llm_advice, llm_available, llm_blocked_reason, rule_advice
from betterbuy.cache import db
from betterbuy.catalog import PRICE_BAND_NOTE, all_names, key_of_name
from betterbuy.discovery import discover
from betterbuy.discovery.ecommerce import same_product_across
from betterbuy.fallback import fallback_links, has_enough_data
from betterbuy.green import build_green
from betterbuy.access import SESSION_KEY as ACCESS_SESSION_KEY
from betterbuy.access import configured as access_configured
from betterbuy.access import verify as access_verify
from betterbuy.http_client import FetchError
from betterbuy.linkdetect import detect
from betterbuy.models import UNKNOWN, Product
from betterbuy.openapi import (FIELD_MATRIX, PLATFORMS, credential_rows, describe,
                               permission_checklist)
from betterbuy.openapi import load as load_credential
from betterbuy.openapi.pdd import ERROR_CODE_TABLE
from betterbuy.pdd_search import attach_links as attach_pdd_links
from betterbuy.pdd_search import search as pdd_keyword_search
from betterbuy.ration import MODE_LABEL as RATION_MODE_LABEL
from betterbuy.recommend import DEFAULT_WEIGHTS, PICK_ORDER, PICK_TITLES, recommend
from betterbuy.risk import LEVEL_HIGH, LEVEL_OK, LEVEL_TODO
from betterbuy.wise import MAX_LINKS_PER_ANALYSIS, run as run_wise
from betterbuy.zero_cost import (COST_ITEMS, FREE_DEPLOY, SHOW_POLICY, daily_limit,
                                 public_demo, quota, quota_rows, usage_summary,
                                 zero_cost_mode)
from scoring import score_items
from scraper import ScrapeError, scrape

# 云端（Streamlit Community Cloud）只把密钥放在 st.secrets 里，
# 不会进 os.environ。这一行把 BETTERBUY_* 的 Secrets 桥接成环境变量；
# 本地没有 secrets.toml 时是空操作，不改变任何行为。
from betterbuy.secrets_bridge import apply as _apply_secrets

_apply_secrets()

MAX_LINKS = 5
ECOMMERCE = {"京东", "淘宝", "天猫", "拼多多"}


def _stretch_key() -> dict:
    """兼容不同 Streamlit 版本的"撑满容器"参数，避免弃用警告也避免旧版报错。"""
    import inspect

    try:
        params = inspect.signature(st.dataframe).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return {"use_container_width": True}
    return {"width": "stretch"} if "width" in params else {"use_container_width": True}


FIT = _stretch_key()

# 仅做排版适配，不含任何动画，不影响加载速度。
st.markdown(
    """
    <style>
    @media (max-width: 640px) {
      h1 { font-size: 1.7rem !important; line-height: 1.25 !important; }
      h2, h3 { font-size: 1.15rem !important; }
      [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
      .block-container { padding-left: 0.8rem !important; padding-right: 0.8rem !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.set_page_config(
    page_title="BetterBuy 智选 · 理性消费助手",
    page_icon="🛍️",
    layout="wide",
)


def _render_access_gate() -> None:
    """公网部署时的可访问口令（只有设了 BETTERBUY_ACCESS_CODE 才生效）。

    仓库是公开的、应用也是公网可达的，所以真正需要保护的不是代码，而是
    **你的电商密钥额度**：任何陌生访客点一下搜索，花的都是你的调用次数。
    设了这个口令，只有拿到口令的人才进得来；不设则完全不拦截。
    """
    if not access_configured():
        return
    if st.session_state.get(ACCESS_SESSION_KEY):
        return

    st.markdown("### 🔒 这个实例需要访问口令")
    st.caption(
        "这是一台公开演示实例。为了避免陌生访客消耗开发者自己的接口额度，"
        "这里加了一道口令。口令由项目作者持有，不随代码公开发布。"
    )
    with st.form("access_form", clear_on_submit=False):
        code = st.text_input("访问口令", type="password", key="bb_access_input")
        submitted = st.form_submit_button("进入")
    if submitted:
        if access_verify(code):
            st.session_state[ACCESS_SESSION_KEY] = True
            st.rerun()
        st.error("口令不对。想先看效果可以访问不带密钥的演示版，或直接看 README 里的截图。")
    st.stop()


_render_access_gate()

LEGACY_COLUMNS = ["商品", "平台", "链接", "价格", "币种", "评分", "满分", "评价数", "好评率%",
                  "实用度(1-10)", "核心参数/备注", "来源", "采集状态", "采集时间"]
LEGACY_NUMERIC = ["价格", "评分", "满分", "评价数", "好评率%", "实用度(1-10)"]


# ---------------------------------------------------------------- 工具函数
def money(value, currency: str = "") -> str:
    if value is None:
        return UNKNOWN
    symbol = {"CNY": "¥", "USD": "$", "EUR": "€", "JPY": "JP¥"}.get(currency or "", "")
    return f"{symbol}{value:,.2f}" if symbol else f"{value:,.2f} {currency or ''}".strip()


def field_source(product: Product, *keys: str) -> str:
    for key in keys:
        source = product.source_of(key)
        if source:
            return f"{source.label} · {source.fetched_at}"
    return "来源未记录"


# ---------------------------------------------------------------- 智选页渲染
VERDICT_STYLE = {
    "buy": ("success", "✅"),
    "compare": ("warning", "🔍"),
    "wait": ("error", "⏸️"),
    "empty": ("info", "✍️"),
}
LEVEL_ICON = {"高": "🔴", "中": "🟠", "低": "🟡", LEVEL_TODO: "⚪", LEVEL_OK: "🟢"}


def _render_product_block(product: Product) -> None:
    """一张商品卡：只有真实字段，缺什么就写「数据源未提供」。"""
    with st.container(border=True):
        left, right = st.columns([1, 3.2])
        with left:
            if product.image_url:
                st.image(product.image_url, **FIT)
        with right:
            tag = {"discovered": "自动发现的同类", "search": "拼多多关键词搜索"}.get(
                product.origin, "你提供的")
            st.markdown(f"**{product.name or UNKNOWN}**　`{tag}`")
            meta = [product.platform, product.brand, product.model]
            st.caption(" · ".join(m for m in meta if m) or "平台与品牌未提供")
            m1, m2, m3 = st.columns(3)
            m1.metric("价格", product.offer.display())
            have, total, _ = product.completeness
            m2.metric("字段完整度", f"{have}/{total}")
            count = product.rating.review_count
            m3.metric("评价数", f"{count:,}" if count is not None else "未提供")
            st.caption("口碑：" + product.rating.display())
            if product.missing:
                st.caption("缺失字段（不填默认值）：" + "、".join(product.missing))
            if product.url:
                st.markdown(f"[打开商品页]({product.url}) · 采集时间 {product.fetched_at}")


def render_wise(w) -> None:
    """渲染一次「智选」分析结果。所有判断都在 betterbuy 里完成，这里只负责展示。"""
    r = w.ration
    style, icon = VERDICT_STYLE.get(r.verdict_key, ("info", "ℹ️"))

    # ---------------- 结论条 ----------------
    if r.verdict_key == "empty":
        st.info(r.headline + "\n\n" + " ".join(r.reasons))
    else:
        getattr(st, style)(
            f"{icon} **{r.verdict}**（置信度：{r.confidence}）  \n{r.headline}"
        )
    st.caption(f"判定方式：{RATION_MODE_LABEL}。每条结论都能在页面底部「查看分析依据」里回溯。")

    for note in w.notes:
        (st.warning if note.startswith(("⚠️", "❌")) else st.caption)(note)

    # ---------------- ① 商品信息 ----------------
    st.markdown("---")
    st.subheader("① 商品信息与可用数据")
    if not w.products:
        st.warning("本次没有采集到商品数据，所以下面不会出现任何价格、评分或商品卡片。")
        st.caption(
            "没有链接也可以只用需求分析——②之后的绿色建议与风险清单不依赖商品数据。"
            if not w.urls else
            "链接采集失败的原因见上方提示。本项目不会用示例数据填补空缺。"
        )
    else:
        for product in w.products:
            _render_product_block(product)

    # ---------------- ② 价格与实用度 ----------------
    st.subheader("② 价格与实用度比较")
    priced = w.priced
    if len(priced) >= 2:
        metrics = w.rec.metrics if w.rec else {}
        rows = []
        for product in priced:
            metric = metrics.get(product.product_id)
            rows.append({
                "商品": product.name,
                "平台": product.platform,
                "价格": product.offer.display(),
                "口碑": product.rating.display(),
                "综合分": round(metric.overall, 1)
                if metric and metric.overall is not None else None,
                "评价数": product.rating.review_count,
            })
        frame = pd.DataFrame(rows)
        st.dataframe(
            frame, hide_index=True, **FIT,
            column_config={
                "评价数": st.column_config.NumberColumn("评价数", format="localized"),
                "综合分": st.column_config.NumberColumn("综合分", format="%.1f"),
            },
        )
        chart = pd.DataFrame(
            {"价格（元）": [p.offer.price for p in priced]},
            index=[p.short(22) for p in priced],
        )
        st.bar_chart(chart, height=240)
        st.caption(
            "综合分 = 性价比 30% + 口碑 35% + 规格 25% + 价格 10%（可在「② 自动识别」里调整权重）。"
            "价格图只用于直观比较，不同货币不会被换算成同一币种。"
        )
    elif len(priced) == 1:
        product = priced[0]
        st.info(
            f"本次只拿到 1 个有价格的商品（{product.name}，{product.offer.display()}），"
            "没有第二个可比对象，因此不做价格排名，也不给「最便宜」这类结论。"
        )
    else:
        st.info(
            "没有可比较的价格。可能原因：没有贴链接、链接来自尚未配置密钥的电商平台，"
            "或数据源未返回价格。**本项目不会为了凑一个比较而编造价格。**"
        )

    # ---------------- ③ AI 购买建议 ----------------
    st.subheader("③ AI 购买建议")
    with st.container(border=True):
        st.markdown("**我是这样理解你的需求的**")
        st.markdown("- " + w.intent.evidence_line())
        if w.intent.category_basis:
            st.caption("品类判定依据：" + w.intent.category_basis)
        if w.intent.missing:
            st.caption("还缺这些信息（补齐后结论会更准）：" + "、".join(w.intent.missing))

    if r.verdict_key != "empty":
        st.markdown("**结论与理由**")
        for reason in r.reasons:
            st.markdown("- " + reason)

        bc = r.budget_check
        band_text = ""
        if bc.band[1] > 0:
            band_text = f"（该品类常见区间 {bc.band[0]:g}–{bc.band[1]:g} 元，经验参考）"
        level_icon = {"合理": "🟢", "偏低": "🟠", "偏高": "🟠"}.get(bc.level, "⚪")
        st.markdown(f"**预算判断**：{level_icon} {bc.level}{band_text}")
        if bc.text:
            st.caption(bc.text)

        if r.focus:
            st.markdown("**这类商品该重点看什么**（已按你的用途排序）")
            with st.container(border=True):
                for item in r.focus:
                    st.markdown(f"**{item['参数']}**　{item['为什么重要']}")
                    if item.get("常见坑") and item["常见坑"] != "—":
                        st.caption("常见坑：" + item["常见坑"])
            st.caption(PRICE_BAND_NOTE)

        if r.checks:
            st.markdown("**下单前请逐条确认**")
            for item in r.checks:
                st.markdown("- " + item)

        if w.advice:
            advice = w.advice
            st.markdown("**商品层面的建议**")
            st.caption(advice.summary)
            if advice.best:
                st.success(
                    f"首选：{advice.best.name}（{advice.best.offer.display()}）\n\n"
                    f"{advice.best_reason}"
                )
            if advice.alternatives and advice.alternatives[0].get("商品") not in ("", "—"):
                st.dataframe(pd.DataFrame(advice.alternatives), hide_index=True, **FIT)
            if advice.pros_cons:
                for row in advice.pros_cons:
                    with st.expander(f"优缺点：{row['商品'][:30]}"):
                        st.markdown("**优点**\n" + "\n".join("- " + x for x in row["优点"]))
                        st.markdown("**缺点**\n" + "\n".join("- " + x for x in row["缺点"]))
            if advice.avoid and advice.avoid[0].get("商品") not in ("", "—"):
                st.markdown("**不推荐的理由**")
                st.dataframe(pd.DataFrame(advice.avoid), hide_index=True, **FIT)

    # ---------------- ④ 绿色消费 ----------------
    st.subheader("④ 还有更环保、更省钱的选择吗？")
    st.caption(
        f"按「{w.green.family_label}」品类给出。" +
        "顺序是固定的：能不能不买 → 能不能修 → 能不能升级 → 能不能买二手 → 最后才是买新的。"
    )
    with st.container(border=True):
        st.markdown(
            " → ".join(f"`{step}`" for step in w.green.ladder)
        )
    if w.green.essential_note:
        st.info("关于必要性：" + w.green.essential_note)
    for item in w.green.items:
        with st.container(border=True):
            st.markdown(f"**{item.title}**　`{item.stage}`")
            st.markdown(item.detail)
            st.caption("可以先自查：" + item.check)
    with st.expander("这些建议的依据与边界"):
        for line in w.green.disclaimers:
            st.markdown("- " + line)

    # ---------------- ⑤ 购买风险 ----------------
    st.subheader("⑤ 购买风险提醒")
    if r.verdict_key != "empty":
        counts = w.risk.counts
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("高风险", counts.get(LEVEL_HIGH, 0))
        k2.metric("待核实", counts.get(LEVEL_TODO, 0))
        k3.metric("已核实通过", counts.get(LEVEL_OK, 0))
        k4.metric("检查项", len(w.risk.checked))
        st.caption(w.risk.note)
        if w.risk.items:
            for item in w.risk.items:
                icon = LEVEL_ICON.get(item.level, "⚪")
                with st.container(border=True):
                    st.markdown(f"**{icon} {item.label}**　`{item.level}`")
                    st.markdown(item.detail)
                    st.caption("依据：" + item.evidence)
                    st.caption("你可以做什么：" + item.action)
        st.caption(
            "「待核实」不等于商品有问题——它表示本项目拿不到判断所需的信息。"
            "本项目没有质检数据与返修率，任何「质量差」的结论都是编的，所以不写。"
        )

    # ---------------- ⑥ 数据来源 ----------------
    st.subheader("⑥ 数据来源与更新时间")
    if w.products:
        rows = []
        for product in w.products:
            if not product.sources:
                rows.append({"商品": product.name, "字段": "—", "来源": "来源未记录",
                             "方式": "—", "采集时间": product.fetched_at, "地址": product.url})
                continue
            for key, source in product.sources.items():
                rows.append({
                    "商品": product.short(24),
                    "字段": "、".join(f for f, k in product.field_sources.items() if k == key) or "说明",
                    "来源": source.label,
                    "方式": source.method,
                    "采集时间": source.fetched_at + ("（缓存）" if source.cached else ""),
                    "地址": source.url,
                })
        st.dataframe(
            pd.DataFrame(rows), hide_index=True, **FIT,
            column_config={"地址": st.column_config.LinkColumn("地址", display_text="打开")},
        )
    else:
        st.caption("本次没有商品数据，因此没有可列出的采集来源。")

    # ---------------- 查看分析依据 ----------------
    if r.verdict_key != "empty":
        with st.expander("查看分析依据（判定规则逐条公开）"):
            st.markdown(
                "下面是本次结论用到的每条规则及其命中情况。**没有任何一条是黑箱的**，"
                "也不存在「模型随手给个分」的环节。"
            )
            st.dataframe(
                pd.DataFrame([
                    {"规则": row["规则"], "是否命中": "是" if row["命中"] else "否",
                     "说明": row["说明"]}
                    for row in r.trace
                ]),
                hide_index=True, **FIT,
            )
            if w.intent.signals:
                st.markdown("**命中的风险信号（每条都来自你原话里的片段）**")
                st.dataframe(
                    pd.DataFrame([{
                        "信号": s.label, "依据": s.evidence,
                        "说明": s.detail, "权重": s.weight,
                    } for s in w.intent.signals]),
                    hide_index=True, **FIT,
                )
            if w.intent.rational:
                st.markdown("**这次做得好的地方**")
                for signal in w.intent.rational:
                    st.markdown(f"- {signal.label}（{signal.evidence}）：{signal.detail}")


def clean_legacy(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    for col in LEGACY_COLUMNS:
        if col not in frame:
            frame[col] = None
    frame = frame[LEGACY_COLUMNS]
    for col in LEGACY_NUMERIC:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    for col in ["商品", "平台", "链接", "币种", "核心参数/备注", "来源", "采集状态", "采集时间"]:
        frame[col] = frame[col].fillna("").astype(str)
    frame["币种"] = frame["币种"].str.strip().str.upper()
    return frame[frame["商品"].str.strip() != ""].reset_index(drop=True)


def demo_rows() -> list[list]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        ["示例工学椅 A（虚构）", "演示数据", "", 599, "CNY", 4.7, 5, 500, None, 8.4,
         "腰托；售后较好", "纯演示，非真实报价", "演示", now],
        ["示例工学椅 B（虚构）", "演示数据", "", 799, "CNY", 4.8, 5, 60, None, 9.2,
         "头枕；可调扶手", "纯演示，非真实报价", "演示", now],
        ["示例工学椅 C（虚构）", "演示数据", "", 459, "CNY", 4.3, 5, 900, None, 7.0,
         "入门配置", "纯演示，非真实报价", "演示", now],
    ]


def init_state() -> None:
    st.session_state.setdefault("products", [])
    st.session_state.setdefault("discovery", None)
    st.session_state.setdefault("rec", None)
    st.session_state.setdefault("advice", None)
    st.session_state.setdefault("run_notes", [])
    st.session_state.setdefault("need_text", "")
    st.session_state.setdefault("legacy", pd.DataFrame(columns=LEGACY_COLUMNS))
    st.session_state.setdefault("weights", dict(DEFAULT_WEIGHTS))
    st.session_state.setdefault("wise", None)


init_state()


# ---------------------------------------------------------------- 页面头部
st.title("🛍️ BetterBuy 智选")
st.caption("面向大学生的 AI 理性消费与绿色消费助手 · AI青年行·2026高校好事计划 参赛作品")
st.markdown(
    "**先问「该不该买」，再问「买哪一个」。** "
    "粘贴一个商品链接，或者直接用一句话说清你想买什么——"
    "系统会给出「推荐购买 / 建议进一步比较 / 建议暂缓购买」的结论、"
    "更省钱的替代路径，以及下单前必须确认的风险点。"
)
st.info(
    "数据全部来自官方公开接口与公开页面，不绕过登录、验证码或访问限制。"
    "拿不到的字段一律显示「数据源未提供」，不用估算值或示例数据顶替。"
)

_usage = usage_summary()
_zc_label = "零成本模式：开启" if _usage["zero_cost"] else "零成本模式：已关闭（可能产生费用）"
_usage_line = (
    f"{_zc_label} · 今日外部请求 {_usage['used']}/{_usage['limit']} 次"
    f"（剩余 {_usage['remaining']}）· 大模型调用：{'禁止' if _usage['zero_cost'] else '允许'}"
)
if _usage["blocked_sources"]:
    st.warning(_usage_line + f" · 已达上限的数据源：{'、'.join(_usage['blocked_sources'])}")
else:
    st.caption(_usage_line + " · 详见「⑥ 零成本与配额」")

if public_demo():
    st.caption(
        "公网演示模式：本页任何人都能打开，所以每日上限已收紧"
        f"（电商各 {daily_limit('pdd')} 次、合计 {_usage['limit']} 次），"
        "避免陌生访客把开发者自己的接口额度烧完。额度用尽后相关按钮会直接拒绝请求，不会产生费用。"
    )

tab_wise, tab_auto, tab_manual, tab_data, tab_ecom, tab_cost, tab_about = st.tabs(
    ["① 智选 · 理性消费分析", "② 自动识别与智能比价", "③ 手动录入与自定义权重",
     "④ 数据与缓存", "⑤ 电商平台接入", "⑥ 零成本与配额", "⑦ 说明与限制"]
)

# ================================================================ ① 智选
with tab_wise:
    st.markdown("##### 说说你想买什么")
    st.caption(
        "三种写法都行：**粘贴商品链接** · **只写商品名称** · **直接描述需求**（最推荐）。"
        f"一次最多 {MAX_LINKS_PER_ANALYSIS} 个链接。不需要填价格、评分或参数。"
    )

    with st.form("wise_form"):
        query = st.text_area(
            "商品链接或购买需求",
            height=110,
            placeholder=(
                "例 1｜贴链接：https://store.steampowered.com/app/413150/\n"
                "例 2｜说需求：我的笔记本是联想拯救者 Y7000P，想买个外接显示器，"
                "预算 1000 元，每天写代码要用"
            ),
            label_visibility="collapsed",
        )
        c1, c2, c3 = st.columns([1.3, 1, 1])
        with c1:
            cat_pick = st.selectbox(
                "商品品类", ["自动识别"] + all_names(), index=0,
                help="自动识别不准时再手动改；改了会在结果里说明",
            )
        with c2:
            cap = st.number_input(
                "预算上限（元，可留空）", min_value=0, value=0, step=50,
                help="也可以直接写在需求里，例如「预算 800 元」",
            )
        with c3:
            auto_discover = st.checkbox(
                "顺带比同类商品", value=True,
                help="目前只对 Steam 有效；其他平台不会假装能找到同类",
            )
        use_cache = st.checkbox("优先使用本地缓存（1 小时内更省请求）", value=True)
        go = st.form_submit_button("开始智能分析", type="primary")

    if go:
        if not query.strip():
            st.warning("先写一句你想买什么，或者贴一个商品链接。")
        else:
            with st.spinner("正在识别商品、解析需求、生成建议…"):
                try:
                    st.session_state.wise = run_wise(
                        query,
                        category_key=key_of_name(cat_pick) if cat_pick != "自动识别" else "",
                        budget_cap=float(cap or 0),
                        auto_discover=auto_discover,
                        use_cache=use_cache,
                    )
                except Exception as exc:  # 兜底：不让一次异常把整页打白
                    st.session_state.wise = None
                    st.error(f"分析过程出错：{type(exc).__name__} {str(exc)[:160]}")

    _wise = st.session_state.get("wise")
    if not _wise:
        with st.container(border=True):
            st.markdown("**还没有分析结果。** 三个可以直接复制的例子：")
            st.markdown(
                "- **数码配件**：我的笔记本是联想拯救者 Y7000P，想买个外接显示器，"
                "预算 1000 元，每天写代码要用\n"
                "- **学习用品**：下学期要用的笔记本和记号笔，预算 60 元，每天上课记笔记\n"
                "- **修还是换**：我现在用的手机电池一天要充三次，想换个新的，预算 3000 元"
            )
            st.caption(
                "也可以直接贴一个商品链接（Steam 商店页可直接识别价格与口碑）。"
                "没有链接时，下面的需求分析、绿色建议与风险清单依然完整可用。"
            )
    else:
        render_wise(_wise)

# ================================================================ ② 自动

with tab_auto:
    with st.form("auto_form"):
        st.markdown("**商品链接**（每行一个，最多 %d 个；支持短链接与跳转）" % MAX_LINKS)
        urls_text = st.text_area(
            "商品链接",
            height=120,
            placeholder="https://store.steampowered.com/app/413150/\nhttps://store.steampowered.com/app/105600/",
            label_visibility="collapsed",
        )
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            auto_discover = st.checkbox("自动发现同类竞品", value=True)
            competitor_count = st.slider("竞品数量", 2, 8, 4, help="会自动从同类商品里按口碑与评价量挑选")
        with c2:
            budget_low = st.number_input("预算下限（元，可留空）", min_value=0.0, value=0.0, step=10.0)
            budget_high = st.number_input("预算上限（元，0 表示不限）", min_value=0.0, value=0.0, step=10.0)
        with c3:
            use_cache = st.checkbox("优先使用本地缓存（1 小时内）", value=True)
            cross_platform = st.checkbox(
                "跨平台查找疑似同款", value=True,
                help="对京东/淘宝/拼多多商品，在另外两个平台的推广池里按品牌+型号+标题相似度找同款；需要对方平台的密钥",
            )
            need_text = st.text_input("你的需求（可选）", placeholder="预算 60 元，主要用来放松，能长期玩最好")

        with st.expander("高级：调整综合推荐权重（普通用户不用改）"):
            w1, w2, w3, w4 = st.columns(4)
            w_value = w1.slider("性价比", 0, 100, DEFAULT_WEIGHTS["value"], 5)
            w_rep = w2.slider("口碑", 0, 100, DEFAULT_WEIGHTS["reputation"], 5)
            w_spec = w3.slider("规格/内容", 0, 100, DEFAULT_WEIGHTS["spec"], 5)
            w_price = w4.slider("价格", 0, 100, DEFAULT_WEIGHTS["price"], 5)

        allow_llm = False
        if llm_available():
            allow_llm = st.checkbox(
                "允许把商品数据发送给已配置的外部大模型生成建议（会离开本机）", value=False
            )
            st.caption("提示：你已关闭零成本模式并配置了模型接口，这条路径可能产生费用，请自行确认。")
        else:
            st.caption("大模型路径不可用：" + llm_blocked_reason())
        submitted = st.form_submit_button("开始智能比价", type="primary")

    if submitted:
        urls = list(dict.fromkeys(
            line.strip() for line in urls_text.splitlines() if line.strip()
        ))
        notes: list[str] = []
        if not urls:
            st.warning("请先粘贴至少一个商品链接。")
        elif len(urls) > MAX_LINKS:
            st.warning(f"一次最多处理 {MAX_LINKS} 个链接，当前 {len(urls)} 个。")
        else:
            products: list[Product] = []
            progress = st.progress(0.0, text="准备中…")
            for index, url in enumerate(urls, start=1):
                progress.progress((index - 1) / (len(urls) + 1), text=f"识别第 {index}/{len(urls)} 个链接…")
                try:
                    product = collect(url, use_cache=use_cache,
                                      ttl=None if use_cache else 0)
                    product.origin = "user"   # 缓存命中也应标记为"你提供的链接"
                    products.append(product)
                except FetchError as exc:
                    notes.append(f"❌ `{url[:70]}` 采集失败（{exc.kind}）：{exc.message}")
                except Exception as exc:  # 不吞掉真正的问题
                    notes.append(f"❌ `{url[:70]}` 解析异常：{type(exc).__name__} {str(exc)[:120]}")

            discovery_result = None
            if products and auto_discover:
                progress.progress(0.6, text="正在搜索同类竞品…")
                min_price = budget_low if budget_low and budget_low > 0 else None
                max_price = budget_high if budget_high and budget_high > 0 else None
                try:
                    discovery_result = discover(
                        products[0], min_price=min_price, max_price=max_price,
                        limit=competitor_count,
                    )
                except Exception as exc:
                    notes.append(f"⚠️ 竞品搜索异常：{type(exc).__name__} {str(exc)[:120]}")

                if discovery_result and discovery_result.ok:
                    for index, candidate in enumerate(discovery_result.candidates, start=1):
                        progress.progress(
                            0.6 + 0.35 * index / max(1, len(discovery_result.candidates)),
                            text=f"读取竞品详情 {index}/{len(discovery_result.candidates)}：{candidate.name[:20]}",
                        )
                        try:
                            found = collect(candidate.url, use_cache=use_cache,
                                            ttl=None if use_cache else 0)
                            existing = next((p for p in products if p.product_id == found.product_id), None)
                            if existing:
                                # 你提供的链接里已经有这个商品，合并为一行，避免重复统计
                                existing.notes.append("该商品同时被自动发现为同类竞品，已合并为一行。")
                            else:
                                found.origin = "discovered"
                                products.append(found)
                        except FetchError as exc:
                            notes.append(f"⚠️ 竞品「{candidate.name}」详情读取失败：{exc.message}")
                elif discovery_result:
                    pass  # 具体原因在「② 自动发现的同类竞品」区块展示，避免重复提示

            # ---- 跨平台疑似同款（京东/淘宝/拼多多之间） ----
            cross_result = None
            if products and cross_platform and products[0].platform in ECOMMERCE:
                progress.progress(0.85, text="在其他平台查找疑似同款…")
                try:
                    cross_result = same_product_across(products, limit=2)
                    for matched in cross_result.matches:
                        if not any(p.product_id == matched.product_id for p in products):
                            products.append(matched)
                except FetchError as exc:
                    notes.append(f"⚠️ 跨平台同款检索失败：{exc.message}")
                except Exception as exc:
                    notes.append(f"⚠️ 跨平台同款检索异常：{type(exc).__name__} {str(exc)[:120]}")
            st.session_state.cross_result = cross_result

            progress.progress(1.0, text="完成")
            progress.empty()

            weights = {"value": w_value, "reputation": w_rep, "spec": w_spec, "price": w_price}
            if sum(weights.values()) <= 0:
                notes.append("⚠️ 权重全为 0，已恢复默认权重。")
                weights = dict(DEFAULT_WEIGHTS)

            st.session_state.products = products
            st.session_state.discovery = discovery_result
            st.session_state.run_notes = notes
            st.session_state.need_text = need_text
            st.session_state.weights = weights
            st.session_state.rec = recommend(products, weights) if products else None
            if products and st.session_state.rec:
                advice = None
                if allow_llm:
                    advice = llm_advice(products, st.session_state.rec, need_text, allow_external=True)
                    if advice is None:
                        notes.append("⚠️ 大模型调用未成功，已回退到规则版建议。")
                if advice is None:
                    advice = rule_advice(products, st.session_state.rec, need_text)
                st.session_state.advice = advice
            else:
                st.session_state.advice = None

    # ---------------- 拼多多关键词搜索（官方多多进宝接口） ----------------
    # 结果直接写进 session_state.products，因此下面的 ①~⑨ 就是同一套比较与推荐流程。
    pdd_cred = load_credential("pdd")
    with st.expander("按关键词搜拼多多商品（官方 pdd.ddk.* 接口 · 需多多进宝密钥）", expanded=False):
        st.caption(
            "走多多进宝商品搜索，口径是**推广池**，不是拼多多全站商品库。"
            f"当前密钥状态：{pdd_cred.status_text()}。"
        )
        with st.form("pdd_search_form"):
            c1, c2, c3 = st.columns([2.2, 1, 1])
            pdd_kw = c1.text_input("商品关键词", placeholder="例如：机械键盘 87 键")
            pdd_n = c2.selectbox("返回条数", [10, 20, 30, 50], index=0,
                                 help="官方对分页的硬限制是 10–100 条")
            pdd_sort = c3.selectbox("排序方式", ["综合", "销量"], index=0)
            d1, d2 = st.columns(2)
            pdd_coupon = d1.checkbox("只看有券商品", value=False)
            pdd_detail = d2.checkbox("读取前 3 条详情（每条 1 次调用）", value=False)
            pdd_go = st.form_submit_button("搜索拼多多")

        if pdd_go:
            with st.spinner("正在调用拼多多开放平台…"):
                pdd_result_new = pdd_keyword_search(
                    pdd_kw, limit=int(pdd_n),
                    sort_type={"综合": None, "销量": 1}[pdd_sort],
                    with_coupon=True if pdd_coupon else None,
                    detail_count=3 if pdd_detail else 0,
                )
            st.session_state.pdd_result = pdd_result_new
            if pdd_result_new.ok:
                weights = dict(DEFAULT_WEIGHTS)
                st.session_state.products = pdd_result_new.products
                st.session_state.discovery = None
                st.session_state.cross_result = None
                st.session_state.weights = weights
                st.session_state.need_text = ""
                st.session_state.rec = recommend(pdd_result_new.products, weights)
                st.session_state.advice = rule_advice(
                    pdd_result_new.products, st.session_state.rec, ""
                )
                st.session_state.run_notes = [
                    f"拼多多关键词「{pdd_result_new.keyword}」：{pdd_result_new.message}"
                    f"（接口调用 {pdd_result_new.api_calls} 次；来源 {pdd_result_new.used_api}）"
                ]
            else:
                st.session_state.run_notes = []

    # 结果渲染在展开面板之外：提交后即使面板收起，错误/表格依然在页面上
    pdd_result = st.session_state.get("pdd_result")
    if pdd_result is not None:
        with st.container(border=True):
            st.markdown(f"**拼多多搜索结果** `{pdd_result.keyword}`")
            if not pdd_result.ok:
                st.error(f"{pdd_result.kind_label}：{pdd_result.message}")
                if pdd_result.hint:
                    st.caption(pdd_result.hint)
                if pdd_result.code:
                    st.caption(
                        f"原始错误码：code={pdd_result.code}"
                        + (f"，sub_code={pdd_result.sub_code}" if pdd_result.sub_code else "")
                    )
            else:
                rows = [{
                    "商品": p.name or UNKNOWN,
                    "平台": p.platform,
                    "价格": p.offer.display(),
                    "价格口径": p.offer.terms or "口径未说明",
                    "店铺": p.specs.get("店铺", UNKNOWN),
                    "已售": p.specs.get("已售", UNKNOWN),
                    "商品链接": p.url or "接口未返回",
                } for p in pdd_result.products]
                st.dataframe(pd.DataFrame(rows), hide_index=True, **FIT)
                st.caption(
                    "价格 = 接口返回的拼团价（原单位为分，已换算成元）；"
                    "券后价按「拼团价 − 券面额」估算，未考虑用券门槛、限购与运费。"
                    "搜索接口不返回商品链接，需要可点击的官方链接请点下面的按钮。"
                )
                if st.button("生成官方推广链接（需转链权限）", key="pdd_make_links"):
                    with st.spinner("正在调用 pdd.ddk.goods.promotion.url.generate…"):
                        filled, msg = attach_pdd_links(pdd_result.products)
                    (st.success if filled else st.warning)(msg)
                    if filled:
                        st.session_state.run_notes = [msg]
                        st.rerun()

    products: list[Product] = st.session_state.products
    rec = st.session_state.rec
    advice = st.session_state.advice
    discovery_result = st.session_state.discovery

    for note in st.session_state.run_notes:
        (st.warning if note.startswith(("⚠️", "❌")) else st.info)(note)

    if not products:
        with st.container(border=True):
            st.markdown("**还没有结果。** 可以这样开始：")
            st.markdown(
                "1. 在下面复制一个真实商品链接（Steam 商店任意游戏页，例如 "
                "`https://store.steampowered.com/app/413150/`）；\n"
                "2. 粘贴到上面的输入框，点「开始智能比价」；\n"
                "3. 系统会自动识别商品、找同类竞品、给出五类推荐和购买建议；\n"
                "4. 也可以展开上方「按关键词搜拼多多商品」，用官方接口按关键词找货"
                "（需要你自己的多多进宝 client_id / secret / pid）。"
            )

    if products:
        # ---------------- 3. 商品识别状态 ----------------
        st.subheader("① 商品识别状态")
        st.caption("每个商品都标注了字段完整度与缺失项；缺失就是数据源没给，不做推测。")
        cols = st.columns(min(3, len(products)))
        for index, product in enumerate(products):
            with cols[index % len(cols)]:
                with st.container(border=True):
                    if product.image_url:
                        st.image(product.image_url, **FIT)
                    st.markdown(f"**{product.name or '未识别名称'}**")
                    st.caption(product.status or "已识别")
                    st.write(f"💰 {product.offer.display()}")
                    st.write(f"⭐ {product.rating.display()}")
                    have, total, ratio = product.completeness
                    st.progress(ratio, text=f"字段完整度 {have}/{total}")
                    st.caption(f"数据采集时间：{product.fetched_at or '未记录'}")
                    if product.missing:
                        st.caption("缺失：" + "、".join(product.missing))
                    if not has_enough_data(product):
                        st.warning("数据不足，无法对这件商品做可靠比较。可去平台自行核实：")
                        for link in fallback_links(product, limit=2)[:2]:
                            st.link_button(f"去{link['平台']}看", link["链接"])
                    with st.expander("来源与说明"):
                        st.write(f"平台：{product.platform}")
                        st.write(f"链接：{product.url}")
                        for key in ("商品名称", "品牌", "价格", "好评率", "核心参数", "商品类别"):
                            mapping = {
                                "商品名称": ("name", "detail"),
                                "品牌": ("brand", "detail"),
                                "价格": ("price",),
                                "好评率": ("rating_rate", "rating"),
                                "核心参数": ("specs", "detail"),
                                "商品类别": ("category", "detail"),
                            }[key]
                            st.caption(f"{key}：{field_source(product, *mapping)}")
                        for note in product.notes:
                            st.caption("· " + note)

        # ---------------- 4. 竞品列表 ----------------
        st.subheader("② 自动发现的同类竞品")
        if discovery_result is None:
            st.caption("本次未启用自动竞品发现。")
        elif not discovery_result.ok:
            st.warning(discovery_result.note)
        else:
            st.success(discovery_result.note)
            rows = []
            base_ids = {p.product_id for p in products if p.origin in ("user", "search")}
            for product in products:
                rows.append({
                    "来源": ("拼多多搜索" if product.origin == "search"
                             else "你提供的链接" if product.product_id in base_ids else "自动发现"),
                    "商品": product.name,
                    "价格": product.offer.display(),
                    "口碑": product.rating.display(),
                    "评价数": product.rating.review_count,
                    "数据完整度": f"{product.completeness[0]}/{product.completeness[1]}",
                    "链接": product.url,
                })
            st.dataframe(
                pd.DataFrame(rows), hide_index=True, **FIT,
                column_config={
                    "评价数": st.column_config.NumberColumn("评价数", format="localized"),
                    "链接": st.column_config.LinkColumn("链接", display_text="打开"),
                },
            )
            st.caption(
                "⚠️ 这里的竞品属于「同类型不同商品」，不是同一款在不同平台的价格。"
                "跨平台同款请看下一节「②b」。"
            )

        # ---------------- 4b. 跨平台疑似同款 ----------------
        cross_result = st.session_state.get("cross_result")
        st.subheader("②b 跨平台疑似同款（京东 / 淘宝天猫 / 拼多多）")
        if products[0].platform not in ECOMMERCE:
            st.caption(
                f"「{products[0].platform}」商品不在电商商品体系内，没有跨平台同款概念。"
                "该能力只对京东/淘宝/拼多多商品生效。"
            )
        elif cross_result is None:
            st.caption("本次未启用跨平台同款检索。可在上方勾选「跨平台查找疑似同款」后重试。")
        elif cross_result.matches:
            st.success(cross_result.note)
            st.dataframe(
                pd.DataFrame([
                    {
                        "平台": m.platform,
                        "商品": m.name,
                        "价格": m.offer.display(),
                        "口碑": m.rating.display(),
                        "数据完整度": f"{m.completeness[0]}/{m.completeness[1]}",
                        "链接": m.url,
                    }
                    for m in cross_result.matches
                ]),
                hide_index=True, **FIT,
                column_config={"链接": st.column_config.LinkColumn("链接", display_text="打开")},
            )
            for matched in cross_result.matches:
                detail = next((n for n in matched.notes if n.startswith("跨平台疑似同款")), "")
                if detail:
                    st.caption(f"· {matched.platform}「{matched.name[:28]}」：{detail}")
        else:
            st.warning(cross_result.note)
            for reason in cross_result.skipped:
                st.caption("· " + reason)

        # ---------------- 5. 核心参数对比表 ----------------
        st.subheader("③ 核心参数对比表")
        overview = pd.DataFrame([
            {
                "商品": p.name,
                "价格": money(p.offer.price, p.offer.currency),
                "好评率": f"{p.rating.positive_rate:.0f}%" if p.rating.positive_rate is not None else UNKNOWN,
                "评价数": f"{p.rating.review_count:,}" if p.rating.review_count is not None else UNKNOWN,
                "媒体评分": f"{p.rating.score:g}/{p.rating.scale:g}" if p.rating.score else UNKNOWN,
                "类别": p.category or UNKNOWN,
                "品牌": p.brand or UNKNOWN,
                "型号/版本": p.model or UNKNOWN,
            }
            for p in products
        ])
        st.dataframe(overview, hide_index=True, **FIT)

        spec_keys: list[str] = []
        for product in products:
            for key in product.specs:
                if key not in spec_keys:
                    spec_keys.append(key)
        if spec_keys:
            with st.expander("展开：逐项参数明细（行=参数，列=商品）", expanded=False):
                detail = pd.DataFrame(
                    {product.name: {k: product.specs.get(k, UNKNOWN) for k in spec_keys} for product in products}
                )
                st.dataframe(detail, **FIT)
        st.caption("参数来自各平台公开数据；「数据源未提供」表示该平台确实没有给出这个字段。")

        # ---------------- 6/7. 价格与口碑 ----------------
        left, right = st.columns(2)
        with left:
            st.subheader("④ 价格对比")
            price_rows = [{"商品": p.name, "价格": p.offer.price}
                          for p in products if p.offer.price is not None]
            if price_rows:
                chart_df = pd.DataFrame(price_rows).set_index("商品")
                st.bar_chart(chart_df, height=280)
                if rec and rec.price_stats:
                    st.caption(
                        f"最低 {money(rec.price_stats.get('min_price'), rec.currency)} · "
                        f"中位数 {money(rec.price_stats.get('median_price'), rec.currency)} · "
                        f"最高 {money(rec.price_stats.get('max_price'), rec.currency)}"
                    )
            else:
                st.warning("所有商品都拿不到价格，无法画价格图（不会用 0 或虚构价格代替）。")
        with right:
            st.subheader("⑤ 评分与口碑")
            scatter_rows = [
                {"商品": p.name, "评价数": p.rating.review_count, "好评率": p.rating.positive_rate}
                for p in products
                if p.rating.review_count is not None and p.rating.positive_rate is not None
            ]
            if len(scatter_rows) >= 2:
                st.scatter_chart(pd.DataFrame(scatter_rows), x="评价数", y="好评率", height=280)
                st.caption("横轴评价数越多、纵轴好评率越高，口碑越可靠；左上角=评价少但分数高（需谨慎）。")
            else:
                st.caption("有完整口碑数据的商品不足 2 个，不做散点图。")
            for product in products:
                if product.rating.recent_positive_rate is not None:
                    st.caption(
                        f"· {product.name}：近 30 天 {product.rating.recent_positive_rate:.0f}%"
                        f"（{product.rating.recent_review_count or 0:,} 条）"
                    )
            if rec and rec.notes:
                for note in rec.notes:
                    st.info(note)

        # ---------------- 8. 分类推荐 ----------------
        st.subheader("⑥ 分类最优商品推荐")
        if rec:
            pick_cols = st.columns(min(3, len(rec.picks)))
            for index, pick in enumerate(rec.picks):
                with pick_cols[index % len(pick_cols)]:
                    with st.container(border=True):
                        st.markdown(f"**{pick.title}**")
                        if pick.empty:
                            st.warning(pick.empty_note)
                            continue
                        st.markdown(f"### {pick.product.name}")
                        st.caption(f"得分 {pick.score} · 置信度 {pick.confidence}")
                        st.write(pick.reason)
                        st.markdown("**优势**")
                        for item in pick.advantages:
                            st.write("· " + item)
                        st.markdown("**缺点与风险**")
                        for item in pick.drawbacks:
                            st.write("· " + item)
                        st.markdown("**适合**")
                        st.write(pick.audience)
                        if pick.data_gaps:
                            st.caption("数据不足：" + "；".join(pick.data_gaps[:4]))
                        st.caption("方法：" + pick.basis)
            st.caption(
                "评选规则对每个类别都要求至少 2 个有效样本；样本不足时**不产出冠军**，而是说明原因。"
                "「规格/内容」在游戏类目下用媒体评分与内容体量近似，不代表硬件性能。"
            )

        # ---------------- 9. 购买建议 ----------------
        st.subheader("⑦ 综合购买建议")
        if advice:
            st.caption(f"生成方式：{advice.mode}" + ("（未调用外部服务）" if advice.mode == "规则模式" else ""))
            st.markdown(f"**{advice.summary}**")
            if advice.best:
                st.success(f"首选：{advice.best.name}（{advice.best.offer.display()}）\n\n{advice.best_reason}")
            if advice.alternatives:
                st.markdown("**不同预算下的替代选择**")
                st.dataframe(
                    pd.DataFrame(advice.alternatives), hide_index=True, **FIT,
                    column_config={"链接": st.column_config.LinkColumn("链接", display_text="打开")},
                )
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**各商品关键优缺点**")
                for item in advice.pros_cons:
                    with st.expander(item["商品"]):
                        st.write("优点：" + "；".join(item["优点"]))
                        st.write("缺点：" + "；".join(item["缺点"]))
                st.markdown("**不推荐购买的情况**")
                for item in advice.avoid:
                    st.write(f"· {item['商品']}：{item['原因']}")
            with col_b:
                st.markdown("**购买前需要确认的问题**")
                for line in advice.checklist:
                    st.write("· " + line)
                if advice.notes:
                    st.markdown("**说明**")
                    for note in advice.notes:
                        st.caption("· " + note)
        else:
            st.caption("没有生成建议。")

        # ---------------- 10. 来源与可信度 ----------------
        st.subheader("⑧ 数据来源、更新时间与可信度")
        source_rows = []
        for product in products:
            for field, keys in {
                "商品名称": ("name", "detail"),
                "价格": ("price",),
                "好评率": ("rating_rate", "rating"),
                "核心参数": ("specs", "detail"),
            }.items():
                source_rows.append({
                    "商品": product.name,
                    "字段": field,
                    "来源": field_source(product, *keys),
                    "方式": (product.source_of(keys[0]) or product.source_of("detail")).method
                    if (product.source_of(keys[0]) or product.source_of("detail")) else "—",
                })
        st.dataframe(pd.DataFrame(source_rows), hide_index=True, **FIT)
        st.caption(
            f"本次结果生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。"
            "价格是采集时刻的公开标价，可能随时变化；缓存有效期默认 1 小时。"
        )
        with st.expander("可信度自评（点击展开）"):
            st.markdown(
                """
                - **高**：字段完整度 ≥ 80%，且评价数 ≥ 500（来自平台官方接口 + 公开商品页交叉核对）。
                - **中**：字段完整度 ≥ 60%，评价数 ≥ 50。
                - **低**：字段残缺或评价样本太少，只能作为线索，不能作为结论。
                - 所有缺失字段都显示为「数据源未提供」，**没有用 0、默认值或估算值填充**。
                """
            )

        # ---------------- 11. 数据不足时的出口 ----------------
        weak = [p for p in products if not has_enough_data(p)]
        if weak:
            st.subheader("⑨ 数据不足的商品：直接去平台核实")
            st.caption(
                "这些商品缺少价格，或口碑/规格都拿不到，本项目不会用估算值凑一个结论。"
                "下面是平台自己的公开搜索页，点开即可人工核对——链接里不含推广参数。"
            )
            for product in weak:
                with st.expander(f"{product.name or '未识别的商品'} · 缺失 {len(product.missing)} 项"):
                    st.write("缺失项：" + ("、".join(product.missing) or "无记录"))
                    for link in fallback_links(product, limit=3):
                        st.markdown(f"- [{link['平台']} · {link['说明']}]({link['链接']})")

# ================================================================ ③ 手动
with tab_manual:
    st.subheader("手动录入 / 调整 / 自定义权重")
    st.caption(
        "这条流程保留自 V0.1：适合自动识别覆盖不到的商城，或你想自己给「实用度」打分。"
        "与自动流程可以混用。"
    )
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_a:
        if st.button("载入虚构演示数据"):
            st.session_state.legacy = clean_legacy(pd.DataFrame(demo_rows(), columns=LEGACY_COLUMNS))
    with col_b:
        uploaded = st.file_uploader("导入 CSV（列名见下表）", type="csv")
        if uploaded and st.button("导入 CSV"):
            try:
                frame = pd.read_csv(io.BytesIO(uploaded.getvalue()), encoding="utf-8-sig")
                if "商品" not in frame.columns:
                    st.error("CSV 必须包含「商品」列")
                else:
                    st.session_state.legacy = clean_legacy(frame)
                    st.success("导入完成")
            except (ValueError, UnicodeDecodeError, pd.errors.ParserError) as exc:
                st.error(f"CSV 读取失败：{exc}")
    with col_c:
        if st.button("清空手动表"):
            st.session_state.legacy = pd.DataFrame(columns=LEGACY_COLUMNS)
        if st.button("把自动结果导入此表"):
            rows = [p.to_legacy_row() for p in st.session_state.products]
            if rows:
                st.session_state.legacy = clean_legacy(pd.DataFrame(rows, columns=LEGACY_COLUMNS))
                st.success(f"已导入 {len(rows)} 个自动识别的商品，可继续手动补实用度")
            else:
                st.warning("还没有自动识别的结果")

    with st.expander("手动添加一条（旧版爬虫/补录）", expanded=False):
        url_in = st.text_input("商品页链接", key="legacy_url")
        if st.button("尝试抓取这个链接"):
            try:
                st.session_state.legacy = clean_legacy(
                    pd.concat([st.session_state.legacy,
                               pd.DataFrame([scrape(url_in)], columns=LEGACY_COLUMNS)],
                              ignore_index=True)
                )
                st.success("已抓入表格")
            except ScrapeError as exc:
                st.warning(f"抓取失败：{exc}")
            except Exception as exc:
                st.error(f"解析异常：{str(exc)[:140]}")

    edited = st.data_editor(
        st.session_state.legacy, num_rows="dynamic", hide_index=True,
        **FIT, key="legacy_editor", disabled=["采集时间"],
        column_config={
            "链接": st.column_config.LinkColumn("链接", display_text="打开"),
            "价格": st.column_config.NumberColumn("价格", min_value=0, format="%.2f"),
            "评分": st.column_config.NumberColumn("评分", min_value=0),
            "满分": st.column_config.NumberColumn("满分", min_value=0),
            "评价数": st.column_config.NumberColumn("评价数", min_value=0, step=1),
            "好评率%": st.column_config.NumberColumn("好评率%", min_value=0, max_value=100),
            "实用度(1-10)": st.column_config.NumberColumn("实用度(1-10)", min_value=0, max_value=10),
        },
    )
    st.session_state.legacy = clean_legacy(edited)

    st.markdown("**自定义权重**")
    p1, p2, p3 = st.columns(3)
    w_price = p1.slider("价格权重", 0, 100, 35, 5, key="legacy_w_price")
    w_utility = p2.slider("实用度权重", 0, 100, 40, 5, key="legacy_w_utility")
    w_rep = p3.slider("口碑权重", 0, 100, 25, 5, key="legacy_w_rep")

    if sum((w_price, w_utility, w_rep)) == 0:
        st.warning("至少设置一个非零权重")
    elif st.session_state.legacy.empty:
        st.info("先抓取/导入/载入演示数据。")
    else:
        frame = st.session_state.legacy
        currencies = sorted(c for c in frame["币种"].unique() if c)
        if not currencies:
            st.warning("请先给商品填写币种，例如 CNY、USD。不同币种不能直接比较。")
        else:
            currency = st.selectbox("只比较此币种", currencies)
            subset = frame[frame["币种"] == currency]
            if len(currencies) > 1:
                st.caption("检测到多种货币，已分组；工具不做汇率换算。")
            result = score_items(subset, (w_price, w_utility, w_rep))
            view = ["商品", "价格", "币种", "实用度(1-10)", "评分", "满分", "评价数",
                    "好评率%", "价格分", "实用分", "口碑分", "综合分", "口碑依据", "链接"]
            st.dataframe(
                result[view], **FIT, hide_index=True,
                column_config={"链接": st.column_config.LinkColumn("商品链接", display_text="查看")},
            )
            complete = result.dropna(subset=["综合分"])
            if not complete.empty:
                best = complete.iloc[0]
                st.success(
                    f"手动表综合推荐：{best['商品']}（{best['综合分']:.1f} 分）。"
                    "分数依赖你填的实用度与口碑，买前请核对实际支付价与售后。"
                )
            if len(complete) < len(result):
                st.warning(
                    f"有 {len(result) - len(complete)} 件商品缺少必要数据，无法参与综合排名；"
                    "不会用零或虚构数据填充。"
                )
            st.download_button(
                "下载手动对比结果 CSV", result.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"手动对比_{currency}_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv",
            )

# ================================================================ ④ 数据
with tab_data:
    st.subheader("本地数据库与采集记录")
    stats = db.stats()
    cols = st.columns(3)
    cols[0].metric("已保存商品", stats["商品数"])
    cols[1].metric("价格记录", stats["价格记录数"])
    cols[2].metric("采集日志", stats["采集日志数"])
    st.caption(f"数据库文件：`{db.path}`（SQLite，只在本机）")

    st.markdown("**价格历史**")
    saved = db.load_recent(50)
    if saved:
        labels = {f"{p.name}（{p.product_id}）": p.product_id for p in saved}
        chosen = st.selectbox("选择商品", list(labels))
        history = db.price_history(labels[chosen])
        if history:
            st.dataframe(pd.DataFrame(history), hide_index=True, **FIT)
            st.caption("同一价格不会重复记录；每次价格变化追加一条，可用于观察波动。")
        else:
            st.caption("这个商品还没有价格记录。")
    else:
        st.caption("数据库中还没有商品。先在上面的自动流程里识别一个商品。")

    st.markdown("**最近 30 条采集日志**")
    logs = db.recent_logs(30)
    if logs:
        st.dataframe(pd.DataFrame(logs), hide_index=True, **FIT)
        st.caption("失败记录会写明失败类型：blocked=访问限制、robots=网站禁止、notfound=页面不存在、network=网络问题。")
    else:
        st.caption("暂无记录。")

    st.divider()
    c1, c2 = st.columns(2)
    if c1.button("清空商品与价格历史"):
        db.clear_products()
        st.success("已清空商品与价格记录")
    if c2.button("清空全部（含采集日志）"):
        db.clear_all()
        st.success("已清空全部本地数据")

# ================================================================ ⑦ 说明
with tab_about:
    st.subheader("能力边界（请务必先看这一节）")
    rows = [
        {"能力": "Steam 商店链接自动识别", "状态": "✅ 真实可用",
         "说明": "官方公开 API + 公开商品页；已验证多个真实链接"},
        {"能力": "其他网站商品识别", "状态": "⚠️ 视站点而定",
         "说明": "仅当页面发布标准商品结构化数据（schema.org/JSON-LD）时可自动读取"},
        {"能力": "同类型竞品自动发现", "状态": "✅ 仅 Steam",
         "说明": "用 Steam 搜索页按「类型 + 价格区间」检索真实商品"},
        {"能力": "同款商品跨平台比价", "状态": "❌ 未实现",
         "说明": "只有一个平台适配器，无法做同款跨平台比价，界面会明确提示"},
        {"能力": "用户评测正文采集", "状态": "❌ 不可得",
         "说明": "Steam 评测接口返回占位页；只用官方汇总的好评率与评价数"},
        {"能力": "大模型购买建议", "状态": "⚙️ 可选",
         "说明": "未配置接口时用规则算法；配置后才可选用，且需你手动勾选同意"},
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, **FIT)

    st.subheader("已注册的采集适配器")
    st.dataframe(pd.DataFrame(supported_platforms()), hide_index=True, **FIT)

    st.subheader("推荐算法怎么算的")
    st.markdown(
        """
| 类别 | 公式 | 数据要求 |
|---|---|---|
| 性价比最优 | 平滑好评率 × (组内最低价 ÷ 本品价) | 价格 + 好评率 |
| 口碑较好 | (好评率×评价数 + 70×50) ÷ (评价数+50) | 好评率 |
| 规格/内容最优 | 媒体评分 60% + 支持语言 20% + 功能特性 20% | 至少 2 项 |
| 预算友好 | 先筛口碑 ≥ 75 分，再取最低价 | 价格 + 好评率 |
| 综合最推荐 | 性价比 30% + 口碑 35% + 规格 25% + 价格 10% | 权重覆盖 ≥ 60% |

约束：同一类别至少 2 个有效样本，否则**不产出冠军**并说明原因；
多种货币不换算、只比较数量最多的那一种；缺失字段不补零。
        """
    )

    st.subheader("合规与隐私")
    st.markdown(
        """
- 只访问公开页面与公开接口；遵守 `robots.txt`；请求之间默认间隔 1.2 秒；不批量抓取。
- 不绕过登录、验证码、付费墙或风控；遇到 401/403/429 直接停止并如实报错。
- 数据只存在本机 SQLite（`data/betterbuy.sqlite3`），不上传任何服务器，不建立账号体系。
- 大模型为**可选**能力：只有你勾选同意、且本机配置了接口时才会把商品事实发出去，不包含个人信息。
- 不得用本工具做绕过平台规则的价格爬取；采集结果仅供个人比价参考。
        """
    )

    st.subheader("已知限制")
    st.markdown(
        """
1. 京东/淘宝天猫/拼多多走**官方开放接口**，但都需要你先申请资质并配置密钥；
   未配置时这三个平台只能识别链接、不能采集商品（不会退回抓页面）。
2. 三家都是「推广/联盟」口径，只有进入推广池的商品查得到；不是全站商品库。
3. 三家都**不提供好评率百分比**，也不提供评价正文，因此电商商品没有口碑分，
   推荐结果会标注「数据不足」，不会用销量凑一个假好评率。
4. 跨平台同款是「品牌 + 型号 + 标题相似度」算出来的**疑似同款**，不是平台官方标识。
5. 自动竞品发现（同平台同类）目前只在 Steam 上验证过；电商平台的同类检索靠关键词接口。
6. Steam 好评率使用页面提供的**语言维度**汇总口径，与全语言总数可能不同，界面已标注。
7. 价格是采集时刻的公开标价，不含运费、税费与券后价。
8. 未做真实用户访谈，推荐权重基于常识设定，可自行调整。
9. 为保证零成本，每个数据源都有**每日请求上限**，超限当天直接停止外部请求（详见「⑥ 零成本与配额」）。
10. 免费部署（Streamlit Cloud）的文件系统非持久，重启后本地缓存与价格历史会丢失。
11. 理性消费分析用的是**规则引擎**（关键词 + 判定表 + 阈值），不是大模型。
    它能识别品类、预算、用途、频率与风险信号，但不懂真正的语义
    （例如「我不想要太吵的」不一定会被理解为「安静是硬需求」），所以解析结果一律展示给你确认。
12. 品类知识库目前覆盖 14 类商品，其他品类走兜底逻辑，不会硬套不存在的规则。
13. 绿色消费建议是**定性判断**，不含任何碳排放、节约金额的量化估算——没有可靠数据就不给数字。
14. 「推荐购买」只在**有真实商品数据**时才会出现；没有价格数据时最多到「建议进一步比较」。
15. 判定阈值（风险累计 5 分建议暂缓、3 分建议比较）基于常识设定，**没有用真实用户行为校准过**。
16. 兼容性只能提醒你确认，不能替你判定。因为拿不到你的设备型号，任何「兼容/不兼容」的结论都是猜的。
11. 大模型增强在零成本模式下**默认禁用**，推荐与建议全部由本地规则算法生成。
        """
    )

    st.caption(
        f"BetterBuy 智选 · 公益版 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        "数据来源与算法均可在「④ 数据与缓存」核查。本页所有结论均由规则引擎产生，未调用大模型。"
    )

# ================================================================ ⑤ 电商平台
with tab_ecom:
    st.subheader("电商开放平台接入状态")
    st.caption(
        "京东 / 淘宝天猫 / 拼多多的自动识别都走**官方开放接口**，不抓商品页、不绕验证码。"
        "密钥只在本机后端环境变量里读取，不会出现在网页、前端代码或日志中。"
    )

    cred_rows = credential_rows()
    configured_count = sum(1 for row in cred_rows if row["密钥状态"] == "已配置")
    if configured_count == 0:
        st.warning(
            "三家平台都还没有配置密钥，所以现在粘贴京东/淘宝/拼多多链接会明确报"
            "「未配置密钥」，不会返回任何商品数据。这是刻意设计：宁可什么都不显示，"
            "也不用估算值或示例数据冒充真实价格。"
        )
    else:
        st.success(f"已配置 {configured_count}/3 个平台，配置好的平台可以直接粘贴链接采集。")

    st.dataframe(pd.DataFrame(cred_rows), hide_index=True, **FIT)

    st.markdown("---")
    st.subheader("① 各平台需要什么")
    for item in PLATFORMS:
        with st.expander(f"{item['label']} · 密钥与接口清单"):
            st.markdown(f"**网关**：`{item['gateway']}`")
            st.markdown(f"**接入资格**：{item['qualification']}")
            st.markdown("**环境变量**（在启动前设置，不要写进代码或网页）")
            st.dataframe(pd.DataFrame(describe(item["key"])), hide_index=True, **FIT)
            st.markdown("**用到的接口**")
            st.dataframe(pd.DataFrame(item["apis"]), hide_index=True, **FIT)

    st.markdown("---")
    st.subheader("② 每个字段到底能不能拿到")
    st.caption("这张表是按官方文档整理的，✅ 有、⚠️ 有条件、❌ 接口不提供。取不到的一律显示「数据源未提供」。")
    st.dataframe(pd.DataFrame(FIELD_MATRIX), hide_index=True, **FIT)
    st.error(
        "关键结论：三家都不提供「好评率百分比」，也都不提供评价正文。 "
        "淘宝只有布尔值 h_good_rate（还需 SA 级权限），拼多多完全没有口碑字段。"
        "所以这几个平台上的商品，本项目不会算口碑分，推荐结果里会明确标注「数据不足」，"
        "不会用销量或评价数凑一个假的好评率。"
    )

    st.markdown("---")
    st.subheader("③ 链接识别自测（纯本地，不联网、不需要密钥）")
    probe = st.text_input(
        "粘贴一个商品链接试试",
        placeholder="https://item.jd.com/100012043978.html",
        key="ecom_probe",
    )
    if probe.strip():
        info = detect(probe)
        if not info.ok:
            st.error(f"未识别：{info.note}")
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("平台", info.label)
            col2.metric("商品 ID", info.item_id or "未取到")
            col3.metric("是否需要展开短链", "是" if info.is_short else "否")
            if info.note:
                st.caption(info.note)
            if info.item_id:
                st.code(f"product_id = {info.product_id}", language="text")
            st.caption(
                "识别到 ID 之后，采集会走对应平台的官方接口；接口未配置密钥时会明确报错，"
                "不会退回到抓页面。"
            )

    st.markdown("---")
    st.subheader("④ 需要你去申请的权限")
    st.caption("这些是平台侧的资质，程序无法代办。按这张表逐条办完，把密钥填进环境变量即可。")
    st.dataframe(pd.DataFrame(permission_checklist()), hide_index=True, **FIT)

    with st.expander("配置好密钥后怎么验证（命令行）"):
        st.code(
            "set BETTERBUY_JD_APP_KEY=你的appkey\n"
            "set BETTERBUY_JD_APP_SECRET=你的secret\n"
            "python scripts/check_openapi.py --platform jd --live\n",
            language="bat",
        )
        st.caption(
            "脚本会先检查密钥是否齐全，再做一次真实调用，并打印「请求成功/权限不足/签名错误」"
            "与返回的字段清单。它不会写入任何商品数据到界面。"
        )

    with st.expander("拼多多专用自检与错误码对照"):
        st.code(
            "python scripts/check_pdd.py                       # 离线：只查配置与参数，不发请求\n"
            "python scripts/check_pdd.py --live --keyword 机械键盘\n"
            "python scripts/check_pdd.py --live --detail --link  # 再验详情与转链\n",
            language="bash",
        )
        st.caption(
            "退出码直接对应失败类型：0 成功 / 2 未配置 / 3 凭证错误 / 4 权限不足 / "
            "5 需要授权备案 / 6 限流 / 7 无结果 / 8 网络或其他。"
        )
        st.dataframe(pd.DataFrame(ERROR_CODE_TABLE), hide_index=True, **FIT)

# ================================================================ ⑥ 零成本
with tab_cost:
    st.subheader("零成本运行状态")
    usage = usage_summary()
    if usage["zero_cost"]:
        st.success(
            "零成本模式已开启。当前所有外部请求都是免费接口（Steam 公开接口/页面 + 三家电商的免费推广接口），"
            "推荐与建议全部由本地 Python 规则算法生成，不调用任何按量计费的模型接口。"
        )
    else:
        st.error(
            "零成本模式已关闭。此时若你配置了 BETTERBUY_LLM_* 三项，大模型路径会被启用，"
            "可能产生费用。开发者阶段建议保持开启。"
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("今日外部请求", f"{usage['used']} 次")
    m2.metric("每日上限（合计）", f"{usage['limit']} 次")
    m3.metric("剩余额度", f"{usage['remaining']} 次")
    m4.metric("大模型调用", "禁止" if usage["zero_cost"] else "允许")

    if usage["exhausted"]:
        st.error(
            "今日额度已用尽，所有外部请求已停止（这是零成本约束的硬性保护）。"
            "明天自动恢复；也可以按下面的说明调高上限。"
        )
    elif usage["blocked_sources"]:
        st.warning("以下数据源今日已达上限，请求会被直接拦下：" + "、".join(usage["blocked_sources"]))

    st.markdown("---")
    st.subheader("① 每日调用配额")
    st.caption(
        "这些数字是**本项目自设的保守上限**，不是平台公布的免费额度——平台真实额度随账号等级变化，"
        "以你控制台显示的为准。这里取保守值，是为了在最坏情况下也不会产生账单或触发风控。"
        "超限时请求不会发出，也不会自动重试。"
    )
    st.dataframe(pd.DataFrame(quota_rows()), hide_index=True, **FIT)
    st.caption(
        "计数口径：每采集一个商品链接记 1 次；电商开放接口按实际 HTTP 请求记 1 次。"
        "计数存在本地 SQLite，进程重启不清零，跨天自动从 0 开始。"
    )
    with st.expander("怎么调整上限"):
        st.code(
            "set BETTERBUY_DAILY_LIMIT_TOTAL=800   # 所有来源合计\n"
            "set BETTERBUY_DAILY_LIMIT_STEAM=600   # 单个来源\n"
            "set BETTERBUY_DAILY_LIMIT_JD=200\n"
            "set BETTERBUY_DAILY_LIMIT_TAOBAO=200\n"
            "set BETTERBUY_DAILY_LIMIT_PDD=200",
            language="bat",
        )
        st.caption("改完要重启应用才生效。设为 0 表示当天完全禁止该来源的请求。")

    st.markdown("---")
    st.subheader("② 哪些地方会花钱、哪些不会")
    for item in COST_ITEMS:
        with st.container(border=True):
            st.markdown(f"**{item['项目']}** — {item['费用']}")
            st.caption(item["说明"])

    st.markdown("---")
    st.subheader("③ 把数据公开给其他用户看，各平台允许吗")
    st.caption(
        "以下是依据各平台公开的推广者协议与接口文档整理的操作要点，不构成法律意见；"
        "真正上线前请以你签署的协议原文为准。如果拿不准，就先只在本地或小范围试用。"
    )
    for item in SHOW_POLICY:
        with st.container(border=True):
            st.markdown(f"**{item['平台']}** — {item['能否公开展示']}")
            st.caption(item["要点"])

    st.markdown("---")
    st.subheader("④ 免费部署方案")
    for item in FREE_DEPLOY:
        with st.container(border=True):
            st.markdown(f"**{item['方案']}** — {item['费用']} · 适用：{item['适用']}")
            st.caption(item["要点"])
    st.warning(
        "Streamlit Community Cloud 免费档的文件系统不是持久存储：应用休眠或重新部署后，"
        "本地 SQLite 缓存会丢失，需要重新采集。这不影响功能，只影响「是否命中缓存」。"
        "如果你需要长期保留价格历史，只能在自己机器上跑，或改用持久化存储（那会产生费用，本版不引入）。"
    )
    with st.expander("Streamlit Community Cloud 免费部署步骤"):
        st.markdown(
            "1. 把项目推到 GitHub 公开仓库（`data/`、`reports/`、`backups/` 已在 `.gitignore` 里，不会上传）；\n"
            "2. 打开 https://share.streamlit.io ，用 GitHub 登录；\n"
            "3. New app → 选仓库与分支 → Main file path 填 `app.py` → Deploy；\n"
            "4. 需要密钥的话，在 App settings → Secrets 里按 TOML 写，例如：\n"
            "   ```toml\n"
            "   BETTERBUY_JD_APP_KEY = \"你的key\"\n"
            "   BETTERBUY_JD_APP_SECRET = \"你的secret\"\n"
            "   ```\n"
            "5. 免费档不需要绑卡，也不会自动升级到付费档。"
        )
        st.caption(
            "本项目没有引入任何需要付费的组件（无云数据库、无对象存储、无模型 API、无监控服务），"
            "所以免费档就是完整功能，不存在「免费版阉割」。"
        )

    st.markdown("---")
    st.subheader("⑤ 零成本能做到 / 做不到")
    col_yes, col_no = st.columns(2)
    with col_yes:
        st.markdown("**可以，而且现在就能跑**")
        for line in [
            "粘贴 Steam 链接 → 自动识别商品、价格、好评率、评价数",
            "自动发现同类竞品（Steam 搜索页）",
            "多商品对比表、价格图、口碑散点图",
            "五类推荐（性价比/规格/预算/口碑/综合），每类都写明依据",
            "规则版购买建议（预算解析、分档替代、确认清单）",
            "SQLite 缓存 + 价格历史 + 采集时间展示",
            "京东/淘宝/拼多多的链接识别与商品 ID 提取（不联网）",
            "每日配额硬限制，超限即停",
        ]:
            st.write("· " + line)
    with col_no:
        st.markdown("**做不到，或者需要额外条件**")
        for line in [
            "京东/淘宝/拼多多的真实价格：需要你申请到推广接口权限（接口本身免费）",
            "这三家的好评率与评价正文：接口根本不返回，给钱也拿不到",
            "跨平台「同款」官方标识：平台没有这种接口，只能用标题相似度推断",
            "评测正文（Steam）：接口返回占位页，拿不到",
            "云端持久化价格历史：免费档文件系统非持久",
            "大模型润色过的建议：默认禁用（会产生费用）",
        ]:
            st.write("· " + line)

    st.markdown("---")
    st.subheader("⑥ 如果你以后想改配置")
    st.code(
        "BETTERBUY_ZERO_COST=1              # 1=零成本模式（默认），0=允许付费路径\n"
        "BETTERBUY_DAILY_LIMIT_TOTAL=800    # 所有来源每日合计上限\n"
        "BETTERBUY_CACHE_TTL=3600           # 缓存有效期（秒），越大越省请求\n"
        "BETTERBUY_MIN_INTERVAL=1.2         # 同一主机请求最小间隔（秒）\n"
        "BETTERBUY_DB=data/betterbuy.sqlite3  # 数据库位置",
        language="bat",
    )
    st.caption("这些都在后端环境变量里，不会出现在网页或前端代码中。")
