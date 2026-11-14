"""透明可解释的同币种产品比较公式。"""
from __future__ import annotations

import math
import pandas as pd


def number(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(str(v).strip())
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def reputation_score(row):
    """好评率优先；无好评率时将星级/满分转换成口碑分（非好评率）。"""
    positive = number(row.get("好评率%"))
    rating, scale = number(row.get("评分")), number(row.get("满分"))
    if positive is not None and 0 <= positive <= 100:
        raw = positive
        desc = "公开好评率/手动补录"
    elif rating is not None and scale is not None and scale > 0 and 0 <= rating <= scale:
        raw = 100 * rating / scale
        desc = "评分换算（不是好评率）"
    else:
        return None, "缺少好评率或有效评分"
    count = number(row.get("评价数"))
    if count is not None and count >= 0:
        # 仅当明确有评价数量时使用贝叶斯平滑：对少量评价的高分更谨慎。
        score = (raw * count + 75 * 20) / (count + 20)
        return round(score, 1), desc + "；按评价量平滑"
    return round(raw, 1), desc + "；评价数未知"


def score_items(frame: pd.DataFrame, weights=(35, 40, 25)) -> pd.DataFrame:
    """返回排序结果；缺少必要字段不计算综合分。相互比较的商品必须同币种。"""
    df = frame.copy()
    total_weights = sum(weights)
    if total_weights <= 0:
        raise ValueError("至少设置一个非零权重")
    prices = [number(v) for v in df["价格"]]
    valid_prices = [v for v in prices if v is not None and v > 0]
    min_price = min(valid_prices) if valid_prices else None
    rows = []
    for _, row in df.iterrows():
        price = number(row.get("价格"))
        utility = number(row.get("实用度(1-10)"))
        rep, method = reputation_score(row)
        price_score = round(100 * min_price / price, 1) if price and price > 0 and min_price else None
        utility_score = round(utility * 10, 1) if utility is not None and 0 <= utility <= 10 else None
        all_scores = (price_score, utility_score, rep)
        full = None
        # 设置为 0 的指标不要求补齐；例如可先按「价格+口碑」比较。
        if all(value is not None for value, weight in zip(all_scores, weights) if weight > 0) and row.get("币种"):
            full = round(sum(value * weight for value, weight in zip(all_scores, weights) if weight > 0)
                         / total_weights, 1)
        line = row.to_dict()
        line.update({"价格分": price_score, "实用分": utility_score, "口碑分": rep,
                     "口碑依据": method, "综合分": full})
        rows.append(line)
    out = pd.DataFrame(rows)
    return out.sort_values(by="综合分", ascending=False, na_position="last", kind="stable").reset_index(drop=True)
