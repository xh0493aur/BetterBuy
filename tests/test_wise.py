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


class FakeSearch:
    """假的官方搜索接口：只验证编排层行为，不联网。"""

    ok = True
    kind = ""
    used_api = "pdd.ddk.goods.search"

    def __init__(self, products=None, ok=True, kind="", exc=None):
        self.products = products if products is not None else []
        self.ok = ok
        self.kind = kind
        self.exc = exc
        self.calls: list[str] = []

    def __call__(self, keyword, limit=10):
        self.calls.append(keyword)
        if self.exc:
            raise self.exc
        return self


def pdd_product(index: int, price: float) -> Product:
    product = Product(product_id=f"pdd:{index}", platform="拼多多", url="",
                      name=f"机械键盘 87 键 第 {index} 款")
    product.offer = Offer(price=price, currency="CNY", source_key="pdd")
    product.specs = {"店铺": "某旗舰店"}
    product.origin = "search"
    product.recompute_missing()
    return product


class SearchKeywordTests(unittest.TestCase):
    def test_alias_wins_over_whole_sentence(self):
        from betterbuy.intent import parse_intent
        from betterbuy.wise import search_keyword

        text = "我想花 800 元购买人体工学椅，主要用来学习和打游戏"
        self.assertEqual(search_keyword(parse_intent(text), text), "人体工学椅")

    def test_owned_device_does_not_become_keyword(self):
        from betterbuy.intent import parse_intent
        from betterbuy.wise import search_keyword

        text = "我的笔记本是拯救者 Y7000P，想买个外接显示器，预算 1000"
        keyword = search_keyword(parse_intent(text), text)
        self.assertEqual(keyword, "显示器")

    def test_short_name_without_category_is_used_as_is(self):
        from betterbuy.intent import parse_intent
        from betterbuy.wise import search_keyword

        self.assertEqual(search_keyword(parse_intent("星巴克杯子"), "星巴克杯子"), "星巴克杯子")

    def test_long_unrecognised_sentence_is_not_searched(self):
        from betterbuy.intent import parse_intent
        from betterbuy.wise import search_keyword

        text = "最近有点想买点什么，但是还没想好要买啥，先看看再说吧"
        self.assertEqual(search_keyword(parse_intent(text), text), "")


class KeywordSearchFlowTests(unittest.TestCase):
    def test_successful_search_feeds_products_and_recommendation(self):
        fake = FakeSearch([pdd_product(1, 199.0), pdd_product(2, 359.0)])
        result = run("机械键盘 87 键", search_fn=fake, collect_fn=FakeCollector())
        self.assertEqual(fake.calls, ["机械键盘"])
        self.assertEqual(len(result.products), 2)
        self.assertTrue(result.facts_ok)
        self.assertIsNotNone(result.rec)
        self.assertIsNotNone(result.advice)
        self.assertTrue(any("真实商品" in n for n in result.notes))

    def test_no_credential_is_reported_quietly(self):
        fake = FakeSearch(ok=False, kind="no_credential")
        result = run("机械键盘 87 键", search_fn=fake, collect_fn=FakeCollector())
        self.assertEqual(result.products, [])
        self.assertEqual(result.search_kind, "no_credential")
        # 用 ℹ️ 而不是 ⚠️：这不是用户的错，也不该吓到普通用户
        self.assertTrue(all(not n.startswith("⚠️") for n in result.notes))
        self.assertTrue(any("没有取到商品数据" in n for n in result.notes))
        # 没数据也要给出判断、绿色建议与风险清单
        self.assertTrue(result.ration.verdict)
        self.assertTrue(result.green.items)
        self.assertTrue(result.risk.items)

    def test_budget_cap_drops_obvious_outliers(self):
        fake = FakeSearch([pdd_product(1, 100.0), pdd_product(2, 140.0), pdd_product(3, 900.0)])
        result = run("机械键盘 87 键", budget_cap=100, search_fn=fake, collect_fn=FakeCollector())
        self.assertEqual(len(result.products), 2)
        self.assertTrue(any("超出预算" in n for n in result.notes))

    def test_search_exception_does_not_break_analysis(self):
        fake = FakeSearch(exc=RuntimeError("模拟超时"))
        result = run("机械键盘 87 键", search_fn=fake, collect_fn=FakeCollector())
        self.assertEqual(result.products, [])
        self.assertTrue(any("商品搜索未能完成" in n for n in result.notes))
        self.assertTrue(result.green.items)

    def test_links_skip_search(self):
        fake = FakeSearch([pdd_product(1, 199.0)])
        run("https://store.steampowered.com/app/413150/", search_fn=fake,
            collect_fn=FakeCollector())
        self.assertEqual(fake.calls, [], "贴了链接不该再搜关键词")

    def test_no_search_fn_keeps_old_behaviour(self):
        result = run("机械键盘 87 键", collect_fn=FakeCollector())
        self.assertEqual(result.products, [])
        self.assertEqual(result.searched_keyword, "")


if __name__ == "__main__":
    unittest.main()
