"""引用审计报告：把“引用核验”从控制台日志变成可导出的逐条证据表。

两个入口：

* :func:`build_review_audit`：综述正文 → 每个带 ``[n]`` 的句子 → 对应论文元数据、
  摘要/精读卡片摘录、数字回查结果、语义抽检结果；
* :func:`build_chat_audit`：论文对话「提炼观点」→ 每个观点 → 原文句子 + 页码。

输出 Markdown + 自包含 HTML，适合直接发给导师/面试官，也适合打印。

诚实边界：综述阶段的证据来自**摘要/精读卡片**（不是全文页码）；页码级证据只在
论文对话（全文解析）里存在。报告会明确区分 ``evidence_kind``，不冒充全文核对。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .cli import safe_filename
from .config import PROJECT_ROOT
from .search import Paper
from .verify import (
    VERDICT_PARTIAL,
    VERDICT_SUPPORT,
    VERDICT_UNCOVERED,
    VERDICT_UNSUPPORTED,
    extract_numbers,
    split_cited_sentences,
)

# 证据来源类型：title 用于报告里的“证据类型”说明
EVIDENCE_ABSTRACT = "摘要/精读卡片摘录（非全文页码）"
EVIDENCE_PAGE = "原文句子 + 页码（全文解析）"

STATUS_OK = "确定性检查通过"
STATUS_REVIEW = "需要人工复核"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _excerpt(extract: dict[str, Any], numbers: list[str]) -> str:
    """从摘要/卡片中取一段与数字最相关的证据；没有数字就取开头。"""
    fields = [
        ("摘要", _text(extract.get("abstract"))),
        ("精读卡片", _text(extract.get("one_liner"))),
        ("主要发现", _text(extract.get("findings"))),
        ("方法", _text(extract.get("method"))),
    ]
    for label, text in fields:
        if not text:
            continue
        if numbers:
            for number in numbers:
                raw = re.sub(r"[%％倍万亿]", "", number)
                pos = text.find(raw)
                if pos >= 0:
                    start = max(0, pos - 110)
                    end = min(len(text), pos + 180)
                    return f"{label}：…{text[start:end]}…"
        return f"{label}：{text[:260]}{'…' if len(text) > 260 else ''}"
    return "（没有可展示的摘要/卡片证据）"


def _paper_ref(extract: dict[str, Any]) -> dict[str, Any]:
    paper = Paper(
        paper_id=str(extract.get("paper_id", "")),
        title=str(extract.get("title", "")),
        authors=list(extract.get("authors") or []),
        year=extract.get("year"),
        venue=str(extract.get("venue", "")),
        doi=str(extract.get("doi", "")),
        url=str(extract.get("url", "")),
    )
    doi = _text(paper.doi)
    return {
        "index": int(extract["index"]),
        "title": _text(paper.title),
        "authors": paper.authors,
        "year": paper.year,
        "venue": _text(paper.venue),
        "doi": doi,
        "url": _text(paper.url),
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "reference": paper.reference(),
    }


def _normalise_items(
    report: str, verification: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """优先用 verify_citations 的 items；没有就回退到句级解析。"""
    items = (verification or {}).get("items") or []
    if items:
        out = []
        for item in items:
            out.append({
                "sentence": _text(item.get("sentence")),
                "indices": sorted({int(i) for i in item.get("indices") or []}),
                "numbers": list(item.get("numbers") or []),
                "missing_numbers": list(item.get("missing_numbers") or []),
                "semantic": list(item.get("semantic") or []),
            })
        return out
    out = []
    for sentence, indices in split_cited_sentences(report):
        out.append({
            "sentence": _text(sentence),
            "indices": sorted(set(indices)),
            "numbers": extract_numbers(sentence),
            "missing_numbers": [],
            "semantic": [],
        })
    return out


def _semantic_issue(verdicts: list[dict[str, Any]]) -> bool:
    bad = {
        VERDICT_UNSUPPORTED,
        VERDICT_UNCOVERED,
        VERDICT_PARTIAL,
    }
    return any(str(v.get("verdict", "")) in bad for v in verdicts)


def build_review_audit(
    report: str,
    extracts: list[dict[str, Any]],
    verification: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造综述的引用审计数据。"""
    verification = verification or {}
    meta = dict(meta or {})
    by_index = {int(e["index"]): e for e in extracts if e.get("index") is not None}
    items = _normalise_items(report, verification)
    claims: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    cited: set[int] = set()

    for item in items:
        indices = [i for i in item["indices"] if i in by_index]
        invalid = [i for i in item["indices"] if i not in by_index]
        cited.update(indices)
        if invalid:
            issues.append({
                "type": "无效引用",
                "sentence": item["sentence"],
                "detail": "正文引用了不存在的参考文献编号：" + "、".join(f"[{i}]" for i in invalid),
                "severity": "high",
            })
        if item["missing_numbers"]:
            issues.append({
                "type": "数字未回查到来源",
                "sentence": item["sentence"],
                "detail": "数字 " + "、".join(item["missing_numbers"]) + " 没有在摘要/精读卡片中找到",
                "severity": "high",
            })
        if _semantic_issue(item["semantic"]):
            issues.append({
                "type": "语义抽检需人工看",
                "sentence": item["sentence"],
                "detail": "；".join(
                    f"[{v.get('index')}] {v.get('verdict')}：{v.get('reason', '')}"
                    for v in item["semantic"]
                    if str(v.get("verdict", "")) in {VERDICT_UNSUPPORTED, VERDICT_UNCOVERED, VERDICT_PARTIAL}
                ),
                "severity": "medium",
            })
        sources = []
        for i in indices:
            e = by_index[i]
            sources.append({**_paper_ref(e), "evidence": _excerpt(e, item["numbers"])})
        claims.append({
            "sentence": item["sentence"],
            "citations": indices,
            "invalid_citations": invalid,
            "numbers": item["numbers"],
            "missing_numbers": item["missing_numbers"],
            "semantic": item["semantic"],
            "sources": sources,
        })

    uncited = sorted(i for i in by_index if i not in cited)
    for i in uncited:
        issues.append({
            "type": "参考文献未被正文引用",
            "sentence": "",
            "detail": f"[{i}] {by_index[i].get('title', '')}",
            "severity": "low",
        })

    metrics = dict(verification.get("metrics") or {})
    # 结构性指标以审计明细重算，避免调用方传入的旧 metrics 与 items 不一致。
    checked_numbers = sum(len(item["numbers"]) for item in items)
    missing_numbers = sum(len(item["missing_numbers"]) for item in items)
    metrics.update({
        "sentences_with_citation": len(claims),
        "citation_marks": sum(len(item["indices"]) for item in items),
        "valid_citation_marks": sum(len(c["citations"]) for c in claims),
        "invalid_citation_marks": sum(len(c["invalid_citations"]) for c in claims),
        "reference_count": len(by_index),
        "uncited_references": uncited,
        "uncited_reference_count": len(uncited),
        "numbers_checked": checked_numbers,
        "numbers_found": checked_numbers - missing_numbers,
        "numbers_missing": missing_numbers,
        "number_rate": round((checked_numbers - missing_numbers) / checked_numbers * 100, 1) if checked_numbers else None,
    })

    references = []
    for i, e in sorted(by_index.items()):
        used = sum(1 for c in claims if i in c["citations"])
        references.append({**_paper_ref(e), "cited_in_claims": used, "uncited": used == 0})

    high_issues = [x for x in issues if x["severity"] == "high"]
    status = STATUS_REVIEW if high_issues or issues else STATUS_OK
    return {
        "kind": "review",
        "title": meta.get("topic") or "文献综述",
        "generated_at": meta.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
        "model": meta.get("model", ""),
        "queries": list(meta.get("queries") or []),
        "evidence_kind": EVIDENCE_ABSTRACT,
        "status": status,
        "metrics": metrics,
        "claims": claims,
        "references": references,
        "issues": issues,
        "limits": [
            "综述基于摘要/精读卡片，不是全文页码；数字回查只能证明“来源里出现过这个数字”，不等于证明结论正确。",
            "语义抽检由小模型完成，只作参考，不进主指标；人工必须复核高亮项。",
            "参考文献由检索元数据生成，但元数据本身可能有错（作者/年份/DOI），需要回原文核对。",
        ],
    }


