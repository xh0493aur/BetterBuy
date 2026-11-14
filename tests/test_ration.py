"""理性消费判定的测试。

这里钉住三条不能退让的规则：
1. 价格高低不能变成「你冲动了」。
2. 没有商品数据时绝不能给「推荐购买」。
3. 每条结论都能在 trace 里找到依据。
"""
import unittest

from betterbuy.ration import (MODE_LABEL, VERDICT_BUY, VERDICT_COMPARE,
                              VERDICT_WAIT, analyze)


class VerdictTests(unittest.TestCase):
    def test_impulse_combo_goes_wait(self):
        r = analyze("限时秒杀最后一天，好想要这个机械键盘，同学都买了")
        self.assertEqual(r.verdict, VERDICT_WAIT)
        self.assertTrue(any("冲动" in reason for reason in r.reasons))

    def test_bare_product_name_goes_wait(self):
        r = analyze("人体工学椅")
        self.assertEqual(r.verdict, VERDICT_WAIT)

    def test_no_data_never_recommends_buy(self):
        """需求再清楚，没有真实商品数据也只能到「进一步比较」。"""
        r = analyze("想花 800 元买人体工学椅，每天在宿舍学习用", facts_ok=False)
        self.assertEqual(r.verdict, VERDICT_COMPARE)
        self.assertNotEqual(r.verdict, VERDICT_BUY)
        self.assertTrue(any("没有拿到" in reason for reason in r.reasons))

    def test_clear_need_with_data_can_recommend(self):
        r = analyze("想花 800 元买人体工学椅，每天在宿舍学习用，优先看口碑",
                    product_count=3, priced_count=3, facts_ok=True)
        self.assertEqual(r.verdict, VERDICT_BUY)

    def test_high_budget_is_not_impulse(self):
        r = analyze("我想花 9000 元买人体工学椅，每天在宿舍学习和打游戏用",
                    product_count=2, priced_count=2, facts_ok=True)
        self.assertEqual(r.budget_check.level, "偏高")
        joined = " ".join(r.reasons)
        self.assertNotIn("冲动消费", joined)
        # 预算偏高应落到「进一步比较」，而不是「暂缓」
        self.assertEqual(r.verdict, VERDICT_COMPARE)

    def test_low_budget_is_flagged_but_not_rejected(self):
        r = analyze("想花 150 元买个外接显示器，每天写代码用")
        self.assertEqual(r.budget_check.level, "偏低")
        # 预算偏低只是提醒，不能变成「别买了」
        self.assertNotEqual(r.verdict, VERDICT_WAIT)
        self.assertTrue(any("偏低" in reason for reason in r.reasons))

    def test_problem_statement_points_to_cheap_fix(self):
        r = analyze("我的耳机耳罩掉皮了，想直接换一个新的耳机，预算 300")
        self.assertEqual(r.verdict, VERDICT_COMPARE)
        joined = " ".join(r.reasons)
        self.assertIn("耳罩", joined)

    def test_owned_and_low_frequency_goes_wait(self):
        r = analyze("我的键盘还能用，只是偶尔想换个新的，预算 300",
                    product_count=1, priced_count=1, facts_ok=True)
        self.assertEqual(r.verdict, VERDICT_WAIT)

    def test_empty_input_gives_no_verdict(self):
        for text in ("", "   ", "https://store.steampowered.com/app/413150/"):
            r = analyze(text)
            self.assertEqual(r.verdict_key, "empty")
            self.assertEqual(r.verdict, "")

    def test_verdict_is_always_one_of_three(self):
        samples = [
            "人体工学椅",
            "想花 800 元买人体工学椅，每天学习用",
            "限时秒杀，好想要这个显示器",
            "我的笔记本是 Y7000P，想买个显示器，预算 1000，每天写代码",
        ]
        for text in samples:
            self.assertIn(analyze(text).verdict, (VERDICT_BUY, VERDICT_COMPARE, VERDICT_WAIT))


class ExplainabilityTests(unittest.TestCase):
    def test_trace_entries_are_complete(self):
        r = analyze("想花 800 元买人体工学椅，每天在宿舍学习用")
        self.assertTrue(r.trace)
        for row in r.trace:
            self.assertEqual(set(row) >= {"规则", "命中", "说明"}, True)
            self.assertTrue(row["说明"])

    def test_price_is_declared_excluded_from_impulse_judgement(self):
        r = analyze("想花 800 元买人体工学椅，每天学习用")
        rows = {row["规则"]: row for row in r.trace}
        self.assertIn("价格是否被用于判定冲动消费", rows)
        self.assertIn("否", rows["价格是否被用于判定冲动消费"]["说明"])

    def test_mode_label_says_it_is_not_a_model(self):
        self.assertIn("非大模型", MODE_LABEL)
        r = analyze("人体工学椅，800 元，每天学习用")
        self.assertEqual(r.disclaimer, MODE_LABEL)

    def test_confidence_rises_with_information(self):
        poor = analyze("人体工学椅")
        rich = analyze("想花 800 元买人体工学椅，每天在宿舍学习用",
                       product_count=2, priced_count=2, facts_ok=True)
        order = {"低": 0, "中": 1, "高": 2}
        self.assertGreater(order[rich.confidence], order[poor.confidence])


class FocusAndChecklistTests(unittest.TestCase):
    def test_focus_specs_come_from_category(self):
        r = analyze("想买个显示器，预算 1000，每天打游戏用")
        names = [item["参数"] for item in r.focus]
        self.assertTrue(any("刷新率" in n for n in names), names)

    def test_checklist_mentions_owned_device(self):
        r = analyze("我的笔记本是拯救者 Y7000P，想买个外接显示器，预算 1000")
        self.assertTrue(any("笔记本电脑" in item for item in r.checks))

    def test_other_category_is_honest_about_no_rules(self):
        r = analyze("想买个随便什么东西，预算 100，每天用")
        self.assertTrue(any("未收录" in item for item in r.checks))


if __name__ == "__main__":
    unittest.main()
