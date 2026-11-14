"""绿色消费建议与购买风险提示的测试。

两条红线：
1. 绿色建议里不许出现任何编造的量化环境效益（碳减排、节约金额）。
2. 说不清的东西必须是「待核实」，不能变成「质量有问题」。
"""
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from betterbuy.green import build_green
from betterbuy.models import Offer, Product, Rating, Source
from betterbuy.risk import (LEVEL_HIGH, LEVEL_OK, LEVEL_TODO, assess)

ROOT = Path(__file__).resolve().parent.parent
#: 禁止出现在建议文案里的量化环境指标写法
FORBIDDEN = [
    r"\d+\s*(kg|千克|公斤|吨)\s*(CO2|二氧化碳|碳)",
    r"(减排|减少碳排放|碳足迹)\s*\d+",
    r"省下\s*\d+",
    r"节约\s*\d+\s*元",
]


def make_product(**kwargs) -> Product:
    product = Product(
        product_id=kwargs.pop("product_id", "p:1"),
        platform=kwargs.pop("platform", "Steam"),
        url=kwargs.pop("url", "https://example.com/1"),
        **kwargs,
    )
    if not product.sources:
        product.sources["price"] = Source("test", "测试来源", product.url)
        product.field_sources["price"] = "price"
    return product


class GreenTests(unittest.TestCase):
    def test_no_quantified_environmental_claims_in_source(self):
        for name in ("catalog.py", "green.py"):
            text = (ROOT / "betterbuy" / name).read_text(encoding="utf-8")
            for pattern in FORBIDDEN:
                self.assertIsNone(
                    re.search(pattern, text),
                    f"{name} 里出现了被禁止的量化环境表述：{pattern}",
                )

    def test_every_tip_has_an_actionable_check(self):
        for key in ("laptop", "chair", "headphone", "stationery", "game"):
            advice = build_green(key)
            self.assertTrue(advice.items, key)
            for item in advice.items:
                self.assertTrue(item.check.endswith(("?", "？")), f"{key}/{item.title} 缺少自查问句")
                self.assertGreater(len(item.detail), 10)

    def test_ladder_is_in_the_right_order(self):
        advice = build_green("laptop")
        self.assertEqual(advice.ladder[0], "能不能不买")
        self.assertEqual(advice.ladder[-1], "最后才是买新的")

    def test_disclaimers_state_no_numbers(self):
        advice = build_green("laptop")
        joined = " ".join(advice.disclaimers)
        self.assertIn("不含任何碳排放", joined)
        self.assertIn("不给数字", joined)

    def test_essential_note_prevents_over_discouragement(self):
        advice = build_green("stationery", essential=True)
        self.assertTrue(advice.essential_note)
        self.assertIn("必需", advice.essential_note)

    def test_unknown_category_still_gives_something(self):
        advice = build_green("other")
        self.assertTrue(advice.items)
        self.assertTrue(advice.family_label)

    def test_family_tips_do_not_duplicate_category_tips(self):
        """同族通用建议不能和品类专属建议说同一件事。"""
        for key in ("laptop", "phone", "headphone", "monitor", "keyboard", "chair"):
            titles = [item.title for item in build_green(key).items]
            self.assertEqual(len(titles), len(set(titles)), f"{key} 有重复建议：{titles}")

    def test_no_account_sharing_or_breaking_tos_advice(self):
        for key in ("game", "laptop", "phone"):
            text = " ".join(
                item.title + item.detail for item in build_green(key).items
            )
            for banned in ("共享账号", "破解", "盗版", "非官方渠道"):
                self.assertNotIn(banned, text, f"{key} 出现了不合规建议：{banned}")


