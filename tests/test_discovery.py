"""竞品发现测试：搜索页解析 + 参数拼装（离线，使用真实页面摘录）。"""
import pathlib
import unittest
from unittest import mock

from betterbuy.discovery import discover, find_same_product_elsewhere
from betterbuy.discovery.steam_search import (
    Candidate, DiscoveryResult, GENRE_SLUG, SteamSearchDiscovery, parse_search_rows,
)
from betterbuy.models import Offer, Product, Rating

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_rows():
    html = (FIXTURES / "search_indie_rows.html").read_text(encoding="utf-8")
    return parse_search_rows(html)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.rows = load_rows()

    def test_all_rows_parsed(self):
        self.assertEqual(len(self.rows), 4)

    def test_price_and_rating(self):
        by_appid = {row.appid: row for row in self.rows}
        terraria = by_appid[105600]
        self.assertEqual(terraria.name, "Terraria")
        self.assertEqual(terraria.price, 42.0)
        self.assertEqual(terraria.currency, "CNY")
        self.assertEqual(terraria.positive_rate, 98.0)
        self.assertEqual(terraria.review_count, 171099)
        self.assertEqual(terraria.label, "好评如潮")

    def test_discount_price_takes_final_value(self):
        stardew = {row.appid: row for row in self.rows}[413150]
        self.assertEqual(stardew.price, 24.0)      # 折后价，而不是被划掉的 48
        self.assertEqual(stardew.positive_rate, 97.0)

    def test_free_item(self):
        free = {row.appid: row for row in self.rows}[999999]
        self.assertEqual(free.price, 0.0)
        self.assertEqual(free.currency, "CNY")

    def test_excludes_base_appid(self):
        html = (FIXTURES / "search_indie_rows.html").read_text(encoding="utf-8")
        rows = parse_search_rows(html, base_appid=105600)
        self.assertNotIn(105600, [row.appid for row in rows])
        self.assertEqual(len(rows), 3)

    def test_candidate_to_product_is_marked_discovered(self):
        product = self.rows[0].to_product()
        self.assertEqual(product.origin, "discovered")
        self.assertEqual(product.platform, "Steam")
        self.assertIn("search", product.sources)


class GenreTests(unittest.TestCase):
    def test_chinese_genre_mapped(self):
        for chinese in ("动作", "独立", "角色扮演", "模拟", "策略"):
            self.assertIn(chinese, GENRE_SLUG)

    def test_search_reports_unmappable_category(self):
        product = Product(product_id="steam:1", platform="Steam", url="u",
                          name="某商品", category="完全未知的类别")
        result = discover(product)
        self.assertFalse(result.ok)
        self.assertIn("无法映射", result.note)


class SearchFlowTests(unittest.TestCase):
    def setUp(self):
        self.html = (FIXTURES / "search_indie_rows.html").read_text(encoding="utf-8")
        self.base = Product(product_id="steam:413150", platform="Steam",
                            url="https://store.steampowered.com/app/413150/",
                            name="Stardew Valley", category="独立 / 角色扮演 / 模拟")
        self.base.offer = Offer(price=48.0, currency="CNY", region="CN")
        self.base.rating = Rating(positive_rate=97.0, review_count=904859)

    def _fake_response(self, url, **kwargs):
        resp = mock.Mock()
        resp.text = self.html
        resp.url = url + "?genre=Indie"
        resp.status_code = 200
        return resp

    def test_search_returns_candidates_with_real_numbers(self):
        with mock.patch("betterbuy.discovery.steam_search.client") as fake:
            fake.get.side_effect = self._fake_response
            result = SteamSearchDiscovery().search(self.base, limit=3)
        self.assertTrue(result.ok)
        self.assertEqual(result.genre, "独立")
        # 4 行里排除基准商品 413150，再排除价格 0（低于 24 元下限），剩 2 个
        self.assertEqual(len(result.candidates), 2)
        self.assertTrue(all(c.appid != 413150 for c in result.candidates))
        self.assertEqual({c.appid for c in result.candidates}, {105600, 322330})
        self.assertIn("同类型不同商品", result.note)
        # 价格区间以基准价 48 元为中心 ±50%
        self.assertEqual(result.price_range, (24.0, 72.0))

    def test_price_filter_applied(self):
        with mock.patch("betterbuy.discovery.steam_search.client") as fake:
            fake.get.side_effect = self._fake_response
            result = SteamSearchDiscovery().search(self.base, min_price=40, max_price=100, limit=5)
        prices = [c.price for c in result.candidates]
        self.assertTrue(all(p >= 40 for p in prices), prices)

    def test_network_failure_is_reported(self):
        from betterbuy.http_client import FetchError

        with mock.patch("betterbuy.discovery.steam_search.client") as fake:
            fake.get.side_effect = FetchError("代理不可用", kind="network")
            result = SteamSearchDiscovery().search(self.base, limit=3)
        self.assertFalse(result.ok)
        self.assertIn("访问失败", result.note)

    def test_empty_result_is_honest(self):
        with mock.patch("betterbuy.discovery.steam_search.client") as fake:
            fake.get.side_effect = lambda url, **kw: mock.Mock(
                text="<html><body>没有结果</body></html>", url=url, status_code=200
            )
            result = SteamSearchDiscovery().search(self.base, limit=3)
        self.assertFalse(result.ok)
        self.assertEqual(result.candidates, [])
        self.assertIn("没有搜到", result.note)


class CrossPlatformTests(unittest.TestCase):
    def test_steam_has_no_cross_platform_equivalence(self):
        """Steam 商品不属于电商商品体系，跨平台同款检索明确不适用。"""
        product = Product(product_id="steam:1", platform="Steam", url="u")
        products, note = find_same_product_elsewhere(product)
        self.assertEqual(products, [])
        self.assertIn("没有跨平台同款", note)
        self.assertIn("电商平台", note)


class NonSteamTests(unittest.TestCase):
    def test_non_steam_product_has_no_discovery_source(self):
        product = Product(product_id="url:1", platform="example.com", url="u")
        result = discover(product)
        self.assertFalse(result.ok)
        self.assertIn("没有可用于", result.note)


if __name__ == "__main__":
    unittest.main()
