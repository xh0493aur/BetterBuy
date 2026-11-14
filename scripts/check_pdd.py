"""拼多多接口独立自检：先离线查配置，再按需真实调用。

用法（在项目根目录执行）::

    python scripts/check_pdd.py                 # 离线自检：只查配置与参数构造，不发请求
    python scripts/check_pdd.py --live          # 真实调用搜索（默认关键词「机械键盘」）
    python scripts/check_pdd.py --live --keyword 蓝牙耳机 --detail --link

退出码（方便 CI 或批处理判断）：
    0 成功 / 2 未配置密钥 / 3 凭证错误 / 4 权限不足 / 5 需要授权备案
    6 限流或超配额 / 7 无匹配商品 / 8 网络或其他错误

安全说明：脚本只打印**变量名与状态**，不打印任何密钥值，
也不会把密钥写进日志或报告文件。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from betterbuy.openapi import load  # noqa: E402
from betterbuy.openapi.pdd import (ERROR_CODE_TABLE, GATEWAY, METHODS, PDDClient,  # noqa: E402
                                   clamp_page_size, normalize_custom_parameters)
from betterbuy.openapi.transport import MIN_INTERVAL, TIMEOUT  # noqa: E402
from betterbuy.pdd_search import KIND_HINT, KIND_LABEL, attach_links, search  # noqa: E402

EXIT = {
    "ok": 0,
    "no_credential": 2,
    "auth": 3,
    "permission": 4,
    "authorize": 5,
    "quota": 6,
    "empty": 7,
    "network": 8,
    "http": 8,
    "api": 8,
}

REQUIRED = ("BETTERBUY_PDD_CLIENT_ID", "BETTERBUY_PDD_CLIENT_SECRET", "BETTERBUY_PDD_PID")
OPTIONAL = ("BETTERBUY_PDD_ACCESS_TOKEN", "BETTERBUY_PDD_CUSTOM_PARAMETERS")


def line(title: str = "") -> None:
    print("-" * 68 if not title else f"\n== {title} " + "=" * max(0, 60 - len(title)))


def show_offline(cred) -> int:
    line("1. 凭据状态（只显示是否配置，不显示值）")
    for name in REQUIRED:
        value = cred.get(name)
        state = "已配置" if value else "缺失"
        extra = ""
        if value:
            extra = f"（长度 {len(value)}" + ("，含空格，建议去掉" if value != value.strip() else "") + "）"
        print(f"  {'✓' if value else '✗'} {name}: {state}{extra}")
    for name in OPTIONAL:
        value = cred.get(name)
        print(f"  {'✓' if value else '·'} {name}: {'已配置' if value else '未配置（可选）'}")

    line("2. 协议参数自检（不联网）")
    print(f"  网关：{GATEWAY}（只允许 POST）")
    print(f"  超时：{TIMEOUT:.0f} 秒；同平台最小间隔：{MIN_INTERVAL:.1f} 秒")
    print(f"  page_size 夹取：输入 1 -> {clamp_page_size(1)}；输入 500 -> {clamp_page_size(500)}")
    custom = normalize_custom_parameters(cred.get("BETTERBUY_PDD_CUSTOM_PARAMETERS"))
    print(f"  custom_parameters：{custom or '未配置（关键词搜索若报 60001 再配）'}")
    print(f"  接口清单：{', '.join(METHODS.values())}")

    line("3. 错误码对照")
    for row in ERROR_CODE_TABLE:
        print(f"  {row['错误码']:<18} {row['分类']:<12} {row['含义']}")

    if not cred.configured:
        line("结论")
        print("  未配置完整密钥，只能做离线自检。")
        print("  本地：在系统环境变量里设置上面三项后重跑本脚本。")
        print("  云端：Streamlit → Settings → Secrets 里按平铺写法填写，例如")
        print('      BETTERBUY_PDD_CLIENT_ID = "..."')
        return EXIT["no_credential"]

    line("结论")
    print("  配置看起来齐全。加 --live 做一次真实调用（会消耗 1 次配额）。")
    return 0


def show_live(args, cred) -> int:
    client = PDDClient(cred)
    line(f"4. 真实调用 pdd.ddk.goods.search（keyword={args.keyword!r}，条数 {args.limit}）")
    result = search(args.keyword, limit=args.limit, client=client)

    if not result.ok:
        print(f"  ✗ 失败分类：{result.kind_label}")
        print(f"    接口原文：{result.message}")
        if result.code:
            print(f"    错误码：code={result.code}"
                  + (f"，sub_code={result.sub_code}" if result.sub_code else ""))
        print(f"    建议：{result.hint or KIND_HINT.get(result.kind, '')}")
        return EXIT.get(result.kind, 8)

    print(f"  ✓ 成功：{result.message}")
    print(f"    search_id={result.search_id or '未返回'}，接口调用 {result.api_calls} 次")
    print(f"    {'商品名':<28} {'拼团价':>8} {'价格口径'}")
    for product in result.products[:5]:
        price = f"{product.offer.price:.2f}" if product.offer.price is not None else "未返回"
        print(f"    {product.name[:26]:<28} {price:>8} {product.offer.terms}")
    missing_note = [p.name[:12] + f"…缺{len(p.missing)}项" for p in result.products[:3]]
    print(f"    字段缺失示例：{', '.join(missing_note)}")

    if args.detail:
        line("5. 真实调用 pdd.ddk.goods.detail（按 goods_sign）")
        sign = next((p.specs.get("goods_sign") for p in result.products if p.specs.get("goods_sign")), "")
        if not sign:
            print("  · 搜索结果里没有 goods_sign，跳过。")
        else:
            try:
                items = client.goods_detail(goods_sign=sign, search_id=result.search_id)
                print(f"  ✓ 返回 {len(items)} 条详情；字段：{', '.join(sorted(items[0])[:12]) if items else '空'}")
            except Exception as exc:  # noqa: BLE001 - 自检脚本要打印原始错误
                kind = getattr(exc, "kind", "api")
                print(f"  ✗ 详情调用失败（{KIND_LABEL.get(kind, kind)}）：{exc}")
                print(f"    建议：{KIND_HINT.get(kind, '')}")

    if args.link:
        line("6. 真实调用 pdd.ddk.goods.promotion.url.generate（转链）")
        filled, message = attach_links(result.products, client=client)
        print(f"  {'✓' if filled else '✗'} {message}")
        if filled:
            sample = next((p.url for p in result.products if p.url), "")
            print(f"    示例链接（已截断）：{sample[:72]}…")

    line("结论")
    print("  拼多多搜索链路可用。以上价格与标题均来自接口返回，未做任何补值。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="拼多多多多进宝接口自检")
    parser.add_argument("--live", action="store_true", help="真实调用接口（会消耗配额）")
    parser.add_argument("--keyword", default="机械键盘", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=10, help="返回条数（夹到 10–100）")
    parser.add_argument("--detail", action="store_true", help="额外调用一次商品详情")
    parser.add_argument("--link", action="store_true", help="额外调用一次转链接口")
    args = parser.parse_args()

    cred = load("pdd")
    code = show_offline(cred)
    if code != 0:
        return code
    if not args.live:
        print("\n（当前为离线自检；加 --live 才能验证签名与权限。）")
        return 0
    return show_live(args, cred)


if __name__ == "__main__":
    sys.exit(main())