class RiskTests(unittest.TestCase):
    def test_no_products_gives_todo_not_no_risk(self):
        report = assess([], category_key="monitor")
        self.assertTrue(report.items)
        self.assertEqual(report.items[0].level, LEVEL_TODO)
        self.assertIn("清单", report.note)
        # 即使没有商品，兼容性与售后仍然要给出待核实项        self.assertTrue(any(i.kind in ("compat_todo", "compat_soft") for i in report.items))
        self.assertTrue(any(i.kind == "after_sales" for i in report.items))

    def test_missing_reviews_is_todo_not_bad_quality(self):
        product = make_product(name="某商品", rating=Rating())
        report = assess([product], category_key="chair")
        item = next(i for i in report.items if i.kind == "no_review")
        self.assertEqual(item.level, LEVEL_TODO)
        self.assertIn("不代表商品不好", item.detail)

    def test_few_reviews_flagged(self):
        product = make_product(name="新品", rating=Rating(positive_rate=100, review_count=3))
        report = assess([product], category_key="chair")
        self.assertTrue(any(i.kind == "few_reviews" for i in report.items))

    def test_stale_price_is_high_risk(self):
        old = (datetime.now() - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        product = make_product(name="旧数据", fetched_at=old,
                               offer=Offer(price=299, currency="CNY"),
                               rating=Rating(positive_rate=95, review_count=900))
        report = assess([product], category_key="chair")
        item = next(i for i in report.items if i.kind == "price_stale")
        self.assertEqual(item.level, LEVEL_HIGH)

    def test_fresh_price_no_staleness_warning(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        product = make_product(name="新数据", fetched_at=now,
                               offer=Offer(price=299, currency="CNY"),
                               rating=Rating(positive_rate=95, review_count=900))
        report = assess([product], category_key="chair")
        self.assertFalse(any(i.kind in ("price_stale", "price_aged") for i in report.items))

    def test_missing_sources_flagged(self):
        product = make_product(name="无来源")
        product.sources.clear()
        product.field_sources.clear()
        report = assess([product], category_key="chair")
        self.assertTrue(any(i.kind == "source_unclear" for i in report.items))

    def test_complete_sources_are_acknowledged(self):
        product = make_product(name="有来源", offer=Offer(price=100, currency="CNY"),
                               rating=Rating(positive_rate=90, review_count=500))
        report = assess([product], category_key="chair")
        self.assertTrue(any(i.level == LEVEL_OK for i in report.items))

    def test_model_mismatch_is_high_risk(self):
        a = make_product(product_id="a", name="小米显示器 A22 节能版", model="A22",
                         offer=Offer(price=499, currency="CNY"),
                         rating=Rating(positive_rate=90, review_count=100))
        b = make_product(product_id="b", name="小米显示器 A22 节能版 增强版", model="A22i",
                         offer=Offer(price=599, currency="CNY"),
                         rating=Rating(positive_rate=90, review_count=100))
        report = assess([a, b], category_key="monitor")
        self.assertTrue(any(i.kind == "model_mismatch" for i in report.items))

    def test_spec_difference_flagged(self):
        a = make_product(product_id="a", name="甲", specs={"容量": "512GB"},
                         offer=Offer(price=1000, currency="CNY"))
        b = make_product(product_id="b", name="乙", specs={"容量": "1TB"},
                         offer=Offer(price=1500, currency="CNY"))
        report = assess([a, b], category_key="laptop")
        self.assertTrue(any(i.kind == "spec_diff" for i in report.items))

    def test_compat_is_todo_not_incompatible(self):
        product = make_product(name="某显示器", offer=Offer(price=999, currency="CNY"),
                               rating=Rating(positive_rate=92, review_count=300))
        report = assess([product], category_key="monitor")
        compat = [i for i in report.items if i.kind in ("compat_todo", "compat_soft")]
        self.assertTrue(compat)
        for item in compat:
            self.assertIn(item.level, (LEVEL_TODO, "低"))
            self.assertNotIn("不兼容", item.label)

    def test_after_sales_always_todo(self):
        product = make_product(name="某商品")
        report = assess([product], category_key="chair")
        item = next(i for i in report.items if i.kind == "after_sales")
        self.assertEqual(item.level, LEVEL_TODO)

    def test_levels_are_from_allowed_set(self):
        allowed = {"高", "中", "低", "待核实", "已核实"}
        product = make_product(name="某商品", offer=Offer(price=100, currency="CNY"))
        report = assess([product], category_key="chair")
        for item in report.items:
            self.assertIn(item.level, allowed)


if __name__ == "__main__":
    unittest.main()
