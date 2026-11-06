"""引用审计的确定性自测：不用模型、不用网络，3 秒跑完。

覆盖：
* 综述级审计：有效/无效引用、数字缺失、未被引用文献、Markdown/HTML 渲染；
* 对话级审计：观点 → 原文句子 + 页码；没有依据时标记高风险。

运行：
    .venv\\Scripts\\python.exe eval/audit_selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.audit import build_chat_audit, build_review_audit, render_html, render_markdown  # noqa: E402

REPORT = "已有研究表明，该方法将准确率提升到 92.5% [1]。但样本量 1200 的数字没有出现在来源里 [2]。不存在的引用 [9]。"
EXTRACTS = [
    {
        "index": 1, "paper_id": "p1", "title": "Paper One", "authors": ["Zhang San"],
        "year": 2024, "venue": "Journal A", "doi": "10.1234/a", "url": "",
        "abstract": "This paper reports accuracy 92.5% on a benchmark.", "one_liner": "",
        "method": "", "data": "", "findings": "",
    },
    {
        "index": 2, "paper_id": "p2", "title": "Paper Two", "authors": ["Li Si"],
        "year": 2023, "venue": "Journal B", "doi": "", "url": "https://example.com/b",
        "abstract": "The effect is stable.", "one_liner": "", "method": "", "data": "", "findings": "",
    },
]
VERIFICATION = {
    "metrics": {
        "sentences_with_citation": 3, "citation_marks": 3, "valid_citation_marks": 2,
        "invalid_citation_marks": 1, "reference_count": 2, "uncited_references": [],
        "uncited_reference_count": 0, "numbers_checked": 2, "numbers_found": 1,
        "numbers_missing": 1, "number_rate": 50.0,
    },
    "items": [
        {"sentence": "已有研究表明，该方法将准确率提升到 92.5% [1]。", "indices": [1],
         "numbers": ["92.5%"], "missing_numbers": [], "semantic": []},
        {"sentence": "但样本量 1200 的数字没有出现在来源里 [2]。", "indices": [2],
         "numbers": ["1200"], "missing_numbers": ["1200"], "semantic": []},
        {"sentence": "不存在的引用 [9]。", "indices": [9],
         "numbers": [], "missing_numbers": [], "semantic": []},
    ],
}


def main() -> int:
    audit = build_review_audit(REPORT, EXTRACTS, VERIFICATION, {"topic": "audit-selftest"})
    assert audit["kind"] == "review"
    assert len(audit["claims"]) == 3, audit["claims"]
    assert audit["claims"][0]["sources"][0]["doi"] == "10.1234/a"
    assert audit["claims"][1]["missing_numbers"] == ["1200"]
    assert audit["claims"][2]["invalid_citations"] == [9]
    assert audit["metrics"]["invalid_citation_marks"] == 1
    assert audit["status"] == "需要人工复核"

    md = render_markdown(audit)
    html = render_html(audit)
    assert "无效编号" in md
    assert "未在来源找到" in md
    assert "引用审计报告" in html
    assert "920" not in html  # 防止把 92.5 误渲染成别的

    chat = build_chat_audit(
        {"title": "Paper X", "doi": "10.1/x", "url": ""},
        {"claims": [
            {"claim": "观点 A", "quotes": [{"page": 3, "sentence": "原文句子 A"}], "usage": "", "caveat": ""},
            {"claim": "观点 B（无依据）", "quotes": [], "usage": "", "caveat": ""},
        ]},
        {"model": "selftest"},
    )
    assert chat["kind"] == "chat"
    assert chat["metrics"]["claims"] == 2
    assert chat["metrics"]["claims_with_quote"] == 1
    assert chat["metrics"]["pages"] == [3]
    assert chat["status"] == "需要人工复核"
    assert "p3" in render_markdown(chat)

    print("✓ audit selftest passed: review 3 claims / chat 2 claims / issues detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
