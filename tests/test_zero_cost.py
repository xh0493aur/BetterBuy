"""零成本约束的测试：开关、配额、超限即停、缺数据跳转。

这些用例不联网。配额用临时 SQLite 文件，不会污染项目里的 data/。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from betterbuy.fallback import fallback_links, has_enough_data, search_url
from betterbuy.models import Offer, Product, Rating
from betterbuy.zero_cost import (DAILY_LIMITS, Quota, QuotaExceeded, daily_limit,
                                 quota_rows, total_limit, zero_cost_mode)


class ZeroCostSwitchTests(unittest.TestCase):
    def test_default_is_zero_cost(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BETTERBUY_ZERO_COST", None)
            self.assertTrue(zero_cost_mode())

    def test_explicit_off(self):
        for value in ("0", "false", "no", "off"):
            with mock.patch.dict(os.environ, {"BETTERBUY_ZERO_COST": value}):
                self.assertFalse(zero_cost_mode(), value)

    def test_blank_means_default_on(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_ZERO_COST": ""}):
            self.assertTrue(zero_cost_mode())


class LlmBlockedTests(unittest.TestCase):
    def test_llm_unavailable_even_if_configured(self):
        env = {
            "BETTERBUY_LLM_BASE_URL": "https://example.com/v1",
            "BETTERBUY_LLM_API_KEY": "k",
            "BETTERBUY_LLM_MODEL": "m",
        }
        from betterbuy.advisor import llm_available
        with mock.patch.dict(os.environ, env):
            os.environ.pop("BETTERBUY_ZERO_COST", None)
            self.assertFalse(llm_available(), "零成本模式下不允许调用大模型")

    def test_llm_available_only_when_zero_cost_off(self):
        env = {
            "BETTERBUY_LLM_BASE_URL": "https://example.com/v1",
            "BETTERBUY_LLM_API_KEY": "k",
            "BETTERBUY_LLM_MODEL": "m",
            "BETTERBUY_ZERO_COST": "0",
        }
        from betterbuy.advisor import llm_available
        with mock.patch.dict(os.environ, env):
            self.assertTrue(llm_available())


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = Quota(Path(self.tmp.name) / "quota.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_consume_counts(self):
        self.assertEqual(self.q.used_today("jd"), 0)
        self.assertEqual(self.q.consume("jd"), 1)
        self.assertEqual(self.q.consume("jd"), 2)
        self.assertEqual(self.q.used_today("jd"), 2)

    def test_limit_exceeded_raises_and_does_not_consume(self):
        env = {"BETTERBUY_DAILY_LIMIT_JD": "2"}
        with mock.patch.dict(os.environ, env):
            self.q.consume("jd")
            self.q.consume("jd")
            with self.assertRaises(QuotaExceeded):
                self.q.consume("jd")
            self.assertEqual(self.q.used_today("jd"), 2, "超限那一次不应计数")

    def test_total_limit_applies_across_sources(self):
        env = {"BETTERBUY_DAILY_LIMIT_TOTAL": "3"}
        with mock.patch.dict(os.environ, env):
            self.q.consume("jd")
            self.q.consume("taobao")
            self.q.consume("pdd")
            with self.assertRaises(QuotaExceeded) as ctx:
                self.q.consume("steam")
            self.assertIn("合计", str(ctx.exception))

    def test_zero_limit_blocks_everything(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_PDD": "0"}):
            with self.assertRaises(QuotaExceeded):
                self.q.consume("pdd")
            self.assertEqual(self.q.used_today("pdd"), 0)

    def test_remaining_never_negative(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_JD": "1"}):
            self.q.consume("jd")
            self.assertEqual(self.q.remaining("jd"), 0)
            with self.assertRaises(QuotaExceeded):
                self.q.consume("jd")
            self.assertEqual(self.q.remaining("jd"), 0)

    def test_reset(self):
        self.q.consume("jd")
        self.q.reset_today("jd")
        self.assertEqual(self.q.used_today("jd"), 0)

    def test_rows_shape(self):
        rows = self.q.rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row), {"数据源", "今日已用", "每日上限", "剩余", "状态"})
        self.assertTrue(any(r["数据源"].startswith("合计") for r in rows))

    def test_daily_limit_env_override(self):
        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_STEAM": "42"}):
            self.assertEqual(daily_limit("steam"), 42)
        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_STEAM": "abc"}):
            self.assertEqual(daily_limit("steam"), DAILY_LIMITS["steam"],
                             "非法值应回退到默认值而不是崩掉")

    def test_default_limits_are_conservative(self):
        for source, value in DAILY_LIMITS.items():
            self.assertGreater(value, 0, source)
            self.assertLessEqual(value, 1000, f"{source} 默认上限过于激进")
        self.assertLessEqual(total_limit(), 2000)


class TransportQuotaTests(unittest.TestCase):
    """配额耗尽时，请求必须**根本不发出去**。"""

    def test_post_blocks_before_network(self):
        from betterbuy.openapi import transport
        from betterbuy.openapi.errors import ApiError

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_quota = Quota(Path(tmp.name) / "q.sqlite3")

        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_JD": "0"}):
            with mock.patch.object(transport, "quota", fake_quota):
                with mock.patch.object(transport.requests, "post") as post_spy:
                    with self.assertRaises(ApiError) as ctx:
                        transport.post("https://api.jd.com/routerjson", {"a": 1}, platform="jd")
                    post_spy.assert_not_called()
                self.assertEqual(ctx.exception.kind, "quota")

    def test_post_counts_when_allowed(self):
        from betterbuy.openapi import transport

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_quota = Quota(Path(tmp.name) / "q.sqlite3")

        class FakeResp:
            status_code = 200
            text = '{"ok":1}'

            def json(self):
                return {"ok": 1}

        with mock.patch.object(transport, "quota", fake_quota):
            with mock.patch.object(transport.requests, "post", return_value=FakeResp()):
                transport.post("https://api.jd.com/routerjson", {"a": 1}, platform="jd")
        self.assertEqual(fake_quota.used_today("jd"), 1)


class CollectQuotaTests(unittest.TestCase):
    def test_collect_reports_quota_kind(self):
        import betterbuy.adapters as adapters
        from betterbuy.http_client import FetchError

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_quota = Quota(Path(tmp.name) / "q.sqlite3")

        with mock.patch.dict(os.environ, {"BETTERBUY_DAILY_LIMIT_STEAM": "0"}):
            with mock.patch.object(adapters, "quota", fake_quota):
                with self.assertRaises(FetchError) as ctx:
                    adapters.collect("https://store.steampowered.com/app/413150/",
                                     use_cache=False)
        self.assertEqual(ctx.exception.kind, "quota")


def _product(**kwargs) -> Product:
    base = Product(product_id="t:1", platform="京东", url="https://item.jd.com/1.html",
                   name="某品牌 有线鼠标 M100")
    base.offer = Offer(price=99.0, currency="CNY")
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


class FallbackLinkTests(unittest.TestCase):
    def test_search_url_encodes_keyword(self):
        url = search_url("jd", "红米 显示器 A22")
        self.assertTrue(url.startswith("https://search.jd.com/Search?keyword="))
        self.assertNotIn(" ", url)

    def test_unknown_platform_returns_empty(self):
        self.assertEqual(search_url("不存在的平台", "x"), "")

    def test_tmall_and_taobao_both_supported(self):
        self.assertIn("tmall.com", search_url("天猫", "x"))
        self.assertIn("taobao.com", search_url("淘宝", "x"))

    def test_original_link_comes_first(self):
        product = _product()
        links = fallback_links(product)
        self.assertEqual(links[0]["链接"], product.url)

    def test_no_invented_link_for_unknown_platform(self):
        product = Product(product_id="x:1", platform="某小众商城", url="", name="某个东西")
        links = fallback_links(product)
        for link in links:
            self.assertTrue(link["链接"].startswith("http"), link)

    def test_has_enough_data_rules(self):
        product = _product()
        self.assertFalse(has_enough_data(product), "只有价格、没有口碑和规格，不算够")
        product.rating = Rating(positive_rate=95.0, review_count=100)
        self.assertTrue(has_enough_data(product))

    def test_missing_price_is_never_enough(self):
        product = _product()
        product.offer = Offer(price=None, currency="")
        product.rating = Rating(positive_rate=99.0, review_count=10_000)
        self.assertFalse(has_enough_data(product), "没有价格就无法比较")

    def test_insufficient_data_offers_multiple_platforms(self):
        product = _product()
        links = fallback_links(product, limit=3)
        platforms = {link["平台"] for link in links}
        self.assertGreaterEqual(len(platforms), 2)


class PaidEndpointScanTests(unittest.TestCase):
    """防止有人后来偷偷把付费端点写进代码。"""

    def test_project_has_no_hardcoded_paid_endpoints(self):
        import importlib.util

        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "_check_zero_cost", root / "scripts" / "check_zero_cost.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        self.assertEqual(module.scan_paid_hosts(), [],
                         "代码里出现了写死的付费服务端点，请确认是否会被调用")


if __name__ == "__main__":
    unittest.main()
