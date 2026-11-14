"""分类推荐算法测试：公式、样本门槛、货币分组、不产生"假冠军"。"""
import unittest

from betterbuy.models import Offer, Product, Rating
from betterbuy.recommend import (
    BUDGET_QUALITY_FLOOR, DEFAULT_WEIGHTS, MIN_SAMPLE, compute_metrics, recommend,
)


def make(name, price=None, currency="CNY", rate=None, count=None, score=None, scale=100.0,
         specs=None, product_id=None):
    product = Product(product_id=product_id or f"steam:{name}", platform="Steam",
                      url=f"https://example.com/{name}", name=name)
    product.offer = Offer(price=price, currency=currency if price is not None else None, region="CN")
    product.rating = Rating(positive_rate=rate, review_count=count, score=score,
                            scale=scale if score is not None else None, scope="简体中文评测")
    product.specs = specs or {}
    product.recompute_missing()
    return product


class FormulaTests(unittest.TestCase):
    def test_value_is_rate_times_relative_price(self):
        products = [
            make("便宜", price=24.0, rate=90.0, count=1000),
            make("贵", price=48.0, rate=90.0, count=1000),
        ]
        metrics = compute_metrics(products)
        cheap = metrics["steam:便宜"]
        pricey = metrics["steam:贵"]
        self.assertEqual(cheap.price_score, 100.0)
        self.assertEqual(pricey.price_score, 50.0)
        # 用的是平滑后口碑（(90*1000+70*50)/1050 ≈ 89.0），不是原始 90
        self.assertAlmostEqual(cheap.value_score, 89.0, places=1)
        self.assertAlmostEqual(pricey.value_score, 44.5, places=1)
        self.assertAlmostEqual(cheap.value_score / pricey.value_score, 2.0, places=1)

    def test_smoothing_penalises_few_reviews(self):
        few = compute_metrics([make("少", price=10.0, rate=100.0, count=3)])["steam:少"]
        many = compute_metrics([make("多", price=10.0, rate=100.0, count=10000)])["steam:多"]
        self.assertLess(few.smoothed_rate, many.smoothed_rate)
        self.assertGreater(many.smoothed_rate, 99.0)

    def test_missing_price_skips_value_but_keeps_reputation(self):
        products = [make("无价", price=None, rate=95.0, count=800),
                    make("有价", price=30.0, rate=90.0, count=800)]
        metrics = compute_metrics(products)
        self.assertIsNone(metrics["steam:无价"].value_score)
        self.assertIsNotNone(metrics["steam:无价"].smoothed_rate)

    def test_overall_requires_weight_coverage(self):
        """只有口碑数据（权重 35%）时覆盖不足 60%，不能给综合分。"""
        products = [make("只有口碑", price=None, rate=95.0, count=800)]
        metrics = compute_metrics(products)
        self.assertIsNone(metrics["steam:只有口碑"].overall)
        self.assertTrue(any("综合分" in gap for gap in metrics["steam:只有口碑"].gaps))


class SampleThresholdTests(unittest.TestCase):
    def test_empty_input_gives_no_champion(self):
        rec = recommend([])
        self.assertEqual(len(rec.picks), 5)
        self.assertTrue(all(pick.empty for pick in rec.picks))
        for pick in rec.picks:
            self.assertTrue(pick.empty_note)

    def test_single_product_cannot_win(self):
        rec = recommend([make("唯一", price=10.0, rate=99.0, count=5000, score=90)])
        for pick in rec.picks:
            self.assertTrue(pick.empty, f"{pick.title} 不应有冠军")
        self.assertIn(str(MIN_SAMPLE), rec.pick("reputation").empty_note)

    def test_two_products_enable_picks(self):
        products = [make("A", price=10.0, rate=95.0, count=5000, score=90,
                         specs={"支持平台": "Windows / macOS", "支持语言": "12 种：简体中文"}),
                    make("B", price=20.0, rate=80.0, count=400, score=70,
                         specs={"支持平台": "Windows", "支持语言": "5 种：英语"})]
        rec = recommend(products)
        self.assertFalse(rec.pick("reputation").empty)
        self.assertFalse(rec.pick("value").empty)
        self.assertFalse(rec.pick("overall").empty)


class NoFalseWinnerTests(unittest.TestCase):
    def test_most_reviews_does_not_win_reputation(self):
        """评价最多但口碑差的不应该是口碑冠军。"""
        products = [
            make("营销多", price=10.0, rate=62.0, count=50000),
            make("口碑好", price=12.0, rate=96.0, count=2000),
        ]
        rec = recommend(products)
        self.assertEqual(rec.pick("reputation").product.name, "口碑好")

    def test_cheapest_does_not_win_budget_if_quality_too_low(self):
        products = [
            make("最便宜但差评", price=1.0, rate=40.0, count=5000),
            make("贵一点但好评", price=10.0, rate=88.0, count=5000),
            make("更贵但好评", price=20.0, rate=90.0, count=5000),
        ]
        rec = recommend(products)
        pick = rec.pick("budget")
        self.assertNotEqual(pick.product.name, "最便宜但差评")
        self.assertGreaterEqual(pick.product.rating.positive_rate, BUDGET_QUALITY_FLOOR)

    def test_budget_falls_back_with_warning_when_none_qualify(self):
        products = [make("A", price=5.0, rate=40.0, count=500),
                    make("B", price=9.0, rate=45.0, count=500)]
        rec = recommend(products)
        pick = rec.pick("budget")
        self.assertFalse(pick.empty)
        self.assertIn("没有候选达到默认口碑底线", pick.reason)

    def test_spec_needs_two_evaluable(self):
        products = [make("有评分", price=10.0, rate=90.0, count=900, score=88),
                    make("没评分", price=12.0, rate=90.0, count=900)]
        rec = recommend(products)
        self.assertTrue(rec.pick("spec").empty)
        self.assertIn("样本不足", rec.pick("spec").empty_note)


