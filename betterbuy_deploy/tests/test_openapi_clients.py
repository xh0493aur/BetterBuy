"""开放平台客户端测试（用假的 transport，不联网）。

返回体结构按官方文档描述构造，只用于验证**解析与错误翻译**是否正确；
不包含任何真实商品数据，也不会进入界面。
"""
import json
import re
import unittest

from betterbuy.openapi.credentials import Credential
from betterbuy.openapi.errors import ApiError
from betterbuy.openapi.jd import GATEWAY as JD_GATEWAY, JDClient
from betterbuy.openapi.pdd import PDDClient
from betterbuy.openapi.signing import jd_sign
from betterbuy.openapi.taobao import TaobaoClient
from betterbuy.openapi.transport import Transport


def _cred(platform, values):
    from betterbuy.openapi.credentials import SPECS
    label, required, optional = SPECS[platform]
    return Credential(platform=platform, label=label, values=values,
                      required=required, optional=optional)


def _jd_cred():
    return _cred("jd", {"BETTERBUY_JD_APP_KEY": "k", "BETTERBUY_JD_APP_SECRET": "s"})


def _tb_cred():
    return _cred("taobao", {"BETTERBUY_TB_APP_KEY": "k", "BETTERBUY_TB_APP_SECRET": "s",
                            "BETTERBUY_TB_ADZONE_ID": "12345"})


def _pdd_cred():
    return _cred("pdd", {"BETTERBUY_PDD_CLIENT_ID": "k", "BETTERBUY_PDD_CLIENT_SECRET": "s",
                         "BETTERBUY_PDD_PID": "1_2"})


def fake_post(payload: dict, status: int = 200):
    calls: list[tuple[str, dict]] = []

    def _post(url, data, **kwargs):
        calls.append((url, dict(data)))
        return Transport(url=url, status=status, payload=payload, body_text=json.dumps(payload))

    _post.calls = calls  # type: ignore[attr-defined]
    return _post


