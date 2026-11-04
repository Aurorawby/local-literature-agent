"""导出人工标注候选池：每主题合并 B0/B1/B3 的 top 候选（完整标题 + 摘要），
并附上关键词代理标注的判定（rule），供 lit_labeler 盲标与三层评测分析使用。

用法：
    .venv\\Scripts\\python.exe eval\\dump_pool.py
输出：eval/human_eval_pool.json
"""
from __future__ import annotations

import hashlib
import json
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
from lit_agent.llm import ChatLLM  # noqa: E402
from lit_agent.search import (  # noqa: E402
    Http,
    arxiv_search,
    crossref_search,
    dedupe,
    doaj_search,
    expand_synonyms,
    select_candidates,
)
from lit_agent import prompts  # noqa: E402

OUT_DIR = ROOT / "eval"
B0_TAKE = 4       # 从 naive 结果取前几篇（低相关区样本）
TOP_TAKE = 10     # B1/B3 各取前几篇
CAP = 16          # 每主题候选上限
ABSTRACT_CHARS = 700


def uid_of(paper) -> str:
    key = paper.doi or paper.url or paper.title.lower()
    return "p" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


def search_all(query: str, http: Http, per_query: int, notes: list[str]):
    found = []
    for name, func in (
        ("Crossref", lambda: crossref_search(query, rows=per_query, year_from=2019, http=http)),
        ("DOAJ", lambda: doaj_search(query, page_size=per_query, year_from=2019, http=http)),
        ("arXiv", lambda: arxiv_search(query, max_results=per_query, http=http)),
    ):
        try:
            batch = func()
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{name}: {str(exc)[:60]}")
            continue
        found.extend(batch)
        time.sleep(0.8)
    return found


def plan_queries(topic: str, llm: ChatLLM):
    from lit_agent.graph import _english_query, _has_core_term, _normalize_query

    data = llm.chat_json(prompts.QUERY_PLANNER_SYSTEM, prompts.query_planner_user(topic), max_tokens=320)
    queries = [q for q in (_english_query(_normalize_query(x)) for x in (data.get("queries") or [])) if q][:3]
    core = [c.strip() for c in (data.get("core_terms") or []) if c and c.strip()]
    domain = [d.strip() for d in (data.get("domain_terms") or []) if d and d.strip()]
    if core:
        queries = [q if _has_core_term(q, core) else _normalize_query(f"{core[0]} {q}") for q in queries]
    return queries, core, domain


def main() -> int:
    topics = json.loads((OUT_DIR / "topics.json").read_text(encoding="utf-8"))["topics"]
    settings = Settings.load()
    llm = ChatLLM(settings)
    print("LLM:", llm.ping())
    http = Http()

    out_topics = []
    for index, item in enumerate(topics, 1):
        tid, topic = item["id"], item["topic"]
        core, domain = item["core_terms"], item["domain_terms"]
        notes: list[str] = []
        print(f"[{index}/{len(topics)}] {tid}: {topic}")

        # B0：中文主题直接检索（naive 低相关区样本）
        b0_pool = dedupe(search_all(topic, http, 10, notes))
        b0_top = select_candidates(b0_pool, [topic], TOP_TAKE, mode="off")

        # B1/B2/B3 共享候选池（与 run_eval.py 相同流程）
        queries, llm_core, llm_domain = plan_queries(topic, llm)
        pool: list = []
        for query in queries:
            pool.extend(search_all(query, http, 10, notes))
        pool = dedupe(pool)
        core_used = llm_core or core
        domain_used = llm_domain or domain
        b1_top = select_candidates(pool, [topic], TOP_TAKE, mode="off")
        b2_top = select_candidates(pool, [topic], TOP_TAKE, required_terms=core_used, domain_terms=domain_used, mode="hard")
        b3_top = select_candidates(pool, [topic], TOP_TAKE, required_terms=expand_synonyms(core_used), domain_terms=expand_synonyms(domain_used), mode="soft")

        # 合并候选：B1/B3 的 top10 全部进池（人工口径 P@10 分母必须齐），
        # B0 取前 4 作低相关区样本，B2 独有篇补充视角
        chosen: dict[str, dict] = {}

        def add(paper, bucket: str) -> None:
            uid = uid_of(paper)
            if uid not in chosen:
                chosen[uid] = {
                    "uid": uid,
                    "title": paper.title.strip(),
                    "abstract": (paper.abstract or "").strip()[:ABSTRACT_CHARS],
                    "year": paper.year,
                    "venue": (paper.venue or "")[:60],
                    "doi": paper.doi,
                    "url": paper.url,
                    "sources": [],
                    "in": {"b0": False, "b1": False, "b2": False, "b3": False},
                    "rank": {},
                }
            item = chosen[uid]
            if paper.source and paper.source not in item["sources"]:
                item["sources"].append(paper.source)
            item["in"][bucket] = True
            if bucket not in item["rank"]:
                item["rank"][bucket] = len([x for x in chosen.values() if x["in"].get(bucket)])

        for p in b1_top:
            add(p, "b1")
        for p in b3_top:
            add(p, "b3")
        for p in b0_top[:B0_TAKE]:
            add(p, "b0")
        for p in b2_top:
            if uid_of(p) not in chosen:
                add(p, "b2")

        items = list(chosen.values())
        # 关键词代理判定（与 run_eval.py 同口径：topics.json 静态词表）
        for it in items:
            text = f"{it['title']} {it['abstract']}".lower()
            core_hit = any(t.lower() in text for t in core)
            domain_hit = any(t.lower() in text for t in domain)
            it["rule"] = {"core": core_hit, "domain": domain_hit, "both": core_hit and domain_hit}

        out_topics.append({
            "id": tid, "topic": topic, "core_terms": core, "domain_terms": domain,
            "queries": queries, "candidates": len(pool), "notes": notes, "items": items,
        })
        print(f"    候选 {len(pool)} -> 采 {len(items)} 篇（B0 {B0_TAKE} + B1/B3 top{TOP_TAKE} 合并）")

    total = sum(len(t["items"]) for t in out_topics)
    result = {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M"),
            "purpose": "人工盲标候选池：合并 B0(naive 前4) / B1(检索式 top10) / B3(soft top10)，金标准三层评测用",
            "rule_note": "rule 字段是关键词代理标注的判定（topics.json 词表），标注时默认隐藏（盲标）",
        },
        "topics": out_topics,
    }
    (OUT_DIR / "human_eval_pool.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n共 {total} 篇候选，已写入 eval/human_eval_pool.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