class CurrencyTests(unittest.TestCase):
    def test_mixed_currency_does_not_mix(self):
        products = [
            make("人民币1", price=100.0, currency="CNY", rate=90.0, count=800),
            make("人民币2", price=200.0, currency="CNY", rate=88.0, count=800),
            make("美元1", price=20.0, currency="USD", rate=95.0, count=800),
        ]
        rec = recommend(products)
        self.assertEqual(rec.currency, "CNY")
        self.assertTrue(any("汇率" in note for note in rec.notes))
        # 美元商品不参与人民币组的价格计算
        self.assertEqual(rec.price_stats["min_price"], 100.0)

    def test_unknown_currency_is_not_assumed(self):
        products = [make("无币种1", price=10.0, currency="CNY", rate=90.0, count=900)]
        products[0].offer.currency = None
        products.append(make("无币种2", price=20.0, currency="CNY", rate=90.0, count=900))
        products[1].offer.currency = None
        rec = recommend(products)
        self.assertEqual(rec.currency, "")
        self.assertTrue(any("货币未知" in note for note in rec.notes))
        # 同一组"未知货币"时仍会比较价格，但会明确提示这是参考值


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.products = [
            make("甲", price=24.0, rate=92.0, count=137201, score=83,
                 specs={"支持平台": "Windows / macOS / Linux", "支持语言": "12 种：简体中文",
                        "功能特性": "单人、多人、合作"}),
            make("乙", price=48.0, rate=97.0, count=201909, score=89,
                 specs={"支持平台": "Windows / macOS / Linux", "支持语言": "12 种：简体中文",
                        "功能特性": "单人、多人、合作、成就"}),
        ]
        self.rec = recommend(self.products)

    def test_every_pick_explains_itself(self):
        for pick in self.rec.picks:
            if pick.empty:
                continue
            with self.subTest(pick=pick.key):
                self.assertTrue(pick.reason)
                self.assertTrue(pick.advantages)
                self.assertTrue(pick.drawbacks)
                self.assertTrue(pick.audience)
                self.assertIn(pick.confidence, ("高", "中", "低"))
                self.assertTrue(pick.basis)

    def test_drawbacks_are_data_driven(self):
        pick = self.rec.pick("overall")
        joined = " ".join(pick.drawbacks)
        # 两个商品都有 DLC? 没有；但都缺少媒体评分以外的信息不该出现编造
        self.assertNotIn("undefined", joined)

    def test_low_review_count_is_flagged(self):
        products = [make("样本少", price=10.0, rate=100.0, count=5),
                    make("样本多", price=10.0, rate=90.0, count=9000)]
        metrics = compute_metrics(products)
        gaps = " ".join(metrics["steam:样本少"].gaps)
        self.assertIn("评价仅 5 条", gaps)
        # 平滑后，5 条 100% 好评不能压过 9000 条 90% 好评
        self.assertLess(metrics["steam:样本少"].smoothed_rate,
                        metrics["steam:样本多"].smoothed_rate)

    def test_weight_change_changes_winner(self):
        """把价格权重压到 0，价格优势就不该再主导综合分。"""
        cheap = make("便宜低分", price=10.0, rate=75.0, count=1000, score=60,
                     specs={"支持平台": "Windows", "支持语言": "5 种：英语", "功能特性": "单人"})
        pricey = make("贵高分", price=40.0, rate=96.0, count=1000, score=95,
                      specs={"支持平台": "Windows / macOS", "支持语言": "12 种：简体中文",
                             "功能特性": "单人、多人、合作、成就"})
        price_heavy = recommend([cheap, pricey], {"value": 60, "reputation": 10, "spec": 10, "price": 20})
        quality_heavy = recommend([cheap, pricey], {"value": 0, "reputation": 60, "spec": 40, "price": 0})
        self.assertNotEqual(price_heavy.pick("overall").product.name,
                            quality_heavy.pick("overall").product.name)

    def test_default_weights_are_documented(self):
        self.assertEqual(sum(DEFAULT_WEIGHTS.values()), 100)

    def test_ranked_is_sorted(self):
        ranked = self.rec.ranked()
        scores = [m.overall for _, m in ranked]
        known = [s for s in scores if s is not None]
        self.assertEqual(known, sorted(known, reverse=True))


if __name__ == "__main__":
    unittest.main()
