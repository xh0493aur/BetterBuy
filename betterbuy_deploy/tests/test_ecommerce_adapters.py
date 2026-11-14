"""电商适配器测试：字段映射 + 无密钥降级 + 跨平台同款判定。

返回体结构按官方文档字段名构造（合成样例，仅用于验证映射逻辑），
不含真实商品数据。
"""
import contextlib
import os
import unittest
from unittest import mock

from betterbuy.adapters.jd import JDAdapter, product_from_goods
from betterbuy.adapters.pdd import PDDAdapter, product_from_pdd_goods
from betterbuy.adapters.taobao import TaobaoAdapter, product_from_tbk_item
from betterbuy.discovery.ecommerce import match_score
from betterbuy.http_client import FetchError
from betterbuy.models import Offer, Product


@contextlib.contextmanager
def no_credentials():
    """临时清空所有 BETTERBUY_* 环境变量。"""
    keys = [k for k in os.environ if k.startswith("BETTERBUY_")]
    with mock.patch.dict(os.environ, {}, clear=False):
        for key in keys:
            os.environ.pop(key, None)
        yield


JD_ITEM = {
    "skuId": 100012043978,
    "skuName": "示例手机 12GB+256GB 黑色",
    "brandName": "示例品牌",
    "priceInfo": {"lowestPrice": "199.00", "price": "299.00"},
    "imageUrl": "https://img.example.com/a.jpg",
    "shopInfo": {"shopName": "示例旗舰店"},
    "categoryInfo": {"cid1Name": "手机通讯", "cid2Name": "手机", "cid3Name": "智能手机"},
    "goodComments": 900,
    "totalComments": 1000,
    "owner": "g",
}

TB_ITEM = {
    "item_id": "612345678901",
    "item_basic_info": {
        "title": "示例耳机 蓝牙5.3 降噪",
        "pict_url": "https://img.example.com/b.jpg",
        "brand_name": "示例品牌",
        "category_name": "蓝牙耳机",
        "level_one_category_name": "数码",
        "shop_title": "示例数码专营店",
        "user_type": 1,
        "volume": 3210,
        "provcity": "广东 深圳",
        "free_shipment": True,
        "item_url": "https://detail.tmall.com/item.htm?id=612345678901",
        "h_good_rate": True,
    },
    "price_promotion_info": {"zk_final_price": "89.00", "reserve_price": "129.00"},
}

PDD_ITEM = {
    "goods_id": 123456789,
    "goods_name": "示例纸巾 30包",
    "goods_thumbnail_url": "https://img.example.com/c.jpg",
    "min_group_price": 1290,
    "min_normal_price": 1990,
    "opt_name": "家居百货",
    "mall_name": "示例百货店",
    "sales_tip": "已拼 1.2 万件",
    "promotion_rate": 100,
}


class TestJDMapping(unittest.TestCase):
    def setUp(self):
        self.p = product_from_goods(JD_ITEM, sku_id="100012043978",
                                    url="https://item.jd.com/100012043978.html")

    def test_identity(self):
        self.assertEqual(self.p.product_id, "jd:100012043978")
        self.assertEqual(self.p.platform, "京东")
        self.assertIn("示例手机", self.p.name)

    def test_price_is_yuan(self):
        self.assertAlmostEqual(self.p.offer.price, 199.0)
        self.assertEqual(self.p.offer.currency, "CNY")
        self.assertAlmostEqual(self.p.offer.original_price, 299.0)

    def test_category_joined(self):
        self.assertEqual(self.p.category, "手机通讯 > 手机 > 智能手机")

    def test_review_count_from_api(self):
        self.assertEqual(self.p.rating.review_count, 1000)

    def test_positive_rate_is_derived_and_labelled(self):
        self.assertAlmostEqual(self.p.rating.positive_rate, 90.0)
        self.assertIn("换算", self.p.rating.scope)

    def test_no_review_text_is_reported_missing(self):
        self.assertIn("评价内容", self.p.missing)
        self.assertEqual(self.p.reviews, [])

    def test_source_recorded(self):
        self.assertIn("jd", self.p.sources)
        self.assertEqual(self.p.sources["jd"].method, "官方开放接口")
        self.assertEqual(self.p.source_of("price"), self.p.sources["jd"])

    def test_self_operated_flag(self):
        self.assertEqual(self.p.specs.get("经营模式"), "京东自营")

    def test_missing_price_when_api_gives_none(self):
        p = product_from_goods({"skuId": 1, "skuName": "无价商品"}, sku_id="1", url="u")
        self.assertIsNone(p.offer.price)
        self.assertIsNone(p.offer.currency)
        self.assertIn("价格", p.missing)


