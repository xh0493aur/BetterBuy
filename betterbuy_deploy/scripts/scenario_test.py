"""三个真实使用场景的验收脚本。

运行：
    .venv\\Scripts\\python.exe scripts\\scenario_test.py
    .venv\\Scripts\\python.exe scripts\\scenario_test.py --with-link   # 额外测一条真实 Steam 链接

它会真实调用 betterbuy 的完整链路（不 mock），把每一步的实际输出写进
``reports/scenario_<时间戳>.md``，便于事后核对「说过的话有没有实现」。

**不统计「成功率」这类指标**，因为三个场景是人工设计的，不是用户抽样，
算出百分比只会显得比实际更有说服力。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from betterbuy.wise import run  # noqa: E402

SCENARIOS = [
    {
        "name": "场景一：数码配件，判断兼容性与购买风险",
        "who": "大二学生，宿舍已有一台游戏本，想加一块外接屏写代码",
        "input": "我的笔记本是联想拯救者 Y7000P，想买个外接显示器，预算 1000 元，每天写代码要用",
        "focus": "能否识别出「买的是显示器」而不是「买笔记本」；兼容性怎么给。",
    },
    {
        "name": "场景二：学习用品，判断预算与用途是否匹配",
        "who": "大一新生，开学前采购",
        "input": "下学期要用的笔记本和记号笔，预算 60 元，每天上课记笔记",
        "focus": "文具类是否会被误判成笔记本电脑；预算是否被判定为合理。",
    },
    {
        "name": "场景三：设备还能用，分析修、升级还是换",
        "who": "大三学生，手机电池一天要充三次",
        "input": "我现在用的手机还能开机，就是电池一天要充三次，想换个新的，预算 3000",
        "focus": "会不会直接劝买；有没有先给出「换电池」这条更省钱的路径。",
    },
]


def one(scenario: dict, with_link: bool = False) -> dict:
    result = run(scenario["input"], auto_discover=False, use_cache=True)
    record = {
        "场景": scenario["name"],
        "使用者": scenario["who"],
        "输入": scenario["input"],
        "关注点": scenario["focus"],
        "识别品类": result.category_name,
        "品类判定依据": result.intent.category_basis,
        "需求理解": result.intent.evidence_line(),
        "缺失信息": result.intent.missing,
        "结论": result.ration.verdict or "（不适用）",
        "置信度": result.ration.confidence,
        "结论理由": result.ration.reasons,
        "预算判断": f"{result.ration.budget_check.level}｜{result.ration.budget_check.text}",
        "风险信号": [{"信号": s.label, "依据": s.evidence, "权重": s.weight}
                     for s in result.intent.signals],
        "理性信号": [s.label for s in result.intent.rational],
        "重点参数": [item["参数"] for item in result.ration.focus],
        "确认清单": result.ration.checks,
        "绿色建议": [{"做法": item.title, "自查": item.check} for item in result.green.items],
        "替代路径": result.ration.alternatives,
        "风险项": [{"级别": item.level, "项": item.label, "依据": item.evidence}
                   for item in result.risk.items],
        "商品数": len(result.products),
        "有价格商品数": len(result.priced),
        "数据来源": [
            {"字段": "、".join(f for f, k in p.field_sources.items() if k == key) or "说明",
             "来源": src.label, "时间": src.fetched_at, "地址": src.url}
            for p in result.products for key, src in p.sources.items()
        ] or ["本次无商品数据：只做了需求分析、绿色建议与风险清单"],
        "运行备注": result.notes,
    }
    if with_link:
        link_result = run(
            "https://store.steampowered.com/app/413150/ 每天玩一小时，预算 100 元，"
            "想找个能长期玩的",
            auto_discover=False, use_cache=True,
        )
        record["真实链接附加测试"] = {
            "输入": "https://store.steampowered.com/app/413150/ 每天玩一小时，预算 100 元",
            "识别商品": [p.name for p in link_result.products],
            "价格": [p.offer.display() for p in link_result.products],
            "口碑": [p.rating.display() for p in link_result.products],
            "结论": link_result.ration.verdict,
            "备注": link_result.notes,
        }
    return record


def to_markdown(records: list[dict]) -> str:
    lines = [
        "# BetterBuy 智选 · 三个真实场景测试记录",
        "",
        f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "> 本文件由 `scripts/scenario_test.py` 真实运行生成，内容即程序当时的实际输出。",
        "> 三个场景都是人工设计的使用路径，**不是用户抽样**，因此不计算「成功率」。",
        "",
    ]
    for rec in records:
        lines += [f"## {rec['场景']}", "", f"**使用者**：{rec['使用者']}", "",
                  f"**输入**：`{rec['输入']}`", "", f"**关注点**：{rec['关注点']}", "",
                  f"**识别品类**：{rec['识别品类']}（{rec['品类判定依据']}）", "",
                  f"**需求理解**：{rec['需求理解']}", ""]
        if rec["缺失信息"]:
            lines += [f"**缺失信息**：{'、'.join(rec['缺失信息'])}", ""]
        lines += [f"**结论**：{rec['结论']}（置信度 {rec['置信度']}）", ""]
        for reason in rec["结论理由"]:
            lines.append(f"- {reason}")
        lines += ["", f"**预算判断**：{rec['预算判断']}", ""]
        if rec["风险信号"]:
            lines += ["**风险信号**：", ""]
            for sig in rec["风险信号"]:
                lines.append(f"- {sig['信号']}（依据：{sig['依据']}，权重 {sig['权重']}）")
            lines.append("")
        if rec["理性信号"]:
            lines += ["**理性信号**：" + "、".join(rec["理性信号"]), ""]
        lines += ["**这类商品该重点看什么**：" + "、".join(rec["重点参数"]), ""]
        lines += ["**下单前确认清单**：", ""]
        for item in rec["确认清单"]:
            lines.append(f"- {item}")
        lines += ["", "**绿色／省钱路径**：", ""]
        for item in rec["绿色建议"]:
            lines.append(f"- {item['做法']}（自查：{item['自查']}）")
        lines += ["", "**风险项**：", ""]
        for item in rec["风险项"]:
            lines.append(f"- [{item['级别']}] {item['项']}（依据：{item['依据']}）")
        lines += ["", f"**商品数据**：{rec['商品数']} 个，其中 {rec['有价格商品数']} 个有价格", "",
                  "**数据来源**：", ""]
        for item in rec["数据来源"]:
            if isinstance(item, str):
                lines.append(f"- {item}")
            else:
                lines.append(f"- {item['来源']} · {item['时间']} · {item['字段']}")
        if rec["运行备注"]:
            lines += ["", "**运行备注**：", ""] + [f"- {n}" for n in rec["运行备注"]]
        if "真实链接附加测试" in rec:
            extra = rec["真实链接附加测试"]
            lines += ["", "**真实链接附加测试**：", "",
                      f"- 输入：`{extra['输入']}`",
                      f"- 识别商品：{'、'.join(extra['识别商品']) or '未取到'}",
                      f"- 价格：{'、'.join(extra['价格']) or '未取到'}",
                      f"- 口碑：{'、'.join(extra['口碑']) or '未取到'}",
                      f"- 结论：{extra['结论'] or '未出结论'}"]
            if extra["备注"]:
                lines += [f"- 备注：{n}" for n in extra["备注"]]
        lines.append("")
    lines += [
        "## 测试中发现并修复的问题",
        "",
        "| # | 现象 | 原因 | 修复 |",
        "|---|---|---|---|",
        "| 1 | 「我的笔记本是 Y7000P，想买个外接显示器」被识别成**买笔记本** | 只做关键词匹配，"
        "被前半句的已有设备抢走 | 改成按标点分段 + 购买动词窗口 + 段内取最右品类词"
        "（`catalog.find_purchase_target`），并单独记录「已有设备」用于兼容性核对 |",
        "| 2 | 「手机充电宝」被识别成买手机 | 连缀词取到了修饰语而不是中心词 | 同一段内取**最靠右**的"
        "品类词（中文中心词在后） |",
        "| 3 | 「我现在用的手机…想换个新的」被判为「只有已有物品、无购买目标」 | 句子里没有新的品类词 | "
        "增加「替换已有物品」规则：有购买动词 + 「新/换」时，目标就是已有物品同类 |",
        "| 4 | 「我的耳机耳罩掉皮了，想换个新的」被判为「建议暂缓」 | 规则太粗暴：缺用途就劝退 | "
        "增加「现有物品具体问题」识别；只有「无用途 + 无预算 + 无问题 + 无数据」四条同时成立才暂缓 |",
        "| 5 | 商品采集到了但都没有价格时，购买建议直接抛 `IndexError` | 旧代码假设候选一定有价格 | "
        "`advisor.rule_advice` 增加降级分支：不出分档，只给确认清单 |",
        "| 6 | 显示器的绿色建议里出现了「升级内存与硬盘」 | 外设与整机共用同一套「电脑设备」建议 | "
        "新增 `peripheral` 家族（显示器/键盘/鼠标/存储），给出线材、易损件、二手验机建议 |",
        "| 7 | 界面「与你用途相关」列显示成空复选框 | 该列传的是布尔值，表格渲染成 checkbox | "
        "改成「是 / —」文本 |",
        "| 8 | 风险说明在表格里被截断 | 长文本塞进窄列 | 改为卡片式逐条展示，手机端也不再横向滚动 |",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-link", action="store_true", help="额外测一条真实 Steam 链接")
    args = parser.parse_args()

    records = []
    for scenario in SCENARIOS:
        print(f"运行：{scenario['name']}")
        records.append(one(scenario, with_link=args.with_link))

    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"scenario_{stamp}.md"
    json_path = out_dir / f"scenario_{stamp}.json"
    md_path.write_text(to_markdown(records), encoding="utf-8")
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str),
                         encoding="utf-8")

    for rec in records:
        print(f"  → {rec['识别品类']} | {rec['结论']}（{rec['置信度']}）")
    print(f"\n报告：{md_path}")
    print(f"数据：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
