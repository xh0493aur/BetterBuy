"""联网集成测试（默认跳过，避免无网络时误报失败）。

手动运行：
    Windows PowerShell:  $env:BETTERBUY_LIVE=1; python -m unittest tests.test_live -v
    Git Bash:            BETTERBUY_LIVE=1 python -m unittest tests.test_live -v

只验证一个真实链接的完整链路：官方接口 -> 商品页 -> 标准化 -> 来源记录。
"""
import os
import unittest

LIVE = os.environ.get("BETTERBUY_LIVE") == "1"

@unittest.skipUnless(LIVE, "设置 BETTERBUY_LIVE=1 才运行联网测试")
class LiveSteamTests(unittest.TestCase):
    def test_real_product_end_to_end(self):
        from betterbuy.adapters import collect

        product = collect("https://store.steampowered.com/app/413150/", force=True)
        self.assertEqual(product.platform, "Steam")
        self.assertTrue(product.name)
        self.assertIsNotNone(product.offer.price)
        self.assertEqual(product.offer.currency, "CNY")
        self.assertIsNotNone(product.rating.positive_rate)
        self.assertGreater(product.rating.review_count or 0, 1000)
        self.assertTrue(product.sources)
        self.assertIn("评价内容", product.missing)   # 已知限制，必须如实标缺失
        for source in product.sources.values():
            self.assertTrue(source.fetched_at)

    def test_rejects_non_product_url(self):
        from betterbuy.adapters import collect
        from betterbuy.http_client import FetchError

        with self.assertRaises(FetchError):
            collect("https://store.steampowered.com/sub/1234/", force=True)


if __name__ == "__main__":
    unittest.main()