class TestTaobaoMapping(unittest.TestCase):
    def setUp(self):
        self.p = product_from_tbk_item(TB_ITEM, item_id="612345678901",
                                       url="https://detail.tmall.com/item.htm?id=612345678901")

    def test_platform_is_tmall_when_user_type_1(self):
        self.assertEqual(self.p.platform, "天猫")

    def test_price_from_zk_final_price(self):
        self.assertAlmostEqual(self.p.offer.price, 89.0)
        self.assertAlmostEqual(self.p.offer.original_price, 129.0)

    def test_volume_is_not_review_count(self):
        """30 天销量绝不能当评价数。"""
        self.assertEqual(self.p.specs.get("30天销量"), "3210")
        self.assertIsNone(self.p.rating.review_count)
        self.assertIn("评价数量", self.p.missing)

    def test_boolean_good_rate_is_not_percentage(self):
        self.assertIsNone(self.p.rating.positive_rate)
        self.assertEqual(self.p.rating.label, "好评率高于行业均值")
        self.assertIn("好评率", self.p.missing)

    def test_no_review_text(self):
        self.assertIn("评价内容", self.p.missing)

    def test_notes_explain_limits(self):
        text = " ".join(self.p.notes)
        self.assertIn("好评率百分比", text)


class TestPDDMapping(unittest.TestCase):
    def setUp(self):
        self.p = product_from_pdd_goods(PDD_ITEM, goods_id="123456789",
                                        url="https://mobile.yangkeduo.com/goods.html?goods_id=123456789")

    def test_price_converted_from_fen(self):
        self.assertAlmostEqual(self.p.offer.price, 12.9)
        self.assertAlmostEqual(self.p.offer.original_price, 19.9)

    def test_no_rating_at_all(self):
        self.assertIsNone(self.p.rating.positive_rate)
        self.assertIsNone(self.p.rating.review_count)
        self.assertFalse(self.p.rating.has_any)
        self.assertIn("好评率", self.p.missing)

    def test_commission_thousandths(self):
        self.assertEqual(self.p.specs.get("佣金比例"), "10%")

    def test_category(self):
        self.assertEqual(self.p.category, "家居百货")


class TestAdapterMatching(unittest.TestCase):
    def test_jd_adapter_matches_jd_only(self):
        adapter = JDAdapter()
        self.assertTrue(adapter.matches("https://item.jd.com/100012043978.html"))
        self.assertFalse(adapter.matches("https://detail.tmall.com/item.htm?id=1"))
        self.assertFalse(adapter.matches("https://store.steampowered.com/app/413150/"))

    def test_taobao_adapter_also_takes_tmall(self):
        adapter = TaobaoAdapter()
        self.assertTrue(adapter.matches("https://item.taobao.com/item.htm?id=598765432109"))
        self.assertTrue(adapter.matches("https://detail.tmall.com/item.htm?id=612345678901"))

    def test_cache_key_uses_item_id(self):
        self.assertEqual(JDAdapter().cache_key("https://item.jd.com/100012043978.html"),
                         "jd:100012043978")
        self.assertEqual(PDDAdapter().cache_key(
            "https://mobile.yangkeduo.com/goods.html?goods_id=12345"), "pdd:12345")


class TestNoCredentialDegrade(unittest.TestCase):
    def test_each_adapter_reports_missing_env(self):
        cases = [
            (JDAdapter(), "https://item.jd.com/100012043978.html", "BETTERBUY_JD_APP_KEY"),
            (TaobaoAdapter(), "https://detail.tmall.com/item.htm?id=612345678901",
             "BETTERBUY_TB_APP_KEY"),
            (PDDAdapter(), "https://mobile.yangkeduo.com/goods.html?goods_id=123456789",
             "BETTERBUY_PDD_CLIENT_ID"),
        ]
        with no_credentials():
            for adapter, url, env_name in cases:
                with self.subTest(adapter=adapter.key):
                    with self.assertRaises(FetchError) as ctx:
                        adapter.fetch(url)
                    self.assertEqual(ctx.exception.kind, "no_credential")
                    self.assertIn(env_name, ctx.exception.message)


class TestMatchScore(unittest.TestCase):
    def _product(self, name, brand="", model="", price=None, platform="京东"):
        return Product(product_id=name, platform=platform, url="", name=name,
                       brand=brand, model=model, offer=Offer(price=price, currency="CNY"))

    def test_same_brand_model_scores_high(self):
        a = self._product("示例品牌 A22 显示器 24英寸", brand="示例品牌", model="A22", price=499)
        b = self._product("示例品牌A22显示器 23.8英寸 1080P", brand="示例品牌",
                          model="A22", price=479, platform="淘宝")
        score, reasons = match_score(a, b)
        self.assertGreaterEqual(score, 0.6, reasons)

    def test_different_brand_is_excluded(self):
        a = self._product("测试A 手机", brand="测试A")
        b = self._product("测试B 手机", brand="测试B")
        score, reasons = match_score(a, b)
        self.assertEqual(score, 0.0)
        self.assertIn("品牌不同", reasons)

    def test_price_gap_excludes(self):
        a = self._product("示例品牌 同款 1TB", brand="示例品牌", model="X1", price=100)
        b = self._product("示例品牌 同款 1TB", brand="示例品牌", model="X1", price=900)
        score, _ = match_score(a, b)
        self.assertEqual(score, 0.0)

    def test_unrelated_titles_score_low(self):
        a = self._product("机械键盘 87键 青轴")
        b = self._product("不锈钢保温杯 500ml")
        score, _ = match_score(a, b)
        self.assertLess(score, 0.6)


if __name__ == "__main__":
    unittest.main()
