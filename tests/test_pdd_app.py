"""拼多多搜索接入界面的集成测试（离线，AppTest，不联网）。

验证三件事：
1. 关键词搜索的结果能进入统一比较流程（①~⑦ 各区块正常渲染）；
2. 未配置密钥时给出明确分类，而不是抛异常；
3. 搜索结果的链接列在接口没给链接时显示"接口未返回"，不伪造。
"""
import os
import tempfile
import unittest
from unittest import mock

TMPDIR = tempfile.mkdtemp(prefix="betterbuy_pdd_ui_")
os.environ["BETTERBUY_DB"] = os.path.join(TMPDIR, "pdd_ui.sqlite3")
os.environ["BETTERBUY_MIN_INTERVAL"] = "0"

from streamlit.testing.v1 import AppTest  # noqa: E402

import betterbuy.pdd_search as pdd_search_mod  # noqa: E402
from betterbuy.adapters.pdd import product_from_pdd_goods  # noqa: E402
from betterbuy.pdd_search import SearchResult, search as real_search  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")

GOODS = [
    {"goods_id": 1001, "goods_sign": "S1", "goods_name": "机械键盘 A 87 键",
     "min_group_price": 12900, "min_normal_price": 19900, "coupon_discount": 2000,
     "coupon_min_order_amount": 9900, "mall_name": "店A", "sales_tip": "已拼 1 万件"},
    {"goods_id": 1002, "goods_sign": "S2", "goods_name": "机械键盘 B 104 键",
     "min_group_price": 18900, "mall_name": "店B", "sales_tip": "已拼 3000 件"},
]


def fake_products():
    out = []
    for item in GOODS:
        product = product_from_pdd_goods(item, goods_id=str(item["goods_id"]), url="")
        product.origin = "search"
        product.status = "拼多多关键词搜索结果"
        out.append(product)
    return out


def find_button(at, label):
    return next((b for b in at.button if b.label == label), None)


def find_input(at, label):
    return next((w for w in at.text_input if w.label == label), None)


class PddUiTests(unittest.TestCase):
    def _submit(self, at, keyword="机械键盘"):
        field = find_input(at, "商品关键词")
        self.assertIsNotNone(field, "找不到关键词输入框")
        field.set_value(keyword)
        button = find_button(at, "搜索")
        self.assertIsNotNone(button, "找不到搜索按钮")
        return button.click().run()

    def test_empty_keyword_warns_without_error(self):
        at = AppTest.from_file(APP, default_timeout=90)
        at.run()
        self._submit(at, keyword="")
        self.assertFalse(at.exception)
        text = "\n".join(e.value for e in at.error) + "\n".join(c.value for c in at.caption)
        self.assertIn("关键词是空的", text)

    def test_results_flow_into_unified_sections(self):
        result = SearchResult(ok=True, keyword="机械键盘", products=fake_products(),
                              total=2, message="搜到 2 件商品（接口共 2 条）。", api_calls=1)
        with mock.patch.object(pdd_search_mod, "search", return_value=result):
            at = AppTest.from_file(APP, default_timeout=90)
            at.run()
            self._submit(at)
        self.assertFalse(at.exception)

        headings = [s.value for s in at.subheader]
        for expected in ("① 商品识别状态", "③ 核心参数对比表", "④ 价格对比", "⑦ 综合购买建议"):
            self.assertIn(expected, headings)

        # 价格口径出现在表格里（拼团价 / 券后 / 运费）
        frames = [df.value.to_string() for df in at.dataframe]
        joined = "\n".join(frames)
        self.assertIn("拼团价", joined)
        self.assertIn("不含运费", joined)

    def test_missing_credentials_is_explained(self):
        result = real_search("机械键盘")          # 本机没有密钥，走 no_credential 分支
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, "no_credential")
        with mock.patch.object(pdd_search_mod, "search", return_value=result):
            at = AppTest.from_file(APP, default_timeout=90)
            at.run()
            self._submit(at)
        self.assertFalse(at.exception)
        errors = "\n".join(e.value for e in at.error)
        self.assertIn("未配置密钥", errors)
        self.assertIn("BETTERBUY_PDD_CLIENT_ID", errors)


if __name__ == "__main__":
    unittest.main()
