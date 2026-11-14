"""拼多多关键词搜索链路的离线测试。

全部用假 transport，不发真实请求，也不需要密钥：
- 返回体按官方文档的字段形状构造，用于验证映射与错误分类；
- 断言里固定检查「不编造链接、不补价格」这两条底线。
"""
import unittest

from betterbuy.openapi.credentials import SPECS, Credential
from betterbuy.openapi.errors import ApiError
from betterbuy.openapi.pdd import PDDClient, clamp_page_size, normalize_custom_parameters
from betterbuy.openapi.transport import Transport, _classify
from betterbuy.pdd_search import attach_links, search

GOODS = {
    "goods_id": 10086,
    "goods_sign": "SIGN-AAA",
    "goods_name": "机械键盘 87 键 热插拔",
    "goods_desc": "机械键盘 87 键 热插拔 白光",
    "min_group_price": 12900,
    "min_normal_price": 19900,
    "coupon_discount": 2000,
    "coupon_min_order_amount": 9900,
    "mall_name": "某某外设旗舰店",
    "sales_tip": "已拼 3.2 万件",
    "opt_name": "电脑外设",
    "goods_thumbnail_url": "https://img.example.com/a.jpg",
}


def pdd_cred(**overrides):
    label, required, optional = SPECS["pdd"]
    values = {"BETTERBUY_PDD_CLIENT_ID": "cid", "BETTERBUY_PDD_CLIENT_SECRET": "sec",
              "BETTERBUY_PDD_PID": "1_2"}
    values.update(overrides)
    return Credential(platform="pdd", label=label, values=values,
                      required=required, optional=optional)


def client_for(payload, status=200, cred=None):
    """返回 (client, calls)。"""
    calls = []

    def _post(url, data, **kwargs):
        calls.append((url, dict(data)))
        return Transport(url=url, status=status, payload=payload, body_text="{}")

    return PDDClient(cred or pdd_cred(), post_fn=_post), calls


def ok_search(items=None, total=1, search_id="SID"):
    return {"goods_search_response": {"goods_list": list(items if items is not None else [GOODS]),
                                      "total_count": total, "search_id": search_id}}


def err(code, msg="业务服务错误", sub_code="", sub_msg=""):
    return {"error_response": {"error_code": code, "error_msg": msg,
                               "sub_code": sub_code, "sub_msg": sub_msg}}


class TestSearchMapping(unittest.TestCase):
    def test_success_builds_products_with_real_fields(self):
        client, calls = client_for(ok_search())
        result = search("机械键盘", client=client)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.products), 1)
        p = result.products[0]
        self.assertEqual(p.platform, "拼多多")
        self.assertEqual(p.name, "机械键盘 87 键 热插拔")
        self.assertEqual(p.offer.price, 129.0)          # 分 -> 元
        self.assertEqual(p.offer.currency, "CNY")
        self.assertEqual(p.origin, "search")
        self.assertEqual(p.specs["店铺"], "某某外设旗舰店")
        self.assertEqual(len(calls), 1)

    def test_price_terms_state_coupon_and_shipping(self):
        client, _ = client_for(ok_search())
        p = search("x", client=client).products[0]
        terms = p.offer.terms
        self.assertIn("拼团价", terms)
        self.assertIn("券后约 ¥109.00", terms)            # 129 - 20
        self.assertIn("满 ¥99 可用", terms)
        self.assertIn("不含运费", terms)

    def test_no_link_is_invented(self):
        client, _ = client_for(ok_search())
        p = search("x", client=client).products[0]
        self.assertEqual(p.url, "")
        self.assertTrue(any("不返回商品链接" in n for n in p.notes))

    def test_missing_fields_are_marked_not_filled(self):
        slim = {"goods_id": 1, "goods_name": "只有名字的商品"}
        client, _ = client_for(ok_search([slim]))
        p = search("x", client=client).products[0]
        self.assertIsNone(p.offer.price)
        self.assertIn("价格", p.missing)
        self.assertIn("好评率", p.missing)

    def test_image_from_thumbnail(self):
        client, _ = client_for(ok_search())
        p = search("x", client=client).products[0]
        self.assertEqual(p.image_url, "https://img.example.com/a.jpg")

    def test_duplicate_ids_deduped(self):
        client, _ = client_for(ok_search([GOODS, dict(GOODS)]))
        self.assertEqual(len(search("x", client=client).products), 1)


