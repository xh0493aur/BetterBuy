"""标准化与同款判定测试。"""
import unittest

from betterbuy.models import Offer, Product, Rating
from betterbuy.normalize import (
    clean_text, dedupe_products, guess_brand, guess_model, norm_key,
    parse_int, parse_percent, parse_price, same_product, similarity, strip_site_suffix,
)


class CleanTests(unittest.TestCase):
    def test_fullwidth_and_spaces(self):
        self.assertEqual(clean_text("　ＡＢＣ　１２３  "), "ABC 123")

    def test_limit_truncates(self):
        self.assertTrue(clean_text("一二三四五", limit=4).endswith("…"))

    def test_strip_site_suffix(self):
        self.assertEqual(strip_site_suffix("REDMI显示器A22 节能版立即购买-小米商城"), "REDMI显示器A22 节能版")
        self.assertEqual(strip_site_suffix("Stardew Valley on Steam"), "Stardew Valley on Steam")


class PriceTests(unittest.TestCase):
    def test_symbol_detection(self):
        self.assertEqual(parse_price("¥ 48.00"), (48.0, "CNY"))
        self.assertEqual(parse_price("$19.99"), (19.99, "USD"))

    def test_currency_hint(self):
        value, currency = parse_price("48", "cny")
        self.assertEqual((value, currency), (48.0, "CNY"))

    def test_no_currency_no_guess(self):
        value, currency = parse_price("48.00")
        self.assertEqual(value, 48.0)
        self.assertIsNone(currency)   # 不能默认人民币

    def test_thousands_separator(self):
        self.assertEqual(parse_price("1,299.00元")[0], 1299.0)

    def test_garbage(self):
        self.assertEqual(parse_price("暂无报价"), (None, None))
        self.assertEqual(parse_price(None), (None, None))

    def test_negative_rejected(self):
        self.assertIsNone(parse_price("-5")[0])


class NumberTests(unittest.TestCase):
    def test_wan(self):
        self.assertEqual(parse_int("1.2万"), 12000)
        self.assertEqual(parse_int("137,201"), 137201)

    def test_percent_variants(self):
        self.assertEqual(parse_percent("97%"), 97.0)
        self.assertEqual(parse_percent(0.97), 97.0)
        self.assertEqual(parse_percent(97), 97.0)
        self.assertIsNone(parse_percent("暂无"))


class BrandModelTests(unittest.TestCase):
    def test_brand_from_title(self):
        self.assertEqual(guess_brand("小米 REDMI显示器A22"), "小米")
        self.assertEqual(guess_brand("Apple iPhone 16"), "Apple")

    def test_brand_unknown(self):
        self.assertEqual(guess_brand("普通商品"), "")

    def test_model_tokens(self):
        model = guess_model("REDMI显示器A22 节能版")
        self.assertIn("A22", model)

    def test_model_empty(self):
        self.assertEqual(guess_model(""), "")


class MatchTests(unittest.TestCase):
    def test_norm_key(self):
        self.assertEqual(norm_key("Stardew  Valley！"), "stardewvalley")

    def test_similarity_bounds(self):
        self.assertEqual(similarity("Stardew Valley", "Stardew Valley"), 1.0)
        self.assertLess(similarity("Stardew Valley", "Terraria"), 0.3)

    def test_same_product_by_id(self):
        a = Product(product_id="steam:1", platform="Steam", url="u")
        b = Product(product_id="steam:1", platform="Other", url="u2")
        self.assertTrue(same_product(a, b))

    def test_same_product_rejects_different_brand(self):
        a = Product(product_id="x:1", platform="S", url="u", name="A22 显示器", brand="小米")
        b = Product(product_id="x:2", platform="S", url="u", name="A22 显示器", brand="戴尔")
        self.assertFalse(same_product(a, b))

    def test_same_product_rejects_similar_name_different_spec(self):
        a = Product(product_id="x:1", platform="S", url="u", name="笔记本 16GB 512GB")
        b = Product(product_id="x:2", platform="S", url="u", name="笔记本 32GB 1TB")
        self.assertFalse(same_product(a, b, threshold=0.9))

    def test_dedupe_keeps_last(self):
        a = Product(product_id="p:1", platform="S", url="u", name="旧")
        b = Product(product_id="p:1", platform="S", url="u", name="新")
        result = dedupe_products([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "新")


if __name__ == "__main__":
    unittest.main()
