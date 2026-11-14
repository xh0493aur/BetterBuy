"""SQLite 存储层测试：快照、价格历史去重、采集日志、清理。"""
import tempfile
import unittest
from pathlib import Path

from betterbuy.cache import Database
from betterbuy.models import Offer, Product, Rating, Source


def make(name="测试商品", price=10.0, currency="CNY", product_id="steam:1", fetched_at="2026-11-14 10:00:00"):
    product = Product(product_id=product_id, platform="Steam",
                      url=f"https://example.com/{product_id}", name=name, fetched_at=fetched_at)
    product.offer = Offer(price=price, currency=currency, region="CN", source_key="price")
    product.rating = Rating(positive_rate=90.0, review_count=100)
    product.add_source("price", Source(adapter="steam", label="Steam 官方公开 API", url="u"), ["price"])
    return product


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        product = make()
        self.db.save_product(product)
        loaded = self.db.load_product("steam:1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "测试商品")
        self.assertEqual(loaded.offer.price, 10.0)
        self.assertEqual(loaded.sources["price"].label, "Steam 官方公开 API")

    def test_load_missing_returns_none(self):
        self.assertIsNone(self.db.load_product("steam:not-exist"))

    def test_price_history_appends_only_on_change(self):
        self.db.save_product(make(price=10.0))
        self.db.save_product(make(price=10.0, fetched_at="2026-11-14 11:00:00"))
        history = self.db.price_history("steam:1")
        self.assertEqual(len(history), 1, "同价不应重复记录")

        self.db.save_product(make(price=8.0, fetched_at="2026-11-14 12:00:00"))
        history = self.db.price_history("steam:1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["price"], 8.0)   # 最新在前

    def test_currency_change_counts_as_change(self):
        self.db.save_product(make(price=10.0, currency="CNY"))
        self.db.save_product(make(price=10.0, currency="USD", fetched_at="2026-11-14 12:00:00"))
        self.assertEqual(len(self.db.price_history("steam:1")), 2)

    def test_no_price_does_not_create_history(self):
        product = make()
        product.offer = Offer(price=None)
        self.db.save_product(product)
        self.assertEqual(self.db.price_history("steam:1"), [])
        self.assertIsNotNone(self.db.load_product("steam:1"))

    def test_upsert_updates_payload(self):
        self.db.save_product(make(name="旧名"))
        self.db.save_product(make(name="新名"))
        self.assertEqual(self.db.load_product("steam:1").name, "新名")
        self.assertEqual(self.db.stats()["商品数"], 1)

    def test_cached_age(self):
        self.db.save_product(make())
        age = self.db.cached_age_seconds("steam:1")
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)
        self.assertIsNone(self.db.cached_age_seconds("steam:missing"))

    def test_fetch_log(self):
        self.db.log_fetch("https://x", "steam", True, "", "已识别")
        self.db.log_fetch("https://y", "steam", False, "blocked", "403 访问限制", 403)
        logs = self.db.recent_logs()
        self.assertEqual(len(logs), 2)
        failed = [row for row in logs if row["ok"] == 0][0]
        self.assertEqual(failed["kind"], "blocked")
        self.assertEqual(failed["http_status"], 403)

    def test_stats_and_clear(self):
        self.db.save_product(make())
        self.db.log_fetch("https://x", "steam", True)
        self.assertEqual(self.db.stats(), {"商品数": 1, "价格记录数": 1, "采集日志数": 1})
        self.db.clear_products()
        self.assertEqual(self.db.stats()["商品数"], 0)
        self.assertEqual(self.db.stats()["价格记录数"], 0)
        self.assertEqual(self.db.stats()["采集日志数"], 1)

        self.db.clear_all()
        self.assertEqual(self.db.stats()["采集日志数"], 0)

    def test_load_recent(self):
        self.db.save_product(make(product_id="steam:1", fetched_at="2026-11-14 10:00:00"))
        self.db.save_product(make(product_id="steam:2", fetched_at="2026-11-14 11:00:00"))
        recent = self.db.load_recent(10)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].product_id, "steam:2")

    def test_persists_across_instances(self):
        self.db.save_product(make())
        other = Database(self.db.path)
        self.assertEqual(other.load_product("steam:1").name, "测试商品")


if __name__ == "__main__":
    unittest.main()
