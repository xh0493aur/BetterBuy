"""Steam 适配器离线测试（使用真实响应摘录的夹具，不联网）。"""
import json
import pathlib
import unittest

from betterbuy.adapters.steam import (
    apply_store_rating, parse_appid, product_from_appdetails,
)
from betterbuy.models import Source

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_appdetails() -> dict:
    raw = json.loads((FIXTURES / "appdetails_413150_excerpt.json").read_text(encoding="utf-8"))
    return raw["413150"]["data"]


def make_source(**kwargs) -> Source:
    base = dict(adapter="steam", label="Steam 官方公开 API",
                url="https://store.steampowered.com/api/appdetails?appids=413150",
                fetched_at="2026-11-14 12:39:37")
    base.update(kwargs)
    return Source(**base)


class AppIdTests(unittest.TestCase):
    def test_all_url_forms(self):
        cases = {
            "https://store.steampowered.com/app/413150/": 413150,
            "https://store.steampowered.com/app/413150/Stardew_Valley/": 413150,
            "https://store.steampowered.com/app/413150/?snr=1_5_9__300": 413150,
            "store.steampowered.com/app/413150": 413150,
            "steam://store/413150": 413150,
            "https://s.team/a/413150/": 413150,
            "413150": 413150,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(parse_appid(url), expected)

    def test_bundle_and_sub_are_not_apps(self):
        self.assertIsNone(parse_appid("https://store.steampowered.com/sub/1234/"))
        self.assertIsNone(parse_appid("https://store.steampowered.com/bundle/88/"))
        self.assertIsNone(parse_appid(""))


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.data = load_appdetails()
        self.product = product_from_appdetails(413150, self.data, "https://store.steampowered.com/app/413150/",
                                               make_source())

    def test_identity_fields(self):
        self.assertEqual(self.product.product_id, "steam:413150")
        self.assertEqual(self.product.name, "Stardew Valley")
        self.assertEqual(self.product.brand, "ConcernedApe")
        self.assertEqual(self.product.category, "独立 / 角色扮演 / 模拟")
        self.assertEqual(self.product.subcategory, "独立")

    def test_price_is_converted_from_cents(self):
        self.assertEqual(self.product.offer.price, 48.0)
        self.assertEqual(self.product.offer.currency, "CNY")
        self.assertEqual(self.product.offer.region, "CN")
        self.assertIn("¥48.00", self.product.offer.display())

    def test_review_total_from_api(self):
        self.assertEqual(self.product.rating.review_count, 904859)
        self.assertEqual(self.product.rating.score, 89.0)
        self.assertEqual(self.product.rating.scale, 100.0)
        # 好评率必须来自商品页；仅凭 API 不能编一个出来
        self.assertIsNone(self.product.rating.positive_rate)

    def test_specs_extraction(self):
        specs = self.product.specs
        self.assertEqual(specs["支持平台"], "Windows / macOS / Linux")
        self.assertEqual(specs["Metacritic"], "89/100")
        self.assertEqual(specs["发售日期"], "2016 年 2 月 26 日")
        self.assertIn("简体中文", specs["支持语言"])
        self.assertNotIn("具有完全音频支持的语言", specs["支持语言"])
        self.assertEqual(specs["评价总数（全语言）"], "904,859 条")
        self.assertIn("Windows Vista", specs["最低配置"])
        self.assertNotIn("最低配置:", specs["最低配置"][:10])
        self.assertIn("1 个 DLC", specs["追加内容"])

    def test_provenance_recorded(self):
        self.assertIn("detail", self.product.sources)
        self.assertEqual(self.product.field_sources["price"], "price")
        self.assertIn("Steam 官方公开 API", self.product.source_text("price"))
        self.assertIn("2026-11-14 12:39:37", self.product.source_text("price"))

    def test_missing_reflects_api_only_limits(self):
        """只用官方接口时，好评率本来就没有，必须如实标缺失。"""
        self.product.recompute_missing()
        self.assertIn("好评率", self.product.missing)
        self.assertIn("评价内容", self.product.missing)
        self.assertNotIn("价格", self.product.missing)

    def test_notes_explain_limits(self):
        joined = " ".join(self.product.notes)
        self.assertIn("评测正文", joined)
        self.assertIn("中国区", joined)


class FreeGameTests(unittest.TestCase):
    def test_free_game_price_zero(self):
        data = dict(load_appdetails())
        data.pop("price_overview")
        data["is_free"] = True
        product = product_from_appdetails(730, data, "https://store.steampowered.com/app/730/", make_source())
        self.assertEqual(product.offer.price, 0.0)
        self.assertEqual(product.offer.display(), "免费")

    def test_missing_price_is_not_zero(self):
        data = dict(load_appdetails())
        data.pop("price_overview")
        data["is_free"] = False
        product = product_from_appdetails(999, data, "https://store.steampowered.com/app/999/", make_source())
        self.assertIsNone(product.offer.price)
        self.assertEqual(product.offer.display(), "数据源未提供")


class StoreRatingTests(unittest.TestCase):
    def setUp(self):
        html = (FIXTURES / "app_page_413150_reviews.html").read_text(encoding="utf-8")
        self.product = product_from_appdetails(413150, load_appdetails(),
                                               "https://store.steampowered.com/app/413150/", make_source())
        apply_store_rating(
            self.product, html,
            make_source(label="Steam 公开商品页",
                        url="https://store.steampowered.com/app/413150/?cc=cn&l=schinese"),
        )

    def test_positive_rate_parsed_with_scope(self):
        self.assertEqual(self.product.rating.positive_rate, 97.0)
        self.assertEqual(self.product.rating.review_count, 201909)
        self.assertEqual(self.product.rating.scope, "简体中文评测")
        self.assertEqual(self.product.rating.label, "好评如潮")

    def test_recent_reviews_separated(self):
        self.assertEqual(self.product.rating.recent_positive_rate, 98.0)
        self.assertEqual(self.product.rating.recent_review_count, 5728)

    def test_rating_source_is_store_page(self):
        self.assertTrue(self.product.source_of("rating_rate").url.startswith("https://store.steampowered.com/app/"))

    def test_html_without_summary_adds_note(self):
        product = product_from_appdetails(413150, load_appdetails(),
                                          "https://store.steampowered.com/app/413150/", make_source())
        apply_store_rating(product, "<html><body>无评测</body></html>", make_source())
        self.assertIsNone(product.rating.positive_rate)
        self.assertTrue(any("未提供可解析的好评率" in n for n in product.notes))


if __name__ == "__main__":
    unittest.main()
