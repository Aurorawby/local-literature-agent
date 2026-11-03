"""离线检查：学术检索三个源是否正常返回并成功归一化。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from lit_agent.search import (  # noqa: E402
    Http,
    arxiv_search,
    crossref_search,
    dedupe,
    openalex_search,
    select_candidates,
)

http = Http()
queries = ["large language model credit risk"]  # 英文检索式

for q in queries:
    print("=" * 70)
    print("查询:", q)

    oa = openalex_search(q, per_page=8, year_from=2019, http=http)
    print(f"OpenAlex: {len(oa)} 篇")
    for p in oa[:4]:
        print(f"  - {p.year} | {p.citations:>5} 引用 | {p.title[:70]}")
        print(f"    摘要 {len(p.abstract)} 字 | DOI={p.doi or '无'} | 期刊={p.venue[:40]}")

    arx = arxiv_search(q, max_results=5, http=http)
    print(f"arXiv: {len(arx)} 篇")
    for p in arx[:3]:
        print(f"  - {p.year} | {p.title[:70]} | pdf={bool(p.pdf_url)}")
        print(f"    摘要 {len(p.abstract)} 字")

    cr = crossref_search(q, rows=5, year_from=2019, http=http)
    print(f"Crossref: {len(cr)} 篇")
    for p in cr[:2]:
        print(f"  - {p.year} | {p.title[:70]} | DOI={p.doi}")

    merged = dedupe(oa + arx + cr)
    print(f"\n去重后合计: {len(merged)} 篇")
    picked = select_candidates(merged, ["large language model", "credit risk"],
                               limit=5)
    print("Top 5 候选:")
    for i, p in enumerate(picked, 1):
        print(f"  [{i}] {p.year} {p.source:9s} {p.title[:60]}")
