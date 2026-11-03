"""检索层 A/B 评测。

三个条件（同一批 10 个主题）：

* **B0 naive**  ：直接用中文主题当检索式，无检索式规划、无相关性约束；
* **B1 queries**：用模型的英文学术检索式 + 多源检索 + 去重打分，但**不加**核心概念/领域硬约束；
* **B2 full**   ：英文学术检索式 + 去重打分 + **核心概念/领域硬约束**（当前线上逻辑）。

评测指标（关键词代理标注，口径见 eval/topics.json）：
* Precision@10：前 10 篇里同时命中 core + domain 的比例；
* Core@10：前 10 篇里命中 core 的比例；
* Domain@10：前 10 篇里命中 domain 的比例。

用法：
    .venv\\Scripts\\python.exe eval\\run_eval.py [--per-query 10] [--top-k 10]

输出：eval/results.json + eval/RESULTS.md
"""

from __future__ import annotations

import argparse
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

from lit_agent.config import Settings  # noqa: E402
from lit_agent.graph import _english_query, _has_core_term, _normalize_query  # noqa: E402
from lit_agent.llm import ChatLLM  # noqa: E402
from lit_agent.search import (  # noqa: E402
    Http,
    SourceRateLimited,
    arxiv_search,
    crossref_search,
    dedupe,
    doaj_search,
    expand_synonyms,
    relevance_score,
    select_candidates,
)
from lit_agent import prompts  # noqa: E402

OUT_DIR = ROOT / "eval"


def load_topics() -> list[dict]:
    return json.loads((OUT_DIR / "topics.json").read_text(encoding="utf-8"))["topics"]


def on_topic(paper, core_terms: list[str], domain_terms: list[str]) -> dict[str, bool]:
    text = f"{paper.title} {paper.abstract}".lower()
    core = any(term.lower() in text for term in core_terms)
    domain = any(term.lower() in text for term in domain_terms)
    return {"core": core, "domain": domain, "both": core and domain}


def ratio(papers, core_terms, domain_terms, key: str) -> float:
    if not papers:
        return 0.0
    hits = sum(1 for p in papers if on_topic(p, core_terms, domain_terms)[key])
    return round(hits / len(papers) * 100, 1)


def search_all(query: str, http: Http, per_query: int, notes: list[str]):
    """在三个数据源里检索一条检索式。"""
    found = []
    for name, func in (
        ("Crossref", lambda: crossref_search(
            query, rows=per_query, year_from=2019, http=http)),
        ("DOAJ", lambda: doaj_search(
            query, page_size=per_query, year_from=2019, http=http)),
        ("arXiv", lambda: arxiv_search(query, max_results=per_query, http=http)),
    ):
        try:
            batch = func()
        except SourceRateLimited as exc:
            notes.append(f"{name} 跳过：{str(exc)[:50]}")
            continue
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name} 出错：{exc}")
            continue
        found.extend(batch)
        time.sleep(0.8)
    return found


