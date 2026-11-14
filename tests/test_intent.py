"""品类知识库与需求解析的测试。

重点钉住两件事：
1. 不能把「我的笔记本是 XX，想买个显示器」解析成买笔记本。
2. 解析不出来的东西要老实进 missing，不能猜。
"""
import unittest

from betterbuy.catalog import (CATEGORIES, OTHER_KEY, find_category,
                               find_purchase_target, get_category, key_of_name)
from betterbuy.intent import (FREQUENCY_HIGH, FREQUENCY_LOW, parse_intent,
                              strip_urls)


class CatalogTests(unittest.TestCase):
    def test_common_categories_are_covered(self):
        for alias in ("笔记本电脑", "显示器", "机械键盘", "蓝牙耳机", "人体工学椅",
                      "台灯", "移动硬盘", "充电宝", "教材"):
            key, matched = find_category(alias)
            self.assertNotEqual(key, OTHER_KEY, f"{alias} 应该能识别出品类")
            self.assertTrue(matched)

    def test_longest_alias_wins(self):
        # 「人体工学椅」不能被「椅子」抢走
        key, matched = find_category("想买人体工学椅")
        self.assertEqual(key, "chair")
        self.assertEqual(matched, "人体工学椅")

    def test_unknown_text_falls_back_to_other(self):
        key, matched = find_category("我想买个不知道什么东西")
        self.assertEqual(key, OTHER_KEY)
        self.assertEqual(matched, "")

    def test_key_of_name_roundtrip(self):
        for key in CATEGORIES:
            self.assertEqual(key_of_name(get_category(key).name), key)

    def test_every_category_has_green_advice(self):
        for key, category in CATEGORIES.items():
            self.assertTrue(category.green, f"{key} 缺少绿色建议")
            for path in category.green:
                self.assertTrue(path.check, f"{key} 的「{path.title}」没有自查问题")

    def test_price_band_is_sane(self):
        for key, category in CATEGORIES.items():
            if key == OTHER_KEY:
                continue
            self.assertGreater(category.price_high, category.price_low, key)
            self.assertGreater(category.price_low, 0, key)


class PurchaseTargetTests(unittest.TestCase):
    def test_owned_device_is_not_the_target(self):
        """真实需求里最常见的坑：前半句说已有设备，后半句才是购买目标。"""
        key, alias, owned, _ = find_purchase_target(
            "我的笔记本是联想拯救者 Y7000P，想买个外接显示器，预算 1000 元"
        )
        self.assertEqual(key, "monitor")
        self.assertEqual(alias, "显示器")
        self.assertIn("laptop", owned)

    def test_compound_noun_head_wins(self):
        """「手机充电宝」的头词是充电宝，不是手机。"""
        key, alias, owned, _ = find_purchase_target(
            "我想买一个手机充电宝，预算 150，我的手机是 iPhone 15"
        )
        self.assertEqual(key, "powerbank")
        self.assertEqual(alias, "充电宝")
        self.assertIn("phone", owned)

    def test_replacement_without_new_noun(self):
        """「我现在用的手机…想换个新的」目标就是手机本身。"""
        key, _, owned, basis = find_purchase_target(
            "我现在用的手机还能开机，就是电池一天要充三次，想换个新的，预算 3000"
        )
        self.assertEqual(key, "phone")
        self.assertIn("phone", owned)
        self.assertIn("替换", basis)

    def test_plain_need_without_verb(self):
        key, _, _, _ = find_purchase_target("人体工学椅，800 元左右")
        self.assertEqual(key, "chair")

    def test_notebook_paper_is_stationery_not_laptop(self):
        key, _, _, _ = find_purchase_target("下学期要用的笔记本本子和记号笔，预算 60")
        self.assertEqual(key, "stationery")

    def test_empty_text(self):
        key, alias, owned, basis = find_purchase_target("")
        self.assertEqual(key, OTHER_KEY)
        self.assertEqual(owned, [])
        self.assertIn("空", basis)


class IntentTests(unittest.TestCase):
    def test_parses_budget_purpose_frequency(self):
        intent = parse_intent("想花 800 元买人体工学椅，每天在宿舍学习和打游戏用")
        self.assertEqual(intent.category_key, "chair")
        self.assertEqual(intent.budget_high, 800)
        self.assertIn("学习", intent.purposes)
        self.assertIn("游戏", intent.purposes)
        self.assertEqual(intent.frequency, FREQUENCY_HIGH)

    def test_low_frequency_is_detected(self):
        intent = parse_intent("想买个显示器，可能用得上，偶尔看看剧")
        self.assertEqual(intent.frequency, FREQUENCY_LOW)
        self.assertTrue(any(s.key == "low_frequency" for s in intent.signals))

    def test_promo_and_emotion_are_separate_signals(self):
        intent = parse_intent("限时秒杀最后一天，好想要这个机械键盘，同学都买了")
        keys = {s.key for s in intent.signals}
        self.assertIn("promo_pressure", keys)
        self.assertIn("emotion_driven", keys)

    def test_problem_statement_is_recognised(self):
        intent = parse_intent("我的耳机耳罩掉皮了，想换一个新的耳机，预算 300")
        self.assertTrue(intent.has_problem)
        self.assertIn("掉皮", intent.problem_described)
        # 说清了问题就不该被判定为「已有同类物品却没说原因」
        self.assertNotIn("owned_unexplained", {s.key for s in intent.signals})

    def test_high_budget_is_not_an_impulse_judgement(self):
        """预算高只影响「预算匹配」，不能变成「你冲动了」。"""
        intent = parse_intent("我想花 9000 元买个人体工学椅，每天在宿舍学习用")
        labels = {s.key: s.label for s in intent.signals}
        self.assertIn("budget_loose", labels)
        self.assertNotIn("冲动", labels["budget_loose"])

    def test_missing_fields_are_reported_not_guessed(self):
        intent = parse_intent("人体工学椅")
        self.assertFalse(intent.has_budget)
        self.assertIn("购买用途", intent.missing)
        self.assertIn("预算", intent.missing)
        self.assertEqual(intent.purposes, [])

    def test_urls_are_stripped(self):
        self.assertEqual(strip_urls("看看 https://store.steampowered.com/app/1/ 谢谢"), "看看   谢谢")
        intent = parse_intent("https://store.steampowered.com/app/413150/")
        self.assertIsNone(intent.budget_high)

    def test_empty_input_returns_empty_intent(self):
        intent = parse_intent("")
        self.assertFalse(intent.has_budget)
        self.assertEqual(intent.category_key, OTHER_KEY)
        self.assertIn("需求描述", intent.missing)


if __name__ == "__main__":
    unittest.main()
