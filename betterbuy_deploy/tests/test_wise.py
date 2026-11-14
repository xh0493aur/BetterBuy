"""编排层的测试：全部注入假采集函数，不联网、不需要密钥。

重点验证「失败也要有用」：采集挂了仍然要给出绿色建议和检查清单，
因为公益场景里最常见的情况就是拿不到数据。
"""
import unittest

from betterbuy.models import Offer, Product, Rating
from betterbuy.wise import MAX_LINKS_PER_ANALYSIS, extract_urls, run


def fake_product(url: str = "https://store.steampowered.com/app/413150/",
                 name: str = "Hades", price: float | None = 92.0) -> Product:
    return Product(
        product_id="steam:413150",
        platform="Steam",
        url=url,
        name=name,
        brand="Supergiant Games",
        category="动作游戏",
        offer=Offer(price=price, currency="CNY" if price is not None else None),
        rating=Rating(positive_rate=96, review_count=53885),
        specs={"支持平台": "Windows / macOS"},
        origin="user",
    )


class FakeCollector:
    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.calls: list[str] = []

    def __call__(self, url, use_cache=True, ttl=None):
        self.calls.append(url)
        if self.behaviour == "fail":
            raise RuntimeError("模拟的网络失败")
        if self.behaviour == "price_missing":
            return fake_product(url, price=None)
        return fake_product(url)


class ExtractTests(unittest.TestCase):
    def test_extracts_and_dedupes(self):
        text = ("看这个 https://item.jd.com/1.html 和 "
                "https://store.steampowered.com/app/413150/，还有 https://item.jd.com/1.html")
        urls = extract_urls(text)
        self.assertEqual(len(urls), 2)
        self.assertEqual(urls[0], "https://item.jd.com/1.html")

    def test_trailing_punctuation_is_trimmed(self):
        self.assertEqual(extract_urls("看看（https://example.com/a）"),
                         ["https://example.com/a"])

    def test_no_url(self):
        self.assertEqual(extract_urls("我想买个人体工学椅"), [])


class RunTests(unittest.TestCase):
    def test_natural_language_only(self):
        collector = FakeCollector()
        result = run("我的笔记本是 Y7000P，想买个外接显示器，预算 1000，每天写代码",
                     collect_fn=collector)
        self.assertEqual(collector.calls, [])   # 没有链接就不该发任何请求
        self.assertEqual(result.category_key, "monitor")
        self.assertIn("笔记本电脑", " ".join(result.ration.checks))
        self.assertTrue(result.green.items)
        self.assertTrue(result.risk.items)
        self.assertEqual(result.ration.verdict_key, "compare")

    def test_url_is_collected_and_analysed(self):
        collector = FakeCollector()
        result = run("https://store.steampowered.com/app/413150/ 这个值得买吗，每天玩一小时",
                     collect_fn=collector)
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(len(result.products), 1)
        self.assertTrue(result.facts_ok)
        self.assertIsNotNone(result.advice)
        self.assertIsNotNone(result.rec)

    def test_collect_failure_still_produces_advice(self):
        result = run("https://store.steampowered.com/app/413150/ 想花 800 元买人体工学椅",
                     collect_fn=FakeCollector("fail"))
        self.assertEqual(result.products, [])
        self.assertTrue(result.notes)
        self.assertIn("采集失败", result.notes[0])
        # 关键：没有数据也要有可用输出
        self.assertTrue(result.green.items)
        self.assertTrue(result.ration.checks)
        self.assertEqual(result.ration.verdict_key, "compare")

    def test_price_missing_means_not_facts_ok(self):
        result = run("https://store.steampowered.com/app/413150/ 想花 100 元买个游戏，每天玩",
                     collect_fn=FakeCollector("price_missing"))
        self.assertEqual(len(result.products), 1)
        self.assertFalse(result.facts_ok)
        self.assertEqual(result.ration.verdict_key, "compare")
        self.assertIsNotNone(result.advice)
        # 没有价格时不能给出分档推荐，只能给清单
        self.assertIn("拿不到可用价格", result.advice.summary)

    def test_link_only_input_asks_for_need(self):
        """只贴链接、不说需求时，不给「推荐购买」，而是说清拿到的数据回答不了什么。"""
        result = run("https://store.steampowered.com/app/413150/",
                     collect_fn=FakeCollector())
        self.assertEqual(result.ration.verdict_key, "compare")
        self.assertNotEqual(result.ration.verdict, "推荐购买")
        self.assertIn("是否适合你", " ".join(result.ration.reasons))

    def test_category_override_is_reported(self):
        result = run("想买个键盘，预算 300", category_key="mouse",
                     collect_fn=FakeCollector())
        self.assertEqual(result.category_key, "mouse")
        self.assertEqual(result.category_source, "你手动选择")
        self.assertTrue(any("鼠标" in note and "机械键盘" in note or "键盘" in note
                            for note in result.notes))

    def test_budget_cap_is_injected_into_intent(self):
        result = run("想买个显示器", budget_cap=1200, collect_fn=FakeCollector())
        self.assertEqual(result.intent.budget_high, 1200)
        self.assertTrue(result.intent.has_budget)

    def test_link_count_is_capped(self):
        collector = FakeCollector()
        query = "\n".join(f"https://store.steampowered.com/app/{i}/" for i in range(1, 8))
        result = run(query, collect_fn=collector)
        self.assertEqual(len(collector.calls), MAX_LINKS_PER_ANALYSIS)
        self.assertEqual(len(result.urls), MAX_LINKS_PER_ANALYSIS)

    def test_discovery_runs_only_for_steam(self):
        calls = {"discover": 0}

        def fake_discover(product, **kwargs):
            calls["discover"] += 1

            class Result:
                ok = False
                candidates = []
                note = "测试用"

            return Result()

        run("https://store.steampowered.com/app/413150/", collect_fn=FakeCollector(),
            discover_fn=fake_discover)
        self.assertEqual(calls["discover"], 1)

        calls["discover"] = 0
        run("https://item.jd.com/100012043978.html",
            collect_fn=lambda url, use_cache=True, ttl=None: Product(
                product_id="jd:1", platform="京东", url=url, name="某商品"),
            discover_fn=fake_discover)
        self.assertEqual(calls["discover"], 0)

    def test_empty_query_is_safe(self):
        result = run("", collect_fn=FakeCollector())
        self.assertEqual(result.products, [])
        self.assertEqual(result.ration.verdict_key, "empty")
        self.assertTrue(result.green.items)


if __name__ == "__main__":
    unittest.main()
