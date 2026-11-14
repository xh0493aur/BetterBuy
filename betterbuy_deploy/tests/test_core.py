import unittest
import pandas as pd
from scraper import parse_product_html, check_url, ScrapeError
from scoring import reputation_score, score_items


class ParserTests(unittest.TestCase):
    def test_graph_json_ld(self):
        html = '''<html><script type="application/ld+json">{
        "@context":"https://schema.org", "@graph":[{"@type":"WebSite","name":"Store"},
        {"@type":"Product","name":"测试电脑椅","offers":{"@type":"Offer","price":"699.00","priceCurrency":"CNY"},
        "aggregateRating":{"ratingValue":"4.8","bestRating":"5","reviewCount":"300"}}]}
        </script></html>'''
        p = parse_product_html(html, 'https://shop.example.com/chair')
        self.assertEqual(p['商品'], '测试电脑椅')
        self.assertEqual(p['价格'], 699)
        self.assertEqual(p['币种'], 'CNY')
        self.assertEqual(p['评分'], 4.8)
        self.assertEqual(p['评价数'], 300)
        self.assertIsNone(p['好评率%'])  # 4.8/5 不是 96% 好评率

    def test_meta_fallback(self):
        html = '''<title>椅子C</title><meta property="product:price:amount" content="¥500.00">
        <meta property="product:price:currency" content="CNY">'''
        p = parse_product_html(html, 'https://shop.example.com/chair')
        self.assertEqual(p['价格'], 500)
        self.assertEqual(p['币种'], 'CNY')
        self.assertIsNone(p['评分'])

    def test_no_price_guess(self):
        p = parse_product_html('<title>限时优惠 299 元！</title>', 'https://shop.example.com/p')
        self.assertIsNone(p['价格'])

    def test_private_url_block(self):
        for u in ['http://127.0.0.1/private', 'http://localhost:8501', 'file:///etc/passwd']:
            with self.subTest(u=u), self.assertRaises(ScrapeError):
                check_url(u)


class ScoreTests(unittest.TestCase):
    def test_star_not_positive_rate(self):
        s, note = reputation_score({'评分': 4.8, '满分': 5, '评价数': None})
        self.assertEqual(s, 96.0)
        self.assertIn('不是好评率', note)

    def test_uncertain_reviews(self):
        a, _ = reputation_score({'评分': 5, '满分': 5, '评价数': 1})
        b, _ = reputation_score({'评分': 5, '满分': 5, '评价数': 1000})
        self.assertLess(a, b)

    def test_zero_weight_allows_missing_utility(self):
        frame = pd.DataFrame([{'商品':'a', '价格':100, '币种':'CNY', '实用度(1-10)':None,
                               '评分':5, '满分':5, '评价数':200}])
        score = score_items(frame, (50, 0, 50))
        self.assertIsNotNone(score.iloc[0]['综合分'])

    def test_missing_utility_not_ranked(self):
        frame = pd.DataFrame([
            {'商品':'a', '价格':100, '币种':'CNY', '实用度(1-10)':None, '评分':5, '满分':5, '评价数':200},
            {'商品':'b', '价格':200, '币种':'CNY', '实用度(1-10)':9, '评分':4, '满分':5, '评价数':200},
        ])
        x = score_items(frame)
        self.assertEqual(x.iloc[0]['商品'], 'b')
        self.assertTrue(pd.isna(x.iloc[1]['综合分']))


if __name__ == '__main__':
    unittest.main()
