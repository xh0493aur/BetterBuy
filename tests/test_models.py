"""数据模型测试：来源记录、缺失状态、完整度、旧表格兼容。"""
import unittest

from betterbuy.models import Offer, Product, Rating, Source, UNKNOWN, now_text


def make_product(**kwargs) -> Product:
    base = dict(product_id="steam:1", platform="Steam", url="https://example.com/p/1")
    base.update(kwargs)
    return Product(**base)


class SourceTests(unittest.TestCase):
    def test_field_source_mapping(self):
        product = make_product(name="测试商品")
        source = Source(adapter="steam", label="Steam 官方公开 API", url="https://api/x", fetched_at="2026-11-14 10:00:00")
        product.add_source("detail", source, ["name", "brand"])
        self.assertEqual(product.field_sources["name"], "detail")
        self.assertIn("Steam 官方公开 API", product.source_text("name"))
        self.assertIn("2026-11-14 10:00:00", product.source_text("name"))
        self.assertEqual(product.source_text("unknown_field"), "来源未记录")

    def test_cached_source_text(self):
        source = Source(adapter="a", label="L", url="u", fetched_at="2026-11-14 10:00:00", cached=True)
        self.assertIn("本地缓存", source.describe())


class MissingTests(unittest.TestCase):
    def test_recompute_missing_lists_every_gap(self):
        product = make_product()
        product.recompute_missing()
        for label in ("商品名称", "价格", "好评率", "核心参数"):
            self.assertIn(label, product.missing)

    def test_missing_disappears_when_field_present(self):
        product = make_product(name="A", brand="B", model="C", category="D", image_url="https://i")
        product.offer = Offer(price=10.0, currency="CNY")
        product.rating = Rating(positive_rate=90.0, review_count=100)
        product.specs = {"k": "v"}
        product.recompute_missing()
        self.assertEqual(product.missing, ["评价内容"])
        have, total, ratio = product.completeness
        self.assertEqual((have, total), (10, 10))
        self.assertEqual(ratio, 1.0)

    def test_unknown_placeholder_is_explicit(self):
        self.assertEqual(UNKNOWN, "数据源未提供")


class OfferTests(unittest.TestCase):
    def test_price_unknown_is_not_zero(self):
        offer = Offer()
        self.assertIsNone(offer.price)
        self.assertFalse(offer.is_known)
        self.assertEqual(offer.display(), UNKNOWN)

    def test_free_is_zero_with_currency(self):
        offer = Offer(price=0.0, currency="CNY")
        self.assertEqual(offer.display(), "免费")
        self.assertTrue(offer.is_known)

    def test_discount_display(self):
        offer = Offer(price=24.0, currency="CNY", original_price=48.0, discount_percent=50)
        text = offer.display()
        self.assertIn("¥24.00", text)
        self.assertIn("原价", text)
        self.assertEqual(offer.discount_text, "-50%")

    def test_unknown_currency_does_not_assume_cny(self):
        offer = Offer(price=99.0, currency=None)
        self.assertFalse(offer.is_known)
        self.assertNotIn("¥", offer.display())


class RatingTests(unittest.TestCase):
    def test_display_reports_scope(self):
        rating = Rating(positive_rate=97.0, review_count=201909, scope="简体中文评测",
                        score=89.0, scale=100.0)
        text = rating.display()
        self.assertIn("97% 好评", text)
        self.assertIn("201,909", text)
        self.assertIn("简体中文评测口径", text)
        self.assertIn("89/100", text)

    def test_empty_rating(self):
        self.assertEqual(Rating().display(), UNKNOWN)
        self.assertFalse(Rating().has_any)


class LegacyRowTests(unittest.TestCase):
    def test_to_legacy_row_maps_fields(self):
        product = make_product(name="椅子", platform="Steam")
        product.offer = Offer(price=599.0, currency="cny")
        product.rating = Rating(positive_rate=96.0, review_count=500, score=4.8, scale=5.0)
        product.specs = {"支持平台": "Windows"}
        row = product.to_legacy_row()
        self.assertEqual(row["商品"], "椅子")
        self.assertEqual(row["价格"], 599.0)
        self.assertEqual(row["币种"], "CNY")   # 统一大写，方便旧表格分组
        self.assertEqual(row["好评率%"], 96.0)
        self.assertIn("支持平台", row["核心参数/备注"])

    def test_legacy_row_without_price_marks_source(self):
        product = make_product(name="椅子")
        row = product.to_legacy_row()
        self.assertIsNone(row["价格"])
        self.assertEqual(row["来源"], "数据源未提供")


class SerializationTests(unittest.TestCase):
    def test_round_trip(self):
        product = make_product(name="A")
        product.offer = Offer(price=1.0, currency="CNY")
        product.rating = Rating(positive_rate=80.0)
        product.add_source("detail", Source(adapter="x", label="y", url="z"), ["name"])
        restored = Product.from_dict(product.to_dict())
        self.assertEqual(restored.name, "A")
        self.assertEqual(restored.offer.currency, "CNY")
        self.assertIn("detail", restored.sources)


if __name__ == "__main__":
    unittest.main()
