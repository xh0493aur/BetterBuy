"""实时验证脚本：用真实链接跑完整采集，输出字段覆盖率与来源报告。

用法：
    python scripts/verify_live.py                     # 用内置的 5 个真实 Steam 链接
    python scripts/verify_live.py <url1> <url2> ...   # 自己指定链接
    python scripts/verify_live.py --no-cache          # 强制重新请求网络

产物：reports/live_verify_<时间>.json 与同名 .md
说明：报告中"缺失"字段代表数据源确实没有提供，不是脚本失败。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from betterbuy.adapters import collect, supported_platforms  # noqa: E402
from betterbuy.http_client import FetchError  # noqa: E402

#: 五个真实商品页（Steam 商店，公开可访问）
DEFAULT_URLS = [
    "https://store.steampowered.com/app/413150/",   # Stardew Valley
    "https://store.steampowered.com/app/105600/",   # Terraria
    "https://store.steampowered.com/app/322330/",   # 饥荒联机版
    "https://store.steampowered.com/app/1245620/",  # 艾尔登法环
    "https://store.steampowered.com/app/1145360/",  # Hades
]

FIELDS = ["商品名称", "品牌", "型号/版本", "商品类别", "价格", "货币",
          "好评率", "评价数量", "商品图片", "核心参数"]


def has_field(product, name: str) -> bool:
    return name not in product.missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*", default=None)
    parser.add_argument("--no-cache", action="store_true", help="忽略本地缓存")
    args = parser.parse_args()

    urls = args.urls or DEFAULT_URLS
    print(f"开始验证 {len(urls)} 个链接（缓存{'关闭' if args.no_cache else '开启'}）\n")
    print("已注册适配器：")
    for row in supported_platforms():
        print(f"  - {row['适配器']}（{row['标识']}）：{row['采集方式']}")
    print()

    results = []
    for index, url in enumerate(urls, start=1):
        started = time.time()
        try:
            product = collect(url, force=args.no_cache)
            elapsed = time.time() - started
            coverage = {field: has_field(product, field) for field in FIELDS}
            hit = sum(coverage.values())
            results.append({
                "url": url,
                "ok": True,
                "elapsed_sec": round(elapsed, 1),
                "name": product.name,
                "platform": product.platform,
                "price": product.offer.price,
                "currency": product.offer.currency,
                "positive_rate": product.rating.positive_rate,
                "review_count": product.rating.review_count,
                "status": product.status,
                "coverage": coverage,
                "coverage_ratio": round(hit / len(FIELDS), 2),
                "missing": product.missing,
                "sources": {k: {"label": s.label, "url": s.url, "fetched_at": s.fetched_at}
                            for k, s in product.sources.items()},
                "notes": product.notes,
            })
            print(f"[{index}/{len(urls)}] ✅ {product.name}  "
                  f"{product.offer.display()}  {product.rating.display()}")
            print(f"      字段覆盖 {hit}/{len(FIELDS)}，耗时 {elapsed:.1f}s，缺失：{product.missing or '无'}")
        except FetchError as exc:
            results.append({"url": url, "ok": False, "kind": exc.kind, "error": exc.message})
            print(f"[{index}/{len(urls)}] ❌ {url}\n      {exc.kind}: {exc.message}")
        except Exception as exc:  # noqa: BLE001 - 报告需要看到真实异常类型
            results.append({"url": url, "ok": False, "kind": type(exc).__name__, "error": str(exc)[:200]})
            print(f"[{index}/{len(urls)}] ❌ {url}\n      {type(exc).__name__}: {exc}")

    succeeded = [r for r in results if r["ok"]]
    print(f"\n成功 {len(succeeded)}/{len(results)}")
    if succeeded:
        avg = sum(r["coverage_ratio"] for r in succeeded) / len(succeeded)
        print(f"平均字段覆盖率：{avg:.0%}")

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cache": "off" if args.no_cache else "on",
        "total": len(results),
        "succeeded": len(succeeded),
        "results": results,
    }
    json_path = reports / f"live_verify_{stamp}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 实时采集验证报告（{payload['generated_at']}）",
        "",
        f"- 链接总数：{len(results)}，成功：{len(succeeded)}",
        f"- 缓存：{payload['cache']}",
        "",
        "| # | 链接 | 结果 | 商品名 | 价格 | 好评率 | 字段覆盖 | 缺失 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(results, start=1):
        if row["ok"]:
            lines.append(
                f"| {index} | {row['url']} | ✅ | {row['name']} | "
                f"{row['currency']} {row['price']} | {row['positive_rate']}% | "
                f"{row['coverage_ratio']:.0%} | {'、'.join(row['missing']) or '无'} |"
            )
        else:
            lines.append(f"| {index} | {row['url']} | ❌ {row['kind']} | — | — | — | — | {row['error'][:80]} |")
    md_path = reports / f"live_verify_{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n报告已写入：\n  {json_path}\n  {md_path}")
    return 0 if len(succeeded) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
