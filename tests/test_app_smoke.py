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
from betterbuy.models import Offer, Product, Rating, Source  # noqa: E402

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
        """走「购买建议」首屏流程（首页直接输入 + 点「帮我分析」）。"""
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.run()
        area = next(t for t in at.text_area if t.label == "商品链接或购买需求")
        area.set_value(text)
        for box in at.checkbox:
            if box.label.startswith("顺带找同类商品"):
                box.set_value(discover)
        select = next(s for s in at.selectbox if s.label.startswith("商品品类"))
        select.set_value(category)
        next(b for b in at.button if b.label == "帮我分析").click()
        at.run()
        return at

    def test_initial_page_has_no_error(self):
        at = self._run()
        self.assertEqual(list(at.exception), [])
        self.assertEqual(len(at.tabs), 3)
        # 没有结果时要给出可操作的引导，而不是空白
        self.assertTrue(any("还没有分析结果" in md.value for md in at.markdown))

    # ---------------- 首页首屏（定位与信息层级） ----------------
    def test_home_hero_states_value_proposition(self):
        """首屏必须有产品名 + 一句话价值，且技术说明不抢注意力。"""
        at = self._run()
        self.assertEqual(list(at.exception), [])
        hero = next(m.value for m in at.markdown if "先想清楚该不该买" in m.value)
        self.assertIn("BetterBuy 智选", hero)
        self.assertIn("先想清楚该不该买，再选适合你的。", hero)
        self.assertIn("粘贴商品链接", hero)
        # 参赛信息从首屏移到页脚
        top = " ".join(c.value for c in at.caption[:3])
        self.assertNotIn("参赛作品", top)
        self.assertIn("参赛作品", " ".join(c.value for c in at.caption))

    def test_navigation_is_three_pages(self):
        """主导航收敛为三页：购买建议 / 商品对比 / 使用说明。"""
        at = self._run()
        self.assertEqual([t.label for t in at.tabs], ["购买建议", "商品对比", "使用说明"])

    def test_budget_defaults_to_empty(self):
        """预算默认空（原来默认 0，用户会误以为「预算就是 0 元」）。"""
        at = self._run()
        budget = next(n for n in at.number_input if n.label.startswith("预算（元"))
        self.assertIsNone(budget.value)
        self.assertIn("可选", budget.placeholder)

    def test_example_buttons_fill_the_input(self):
        """示例必须点一下就填进去，不能让用户手动复制。"""
        at = self._run()
        labels = [b.label for b in at.button]
        for expected in ("1000 元选显示器", "手机该修还是换", "开学用品采购", "先看看要不要买"):
            self.assertIn(expected, labels)
        next(b for b in at.button if b.label == "手机该修还是换").click()
        at.run()
        area = next(t for t in at.text_area if t.label == "商品链接或购买需求")
        self.assertIn("电池一天要充三次", area.value)

    def test_result_explains_method_source_and_time(self):
        """结论下方要说清：怎么分析的、数据从哪来、什么时候取的。"""
        at = self._run_wise("\n".join(self.urls))
        self.assertEqual(list(at.exception), [])
        caps = " ".join(c.value for c in at.caption)
        self.assertIn("分析方式：规则引擎（非大模型）", caps)
        self.assertIn("数据来源：Steam", caps)
        self.assertIn("数据更新时间：", caps)
        self.assertIn("先别急着下单", " ".join(m.value for m in at.markdown))

    def test_advanced_settings_hides_run_status(self):
        """运行状态与平台接入收进「使用说明 · 高级设置」，首页不再露出。"""
        at = self._run()
        caps = " ".join(c.value for c in at.caption)
        self.assertNotIn("零成本模式：开启", caps)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("零成本模式：开启", text)
        self.assertIn("拼多多", text)
        self.assertIn("今日外部请求", text)

    def test_wise_flow_with_real_cached_products(self):
        at = self._run_wise("\n".join(self.urls) + "\n这个每天玩一小时，预算 100 元")
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        for expected in ("① 商品信息与可用数据", "② 价格与实用度比较", "③ 购买建议与理由",
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

    def test_stale_wise_module_degrades_instead_of_crashing(self):
        """线上进程缓存旧 betterbuy.wise 时（新 app.py + 旧 run 函数），不能整页报红。

        真实事故：用户点「帮我分析」看到
        TypeError: run() got an unexpected keyword argument 'search_fn'。
        这里模拟一个没有 search_fn 参数的旧 run，验证降级提示与结果仍可用。
        """
        from betterbuy import wise as wise_mod

        real_run = wise_mod.run

        def legacy_run(query, *, category_key="", budget_cap=0.0, auto_discover=True,
                       max_links=3, use_cache=True, collect_fn=None, discover_fn=None):
            return real_run(
                query, category_key=category_key, budget_cap=budget_cap,
                auto_discover=auto_discover, max_links=max_links, use_cache=use_cache,
            )

        with mock.patch.object(wise_mod, "run", legacy_run):
            at = self._run_wise("机械键盘 87 键")

        self.assertEqual(list(at.exception), [])
        self.assertFalse(
            any("分析过程出错" in e.value for e in at.error),
            [e.value for e in at.error],
        )
        warnings = " ".join(w.value for w in at.warning)
        self.assertIn("上一个版本", warnings)
        self.assertIn("Reboot", warnings)
        # 降级后仍然出完整结果，而不是白页
        headers = [s.value for s in at.subheader]
        self.assertIn("⑤ 购买风险提醒", headers)

    def test_footer_shows_build_version(self):
        """页脚带构建版本，便于排查「页面是新的、后端模块是旧的」。"""
        from betterbuy.site import BUILD

        at = self._run()
        self.assertIn(BUILD, " ".join(c.value for c in at.caption))

    def test_compare_page_holds_links_and_manual_entry(self):
        """「商品对比」一页装下链接比价与手动录入，不再各占一个页签。"""
        at = self._run(self.urls)
        self.assertEqual(list(at.exception), [])
        headers = [s.value for s in at.subheader]
        for expected in ("① 商品识别状态", "② 自动发现的同类竞品", "③ 核心参数对比表",
                         "④ 价格对比", "⑤ 评分与口碑", "⑥ 分类最优商品推荐",
                         "⑦ 综合购买建议", "⑧ 数据来源、更新时间与可信度"):
            self.assertIn(expected, headers)
        # 手动录入还在，但收进了折叠区
        self.assertTrue(any("手动录入" in e.label for e in at.expander))
        # 原来独立成页的配置入口不再出现
        for hidden in ("本地数据库与采集记录", "电商开放平台接入状态", "零成本运行状态"):
            self.assertNotIn(hidden, headers, f"{hidden} 应该已经隐藏")

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
            # 缓存开关现在在「使用说明 · 高级设置」里，先关掉再提交，才能真的发请求
            next(b for b in at.checkbox
                 if b.label.startswith("使用本地缓存")).set_value(False)
            at.run()
            for box in at.checkbox:
                if box.label == "自动发现同类竞品":
                    box.set_value(False)
            next(t for t in at.text_area if t.label == "商品链接").set_value("\n".join(self.urls))
            next(b for b in at.button if b.label == "开始智能比价").click()
            at.run()

        self.assertEqual(list(at.exception), [])
        warnings = " ".join(w.value for w in at.warning)
        self.assertIn("上限", warnings)
        # 额度状态在高级设置里仍然可查
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("零成本模式", text)
        self.assertIn("今日外部请求", text)


class WiseNameSearchTests(unittest.TestCase):
    """只输入商品名时，也要拿到真实商品，而不是只给建议。

    这里把搜索函数换成假实现，验证的是**界面与编排层的接线**：
    1. 没贴链接 → 一定走搜索，并且搜索用的关键词是从输入里提炼的；
    2. 搜索成功 → 商品进入 ① 卡片、② 价格比较、③ 建议；
    3. 搜索失败（未配置密钥等）→ 如实说明，不编价格，其余区块照常输出。
    """

    def _open(self):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.run()
        return at

    def _submit(self, at, text):
        area = next(t for t in at.text_area if t.label == "商品链接或购买需求")
        area.set_value(text)
        next(b for b in at.button if b.label == "帮我分析").click()
        return at

    def _fake_products(self):
        first = Product(product_id="pdd:1", platform="拼多多", url="",
                        name="机械键盘 87 键 有线版")
        first.offer = Offer(price=199.0, currency="CNY", source_key="pdd")
        first.specs = {"店铺": "某数码旗舰店", "已售": "1 万+"}
        first.status = "拼多多关键词搜索结果"
        second = Product(product_id="pdd:2", platform="拼多多", url="",
                         name="机械键盘 87 键 无线版")
        second.offer = Offer(price=359.0, currency="CNY", source_key="pdd")
        second.specs = {"店铺": "另一家旗舰店", "已售": "3000+"}
        second.status = "拼多多关键词搜索结果"
        for product in (first, second):
            product.origin = "search"
            product.recompute_missing()
        return [first, second]

    def _result(self, ok=True, kind="", products=None):
        from betterbuy.pdd_search import SearchResult

        return SearchResult(
            ok=ok, keyword="机械键盘", kind=kind, products=products or [],
            message="搜到 2 件商品" if ok else "拼多多接口未配置密钥",
            used_api="pdd.ddk.goods.search", api_calls=1 if ok else 0,
        )

    def test_name_only_input_searches_and_renders_real_products(self):
        at = self._open()
        self._submit(at, "机械键盘 87 键")
        with mock.patch("betterbuy.pdd_search.search",
                        return_value=self._result(products=self._fake_products())):
            at.run()
        self.assertEqual(list(at.exception), [])
        caps = " ".join(c.value for c in at.caption)
        self.assertIn("搜索关键词：「机械键盘」", caps)
        self.assertIn("真实商品", caps)
        headers = [s.value for s in at.subheader]
        self.assertIn("① 商品信息与可用数据", headers)
        self.assertIn("② 价格与实用度比较", headers)
        # 两件商品都有价格 → 应该出现比较表，而不是"没有可比价格"
        self.assertGreaterEqual(len(at.dataframe), 1)

    def test_search_failure_is_explained_without_fake_price(self):
        at = self._open()
        self._submit(at, "机械键盘 87 键")
        with mock.patch("betterbuy.pdd_search.search",
                        return_value=self._result(ok=False, kind="no_credential")):
            at.run()
        self.assertEqual(list(at.exception), [])
        caps = " ".join(c.value for c in at.caption)
        self.assertIn("没有取到商品数据", caps)
        self.assertIn("不用估算值顶替", caps)
        # 没数据也必须继续给出绿色建议与风险清单
        headers = [s.value for s in at.subheader]
        self.assertIn("④ 还有更环保、更省钱的选择吗？", headers)
        self.assertIn("⑤ 购买风险提醒", headers)

    def test_links_take_priority_over_search(self):
        """贴了链接就不该再去打搜索接口（省一次配额，也避免答非所问）。"""
        at = self._open()
        self._submit(at, "https://store.steampowered.com/app/413150/")
        with mock.patch("betterbuy.pdd_search.search") as spy, \
                mock.patch("betterbuy.adapters.collect", side_effect=RuntimeError("测试中不联网")):
            at.run()
        self.assertFalse(spy.called, "贴了链接却仍然调用了关键词搜索")


if __name__ == "__main__":
    unittest.main()
