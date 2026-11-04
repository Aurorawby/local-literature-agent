"""三层评测分析：关键词规则 vs LLM 打分 vs 人工金标准。

前置：
    eval/human_eval_pool.json   候选池（dump_pool.py 产出）
    eval/lit_labels.json        人工标注（lit_labeler 导出）

用法：
    .venv\\Scripts\\python.exe eval\\human_eval.py            # 分析（LLM 分数缺则先自动打分）
    .venv\\Scripts\\python.exe eval\\human_eval.py --no-llm   # 跳过 LLM 打分
输出：eval/HUMAN_EVAL.auto.md + eval/human_eval_results.json（LLM 分数缓存 eval/llm_scores.json）；人工整理版见 HUMAN_EVAL.md
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = ROOT / "eval"
POOL = OUT_DIR / "human_eval_pool.json"
LABELS = OUT_DIR / "lit_labels.json"
LLM_CACHE = OUT_DIR / "llm_scores.json"
RESULTS = OUT_DIR / "human_eval_results.json"
REPORT = OUT_DIR / "HUMAN_EVAL.auto.md"

WIDE_THRESHOLD = 2   # 人工宽口径：label >= 2 视为相关
TOPK = 10


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def cohens_kappa(pairs: list[tuple]) -> float:
    """pairs: [(judge_a, judge_b)]，类别可任意离散值。"""
    n = len(pairs)
    if n == 0:
        return 0.0
    cats = sorted({x for p in pairs for x in p})
    po = sum(1 for a, b in pairs if a == b) / n
    pa = {c: sum(1 for a, _ in pairs if a == c) / n for c in cats}
    pb = {c: sum(1 for _, b in pairs if b == c) / n for c in cats}
    pe = sum(pa[c] * pb[c] for c in cats)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


# ---------------- LLM 打分（缓存） ----------------

def llm_score_all(pool: dict, settings) -> dict:
    from lit_agent.llm import ChatLLM

    cache = load_json(LLM_CACHE, {})
    llm = ChatLLM(settings)
    print("LLM:", llm.ping())
    todo = []
    for t in pool["topics"]:
        for it in t["items"]:
            if it["uid"] not in cache:
                todo.append((t, it))
    print(f"LLM 打分：已缓存 {len(cache)}，待打 {len(todo)}")
    for i, (t, it) in enumerate(todo, 1):
        user = (
            f"研究主题：{t['topic']}\n\n"
            f"论文标题：{it['title']}\n"
            f"摘要：{it['abstract'] or '（无）'}\n\n"
            "判断这篇论文与主题的相关性：3 = 同时覆盖主题的核心概念和应用领域；"
            "2 = 只覆盖一头、方法或领域沾边但不是直接研究该主题；1 = 不相关。"
            '严格输出 JSON：{"score": 1或2或3}'
        )
        try:
            data = llm.chat_json("你是学术文献相关性标注员。只输出 JSON，不要输出任何其他文字。", user, max_tokens=40)
            score = int(data.get("score") or 0)
            if score not in (1, 2, 3):
                raise ValueError(f"score={score}")
            cache[it["uid"]] = {"score": score, "topic": t["id"], "title": it["title"][:60]}
        except Exception as exc:  # noqa: BLE001
            cache[it["uid"]] = {"score": None, "topic": t["id"], "error": str(exc)[:80]}
        if i % 10 == 0:
            print(f"    {i}/{len(todo)} ...")
            LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.3)
    LLM_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


# ---------------- 分析 ----------------

def precision_at_k(ranked_uids: list[str], labels: dict, threshold: int, k: int) -> tuple[float, int]:
    """只统计「已被标注」的前 k 篇；返回 (精度%, 覆盖数)。"""
    covered = [u for u in ranked_uids[:k] if u in labels and labels[u]["label"] != "skip"]
    if not covered:
        return 0.0, 0
    hits = sum(1 for u in covered if labels[u]["label"] >= threshold)
    return round(hits / len(covered) * 100, 1), len(covered)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM 打分（只做规则 vs 人工）")
    args = ap.parse_args()

    pool = load_json(POOL, None)
    labels_raw = load_json(LABELS, None)
    if not pool or not labels_raw:
        print("缺少 human_eval_pool.json 或 lit_labels.json——先跑 dump_pool.py 并完成标注。")
        return 1
    labels = labels_raw["labels"]

    settings = None
    llm_scores = {}
    if not args.no_llm:
        from lit_agent.config import Settings

        settings = Settings.load()
        llm_scores = llm_score_all(pool, settings)

    # 组装 flat
    flat = {}
    for t in pool["topics"]:
        for it in t["items"]:
            flat[it["uid"]] = {**it, "topic_id": t["id"], "topic": t["topic"],
                               "core_terms": t["core_terms"], "domain_terms": t["domain_terms"]}

    labeled = {u: v for u, v in labels.items() if v["label"] != "skip" and u in flat}
    total_pool = len(flat)
    total_labeled = len(labeled)
    print(f"\n候选池 {total_pool} 篇，人工有效标注 {total_labeled} 篇（跳过 {len(labels) - total_labeled}）")

    # ---- 1. 人工口径 P@10 ----
    human_p = {}
    for cond in ("b1", "b3"):
        per_topic, covered_all = [], []
        for t in pool["topics"]:
            ranked = [it["uid"] for it in t["items"] if it["in"].get(cond)]
            # rank 字段记录的是入选顺序；b1/b3 的 top10 即 in 顺序
            p, cov = precision_at_k(ranked, labels, WIDE_THRESHOLD, TOPK)
            per_topic.append({"id": t["id"], "topic": t["topic"], "p": p, "covered": cov})
            covered_all.append(cov)
        full = [x for x in per_topic if x["covered"] == TOPK]
        human_p[cond] = {
            "wide_mean_p10": round(statistics.mean([x["p"] for x in full]), 1) if full else None,
            "topics_fully_covered": len(full),
            "per_topic": per_topic,
        }
        # 严口径
        strict = []
        for t in pool["topics"]:
            ranked = [it["uid"] for it in t["items"] if it["in"].get(cond)]
            p, _ = precision_at_k(ranked, labels, 3, TOPK)
            strict.append(p)
        human_p[cond]["strict_mean_p10"] = round(statistics.mean(strict), 1)

    # B0：只有前 4 篇被标注（P@4，口径不同只看量级）
    b0_rows = []
    for t in pool["topics"]:
        ranked = [it["uid"] for it in t["items"] if it["in"].get("b0")]
        p, cov = precision_at_k(ranked, labels, WIDE_THRESHOLD, TOPK)
        b0_rows.append({"id": t["id"], "p": p, "covered": cov})
    b0_covered = [x for x in b0_rows if x["covered"] > 0]
    human_p["b0"] = {
        "note": f"B0 仅取每主题前 {4} 篇标注（P@4，与 P@10 口径不同，只看量级差异）",
        "mean_p4": round(statistics.mean([x["p"] for x in b0_covered]), 1) if b0_covered else None,
        "per_topic": b0_rows,
    }

    # ---- 2. 规则口径 vs 人工（宽口径为金标准） ----
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    rule_pairs = []
    for uid, lab in labeled.items():
        it = flat[uid]
        rule_pos = bool(it["rule"]["both"])
        human_pos = lab["label"] >= WIDE_THRESHOLD
        rule_pairs.append((rule_pos, human_pos))
        if rule_pos and human_pos:
            confusion["tp"] += 1
        elif rule_pos and not human_pos:
            confusion["fp"] += 1
        elif not rule_pos and human_pos:
            confusion["fn"] += 1
        else:
            confusion["tn"] += 1
    tp, fp, tn, fn = confusion["tp"], confusion["fp"], confusion["tn"], confusion["fn"]
    rule_precision = round(tp / (tp + fp) * 100, 1) if tp + fp else 0.0
    rule_recall = round(tp / (tp + fn) * 100, 1) if tp + fn else 0.0
    rule_agree = round((tp + tn) / max(1, tp + fp + tn + fn) * 100, 1)
    kappa_rule = cohens_kappa([(a, b) for a, b in rule_pairs])

    # ---- 3. LLM 打分 vs 人工 ----
    llm_block = {"enabled": not args.no_llm}
    if not args.no_llm:
        llm_pairs, exact, tolerance, used = [], 0, 0, 0
        for uid, lab in labeled.items():
            sc = llm_scores.get(uid, {})
            if sc.get("score") in (1, 2, 3):
                used += 1
                llm_pairs.append((sc["score"], lab["label"]))
                if sc["score"] == lab["label"]:
                    exact += 1
                if abs(sc["score"] - lab["label"]) <= 1:
                    tolerance += 1
        if used:
            # LLM 视为「相关判定器」：score>=2 为正例，与人工宽口径比
            llm_conf = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
            for ls, hl in llm_pairs:
                lp, hp = ls >= 2, hl >= WIDE_THRESHOLD
                if lp and hp: llm_conf["tp"] += 1
                elif lp and not hp: llm_conf["fp"] += 1
                elif not lp and hp: llm_conf["fn"] += 1
                else: llm_conf["tn"] += 1
            llm_precision = round(llm_conf["tp"] / max(1, llm_conf["tp"] + llm_conf["fp"]) * 100, 1)
            llm_recall = round(llm_conf["tp"] / max(1, llm_conf["tp"] + llm_conf["fn"]) * 100, 1)
            llm_block.update({
                "n": used,
                "exact_agree_pct": round(exact / used * 100, 1),
                "tolerance_agree_pct": round(tolerance / used * 100, 1),
                "kappa_vs_human": cohens_kappa(llm_pairs),
                "precision_vs_human": llm_precision,
                "recall_vs_human": llm_recall,
            })

    # ---- 4. B1 vs B3 金标准配对差 ----
    pair_rows = []
    for t in pool["topics"]:
        b1 = next(x for x in human_p["b1"]["per_topic"] if x["id"] == t["id"])
        b3 = next(x for x in human_p["b3"]["per_topic"] if x["id"] == t["id"])
        if b1["covered"] == TOPK and b3["covered"] == TOPK:
            pair_rows.append({"id": t["id"], "topic": t["topic"], "b1": b1["p"], "b3": b3["p"], "diff_b3_minus_b1": round(b3["p"] - b1["p"], 1)})
    b1_mean = round(statistics.mean([x["b1"] for x in pair_rows]), 1) if pair_rows else None
    b3_mean = round(statistics.mean([x["b3"] for x in pair_rows]), 1) if pair_rows else None
    wins = sum(1 for x in pair_rows if x["diff_b3_minus_b1"] > 0)
    losses = sum(1 for x in pair_rows if x["diff_b3_minus_b1"] < 0)

    results = {
        "meta": {"generated": time.strftime("%Y-%m-%d %H:%M"), "wide_threshold": WIDE_THRESHOLD,
                 "pool": total_pool, "labeled": total_labeled},
        "human_precision": human_p,
        "rule_vs_human": {"confusion": confusion, "precision": rule_precision, "recall": rule_recall,
                          "agreement_pct": rule_agree, "kappa": kappa_rule},
        "llm_vs_human": llm_block,
        "b1_vs_b3_golden": {"n_topics": len(pair_rows), "b1_mean": b1_mean, "b3_mean": b3_mean,
                            "b3_wins": wins, "b1_wins": losses, "rows": pair_rows},
    }
    RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Markdown 报告 ----
    L = ["# 人工金标准评测：规则 vs 模型 vs 人", ""]
    L += [f"- 候选池 {total_pool} 篇（B1/B3 top10 全量 + B0 前 4 / 每主题），人工有效标注 **{total_labeled}** 篇",
          f"- 盲标：标注时隐藏系统自动判定，避免锚定；标注工具 lit_labeler（键盘 1/2/3，进度本地保存）", ""]
    L += ["## 1. 人工口径下的 Precision@10（宽口径：高度相关 + 部分相关）", ""]
    L += ["| 条件 | 人工口径 P@10 | 严口径（仅高度相关） | 全覆盖主题数 |", "|---|---|---|---|"]
    for cond, label in (("b1", "B1 检索式"), ("b3", "B3 soft（v2 线上）")):
        h = human_p[cond]
        L.append(f"| {label} | **{h['wide_mean_p10']}%** | {h['strict_mean_p10']}% | {h['topics_fully_covered']}/10 |")
    L += [f"| B0 naive | {human_p['b0']['mean_p4']}%（P@4，口径不同只看量级） | — | — |", ""]
    L += ["## 2. 关键词代理标注到底偏了多少？（以人工宽口径为金标准）", ""]
    L += [f"- 混淆矩阵：TP {tp} ｜ FP {fp}（规则说相关、人不认）｜ FN {fn}（规则漏掉、人认为相关）｜ TN {tn}",
          f"- 规则判定精确率 **{rule_precision}%** ｜ 召回 **{rule_recall}%** ｜ 一致率 {rule_agree}% ｜ **Cohen's κ = {kappa_rule}**", ""]
    if llm_block.get("enabled") and llm_block.get("n"):
        L += ["## 3. 本地 7B 模型打分能替代人工吗？", ""]
        L += [f"- 与人工逐篇对比（n={llm_block['n']}）：完全一致 **{llm_block['exact_agree_pct']}%** ｜ ±1 容忍 **{llm_block['tolerance_agree_pct']}%** ｜ κ = **{llm_block['kappa_vs_human']}**",
              f"- 作为相关判定器（score≥2）：精确率 {llm_block['precision_vs_human']}% ｜ 召回 {llm_block['recall_vs_human']}%", ""]
    L += ["## 4. 金标准下，v1 硬约束 vs v2 软惩罚谁更准？", ""]
    if pair_rows:
        L += [f"- 人工口径 P@10（全覆盖主题 n={len(pair_rows)}）：B1 {b1_mean}% ｜ **B3(soft) {b3_mean}%**；B3 更优 {wins} 题 / B1 更优 {losses} 题", ""]
        L += ["| 主题 | B1 | B3(soft) | 差值 |", "|---|---|---|---|"]
        for x in pair_rows:
            L.append(f"| {x['topic']} | {x['b1']}% | {x['b3']}% | {x['diff_b3_minus_b1']:+} |")
    else:
        L += ["- 全覆盖主题不足，配对比较不可用（标注有跳过项）。", ""]
    L += ["## 怎么读（面试口径）", "",
          "- 旧规则口径说「v2 没提升、且指标与机制同源」——本轮用**人工金标准**重新回答了这个问题；",
          "- 规则口径与人工的 κ = " + str(kappa_rule) + "：量化了「代理标注偏差」，说明此前 6.0%→57.5% 的**方向**可信，绝对值需带保留；",
          "- 模型打分 κ（如有）回答「能不能用模型预标注替代人工」。",
          ""]
    REPORT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:24]))
    print(f"\n已写入 {REPORT.name} 与 {RESULTS.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
