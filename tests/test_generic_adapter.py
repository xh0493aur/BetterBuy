"""通用网页适配器测试：结构化数据优先、绝不从营销文案里猜价格。"""
import unittest

from betterbuy.adapters.generic_html import open_meta_fields
from betterbuy.adapters.schema_org import parse_jsonld_product


def html_with_jsonld(payload: str) -> str:
    return f'<html><head><script type="application/ld+json">{payload}</script></head><body></body></html>'


class JsonLdTests(unittest.TestCase):
    def test_basic_product(self):
        html = html_with_jsonld("""
        {"@context":"https://schema.org","@type":"Product","name":"测试电脑椅",
         "brand":{"@type":"Brand","name":"示例品牌"},
         "category":"家具",
         "image":"https://example.com/c.jpg",
         "offers":{"@type":"Offer","price":"699.00","priceCurrency":"CNY",
                   "availability":"https://schema.org/InStock"},
         "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.8",
                            "bestRating":"5","reviewCount":"300"}}
        """)
        parsed = parse_jsonld_product(html)
        self.assertEqual(parsed["name"], "测试电脑椅")
        self.assertEqual(parsed["brand"], "示例品牌")
        self.assertEqual(parsed["price"], 699.0)
        self.assertEqual(parsed["currency"], "CNY")
        self.assertEqual(parsed["availability"], "在售")
        self.assertEqual(parsed["score"], 4.8)
        self.assertEqual(parsed["scale"], 5.0)
        self.assertEqual(parsed["review_count"], 300)
        # 星级不能当作好评率
        self.assertIsNone(parsed["positive_rate"])

    def test_graph_nesting(self):
        html = html_with_jsonld("""
        {"@context":"https://schema.org","@graph":[
          {"@type":"WebSite","name":"某商城"},
          {"@type":"Product","name":"嵌套商品","offers":{"price":"19.90","priceCurrency":"USD"}}]}
        """)
        parsed = parse_jsonld_product(html)
        self.assertEqual(parsed["name"], "嵌套商品")
        self.assertEqual(parsed["currency"], "USD")

    def test_multiple_offers_picks_cheapest_same_currency(self):
        html = html_with_jsonld("""
        {"@type":"Product","name":"多报价商品","offers":[
          {"@type":"Offer","price":"200","priceCurrency":"CNY"},
          {"@type":"Offer","price":"180","priceCurrency":"CNY"},
          {"@type":"Offer","price":"30","priceCurrency":"USD"}]}
        """)
        parsed = parse_jsonld_product(html)
        self.assertEqual(parsed["price"], 180.0)
        self.assertEqual(parsed["currency"], "CNY")

    def test_review_body_extracted_when_present(self):
        html = html_with_jsonld("""
        {"@type":"Product","name":"有评价的商品",
         "review":[{"@type":"Review","author":{"name":"张三"},"reviewBody":"用了三个月，支撑很好。"}]}
        """)
        parsed = parse_jsonld_product(html)
        self.assertEqual(len(parsed["reviews"]), 1)
        self.assertEqual(parsed["reviews"][0]["作者"], "张三")
        self.assertIn("支撑很好", parsed["reviews"][0]["内容"])

    def test_no_structured_data(self):
        self.assertIsNone(parse_jsonld_product("<html><title>没有结构化数据</title></html>"))

    def test_broken_json_is_ignored(self):
        self.assertIsNone(parse_jsonld_product(html_with_jsonld("{not json")))

    def test_product_without_name_skipped(self):
        html = html_with_jsonld('{"@type":"Product","offers":{"price":"10","priceCurrency":"CNY"}}')
        self.assertIsNone(parse_jsonld_product(html))


class MetaTests(unittest.TestCase):
    def test_open_meta_fields(self):
        html = '''<html><head>
        <meta property="og:title" content="某商品 - 某商城">
        <meta property="og:image" content="https://example.com/a.jpg">
        <meta property="og:description" content="这是描述">
        <meta property="og:site_name" content="某商城">
        <meta property="product:price:amount" content="299.00">
        <meta property="product:price:currency" content="CNY">
        </head></html>'''
        fields = open_meta_fields(html)
        self.assertEqual(fields["title"], "某商品 - 某商城")
        self.assertEqual(fields["price"], "299.00")
        self.assertEqual(fields["currency"], "CNY")

    def test_title_fallback(self):
        fields = open_meta_fields("<html><head><title>只有标题</title></head></html>")
        self.assertEqual(fields["title"], "只有标题")
        self.assertNotIn("price", fields)


class NoGuessingTests(unittest.TestCase):
    def test_marketing_price_is_not_used(self):
        """标题写「限时 299 元」但没有结构化价格时，不能把 299 当价格。"""
        html = "<html><head><title>限时优惠 299 元！</title></head><body>原价 599 元</body></html>"
        self.assertIsNone(parse_jsonld_product(html))
        self.assertNotIn("price", open_meta_fields(html))


if __name__ == "__main__":
    unittest.main()
