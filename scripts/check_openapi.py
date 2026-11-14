"""开放平台接入自检脚本。

用法：
    python scripts\\check_openapi.py                 # 只看密钥状态 + 链接识别
    python scripts\\check_openapi.py --platform jd --live
    python scripts\\check_openapi.py --platform all --live
    python scripts\\check_openapi.py --link https://item.jd.com/100012043978.html

``--live`` 会真的调用一次接口（各 1 次请求），用来确认：
- 密钥是否有效（签名对不对、app_key 是否存在）；
- 是否已经拿到接口权限；
- 返回体里到底有哪些字段（用于核对字段映射）。

失败会打印平台原始错误码与可执行的处理建议，**不会**用示例数据填充。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from betterbuy.linkdetect import detect  # noqa: E402
from betterbuy.openapi import (ApiError, JDClient, PDDClient, TaobaoClient,  # noqa: E402
                               PLATFORMS, load)
from betterbuy.openapi.fields import all_keys  # noqa: E402

ADVICE = {
    "no_credential": "先在环境变量里配齐密钥（见上方「缺少」列表），再运行 --live。",
    "auth": "密钥无效或签名不匹配：核对 app_key/app_secret 是否抄错、是否有多余空格；"
            "京东的 timestamp 必须是东八区且与本机时间相差不超过 10 分钟。",
    "permission": "密钥有效但应用没有该接口权限：去开放平台控制台为应用申请对应接口。",
    "quota": "调用次数超限：降低频率或第二天再试。",
    "network": "网络不通：确认能访问该网关，必要时设置 HTTPS_PROXY。",
}


def show_credentials() -> None:
    print("=" * 78)
    print("密钥状态（只读环境变量，不落盘、不打印明文）")
    print("=" * 78)
    for item in PLATFORMS:
        cred = load(item["key"])
        mark = "OK  " if cred.configured else "缺失"
        print(f"[{mark}] {item['label']:<10} 网关 {item['gateway']}")
        if not cred.configured:
            print(f"        缺少：{'、'.join(cred.missing)}")
    print()


def show_link(url: str) -> None:
    info = detect(url)
    print("=" * 78)
    print("链接识别")
    print("=" * 78)
    print(f"  输入      : {url}")
    print(f"  平台      : {info.label} ({info.platform})")
    print(f"  商品 ID   : {info.item_id or '未取到'}")
    print(f"  短链      : {'是（需要联网展开）' if info.is_short else '否'}")
    if info.note:
        print(f"  说明      : {info.note}")
    if info.ok:
        print(f"  product_id: {info.product_id}")
    print()


def live_check(platform: str) -> bool:
    print("=" * 78)
    print(f"真实调用：{platform}")
    print("=" * 78)
    try:
        if platform == "jd":
            client = JDClient()
            items = client.goods_query(keyword="手机", pageIndex=1, pageSize=5)
            api = "jd.union.open.goods.query"
        elif platform == "taobao":
            client = TaobaoClient()
            items = client.material_search(q="手机", page_size=5)
            api = "taobao.tbk.dg.material.optional.upgrade"
        elif platform == "pdd":
            client = PDDClient()
            items = client.goods_search(keyword="手机", page_size=5)
            api = "pdd.ddk.goods.search"
        else:
            print(f"  未知平台：{platform}")
            return False
    except ApiError as exc:
        print(f"  [失败] {exc.kind} / code={exc.code or '-'} / sub_code={exc.sub_code or '-'}")
        print(f"  原因：{exc.message}")
        tip = ADVICE.get(exc.kind)
        if tip:
            print(f"  建议：{tip}")
        if exc.request_id:
            print(f"  request_id：{exc.request_id}（找平台客服时可提供）")
        print()
        return False

    print(f"  [成功] 接口 {api} 返回 {len(items)} 条")
    if not items:
        print("  注意：调用成功但没有数据。可能是关键词在该平台推广池里没有结果，"
              "不代表接口不可用。")
        print()
        return True

    print("  第一条记录的字段路径（用于核对字段映射，不是完整数据）：")
    for path in all_keys(items[0])[:40]:
        print(f"    - {path}")
    print()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="BetterBuy 电商开放平台接入自检")
    parser.add_argument("--platform", default="", help="jd / taobao / pdd / all")
    parser.add_argument("--live", action="store_true", help="做一次真实调用（消耗接口配额）")
    parser.add_argument("--link", default="", help="只测试一个链接的识别结果")
    args = parser.parse_args()

    show_credentials()

    if args.link:
        show_link(args.link)

    if not args.live:
        if not args.link:
            print("提示：加 --live 可以做一次真实调用，确认密钥与权限是否可用。")
        return 0

    targets = ["jd", "taobao", "pdd"] if args.platform in ("", "all") else [args.platform]
    results = {platform: live_check(platform) for platform in targets}

    print("=" * 78)
    print("汇总")
    print("=" * 78)
    for platform, ok in results.items():
        print(f"  {platform:<8} {'调用成功' if ok else '未通过'}")
    failed = [p for p, ok in results.items() if not ok]
    if failed:
        print("\n未通过的平台请按上面的建议处理；处理完再跑一次这个脚本。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
