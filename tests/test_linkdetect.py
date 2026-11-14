"""链接识别测试：只做本地解析，不联网。"""
import unittest

from betterbuy.linkdetect import SHORT_HOSTS, detect, normalize_url


class TestDetect(unittest.TestCase):
    def test_jd_item_url(self):
        info = detect("https://item.jd.com/100012043978.html")
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.item_id, "100012043978")
        self.assertFalse(info.is_short)

    def test_jd_mobile_url(self):
        info = detect("https://item.m.jd.com/product/100026667910.html")
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.item_id, "100026667910")

    def test_jd_short_link_has_no_id(self):
        info = detect("https://u.jd.com/abcXYZ")
        self.assertEqual(info.platform, "jd")
        self.assertEqual(info.item_id, "")
        self.assertTrue(info.is_short)
        self.assertIn("展开", info.note)

    def test_jd_query_sku(self):
        info = detect("https://item.jd.com/item.htm?sku=100012043978")
        self.assertEqual(info.item_id, "100012043978")

    def test_taobao_item(self):
        info = detect("https://item.taobao.com/item.htm?spm=a1z10.1&id=598765432109")
        self.assertEqual(info.platform, "taobao")
        self.assertEqual(info.item_id, "598765432109")

    def test_tmall_item(self):
        info = detect("https://detail.tmall.com/item.htm?id=612345678901")
        self.assertEqual(info.platform, "tmall")
        self.assertEqual(info.item_id, "612345678901")
        self.assertEqual(info.product_id, "tmall:612345678901")

    def test_taobao_short_link(self):
        info = detect("https://m.tb.cn/h.5xYzAbC")
        self.assertEqual(info.platform, "taobao")
        self.assertEqual(info.item_id, "")
        self.assertTrue(info.is_short)

    def test_taobao_short_host_is_listed(self):
        self.assertIn("m.tb.cn", SHORT_HOSTS)

    def test_pdd_goods(self):
        info = detect("https://mobile.yangkeduo.com/goods.html?goods_id=123456789")
        self.assertEqual(info.platform, "pdd")
        self.assertEqual(info.item_id, "123456789")

    def test_pdd_goods2(self):
        info = detect("https://mobile.yangkeduo.com/goods2.html?goods_id=987654321")
        self.assertEqual(info.item_id, "987654321")

    def test_pdd_short(self):
        info = detect("https://p.pinduoduo.com/abcdEFG")
        self.assertEqual(info.platform, "pdd")
        self.assertTrue(info.is_short)

    def test_steam_still_works(self):
        info = detect("https://store.steampowered.com/app/413150/")
        self.assertEqual(info.platform, "steam")
        self.assertEqual(info.item_id, "413150")

    def test_steam_short_with_id_needs_no_resolve(self):
        """s.team 是短域名，但路径里已含 appid，无需联网展开。"""
        info = detect("https://s.team/a/413150")
        self.assertEqual(info.platform, "steam")
        self.assertEqual(info.item_id, "413150")
        self.assertFalse(info.is_short)

    def test_unknown_domain(self):
        info = detect("https://example.com/some/page")
        self.assertEqual(info.platform, "unknown")
        self.assertFalse(info.ok)
        self.assertIn("example.com", info.note)

    def test_empty(self):
        info = detect("")
        self.assertEqual(info.platform, "unknown")
        self.assertIn("为空", info.note)

    def test_missing_scheme_is_added(self):
        self.assertEqual(normalize_url("item.jd.com/1.html"), "https://item.jd.com/1.html")

    def test_no_id_never_fabricated(self):
        """拿不到 ID 时必须为空，不能从 URL 里瞎猜一串数字。"""
        info = detect("https://item.taobao.com/item.htm")
        self.assertEqual(info.item_id, "")

    def test_product_id_stable(self):
        a = detect("https://item.jd.com/100012043978.html")
        b = detect("https://item.jd.com/100012043978.html?xx=1")
        self.assertEqual(a.product_id, b.product_id)


if __name__ == "__main__":
    unittest.main()