class TestJDClient(unittest.TestCase):
    def test_request_shape(self):
        post = fake_post({"jd_union_open_goods_query_responce":
                          {"code": "0", "queryResult": json.dumps({"data": []})}})
        JDClient(_jd_cred(), post_fn=post).goods_query(keyword="手机")
        url, params = post.calls[0]
        self.assertEqual(url, JD_GATEWAY)
        self.assertEqual(params["method"], "jd.union.open.goods.query")
        self.assertEqual(params["app_key"], "k")
        self.assertEqual(params["sign_method"], "md5")
        self.assertEqual(params["format"], "json")
        self.assertEqual(params["v"], "1.0")
        self.assertRegex(params["timestamp"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(params["sign"], jd_sign(params, "s"))

    def test_business_params_are_json_string(self):
        post = fake_post({"jd_union_open_goods_query_responce": {"queryResult": "{}"}})
        JDClient(_jd_cred(), post_fn=post).goods_query(keyword="耳机", pageIndex=2)
        _, params = post.calls[0]
        body = json.loads(params["360buy_param_json"])
        self.assertEqual(body["goodsReqDTO"]["keyword"], "耳机")
        self.assertEqual(body["goodsReqDTO"]["pageIndex"], 2)

    def test_unwrap_json_string_result(self):
        inner = {"code": 200, "data": [{"skuId": 1001, "skuName": "测试商品"}]}
        post = fake_post({"jd_union_open_goods_query_responce":
                          {"code": "0", "queryResult": json.dumps(inner, ensure_ascii=False)}})
        items = JDClient(_jd_cred(), post_fn=post).goods_query(keyword="x")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["skuName"], "测试商品")

    def test_invalid_app_key_is_auth_error(self):
        post = fake_post({"error_response": {"code": "21", "zh_desc": "Invalid app_key",
                                             "request_id": "1"}})
        with self.assertRaises(ApiError) as ctx:
            JDClient(_jd_cred(), post_fn=post).goods_query(keyword="x")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertIn("Invalid app_key", ctx.exception.message)

    def test_missing_credential_does_not_call_network(self):
        post = fake_post({})
        client = JDClient(_cred("jd", {}), post_fn=post)
        with self.assertRaises(ApiError) as ctx:
            client.goods_query(keyword="x")
        self.assertEqual(ctx.exception.kind, "no_credential")
        self.assertEqual(post.calls, [])


class TestTaobaoClient(unittest.TestCase):
    def test_request_shape(self):
        post = fake_post({"tbk_item_info_upgrade_get_response": {"results": {}}})
        TaobaoClient(_tb_cred(), post_fn=post).item_info("612345678901")
        url, params = post.calls[0]
        self.assertIn("/router/rest", url)
        self.assertEqual(params["method"], "taobao.tbk.item.info.upgrade.get")
        self.assertEqual(params["v"], "2.0")
        self.assertEqual(params["item_id"], "612345678901")
        self.assertEqual(len(params["sign"]), 32)

    def test_material_search_requires_adzone(self):
        post = fake_post({"tbk_dg_material_optional_upgrade_response": {"result_list": {}}})
        TaobaoClient(_tb_cred(), post_fn=post).material_search(q="耳机")
        _, params = post.calls[0]
        self.assertEqual(params["adzone_id"], "12345")

    def test_parse_result_list(self):
        post = fake_post({"tbk_dg_material_optional_upgrade_response": {
            "result_list": {"map_data": [{"item_id": "1"}, {"item_id": "2"}]},
            "total_results": 2,
        }})
        items = TaobaoClient(_tb_cred(), post_fn=post).material_search(q="x")
        self.assertEqual([i["item_id"] for i in items], ["1", "2"])

    def test_parse_item_detail(self):
        post = fake_post({"tbk_item_details_upgrade_get_response": {
            "results": {"tbk_item_detail": [{"item_id": "9"}]}}})
        items = TaobaoClient(_tb_cred(), post_fn=post).item_details("9")
        self.assertEqual(items[0]["item_id"], "9")

    def test_appkey_not_exists_is_auth(self):
        post = fake_post({"error_response": {"code": 29, "msg": "Invalid app Key",
                                             "sub_code": "isv.appkey-not-exists"}})
        with self.assertRaises(ApiError) as ctx:
            TaobaoClient(_tb_cred(), post_fn=post).item_info("1")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(ctx.exception.sub_code, "isv.appkey-not-exists")

    def test_empty_when_wrapper_missing(self):
        post = fake_post({"_raw": "unexpected"})
        self.assertEqual(TaobaoClient(_tb_cred(), post_fn=post).item_info("1"), [])


class TestPDDClient(unittest.TestCase):
    def test_request_shape(self):
        post = fake_post({"goods_search_response": {"goods_list": []}})
        PDDClient(_pdd_cred(), post_fn=post).goods_search(keyword="手机")
        url, params = post.calls[0]
        self.assertIn("gw-api.pinduoduo.com", url)
        self.assertEqual(params["type"], "pdd.ddk.goods.search")
        self.assertEqual(params["client_id"], "k")
        self.assertEqual(params["data_type"], "JSON")
        self.assertTrue(re.fullmatch(r"\d{10}", params["timestamp"]), params["timestamp"])
        self.assertEqual(params["pid"], "1_2")
        self.assertEqual(params["sign"], params["sign"].upper())

    def test_parse_goods_list(self):
        post = fake_post({"goods_search_response": {
            "goods_list": [{"goods_id": 11, "goods_name": "A"},
                           {"goods_id": 22, "goods_name": "B"}]}})
        items = PDDClient(_pdd_cred(), post_fn=post).goods_search(keyword="x")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["goods_name"], "A")

    def test_client_offline_is_auth(self):
        post = fake_post({"error_response": {"error_code": 10016,
                                             "error_msg": "client下线或者clientId不正确"}})
        with self.assertRaises(ApiError) as ctx:
            PDDClient(_pdd_cred(), post_fn=post).goods_search(keyword="x")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(ctx.exception.code, "10016")

    def test_bool_is_signed_as_lowercase(self):
        post = fake_post({"goods_search_response": {"goods_list": []}})
        PDDClient(_pdd_cred(), post_fn=post).goods_search(keyword="x", with_coupon=True)
        _, params = post.calls[0]
        self.assertEqual(params["with_coupon"], "true")


if __name__ == "__main__":
    unittest.main()
