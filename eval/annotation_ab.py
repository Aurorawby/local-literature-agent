"""标注策略 A/B 模拟：随机抽样 vs LLM 分层 vs LLM 预标注。

目的：回答一个真实的产品/研究问题——
“没有预算标 201 篇时，能不能用 7B 模型帮我省人工？哪种抽样策略能让 P@10 估计最准？”

数据：B3 top10 / 每主题（100 篇，全部有人工金标准），LLM 分数缓存。
方法：固定预算 k 篇/主题（k=3/5），重复 2000 次，比较估计值与全量金标准的偏差/RMSE/±5pp 命中率。

注意：这是**模拟**，不是重新找被试标注；它假设人工标签是金标准，LLM 分数已知。
输出：eval/annotation_ab.json
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

OUT = Path("eval")
pool = json.loads((OUT / "human_eval_pool.json").read_text(encoding="utf-8"))
labels = json.loads((OUT / "lit_labels.json").read_text(encoding="utf-8"))["labels"]
llm = json.loads((OUT / "llm_scores.json").read_text(encoding="utf-8"))

topics = []
for t in pool["topics"]:
    ranked = sorted(
        [(it["rank"]["b3"], it["uid"]) for it in t["items"] if it.get("rank", {}).get("b3")],
        key=lambda x: x[0],
    )[:10]
    rows = []
    for _, uid in ranked:
        rows.append({
            "uid": uid,
            "y": labels[uid]["label"] >= 2,
            "score": llm.get(uid, {}).get("score"),
        })
    topics.append(rows)

FULL = statistics.mean(sum(1 for r in rows if r["y"]) / len(rows) * 100 for rows in topics)


def est_random(rows, k, rng):
    sample = rng.sample(rows, k)
    return sum(1 for r in sample if r["y"]) / k * 100


def est_stratified(rows, k, rng):
    by = {1: [], 2: [], 3: []}
    for r in rows:
        by[r["score"]].append(r)
    n = len(rows)
    raw = {h: k * len(by[h]) / n for h in by}
    alloc = {h: int(raw[h]) for h in by}
    rem = k - sum(alloc.values())
    for h in sorted(by, key=lambda x: -(raw[x] - alloc[x]))[:rem]:
        alloc[h] += 1
    # 预算 >= 非空层数时，保证每个非空层至少 1 个
    if k >= sum(1 for h in by if by[h]):
        for h in by:
            if by[h] and alloc[h] == 0:
                donor = max(by, key=lambda x: alloc[x])
                alloc[donor] -= 1
                alloc[h] += 1
    est = 0.0
    for h, values in by.items():
        if not values:
            continue
        a = min(max(1, alloc[h]), len(values))
        sample = rng.sample(values, a)
        est += len(values) / n * (sum(1 for r in sample if r["y"]) / a * 100)
    return est


def est_llm_prelabel(rows, k, rng):
    audited = set(rng.sample(range(len(rows)), k))
    vals = []
    for i, r in enumerate(rows):
        vals.append(1 if (r["y"] if i in audited else r["score"] >= 2) else 0)
    return sum(vals) / len(vals) * 100


def run(k, reps=2000, seed=42):
    rng = random.Random(seed)
    result = {}
    for name, fn in (
        ("random", est_random),
        ("llm_stratified", est_stratified),
        ("llm_prelabel_audit", est_llm_prelabel),
    ):
        values = []
        for _ in range(reps):
            values.append(statistics.mean(fn(rows, k, rng) for rows in topics))
        bias = statistics.mean(values) - FULL
        mae = statistics.mean(abs(v - FULL) for v in values)
        rmse = (statistics.mean((v - FULL) ** 2 for v in values)) ** 0.5
        within = sum(1 for v in values if abs(v - FULL) <= 5) / reps * 100
        result[name] = {
            "mean": round(statistics.mean(values), 2),
            "bias": round(bias, 2),
            "mae": round(mae, 2),
            "rmse": round(rmse, 2),
            "within_5pp": round(within, 1),
        }
    return result


out = {
    "meta": {
        "purpose": "标注策略 A/B 模拟：无预算标全量时，随机 vs LLM 分层 vs LLM 预标注",
        "data": "B3 top10 / 10 topics = 100 papers; 201 human labels; LLM scores cached",
        "full_gold_p10": round(FULL, 1),
        "reps": 2000,
        "seed": 42,
        "caveat": "模拟，不是重新标注；假设人工标签为金标准；LLM 分数为 7B 缓存。",
    },
    "budget_per_topic": {str(k): run(k) for k in (3, 5)},
}
(OUT / "annotation_ab.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2))
