"""购买建议测试：预算解析、规则建议、以及"不编造商品"的接地性检查。"""
import unittest

from betterbuy.advisor import Advice, llm_advice, llm_available, parse_budget, parse_needs, rule_advice
from betterbuy.models import Offer, Product, Rating
from betterbuy.recommend import recommend


def make(name, price=None, rate=None, count=None, score=None, specs=None):
    product = Product(product_id=f"steam:{name}", platform="Steam",
                      url=f"https://example.com/{name}", name=name)
    product.offer = Offer(price=price, currency="CNY" if price is not None else None, region="CN")
    product.rating = Rating(positive_rate=rate, review_count=count, score=score,
                            scale=100.0 if score is not None else None, scope="简体中文评测")
    product.specs = specs or {}
    product.recompute_missing()
    return product


def sample_products():
    return [
        make("便宜款", price=24.0, rate=92.0, count=137201, score=83,
             specs={"支持平台": "Windows / macOS", "支持语言": "12 种：简体中文"}),
        make("均衡款", price=48.0, rate=97.0, count=201909, score=89,
             specs={"支持平台": "Windows / macOS / Linux", "支持语言": "12 种：简体中文"}),
        make("高价低分", price=90.0, rate=55.0, count=3000, score=60,
             specs={"支持平台": "Windows", "支持语言": "5 种：英语"}),
    ]


class BudgetParseTests(unittest.TestCase):
    def test_range(self):
        low, high, note = parse_budget("预算800-1200元")
        self.assertEqual((low, high), (800.0, 1200.0))
        self.assertIn("区间", note)

    def test_upper_only(self):
        low, high, _ = parse_budget("预算 60 元，主要用来放松")
        self.assertIsNone(low)
        self.assertEqual(high, 60.0)

    def test_not_exceeding(self):
        _, high, note = parse_budget("不超过300元")
        self.assertEqual(high, 300.0)
        self.assertIn("不超过", note)

    def test_plain_amount(self):
        _, high, _ = parse_budget("大概500元吧")
        self.assertEqual(high, 500.0)

    def test_none(self):
        self.assertEqual(parse_budget("想要一个好玩的东西")[:2], (None, None))
        self.assertEqual(parse_budget("")[:2], (None, None))


class NeedParseTests(unittest.TestCase):
    def test_keywords(self):
        tags = parse_needs("预算 500，主要用来学习和办公，偶尔打游戏")
        self.assertIn("学习", tags)
        self.assertIn("办公", tags)
        self.assertIn("游戏", tags)

    def test_empty(self):
        self.assertEqual(parse_needs(""), [])


class RuleAdviceTests(unittest.TestCase):
    def setUp(self):
        self.products = sample_products()
        self.rec = recommend(self.products)

    def test_budget_filters_out_expensive(self):
        advice = rule_advice(self.products, self.rec, "预算 50 元")
        self.assertIsNotNone(advice.best)
        self.assertLessEqual(advice.best.offer.price, 50.0)
        self.assertTrue(any("预算" in note for note in advice.notes))

    def test_over_budget_is_reported_with_gap(self):
        advice = rule_advice(self.products, self.rec, "预算 10 元")
        self.assertIsNone(advice.best)
        self.assertIn("没有符合", advice.summary)
        self.assertTrue(advice.avoid)
        self.assertIn("超出预算", advice.avoid[0]["原因"])

    def test_advice_only_mentions_real_products(self):
        advice = rule_advice(self.products, self.rec, "预算 60 元，用来放松")
        names = {p.name for p in self.products}
        for item in advice.alternatives:
            if item.get("商品") in ("—", ""):
                continue
            self.assertIn(item["商品"], names)
        for item in advice.pros_cons:
            self.assertIn(item["商品"], names)
        for item in advice.avoid:
            self.assertIn(item["商品"], names | {"—"})

    def test_checklist_and_pros_cons_present(self):
        advice = rule_advice(self.products, self.rec, "")
        self.assertGreaterEqual(len(advice.checklist), 3)
        self.assertTrue(advice.pros_cons)
        self.assertTrue(advice.mode.startswith("规则模式"))

    def test_avoid_flags_low_rating(self):
        advice = rule_advice(self.products, self.rec, "")
        flagged = {item["商品"] for item in advice.avoid}
        self.assertIn("高价低分", flagged)

    def test_budget_note_when_no_floor(self):
        advice = rule_advice(self.products, self.rec, "预算 100 元")
        self.assertTrue(any("下限" in note for note in advice.notes))

    def test_alternatives_when_few_products(self):
        products = self.products[:2]
        rec = recommend(products)
        advice = rule_advice(products, rec, "")
        self.assertTrue(advice.alternatives)
        self.assertIn("备选", advice.alternatives[0]["档位"])

    def test_single_product_still_works(self):
        products = self.products[:1]
        rec = recommend(products)
        advice = rule_advice(products, rec, "")
        self.assertIsNotNone(advice.best)
        self.assertTrue(advice.summary)

    def test_empty_products(self):
        advice = rule_advice([], recommend([]), "")
        self.assertIn("没有可比商品", advice.summary)
        self.assertIsNone(advice.best)


class LlmGateTests(unittest.TestCase):
    def test_unavailable_without_env(self):
        self.assertFalse(llm_available())

    def test_returns_none_when_not_allowed(self):
        products = sample_products()
        rec = recommend(products)
        self.assertIsNone(llm_advice(products, rec, "预算 50", allow_external=False))

    def test_returns_none_when_not_configured(self):
        products = sample_products()
        rec = recommend(products)
        self.assertIsNone(llm_advice(products, rec, "预算 50", allow_external=True))


class AdviceDataclassTests(unittest.TestCase):
    def test_default_mode_is_rule(self):
        self.assertEqual(Advice().mode, "规则模式")


if __name__ == "__main__":
    unittest.main()