def plan_queries(topic: str, llm: ChatLLM) -> tuple[list[str], list[str], list[str]]:
    data = llm.chat_json(
        prompts.QUERY_PLANNER_SYSTEM, prompts.query_planner_user(topic), max_tokens=320
    )
    queries = [q for q in (_english_query(_normalize_query(x))
                           for x in (data.get("queries") or [])) if q][:3]
    core = [c.strip() for c in (data.get("core_terms") or []) if c and c.strip()]
    domain = [d.strip() for d in (data.get("domain_terms") or []) if d and d.strip()]
    if core:
        queries = [
            q if _has_core_term(q, core) else _normalize_query(f"{core[0]} {q}")
            for q in queries
        ]
    return queries, core, domain


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-query", type=int, default=10, help="每个源每条检索式取多少条")
    parser.add_argument("--top-k", type=int, default=10, help="评估前 K 篇")
    parser.add_argument("--topics", help="只跑某些 topic id，逗号分隔")
    parser.add_argument("--out", default="results", help="输出文件名前缀（不含扩展名）")
    parser.add_argument("--repeat", type=int, default=1,
                        help="每个主题重复几次（用于估计运行噪声，默认 1）")
    args = parser.parse_args()

    topics = load_topics()
    if args.topics:
        wanted = {t.strip() for t in args.topics.split(",")}
        topics = [t for t in topics if t["id"] in wanted]

    settings = Settings.load()
    llm = ChatLLM(settings)
    print(llm.ping())
    http = Http()
    print(f"评测 {len(topics)} 个主题；每源每检索式 {args.per_query} 条，评估 top-{args.top_k}\n")

    rows: list[dict] = []
    for index, item in enumerate(topics, 1):
        tid, topic = item["id"], item["topic"]
        core, domain = item["core_terms"], item["domain_terms"]
        print(f"[{index}/{len(topics)}] {tid}：{topic}")
        notes: list[str] = []
        started = time.perf_counter()
        runs: list[dict] = []

        for rep in range(max(1, args.repeat)):
            if args.repeat > 1:
                print(f"    第 {rep + 1}/{args.repeat} 次 ...")
            # ---------- B0：naive（中文主题直接当检索式） ----------
            b0_pool = dedupe(search_all(topic, http, args.per_query, notes))
            b0_top = select_candidates(b0_pool, [topic], args.top_k, mode="off")

            # ---------- B1/B2/B3：模型检索式，共享同一批候选 ----------
            queries, llm_core, llm_domain = plan_queries(topic, llm)
            pool: list = []
            for query in queries:
                pool.extend(search_all(query, http, args.per_query, notes))
            pool = dedupe(pool)
            core_used = llm_core or core
            domain_used = llm_domain or domain

            b1_top = select_candidates(pool, [topic], args.top_k, mode="off")
            b2_top = select_candidates(
                pool, [topic], args.top_k,
                required_terms=core_used, domain_terms=domain_used, mode="hard",
            )
            b3_top = select_candidates(
                pool, [topic], args.top_k,
                required_terms=expand_synonyms(core_used),
                domain_terms=expand_synonyms(domain_used),
                mode="soft",
            )

            def pack(top, label: str) -> dict:
                return {
                    "n": len(top),
                    "precision@10": ratio(top, core, domain, "both"),
                    "core@10": ratio(top, core, domain, "core"),
                    "domain@10": ratio(top, core, domain, "domain"),
                    f"{label}_titles": [p.title[:90] for p in top],
                }

            runs.append({
                "queries": queries,
                "candidates": {"b0": len(b0_pool), "pipeline": len(pool)},
                "b0": pack(b0_top, "b0"),
                "b1": pack(b1_top, "b1"),
                "b2": pack(b2_top, "b2"),
                "b3": pack(b3_top, "b3"),
            })

        def aggregate(cond: str) -> dict:
            ps = [r[cond]["precision@10"] for r in runs]
            cs = [r[cond]["core@10"] for r in runs]
            ds = [r[cond]["domain@10"] for r in runs]
            return {
                "n": runs[-1][cond]["n"],
                "precision@10": round(statistics.mean(ps), 1),
                "precision@10_values": ps,
                "precision@10_range": round(max(ps) - min(ps), 1),
                "core@10": round(statistics.mean(cs), 1),
                "domain@10": round(statistics.mean(ds), 1),
            }

        row = {
            "id": tid,
            "topic": topic,
            "queries": runs[-1]["queries"],
            "candidates": runs[-1]["candidates"],
            "elapsed_s": round(time.perf_counter() - started, 1),
            "repeats": max(1, args.repeat),
            "notes": notes,
            "b0": aggregate("b0"),
            "b1": aggregate("b1"),
            "b2": aggregate("b2"),
            "b3": aggregate("b3"),
            "b3_titles": runs[-1]["b3"]["b3_titles"],
            "b2_titles": runs[-1]["b2"]["b2_titles"],
            "b1_titles": runs[-1]["b1"]["b1_titles"],
        }
        rows.append(row)
        print(f"    B0 {row['b0']['precision@10']}% | B1 {row['b1']['precision@10']}% | "
              f"B2(hard) {row['b2']['precision@10']}% | B3(soft) {row['b3']['precision@10']}%"
              f"  （候选 {row['candidates']['b0']}/{row['candidates']['pipeline']}，"
              f"{row['elapsed_s']}s）")

    # ---------- 汇总 ----------
    def mean(key: str, cond: str) -> float:
        values = [row[cond][key] for row in rows if row[cond]["n"] > 0]
        return round(statistics.mean(values), 1) if values else 0.0

    def mean_range(cond: str) -> float:
        values = [row[cond]["precision@10_range"] for row in rows]
        return round(statistics.mean(values), 1) if values else 0.0

    summary = {
        "topics": len(rows),
        "repeats": max(1, args.repeat),
        "conditions": {
            cond: {
                "precision@10": mean("precision@10", cond),
                "precision@10_mean_range": mean_range(cond),
                "core@10": mean("core@10", cond),
                "domain@10": mean("domain@10", cond),
            }
            for cond in ("b0", "b1", "b2", "b3")
        },
    }

    result = {
        "meta": json.loads((OUT_DIR / "topics.json").read_text(encoding="utf-8"))["meta"],
        "config": {"per_query": args.per_query, "top_k": args.top_k},
        "summary": summary,
        "rows": rows,
    }
    (OUT_DIR / f"{args.out}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---------- Markdown ----------
    repeat_note = (f"每个主题重复 **{summary['repeats']}** 次取均值"
                   if summary["repeats"] > 1 else "每个主题跑 1 次")
    lines = [
        "# 检索层 A/B 评测结果",
        "",
        f"- 评测主题：**{summary['topics']} 个**（跨金融、能源、医疗、教育、政策、半导体）",
        f"- 每源每检索式取 {args.per_query} 条，评估前 {args.top_k} 篇；{repeat_note}",
        "- 标注口径：标题/摘要同时命中「核心概念」与「应用领域」才算 on-topic"
        "（关键词代理标注，口径见 `eval/topics.json`）",
        "",
        "## 四种条件",
        "",
        "| 条件 | 做法 |",
        "|---|---|",
        "| B0 naive | 直接用中文主题检索，不规划检索式、不做约束 |",
        "| B1 检索式 | 模型生成英文学术检索式 + 多源检索 + 去重打分，**不加约束** |",
        "| B2 hard（v1） | B1 + 核心概念/领域**硬淘汰**（缺失即丢弃） |",
        "| B3 soft（v2） | B1 + **同义词扩展 + 软惩罚**（缺失只扣分，不丢弃） |",
        "",
        "## 总览（主题均值）",
        "",
        "| 条件 | Precision@10 | 重复波动 | Core@10 | Domain@10 |",
        "|---|---|---|---|---|",
    ]
    for cond, label in (("b0", "B0 naive"), ("b1", "B1 检索式"),
                        ("b2", "B2 hard（v1）"), ("b3", "B3 soft（v2）")):
        stats = summary["conditions"][cond]
        spread = (f"±{stats['precision@10_mean_range']}pp"
                  if summary["repeats"] > 1 else "—")
        lines.append(
            f"| {label} | **{stats['precision@10']}%** | {spread} | "
            f"{stats['core@10']}% | {stats['domain@10']}% |"
        )

    lines += ["", "## 逐主题明细", ""]
    header = "| 主题 | 候选(B0/线上) | B0 | B1 | B2 hard | B3 soft | 耗时 |"
    if summary["repeats"] > 1:
        header = "| 主题 | 候选(B0/线上) | B0 | B1 | B2 hard | B3 soft | 耗时 |"
    lines += [header, "|---|---|---|---|---|---|---|"]

    def cell(row: dict, cond: str) -> str:
        stats = row[cond]
        if summary["repeats"] > 1:
            return f"{stats['precision@10']}%（±{stats['precision@10_range']}）"
        return f"{stats['precision@10']}%"

    for row in rows:
        lines.append(
            f"| {row['topic']} | {row['candidates']['b0']}/{row['candidates']['pipeline']} | "
            f"{cell(row, 'b0')} | {cell(row, 'b1')} | {cell(row, 'b2')} | "
            f"{cell(row, 'b3')} | {row['elapsed_s']}s |"
        )

    lines += [
        "",
        "## 怎么读这个结果",
        "",
        "1. **B0 → B1**：把中文主题换成学术英文检索式 + 多源检索的收益；",
        "2. **B1 → B2**：硬约束的收益（v1 曾据此上线）；",
        "3. **B2 → B3**：v2 把硬淘汰换成同义词扩展 + 软惩罚后的变化——",
        "   重点看**波动是否变小**、以及此前被误杀的主题是否回升；",
        "4. 单次运行噪声较大（检索源实时变化 + 检索式随机），`--repeat` 越多结论越稳。",
        "",
        "## 局限（必读）",
        "",
        "- 关键词代理标注，不是人工逐篇标注；绝对值不可与其他系统比较，只能做同条件对比；",
        "- 主题数 10 个，样本不大；",
        "- 只评测检索层，不覆盖精读/综述质量（那部分用引用核验和人工抽查评估）。",
        "",
    ]
    (OUT_DIR / f"{args.out}.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 78)
    for cond, label in (("b0", "B0 naive"), ("b1", "B1 检索式"),
                        ("b2", "B2 hard(v1)"), ("b3", "B3 soft(v2)")):
        stats = summary["conditions"][cond]
        print(f"{label:14s} Precision@10 = {stats['precision@10']:5.1f}%  "
              f"(core {stats['core@10']:4.1f}% / domain {stats['domain@10']:4.1f}%"
              + (f" / 波动 ±{stats['precision@10_mean_range']}" if summary['repeats'] > 1 else "")
              + ")")
    print("=" * 78)
    print(f"已写入：{OUT_DIR}/{args.out}.md 与 {OUT_DIR}/{args.out}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