def build_chat_audit(
    paper: dict[str, Any] | Any,
    result: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造论文对话「提炼观点」的页码级审计数据。

    ``paper`` 可以是 Paper 对象，也可以是带 title/doi/url 的 dict；
    ``result`` 是 ``PaperChat.claims()`` 的返回值（含 claims/quotes/page）。
    """
    meta = dict(meta or {})
    if isinstance(paper, dict):
        title = _text(paper.get("title"))
        doi = _text(paper.get("doi"))
        url = _text(paper.get("url"))
    else:
        title = _text(getattr(paper, "title", ""))
        doi = _text(getattr(paper, "doi", ""))
        url = _text(getattr(paper, "url", ""))

    raw_claims = result.get("claims") or []
    claims = []
    issues = []
    for i, item in enumerate(raw_claims, start=1):
        claim = _text(item.get("claim"))
        quotes = [
            {"page": q.get("page"), "sentence": _text(q.get("sentence"))}
            for q in (item.get("quotes") or [])
        ]
        if not quotes:
            issues.append({
                "type": "观点没有原文依据",
                "sentence": claim,
                "detail": f"观点 {i} 没有回填到原文句子；可能出自模型概括，必须人工核对。",
                "severity": "high",
            })
        claims.append({
            "claim": claim,
            "quotes": quotes,
            "usage": _text(item.get("usage")),
            "caveat": _text(item.get("caveat")),
        })

    status = STATUS_REVIEW if issues else STATUS_OK
    pages = sorted({q["page"] for c in claims for q in c["quotes"] if q["page"] is not None})
    return {
        "kind": "chat",
        "title": title or "论文对话",
        "generated_at": meta.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
        "model": meta.get("model", ""),
        "doi": doi,
        "url": url,
        "doi_url": f"https://doi.org/{doi}" if doi else "",
        "evidence_kind": EVIDENCE_PAGE,
        "status": status,
        "metrics": {
            "claims": len(claims),
            "claims_with_quote": sum(1 for c in claims if c["quotes"]),
            "quotes": sum(len(c["quotes"]) for c in claims),
            "pages": pages,
        },
        "claims": claims,
        "issues": issues,
        "limits": [
            "页码来自 PDF 解析，扫描版/双栏 PDF 可能错位；引用前请回原文确认。",
            "原文句子由程序按编号回填，但“观点是否被句子支持”仍需人来判断。",
        ],
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _md_link(text: str, url: str) -> str:
    return f"[{text}]({url})" if url else text


def render_markdown(audit: dict[str, Any]) -> str:
    kind = audit.get("kind")
    lines: list[str] = []
    title = audit.get("title", "")
    lines.append(f"# 引用审计报告 · {title}")
    lines.append("")
    lines.append(
        f"> 生成于 {audit.get('generated_at', '')}"
        + (f" ｜ 模型 `{audit['model']}`" if audit.get("model") else "")
    )
    lines.append(f"> 证据类型：{audit.get('evidence_kind', '')}")
    lines.append(f"> 审计结论：**{audit.get('status', '')}**")
    lines.append("")

    metrics = audit.get("metrics") or {}
    lines.append("## 一、指标摘要")
    lines.append("")
    if kind == "review":
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        rows = [
            ("带引用句子", metrics.get("sentences_with_citation", 0)),
            ("引用标记", metrics.get("citation_marks", 0)),
            ("有效引用", metrics.get("valid_citation_marks", 0)),
            ("无效引用", metrics.get("invalid_citation_marks", 0)),
            ("参考文献", metrics.get("reference_count", 0)),
            ("未被引用文献", metrics.get("uncited_reference_count", len(metrics.get("uncited_references") or []))),
            ("数字回查", f"{metrics.get('numbers_found', 0)}/{metrics.get('numbers_checked', 0)}"),
            ("数字可追溯率", f"{metrics.get('number_rate')}%" if metrics.get("number_rate") is not None else "—"),
            ("需要人工复核项", len(audit.get("issues") or [])),
        ]
        for label, value in rows:
            lines.append(f"| {label} | {value} |")
    else:
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---|")
        lines += [
            f"| 观点 | {metrics.get('claims', 0)} |",
            f"| 有原文依据 | {metrics.get('claims_with_quote', 0)} |",
            f"| 原文引文 | {metrics.get('quotes', 0)} |",
            f"| 涉及页码 | {', '.join('p' + str(p) for p in metrics.get('pages', [])) if metrics.get('pages') else '—'} |",
            f"| 需要人工核对项 | {len(audit.get('issues') or [])} |",
        ]
    lines.append("")

    lines.append("## 二、逐条审计")
    lines.append("")
    if kind == "review":
        for n, claim in enumerate(audit.get("claims") or [], start=1):
            lines.append(f"### {n}. {claim['sentence']}")
            cites = "、".join(f"[{i}]" for i in claim.get("citations") or []) or "（无有效引用）"
            lines.append(f"- 引用：{cites}")
            if claim.get("invalid_citations"):
                lines.append("- ⚠️ 无效编号：" + "、".join(f"[{i}]" for i in claim["invalid_citations"]))
            if claim.get("numbers"):
                lines.append("- 数字：" + "、".join(claim["numbers"]))
            if claim.get("missing_numbers"):
                lines.append("- ⚠️ 未在来源找到：" + "、".join(claim["missing_numbers"]))
            if claim.get("semantic"):
                verdicts = "；".join(
                    f"[{v.get('index')}] {v.get('verdict')}（{v.get('reason', '')}）"
                    for v in claim["semantic"]
                )
                lines.append("- 语义抽检（小模型，仅供参考）：" + verdicts)
            lines.append("")
            if claim.get("sources"):
                lines.append("**来源证据：**")
                for src in claim["sources"]:
                    link = src.get("doi_url") or src.get("url")
                    lines.append(
                        f"- [{src['index']}] {_md_link(src['title'], link)}"
                        f"（{src.get('year') or 'n.d.'}，{src.get('venue') or '—'}）"
                    )
                    lines.append(f"  > {src['evidence']}")
            else:
                lines.append("**来源证据：**（没有匹配到有效参考文献）")
            lines.append("")
    else:
        for n, claim in enumerate(audit.get("claims") or [], start=1):
            lines.append(f"### {n}. {claim['claim']}")
            if claim.get("quotes"):
                lines.append("**原文依据：**")
                for q in claim["quotes"]:
                    lines.append(f"> {q['sentence']}")
                    lines.append(f"> —— [p{q['page']}]")
            else:
                lines.append("**原文依据：**（没有回填到原文，必须人工核对）")
            if claim.get("usage"):
                lines.append(f"**怎么用：** {claim['usage']}")
            if claim.get("caveat"):
                lines.append(f"**注意：** {claim['caveat']}")
            lines.append("")

    if kind == "review":
        lines.append("## 三、参考文献覆盖")
        lines.append("")
        lines.append("| # | 论文 | 年份 | DOI | 正文引用次数 |")
        lines.append("|---|---|---|---|---|")
        for ref in audit.get("references") or []:
            doi = ref.get("doi_url") or ref.get("doi") or "—"
            lines.append(
                f"| [{ref['index']}] | {ref['title']} | {ref.get('year') or '—'} | {doi} | "
                f"{ref.get('cited_in_claims', 0)} |"
            )
        lines.append("")

    issues = audit.get("issues") or []
    lines.append("## 四、问题清单")
    lines.append("")
    if not issues:
        lines.append("未发现结构性问题；但“没有报错”不等于“结论正确”，请人工复核语义与原文。")
    else:
        for issue in issues:
            lines.append(
                f"- **{issue.get('type')}**（{issue.get('severity')}）：{issue.get('detail')}"
                + (f"  \n  句子：{issue.get('sentence')}" if issue.get("sentence") else "")
            )
    lines.append("")

    lines.append("## 五、方法与局限")
    lines.append("")
    for limit in audit.get("limits") or []:
        lines.append(f"- {limit}")
    lines.append("")
    lines.append(
        "> 这份报告是**确定性与抽检结果的汇总**，不是“正确性证明”。"
        "引用审计只能证明引用/数字/来源结构是否一致；结论是否成立仍要人读原文。"
    )
    return "\n".join(lines).strip() + "\n"


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _href(value: str) -> str:
    value = _text(value)
    if value.startswith(("http://", "https://")):
        return _h(value)
    return ""


def render_html(audit: dict[str, Any]) -> str:
    kind = audit.get("kind")
    metrics = audit.get("metrics") or {}
    issues = audit.get("issues") or []
    status = audit.get("status", "")
    status_cls = "ok" if status == STATUS_OK else "warn"

    if kind == "review":
        stat_items = [
            ("带引用句子", metrics.get("sentences_with_citation", 0)),
            ("引用标记", metrics.get("citation_marks", 0)),
            ("有效 / 无效", f"{metrics.get('valid_citation_marks', 0)} / {metrics.get('invalid_citation_marks', 0)}"),
            ("数字回查", f"{metrics.get('numbers_found', 0)} / {metrics.get('numbers_checked', 0)}"),
            ("需复核项", len(issues)),
        ]
    else:
        stat_items = [
            ("观点", metrics.get("claims", 0)),
            ("有原文依据", metrics.get("claims_with_quote", 0)),
            ("原文引文", metrics.get("quotes", 0)),
            ("页码", ", ".join(f"p{p}" for p in metrics.get("pages", [])) or "—"),
            ("需人工核对", len(issues)),
        ]
    stats_html = "".join(
        f'<div class="stat"><b>{_h(v)}</b><span>{_h(k)}</span></div>'
        for k, v in stat_items
    )

    claims_html = []
    if kind == "review":
        for n, claim in enumerate(audit.get("claims") or [], start=1):
            cites = "、".join(f"[{i}]" for i in claim.get("citations") or [])
            rows = []
            for src in claim.get("sources") or []:
                link = src.get("doi_url") or src.get("url")
                title = _h(src.get("title"))
                if link and _href(link):
                    title = f'<a href="{_href(link)}" target="_blank" rel="noopener">{title}</a>'
                rows.append(
                    f"<tr><td>[{_h(src.get('index'))}]</td><td>{title}<br>"
                    f"<span class='muted'>{_h(src.get('year') or 'n.d.')} · {_h(src.get('venue') or '—')}</span></td>"
                    f"<td class='quote'>{_h(src.get('evidence'))}</td></tr>"
                )
            issues_html = ""
            if claim.get("missing_numbers"):
                issues_html += f'<p class="bad">⚠️ 数字未在来源找到：{_h("、".join(claim["missing_numbers"]))}</p>'
            if claim.get("invalid_citations"):
                issues_html += f'<p class="bad">⚠️ 无效引用编号：{_h("、".join(f"[{i}]" for i in claim["invalid_citations"]))}</p>'
            if claim.get("semantic"):
                verdicts = "；".join(
                    f"[{_h(v.get('index'))}] {_h(v.get('verdict'))}（{_h(v.get('reason', ''))}）"
                    for v in claim["semantic"]
                )
                issues_html += f'<p class="muted">小模型语义抽检（仅供参考）：{verdicts}</p>'
            claims_html.append(f"""
<details class="claim" open>
  <summary><span class="num">{n}</span>{_h(claim.get('sentence'))}</summary>
  <div class="claim-body">
    <p><b>引用：</b>{_h(cites) or '（无有效引用）'}</p>
    {issues_html}
    <table class="evidence"><thead><tr><th>#</th><th>来源</th><th>证据摘录</th></tr></thead>
    <tbody>{''.join(rows) or '<tr><td colspan="3">没有匹配到有效参考文献</td></tr>'}</tbody></table>
  </div>
</details>""")
    else:
        for n, claim in enumerate(audit.get("claims") or [], start=1):
            quotes = "".join(
                f"<blockquote>{_h(q.get('sentence'))}<cite>—— p{_h(q.get('page'))}</cite></blockquote>"
                for q in claim.get("quotes") or []
            ) or '<p class="bad">没有回填到原文句子，必须人工核对。</p>'
            extra = ""
            if claim.get("usage"):
                extra += f'<p><b>怎么用：</b>{_h(claim["usage"])}</p>'
            if claim.get("caveat"):
                extra += f'<p><b>注意：</b>{_h(claim["caveat"])}</p>'
            claims_html.append(f"""
<details class="claim" open>
  <summary><span class="num">{n}</span>{_h(claim.get('claim'))}</summary>
  <div class="claim-body">{quotes}{extra}</div>
</details>""")

    issues_html = "".join(
        f'<li><b>{_h(x.get("type"))}</b> <span class="sev {_h(x.get("severity"))}">{_h(x.get("severity"))}</span><br>'
        f'{_h(x.get("detail"))}' + (f'<br><span class="muted">句子：{_h(x.get("sentence"))}</span>' if x.get("sentence") else "") + "</li>"
        for x in issues
    ) or "<li>未发现结构性问题；但请人工复核语义与原文。</li>"

    references_html = ""
    if kind == "review":
        refs = "".join(
            f"<tr><td>[{_h(r.get('index'))}]</td><td>{_h(r.get('title'))}</td>"
            f"<td>{_h(r.get('year') or '—')}</td><td>{_h(r.get('venue') or '—')}</td>"
            f"<td>{_h(r.get('cited_in_claims', 0))}</td>"
            f"<td>{_h(r.get('doi') or '—')}</td></tr>"
            for r in audit.get("references") or []
        )
        references_html = f"""
<section>
<h2>参考文献覆盖</h2>
<table><thead><tr><th>#</th><th>论文</th><th>年份</th><th>来源</th><th>引用次数</th><th>DOI</th></tr></thead>
<tbody>{refs}</tbody></table>
</section>"""

    limits = "".join(f"<li>{_h(x)}</li>" for x in audit.get("limits") or [])
    title = _h(audit.get("title"))
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>引用审计报告 · {title}</title>
<style>
  :root {{ --ink:#171614; --muted:#6c675f; --line:#e7e2d9; --bg:#faf9f7; --card:#fff; --accent:#1f4fd8; --bad:#b42318; --warn:#b45309; --ok:#067647; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:980px; margin:0 auto; padding:44px 22px 70px; }}
  h1 {{ font-size:30px; line-height:1.2; margin:0 0 12px; }}
  h2 {{ font-size:20px; margin:34px 0 12px; }}
  .meta {{ color:var(--muted); font-size:13px; line-height:1.9; }}
  .status {{ display:inline-block; margin:10px 0 0; padding:6px 12px; border-radius:999px; font-weight:700; font-size:13px; }}
  .status.ok {{ background:#e7f6ee; color:var(--ok); }}
  .status.warn {{ background:#fff4e2; color:var(--warn); }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin:16px 0 8px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }}
  .stat b {{ display:block; font-size:20px; }}
  .stat span {{ color:var(--muted); font-size:12px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 20px; }}
  details.claim {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 16px; margin:10px 0; }}
  details.claim summary {{ cursor:pointer; font-weight:650; }}
  .num {{ display:inline-block; min-width:26px; height:26px; line-height:26px; text-align:center; border-radius:8px; background:#eef2ff; color:var(--accent); margin-right:8px; font-size:13px; }}
  .claim-body {{ margin-top:10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; margin:10px 0; }}
  th,td {{ border:1px solid var(--line); padding:7px 8px; vertical-align:top; text-align:left; }}
  th {{ background:#f7f5f0; }}
  td.quote {{ color:#3f3a33; }}
  .muted {{ color:var(--muted); font-size:12px; }}
  .bad {{ color:var(--bad); font-size:13px; }}
  blockquote {{ margin:8px 0; padding:8px 12px; background:#f7f5f0; border-left:3px solid var(--accent); }}
  blockquote cite {{ display:block; color:var(--muted); font-size:12px; margin-top:4px; }}
  .sev {{ font-size:11px; padding:1px 6px; border-radius:999px; border:1px solid var(--line); }}
  .sev.high {{ color:var(--bad); }}
  .sev.medium {{ color:var(--warn); }}
  .sev.low {{ color:var(--muted); }}
  footer {{ margin-top:36px; color:var(--muted); font-size:12px; line-height:1.8; }}
  @media(max-width:640px) {{ .wrap {{ padding:26px 14px 50px; }} h1 {{ font-size:24px; }} table {{ display:block; overflow-x:auto; }} }}
  @media print {{ body {{ background:#fff; }} .wrap {{ max-width:none; padding:0; }} details.claim {{ break-inside:avoid; }} }}
</style>
</head>
<body>
<div class="wrap">
  <p class="muted">Local Literature Agent · 引用审计</p>
  <h1>引用审计报告 · {title}</h1>
  <p class="meta">生成于 {_h(audit.get('generated_at'))}{(' ｜ 模型 ' + _h(audit.get('model'))) if audit.get('model') else ''}<br>
     证据类型：{_h(audit.get('evidence_kind'))}</p>
  <div class="status {status_cls}">{_h(status)}</div>
  <div class="stats">{stats_html}</div>
  <section>
    <h2>逐条审计</h2>
    {''.join(claims_html) or '<p class="muted">没有可审计的条目。</p>'}
  </section>
  {references_html}
  <section>
    <h2>问题清单</h2>
    <ul>{issues_html}</ul>
  </section>
  <section>
    <h2>方法与局限</h2>
    <ul>{limits}</ul>
  </section>
  <footer>
    这份报告汇总确定性检查与抽检结果，不是“正确性证明”。引用审计只能证明引用/数字/来源结构是否一致；
    结论是否成立仍要人读原文。
  </footer>
</div>
</body>
</html>
"""


def write_audit(audit: dict[str, Any], stem: str) -> tuple[Path, Path, Path]:
    """把审计报告写入 output/，返回 (markdown, html, json) 路径。"""
    out_dir = PROJECT_ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"{safe_filename(stem)}-{stamp}"
    md_path = base.with_suffix(".md")
    html_path = base.with_suffix(".html")
    json_path = base.with_suffix(".json")
    md_path.write_text(render_markdown(audit), encoding="utf-8")
    html_path.write_text(render_html(audit), encoding="utf-8")
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, html_path, json_path