class TestErrorClassification(unittest.TestCase):
    def test_credential_error(self):
        client, _ = client_for(err(10016, "client下线或者clientId不正确"))
        r = search("x", client=client)
        self.assertFalse(r.ok)
        self.assertEqual(r.kind, "auth")
        self.assertIn("10016", r.code)
        self.assertIn("client_id", r.hint)

    def test_permission_error(self):
        client, _ = client_for(err(20031, "用户没有授权访问此接口"))
        r = search("x", client=client)
        self.assertEqual(r.kind, "permission")
        self.assertIn("权限", r.hint)

    def test_rate_limited(self):
        client, _ = client_for(err(52101, "当前接口被限流,请稍后重试"))
        r = search("x", client=client)
        self.assertEqual(r.kind, "quota")

    def test_rate_limited_too_frequent(self):
        client, _ = client_for(err(70031, "调用过于频繁,请调整调用频率"))
        self.assertEqual(search("x", client=client).kind, "quota")

    def test_needs_authorization_record(self):
        client, _ = client_for(err(50001, "业务服务错误", sub_code="60001",
                                   sub_msg="未传入已经授权备案过的相关参数(pid/custom_parameters)"))
        r = search("x", client=client)
        self.assertEqual(r.kind, "authorize")
        self.assertEqual(r.sub_code, "60001")
        self.assertIn("备案", r.hint)

    def test_signature_error_is_auth(self):
        client, _ = client_for(err(20004, "签名sign校验失败"))
        self.assertEqual(search("x", client=client).kind, "auth")

    def test_empty_result_is_not_an_error(self):
        client, _ = client_for({"goods_search_response": {"goods_list": [], "total_count": 0}})
        r = search("很冷门的词", client=client)
        self.assertFalse(r.ok)
        self.assertEqual(r.kind, "empty")
        self.assertIn("推广池", r.hint)
        self.assertEqual(r.products, [])

    def test_missing_credentials(self):
        empty = pdd_cred(BETTERBUY_PDD_CLIENT_ID="", BETTERBUY_PDD_CLIENT_SECRET="")
        client, calls = client_for(ok_search(), cred=empty)
        r = search("x", client=client)
        self.assertEqual(r.kind, "no_credential")
        self.assertIn("BETTERBUY_PDD_CLIENT_ID", r.message)
        self.assertEqual(calls, [])          # 没配置就不该发请求

    def test_url_input_is_rejected_before_request(self):
        client, calls = client_for(ok_search())
        r = search("https://mobile.yangkeduo.com/goods.html?goods_id=1", client=client)
        self.assertFalse(r.ok)
        self.assertIn("链接", r.message)
        self.assertEqual(calls, [])


class TestRequestShape(unittest.TestCase):
    def test_page_size_is_clamped_to_official_range(self):
        client, calls = client_for(ok_search())
        client.search_page(keyword="x", page_size=1)
        self.assertEqual(calls[0][1]["page_size"], "10")
        client.search_page(keyword="x", page_size=500)
        self.assertEqual(calls[1][1]["page_size"], "100")

    def test_sort_and_coupon_params(self):
        client, calls = client_for(ok_search())
        client.search_page(keyword="x", sort_type=1, with_coupon=True)
        data = calls[0][1]
        self.assertEqual(data["type"], "pdd.ddk.goods.search")
        self.assertEqual(data["sort_type"], "1")
        self.assertEqual(data["with_coupon"], "true")     # 布尔必须小写
        self.assertEqual(data["pid"], "1_2")

    def test_custom_parameters_only_when_configured(self):
        client, calls = client_for(ok_search())
        client.search_page(keyword="x")
        self.assertNotIn("custom_parameters", calls[0][1])

        client2, calls2 = client_for(
            ok_search(), cred=pdd_cred(BETTERBUY_PDD_CUSTOM_PARAMETERS='{"uid":"bb"}'))
        client2.search_page(keyword="x")
        self.assertEqual(calls2[0][1]["custom_parameters"], '{"uid":"bb"}')

    def test_short_uid_is_wrapped_as_json(self):
        self.assertEqual(normalize_custom_parameters("bb"), '{"uid":"bb"}')
        # 看起来像 JSON 但解析不了：宁可不用，也不猜
        self.assertEqual(normalize_custom_parameters("{bad json"), "")
        self.assertEqual(normalize_custom_parameters(""), "")
        self.assertEqual(normalize_custom_parameters("[1,2]"), "")

    def test_sign_present_and_uppercase(self):
        client, calls = client_for(ok_search())
        client.search_page(keyword="x")
        sign = calls[0][1]["sign"]
        self.assertEqual(sign, sign.upper())
        self.assertEqual(len(sign), 32)


