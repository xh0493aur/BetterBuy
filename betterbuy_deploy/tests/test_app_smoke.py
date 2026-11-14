"""界面冒烟测试（离线，不联网）。

做法：把 SQLite 指向临时文件，写入两份由真实响应映射出来的商品快照，
再让 AppTest 走完"提交链接 -> 命中缓存 -> 渲染结果"的完整路径。
这样可以验证界面在真实数据形状下的渲染、指标块与推荐卡片，而不依赖网络。
"""
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

TMPDIR = tempfile.mkdtemp(prefix="betterbuy_smoke_")
os.environ["BETTERBUY_DB"] = os.path.join(TMPDIR, "smoke.sqlite3")
os.environ["BETTERBUY_MIN_INTERVAL"] = "0"

from streamlit.testing.v1 import AppTest  # noqa: E402

import betterbuy.adapters as adapters_mod  # noqa: E402
import betterbuy.cache as cache_mod  # noqa: E402
from betterbuy.adapters.steam import product_from_appdetails  # noqa: E402
from betterbuy.models import Offer, Rating, Source  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def build_products():
    data = json.loads((FIXTURES / "appdetails_413150_excerpt.json").read_text(encoding="utf-8"))["413150"]["data"]
    first = product_from_appdetails(
        413150, data, "https://store.steampowered.com/app/413150/",
        Source(adapter="steam", label="Steam 官方公开 API", url="https://api"),
    )
    first.rating.positive_rate = 97.0
    first.rating.review_count = 201909
    first.rating.scope = "简体中文评测"

    second_data = dict(data)
    second_data.update({
        "name": "Terraria", "steam_appid": 105600,
        "developers": ["Re-Logic"], "publishers": ["Re-Logic"],
        "metacritic": {"score": 83}, "recommendations": {"total": 1000000},
        "price_overview": {"currency": "CNY", "initial": 4200, "final": 4200,
                           "discount_percent": 0, "final_formatted": "¥ 42.00"},
        "dlc": [],
    })
    second = product_from_appdetails(
        105600, second_data, "https://store.steampowered.com/app/105600/",
        Source(adapter="steam", label="Steam 官方公开 API", url="https://api"),
    )
    second.rating.positive_rate = 98.0
    second.rating.review_count = 171099
    second.rating.scope = "简体中文评测"
    second.specs = {"支持平台": "Windows / macOS / Linux", "支持语言": "12 种：简体中文",
                    "功能特性": "单人、多人、合作", "Metacritic": "83/100",
                    "评价总数（全语言）": "1,000,000 条"}
    for product in (first, second):
        product.recompute_missing()
        product.status = "已识别（测试夹具）"
    return first, second


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 让 adapters 与 app 都用临时库（模块级单例可能已被别的测试导入过）
        cache_mod.db = cache_mod.Database()
        adapters_mod.db = cache_mod.db
        first, second = build_products()
        cls.urls = [first.url, second.url]
        for product in (first, second):
            cache_mod.db.save_product(product)

    def _run(self, urls=None, discover=False):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.run()
        if urls:
            area = next(t for t in at.text_area if t.label == "商品链接")
            button = next(b for b in at.button if b.label == "开始智能比价")
            area.set_value("\n".join(urls))
            for box in at.checkbox:
                if box.label == "自动发现同类竞品":
                    box.set_value(discover)
            button.click()
            at.run()
        return at

    def _run_wise(self, text, discover=False, category="自动识别"):
        """走新的「智选」首屏流程。"""
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.run()
        area = next(t for t in at.text_area if t.label == "商品链接或购买需求")
        area.set_value(text)
        for box in at.checkbox:
            if box.label == "顺带比同类商品":
                box.set_value(discover)
        select = next(s for s in at.selectbox if s.label == "商品品类")
        select.set_value(category)
        next(b for b in at.button if b.label == "开始智能分析").click()
        at.run()
        return at

    def test_initial_page_has_no_error(self):
        at = self._run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(len(at.tabs), 7)
        self.assertTrue(any("BetterBuy" in t.value for t in at.title))
        # 没有结果时要给出可操作的引导，而不是空白
        self.assertTrue(any("还没有分析结果" in md.value for md in at.markdown))

    # ---------------- 智选首屏（公益版核心） ----------------
    def test_wise_tab_is_first_and_explains_three_inputs(self):
        at = self._run()
        self.assertEqual(list(at.exception), [])
        self.assertTrue(at.tabs[0].label.startswith("① 智选"))
        joined = " ".join(c.value for c in at.caption)
        self.assertIn("粘贴商品链接", joined)
        self.assertIn("直接描述需求", joined)

    def test_wise_flow_with_real_cached_products(self):
        at = self._run_wise("\n".join(self.urls) + "\n这个每天玩一小时，预算 100 元")
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        for expected in ("① 商品信息与可用数据", "② 价格与实用度比较", "③ AI 购买建议",
                         "④ 还有更环保、更省钱的选择吗？", "⑤ 购买风险提醒",
                         "⑥ 数据来源与更新时间"):
            self.assertIn(expected, headers)
        # 结论必须是三档之一，且写明判定方式不是大模型
        joined = " ".join(m.value for m in at.markdown)
        self.assertTrue(any(v in joined for v in ("推荐购买", "建议进一步比较", "建议暂缓购买")))
        self.assertTrue(any("非大模型" in c.value for c in at.caption))

    def test_wise_flow_without_links_still_gives_advice(self):
        at = self._run_wise("我的笔记本是拯救者 Y7000P，想买个外接显示器，预算 1000，每天写代码")
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        self.assertIn("④ 还有更环保、更省钱的选择吗？", headers)
        self.assertIn("⑤ 购买风险提醒", headers)
        joined = " ".join(m.value for m in at.markdown)
        # 没有商品数据时必须说清楚，而不是编一个价格
        self.assertTrue(any("笔记本" in m for m in joined.split("，")))

    def test_zero_cost_tab_is_present(self):
        at = self._run()
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        self.assertIn("零成本运行状态", headers)
        self.assertIn("① 每日调用配额", headers)
        self.assertIn("④ 免费部署方案", headers)
        # 零成本模式必须默认开启，并且页面明确说大模型是禁止的
        metrics = {m.label: m.value for m in at.metric}
        self.assertEqual(metrics.get("大模型调用"), "禁止")
        self.assertTrue(any("零成本模式已开启" in s.value for s in at.success))

    def test_ecommerce_tab_is_present(self):
        at = self._run()
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        self.assertIn("电商开放平台接入状态", headers)
        self.assertIn("② 每个字段到底能不能拿到", headers)
        # 未配置密钥时必须明确说出来，不能假装能采
        self.assertTrue(any("还没有配置" in w.value for w in at.warning))

    def test_full_flow_renders_all_sections(self):
        at = self._run(self.urls)
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        for expected in ("① 商品识别状态", "② 自动发现的同类竞品", "③ 核心参数对比表",
                         "④ 价格对比", "⑤ 评分与口碑", "⑥ 分类最优商品推荐",
                         "⑦ 综合购买建议", "⑧ 数据来源、更新时间与可信度"):
            self.assertIn(expected, headers)

    def test_tables_and_picks_present(self):
        at = self._run(self.urls)
        self.assertGreaterEqual(len(at.dataframe), 4)
        self.assertGreaterEqual(len(at.markdown), 5)
        joined = " ".join(md.value for md in at.markdown)
        for title in ("性价比最优", "口碑较好", "综合最推荐"):
            self.assertIn(title, joined)

    def test_advice_is_rule_mode_without_llm(self):
        at = self._run(self.urls, )
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("规则模式", captions)
        self.assertIn("未调用外部服务", captions)

    def test_sources_section_explains_provenance(self):
        at = self._run(self.urls)
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("数据源", captions)
        self.assertTrue(any("采集" in c.value for c in at.caption))

    def test_no_fake_numbers_when_currency_known(self):
        at = self._run(self.urls)
        texts = " ".join(str(df.value) for df in at.dataframe)
        self.assertNotIn("nan", texts.lower())

    def test_quota_exhausted_blocks_collection(self):
        """额度为 0 时，界面必须明确报"已达上限"，且不显示商品。"""
        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_TOTAL": "0"}):
            at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
            at.run()
            at.text_area[0].set_value("\n".join(self.urls))
            for box in at.checkbox:
                if box.label.startswith("优先使用本地缓存"):
                    box.set_value(False)
                if box.label == "自动发现同类竞品":
                    box.set_value(False)
            at.button[0].click()
            at.run()

        self.assertEqual(list(at.exception), [])
        warnings = " ".join(w.value for w in at.warning)
        self.assertIn("上限", warnings)
        # 页面顶部横幅也要反映零成本状态
        captions = " ".join(c.value for c in at.caption)
        self.assertIn("零成本模式", captions)


if __name__ == "__main__":
    unittest.main()
