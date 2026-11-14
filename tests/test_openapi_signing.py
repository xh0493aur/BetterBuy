"""签名测试。

说明：三家的签名规则都要求真实 app_key/app_secret 才能做端到端验证，
本项目没有密钥，所以这里只验证**算法实现与文档规则一致**：
排序、拼接、首尾加 secret、MD5 后大写、空值与 sign 不参与、布尔转小写。

这不是"调通了"的证明，端到端验证需要你配置密钥后跑 scripts/check_openapi.py。
"""
import hashlib
import unittest

from betterbuy.openapi.signing import (canon, jd_sign, pdd_sign, signature_string,
                                       taobao_sign)


class TestCanon(unittest.TestCase):
    def test_bool_lowercase(self):
        self.assertEqual(canon(True), "true")
        self.assertEqual(canon(False), "false")

    def test_list_is_compact_json(self):
        self.assertEqual(canon([1, 2, 3]), "[1,2,3]")

    def test_dict_is_compact_json_no_spaces(self):
        self.assertEqual(canon({"a": 1, "b": "中"}), '{"a":1,"b":"中"}')

    def test_number(self):
        self.assertEqual(canon(10.5), "10.5")

    def test_none_is_empty(self):
        self.assertEqual(canon(None), "")


class TestSignatureString(unittest.TestCase):
    def test_sorted_by_key(self):
        text = signature_string({"b": "2", "a": "1"})
        self.assertEqual(text, "a1b2")

    def test_skip_sign(self):
        self.assertEqual(signature_string({"a": "1", "sign": "xxx"}), "a1")

    def test_skip_empty(self):
        self.assertEqual(signature_string({"a": "1", "b": "", "c": None}), "a1")

    def test_keeps_zero(self):
        """0 是有效值，不能被当空值丢掉。"""
        self.assertEqual(signature_string({"a": 0}), "a0")


class TestPlatformSign(unittest.TestCase):
    def setUp(self):
        self.params = {"method": "jd.union.open.goods.query", "app_key": "testkey",
                       "timestamp": "2023-08-30 18:08:41", "v": "1.0"}
        self.secret = "secret123"

    def test_jd_matches_documented_formula(self):
        expected = hashlib.md5(
            (self.secret + signature_string(self.params) + self.secret).encode()
        ).hexdigest().upper()
        self.assertEqual(jd_sign(self.params, self.secret), expected)

    def test_jd_is_uppercase_32(self):
        sign = jd_sign(self.params, self.secret)
        self.assertEqual(sign, sign.upper())
        self.assertEqual(len(sign), 32)

    def test_taobao_same_shape_as_jd(self):
        self.assertEqual(taobao_sign(self.params, self.secret), jd_sign(self.params, self.secret))

    def test_pdd_same_shape(self):
        self.assertEqual(pdd_sign(self.params, self.secret), jd_sign(self.params, self.secret))

    def test_order_does_not_matter(self):
        reversed_params = dict(reversed(list(self.params.items())))
        self.assertEqual(jd_sign(self.params, self.secret), jd_sign(reversed_params, self.secret))

    def test_secret_changes_sign(self):
        self.assertNotEqual(jd_sign(self.params, "a"), jd_sign(self.params, "b"))

    def test_value_changes_sign(self):
        tampered = dict(self.params, v="2.0")
        self.assertNotEqual(jd_sign(self.params, self.secret), jd_sign(tampered, self.secret))

    def test_taobao_hmac_sha256_differs_from_md5(self):
        md5_sign = taobao_sign(self.params, self.secret, "md5")
        hmac_sign = taobao_sign(self.params, self.secret, "hmac-sha256")
        self.assertNotEqual(md5_sign, hmac_sign)
        self.assertEqual(len(hmac_sign), 64)


if __name__ == "__main__":
    unittest.main()