class TestDetailAndLinks(unittest.TestCase):
    def test_detail_enrichment_uses_goods_sign(self):
        calls = []

        def _post(url, data, **kwargs):
            calls.append(dict(data))
            if data["type"] == "pdd.ddk.goods.search":
                payload = ok_search([{"goods_id": 1, "goods_sign": "S1", "goods_name": "键盘",
                                      "min_group_price": 9900}])
            else:
                payload = {"goods_detail_response": {"goods_detail_list": [
                    {"goods_sign": "S1", "goods_name": "键盘", "mall_name": "店A",
                     "goods_image_url": "https://img.example.com/b.jpg", "opt_name": "外设"}]}}
            return Transport(url=url, status=200, payload=payload, body_text="{}")

        client = PDDClient(pdd_cred(), post_fn=_post)
        r = search("键盘", client=client, detail_count=1)
        self.assertEqual(r.api_calls, 2)
        self.assertEqual(calls[1]["type"], "pdd.ddk.goods.detail")
        self.assertEqual(calls[1]["goods_sign"], "S1")
        p = r.products[0]
        self.assertEqual(p.image_url, "https://img.example.com/b.jpg")
        self.assertEqual(p.category, "外设")

    def test_detail_failure_does_not_break_search(self):
        def _post(url, data, **kwargs):
            if data["type"] == "pdd.ddk.goods.search":
                payload = ok_search([{"goods_id": 1, "goods_sign": "S1", "goods_name": "键盘"}])
            else:
                payload = err(20031, "用户没有授权访问此接口")
            return Transport(url=url, status=200, payload=payload, body_text="{}")

        r = search("键盘", client=PDDClient(pdd_cred(), post_fn=_post), detail_count=1)
        self.assertTrue(r.ok)
        self.assertTrue(any("详情接口未取到" in n for n in r.products[0].notes))

    def test_promotion_link_filled_when_api_returns_url(self):
        def _post(url, data, **kwargs):
            payload = {"goods_promotion_url_generate_response": {"goods_promotion_url_list": [
                {"goods_sign": "SIGN-AAA", "mobile_url": "https://mobile.yangkeduo.com/x"}]}}
            return Transport(url=url, status=200, payload=payload, body_text="{}")

        client = PDDClient(pdd_cred(), post_fn=_post)
        products = search("x", client=client_for(ok_search())[0]).products
        filled, msg = attach_links(products, client=client)
        self.assertEqual(filled, 1)
        self.assertEqual(products[0].url, "https://mobile.yangkeduo.com/x")
        self.assertIn("推广", msg)

    def test_promotion_link_failure_keeps_url_empty(self):
        def _post(url, data, **kwargs):
            return Transport(url=url, status=200, payload=err(20031, "用户没有授权访问此接口"),
                             body_text="{}")

        client = PDDClient(pdd_cred(), post_fn=_post)
        products = search("x", client=client_for(ok_search())[0]).products
        filled, msg = attach_links(products, client=client)
        self.assertEqual(filled, 0)
        self.assertEqual(products[0].url, "")      # 失败就留空，不拼接兜底链接
        self.assertIn("权限", msg)

    def test_attach_links_without_sign(self):
        products = search("x", client=client_for(ok_search([{"goods_id": 1, "goods_name": "无 sign"}]))[0]).products
        filled, msg = attach_links(products, client=client_for(ok_search())[0])
        self.assertEqual(filled, 0)
        self.assertIn("goods_sign", msg)


class TestTransportClassification(unittest.TestCase):
    def test_official_codes(self):
        cases = {
            ("10016", "", ""): "auth",
            ("20004", "", ""): "auth",
            ("20031", "", ""): "permission",
            ("30000", "", ""): "permission",
            ("20005", "", ""): "permission",
            ("52101", "", ""): "quota",
            ("70031", "", ""): "quota",
            ("50001", "60001", ""): "authorize",
        }
        for (code, sub, msg), expected in cases.items():
            self.assertEqual(_classify(code, sub, msg), expected, f"{code}/{sub}")

    def test_unknown_falls_back_to_api(self):
        self.assertEqual(_classify("99999", "", "some error"), "api")


if __name__ == "__main__":
    unittest.main()
