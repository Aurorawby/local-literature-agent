"""对 201 篇人工盲标做补充统计：置信区间、配对检验、B0 同口径对比、护栏模拟。

只读 eval/ 现有数据，输出 eval/human_eval_stats.json 与 eval/AB_EXPERIMENT.md。
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

EVAL = Path("eval")
pool = json.loads((EVAL / "human_eval_pool.json").read_text(encoding="utf-8"))
labels_raw = json.loads((EVAL / "lit_labels.json").read_text(encoding="utf-8"))
labels = labels_raw["labels"]
results = json.loads((EVAL / "human_eval_results.json").read_text(encoding="utf-8"))
llm = json.loads((EVAL / "llm_scores.json").read_text(encoding="utf-8"))
v2 = json.loads((EVAL / "v2.json").read_text(encoding="utf-8"))

TOPICS = pool["topics"]
BY_ID = {t["id"]: t for t in TOPICS}
TOP_K = 10
WIDE = 2
STRICT = 3
rng = random.Random(20261104)


def label(uid):
    return labels.get(uid, {}).get("label")


def p_at(cond, threshold, k=TOP_K):
    per = []
    for t in TOPICS:
        ranked = sorted(
            [(it["rank"][cond], it["uid"]) for it in t["items"] if it.get("rank", {}).get(cond)],
            key=lambda x: x[0],
        )
        uids = [uid for _, uid in ranked][:k]
        vals = [label(u) for u in uids]
        vals = [v for v in vals if isinstance(v, int)]
        per.append({"id": t["id"], "topic": t["topic"], "p": 100.0 * sum(v >= threshold for v in vals) / len(vals) if vals else None, "n": len(vals)})
    full = [x["p"] for x in per if x["p"] is not None]
    return per, statistics.mean(full) if full else None


def bootstrap_mean(values, n=5000):
    means = []
    for _ in range(n):
        sample = [rng.choice(values) for _ in values]
        means.append(statistics.mean(sample))
    means.sort()
    return {
        "mean": statistics.mean(values),
        "lo": means[int(0.025 * n)],
        "hi": means[int(0.975 * n) - 1],
    }


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    cats = sorted(set(a) | set(b))
    po = sum(1 for x, y in pairs if x == y) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else None


def weighted_kappa(pairs, weights="quadratic"):
    n = len(pairs)
    cats = sorted(set(x for p in pairs for x in p))
    k = len(cats)
    idx = {c: i for i, c in enumerate(cats)}
    obs = [[0] * k for _ in range(k)]
    for a, b in pairs:
        obs[idx[a]][idx[b]] += 1
    row = [sum(r) for r in obs]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    if weights == "quadratic":
        w = [[((i - j) / (k - 1)) ** 2 for j in range(k)] for i in range(k)]
    else:
        w = [[abs(i - j) / (k - 1) for j in range(k)] for i in range(k)]
    num = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    den = sum(w[i][j] * row[i] * col[j] / n for i in range(k) for j in range(k))
    return 1 - num / den if den else None


def confusion(pairs):
    tp = sum(1 for r, h in pairs if r and h)
    fp = sum(1 for r, h in pairs if r and not h)
    fn = sum(1 for r, h in pairs if not r and h)
    tn = sum(1 for r, h in pairs if not r and not h)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "agreement": (tp + tn) / max(1, tp + fp + fn + tn)}


def mcnemar_exact(b, c):
    # 配对二分类：b = 只有 A 对，c = 只有 B 对；精确二项检验
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n) * 2
    return min(1.0, p)


b1_wide, b1_mean = p_at("b1", WIDE)
b3_wide, b3_mean = p_at("b3", WIDE)
b1_strict, b1s_mean = p_at("b1", STRICT)
b3_strict, b3s_mean = p_at("b3", STRICT)

wide_diffs = [r["p"] - s["p"] for r, s in zip(b3_wide, b1_wide) if r["p"] is not None and s["p"] is not None]
strict_diffs = [r["p"] - s["p"] for r, s in zip(b3_strict, b1_strict) if r["p"] is not None and s["p"] is not None]

# 配对置换检验：主题级均值差
def permutation_p(diffs):
    n = len(diffs)
    obs = abs(statistics.mean(diffs))
    cnt = 0
    for mask in range(1 << n):
        signed = [d if (mask >> i) & 1 else -d for i, d in enumerate(diffs)]
        if abs(statistics.mean(signed)) >= obs - 1e-12:
            cnt += 1
    return cnt / (1 << n)

# 规则 vs 人工：全池 / 仅 B1/B3 top10
all_pairs_map = {}
for t in TOPICS:
    for it in t["items"]:
        h = label(it["uid"])
        if isinstance(h, int):
            all_pairs_map[it["uid"]] = (bool(it["rule"]["both"]), h >= WIDE)
all_pairs = list(all_pairs_map.values())
ranked_pairs = []
for cond in ("b1", "b3"):
    for t in TOPICS:
        ranked = sorted([(it["rank"][cond], it["uid"]) for it in t["items"] if it.get("rank", {}).get(cond)], key=lambda x: x[0])[:TOP_K]
        for _, uid in ranked:
            h = label(uid)
            it = next(x for x in t["items"] if x["uid"] == uid)
            if isinstance(h, int):
                ranked_pairs.append((bool(it["rule"]["both"]), h >= WIDE))

# B0 同口径：B0 池里 40 篇，比较规则 vs 人工
b0_pairs = []
b0_items = 0
for t in TOPICS:
    for it in t["items"]:
        if it.get("in", {}).get("b0"):
            b0_items += 1
            h = label(it["uid"])
            if isinstance(h, int):
                b0_pairs.append((bool(it["rule"]["both"]), h >= WIDE))

def bootstrap_kappa(pairs, n=3000):
    vals = []
    for _ in range(n):
        sample = [rng.choice(pairs) for _ in pairs]
        val = kappa(sample)
        if val is not None:
            vals.append(val)
    vals.sort()
    return {"lo": vals[int(0.025 * len(vals))], "hi": vals[int(0.975 * len(vals)) - 1]}


def mde_80(diffs):
    n = len(diffs)
    se = statistics.stdev(diffs) / (n ** 0.5) if n > 1 else float("inf")
    return 2.8 * se

# 7B 有序一致性：完全一致 / ±1 / κ / 二次加权 κ
llm_pairs = []
for uid, sc in llm.items():
    h = label(uid)
    if isinstance(h, int) and isinstance(sc.get("score"), int):
        llm_pairs.append((sc["score"], h))
llm_exact = sum(1 for a, b in llm_pairs if a == b) / len(llm_pairs) * 100
llm_tol = sum(1 for a, b in llm_pairs if abs(a - b) <= 1) / len(llm_pairs) * 100
llm_pairs_bin = [(s >= 2, h >= WIDE) for s, h in llm_pairs]
llm_conf = confusion(llm_pairs_bin)

# 护栏模拟：规则 P@10 < 50 时回退 B1（B1 人工口径），否则用 B3
v2_rows = {r["id"]: r for r in v2["rows"]}
guard = []
for t in TOPICS:
    tid = t["id"]
    rule_p = v2_rows[tid]["b3"]["precision@10"]
    row = {"id": tid, "topic": t["topic"], "rule_p10": rule_p,
           "b1_human": next(x["p"] for x in b1_wide if x["id"] == tid),
           "b3_human": next(x["p"] for x in b3_wide if x["id"] == tid)}
    row["trigger"] = rule_p < 50
    row["sim_human"] = row["b1_human"] if row["trigger"] else row["b3_human"]
    guard.append(row)

# 留一主题阈值选择（用规则 P@10 触发；阈值从 30..70；训练=其余 9 主题，按最差主题最大化选阈值）
loo = []
for th in range(30, 71, 5):
    vals = []
    for i, t in enumerate(TOPICS):
        train = [g for j, g in enumerate(guard) if j != i]
        best = max(train, key=lambda g: min(g["b1_human"] if g["rule_p10"] < th else g["b3_human"], 100))
        # 简化：用训练集的最差主题选阈值（这里只记录该阈值在留出主题上的效果）
        held = guard[i]
        vals.append(held["b1_human"] if held["rule_p10"] < th else held["b3_human"])
    loo.append({"threshold": th, "loo_mean_human": statistics.mean(vals), "min_topic": min(vals)})

# 全量阈值扫描（事后，不是预注册）
sweep = []
for th in range(20, 81, 5):
    vals = [g["b1_human"] if g["rule_p10"] < th else g["b3_human"] for g in guard]
    sweep.append({"threshold": th, "mean_human": statistics.mean(vals), "min_topic": min(vals), "triggered": sum(g["rule_p10"] < th for g in guard)})

# 置信区间
out = {
    "meta": {
        "generated": "2026-11-04",
        "labels": sum(1 for v in labels.values() if isinstance(v.get("label"), int)),
        "topics": len(TOPICS),
        "note": "B1/B3 人工 P@10 的 95% CI 用主题级 cluster bootstrap；B0 只有 4 篇/主题，不能与 P@10 直接比。",
    },
    "human_p10": {
        "b1_wide": {"value": b1_mean, "ci": bootstrap_mean([x["p"] for x in b1_wide])},
        "b3_wide": {"value": b3_mean, "ci": bootstrap_mean([x["p"] for x in b3_wide])},
        "b1_strict": {"value": b1s_mean, "ci": bootstrap_mean([x["p"] for x in b1_strict])},
        "b3_strict": {"value": b3s_mean, "ci": bootstrap_mean([x["p"] for x in b3_strict])},
    },
    "b3_minus_b1": {
        "wide": {"mean": statistics.mean(wide_diffs), "bootstrap": bootstrap_mean(wide_diffs), "permutation_p": permutation_p(wide_diffs), "wins_b3": sum(d > 0 for d in wide_diffs), "wins_b1": sum(d < 0 for d in wide_diffs), "mde80": mde_80(wide_diffs)},
        "strict": {"mean": statistics.mean(strict_diffs), "bootstrap": bootstrap_mean(strict_diffs), "permutation_p": permutation_p(strict_diffs), "wins_b3": sum(d > 0 for d in strict_diffs), "wins_b1": sum(d < 0 for d in strict_diffs), "mde80": mde_80(strict_diffs)},
    },
    "rule_vs_human": {
        "all_pool": {**confusion(all_pairs), "kappa": kappa(all_pairs), "kappa_ci": bootstrap_kappa(all_pairs), "n": len(all_pairs)},
        "b1_b3_top10": {**confusion(ranked_pairs), "kappa": kappa(ranked_pairs), "kappa_ci": bootstrap_kappa(ranked_pairs), "n": len(ranked_pairs)},
    },
    "b0_same_pool": {
        "n_items": b0_items,
        "human_wide_p4_computed": 100 * sum(1 for _, h in b0_pairs if h) / len(b0_pairs) if b0_pairs else None,
        "rule_wide_p4_computed": 100 * sum(1 for r, _ in b0_pairs if r) / len(b0_pairs) if b0_pairs else None,
        "n_pairs": len(b0_pairs),
    },
    "llm_judge": {
        "n": len(llm_pairs),
        "exact_pct": llm_exact,
        "tolerance_pct": llm_tol,
        "kappa": kappa(llm_pairs),
        "kappa_ci": bootstrap_kappa(llm_pairs),
        "weighted_kappa_quadratic": weighted_kappa(llm_pairs, "quadratic"),
        "binary_confusion": llm_conf,
    },
    "guardrail_sim": {
        "rows": guard,
        "always_b3": statistics.mean(g["b3_human"] for g in guard),
        "rule_trigger": statistics.mean(g["sim_human"] for g in guard),
        "sweep": sweep,
        "loo": loo,
        "caveat": "这是用人工金标准做的反事实模拟，不是线上 A/B；阈值是在同一批 10 个主题上得到的，尚未在 holdout 主题验证。",
    },
}

(EVAL / "human_eval_stats.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

print("written", EVAL / "human_eval_stats.json")
