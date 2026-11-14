"""零成本自检：一条命令确认「现在不会花钱」。

检查四件事：
1. 零成本模式是否开启；
2. 大模型路径是否被拦住（即使配置了 LLM 环境变量）；
3. 今日各数据源配额余量；
4. 代码里是否出现已知的付费服务域名。

用法：
    python scripts/check_zero_cost.py
    python scripts/check_zero_cost.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from betterbuy.advisor import llm_available, llm_blocked_reason  # noqa: E402
from betterbuy.zero_cost import quota_rows, usage_summary, zero_cost_mode  # noqa: E402

#: 已知会产生费用的服务域名（命中任何一条都要人工确认）
PAID_HOSTS = [
    "api.openai.com",
    "dashscope.aliyuncs.com",
    "aip.baidubce.com",
    "open.bigmodel.cn",
    "api.moonshot.cn",
    "ark.cn-beijing.volces.com",
    "api.anthropic.com",
]


def scan_paid_hosts() -> list[tuple[str, int, str]]:
    """扫项目自己的 .py 文件，看有没有写死付费服务端点。

    跳过本文件自身——它的 PAID_HOSTS 常量里当然会出现这些域名。
    """
    hits: list[tuple[str, int, str]] = []
    myself = Path(__file__).resolve()
    for path in ROOT.rglob("*.py"):
        if any(part in (".venv", "__pycache__", "backups") for part in path.parts):
            continue
        if path.resolve() == myself:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            for host in PAID_HOSTS:
                if host in line:
                    hits.append((str(path.relative_to(ROOT)), number, host))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="BetterBuy 零成本自检")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于脚本消费")
    args = parser.parse_args()

    summary = usage_summary()
    rows = quota_rows()
    paid = scan_paid_hosts()
    llm_ok = llm_available()

    ok = summary["zero_cost"] and not llm_ok and not paid

    if args.json:
        print(json.dumps({
            "zero_cost_mode": summary["zero_cost"],
            "llm_available": llm_ok,
            "llm_reason": llm_blocked_reason(),
            "quota": rows,
            "usage": summary,
            "paid_endpoints": paid,
            "verdict": "PASS" if ok else "CHECK",
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print("=" * 62)
    print("BetterBuy 零成本自检")
    print("=" * 62)

    print(f"\n[1] 零成本模式：{'开启' if summary['zero_cost'] else '已关闭（可能产生费用）'}")
    print(f"    大模型路径：{'可用（会产生费用！）' if llm_ok else '已禁用'}")
    if not llm_ok:
        print(f"    原因：{llm_blocked_reason()}")

    print(f"\n[2] 今日请求：{summary['used']}/{summary['limit']} 次，剩余 {summary['remaining']} 次")
    print(f"    {'数据源':<14}{'已用':>6}{'上限':>8}{'剩余':>8}  状态")
    for row in rows:
        print(f"    {row['数据源']:<14}{row['今日已用']:>6}{row['每日上限']:>8}"
              f"{row['剩余']:>8}  {row['状态']}")
    if summary["blocked_sources"]:
        print(f"    已达上限：{'、'.join(summary['blocked_sources'])}")

    print("\n[3] 付费服务端点扫描")
    if paid:
        print("    发现以下代码引用，请人工确认是否会被调用：")
        for file, line, host in paid:
            print(f"      {file}:{line}  {host}")
    else:
        print("    未发现任何写死的付费服务端点（模型接口只从环境变量读取，且默认禁用）")

    print("\n" + "=" * 62)
    if ok:
        print("结论：当前配置不会产生任何费用。")
    else:
        print("结论：有项目需要你确认，别急着公开试用。")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
