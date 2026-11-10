"""文献综述流水线（LangGraph）。

流程::

    plan_queries -> search -> screen -> read -> reflect ─┬─> search (下一轮)
                                                        └─> synthesize -> finalize

设计思路（也是这个作品集的"方法论"卖点）
--------------------------------------
1. **先规划检索式再检索**：把中文主题翻译成学术英文检索式，而不是直接拿口语词去搜；
2. **多源检索 + 去重**：OpenAlex（期刊）、arXiv（预印本）、Crossref（DOI 补全）；
3. **显式筛选环节**：候选论文先打分、再让模型挑，避免把不相关论文喂给总结环节
   ——原版 local-deep-researcher 就是因为缺这一步才会把词典网页当成资料；
4. **逐篇结构化精读**：每篇压成 7 个字段的卡片，而不是直接塞全文摘要；
5. **反思迭代**：从卡片里找知识缺口，再补一轮检索；
6. **确定性参考文献**：引用信息来自检索元数据，不让模型自己编。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from langgraph.graph import END, START, StateGraph

from . import prompts
from .config import Settings
from .llm import ChatLLM, LLMError
from .search import (
    Http,
    Paper,
    SourceRateLimited,
    arxiv_search,
    crossref_search,
    dedupe,
    doaj_search,
    expand_synonyms,
    openalex_search,
    relevance_score,
    select_candidates,
)
from .verify import render_section as render_verification_section
from .verify import verify_citations

Log = Callable[[str], None]

STATE_FIELDS = (
    "topic language keywords queries all_queries round candidates selected "
    "papers seen_ids extracts gaps draft report"
).split()


@dataclass
class ReviewState:
    """图的状态。全部用基础类型，方便序列化与调试。"""

    topic: str
    language: str = "zh"
    keywords: list[str] = field(default_factory=list)
    core_terms: list[str] = field(default_factory=list)
    domain_terms: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    all_queries: list[str] = field(default_factory=list)
    round: int = 0
    candidates: list[dict] = field(default_factory=list)
    selected: list[dict] = field(default_factory=list)
    papers: list[dict] = field(default_factory=list)
    seen_ids: list[str] = field(default_factory=list)
    extracts: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    draft: str = ""
    verification: dict = field(default_factory=dict)
    report: str = ""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _normalize_query(query: str) -> str:
    q = (query or "").replace("_", " ").replace('"', "").replace("'", "")
    q = " ".join(q.split())
    return q[:120]


_CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


_CORE_ALIASES = {
    "large language model": ("llm", "language model", "gpt", "foundation model"),
    "llm": ("large language model", "language model", "gpt"),
    "gpt": ("large language model", "llm", "language model"),
    "transformer": ("bert", "attention", "llm", "language model"),
}


def _has_core_term(query: str, core_terms: list[str]) -> bool:
    """检索式里是否已经包含核心概念（含常见别名，避免重复拼接）。"""
    q = (query or "").lower()
    for term in core_terms:
        t = term.lower()
        if t and t in q:
            return True
        for alias in _CORE_ALIASES.get(t, ()):
            if alias in q:
                return True
    return False


def _english_query(text: str) -> str:
    """只保留英文单词，至少 2 个词才算有效检索式。

    小模型经常生成 "large language model 金融风控" 这种中英混杂的检索式，
    学术库基本搜不到东西，这里直接清洗掉。
    """
    text = _CJK_RE.sub(" ", text or "")
    text = re.sub(r"[^A-Za-z0-9\-\s]", " ", text)
    text = " ".join(text.split())
    return text if len(text.split()) >= 2 else ""


def _paper_ids(paper: Paper | dict) -> list[str]:
    if isinstance(paper, dict):
        pid, doi, title = paper.get("paper_id", ""), paper.get("doi", ""), paper.get("title", "")
    else:
        pid, doi, title = paper.paper_id, paper.doi, paper.title
    ids = []
    if pid:
        ids.append(pid.lower())
    if doi:
        ids.append(f"doi:{doi.lower()}")
    if title:
        ids.append(f"t:{''.join(c for c in title.lower() if c.isalnum())}")
    return ids


def _card_text(index: int, extract: dict) -> str:
    """把一张精读卡片变成给合成环节看的文本。"""
    return (
        f"[{index}] {extract.get('title', '')} ({extract.get('year') or 'n.d.'}, "
        f"{extract.get('venue') or '未知来源'})\n"
        f"  - 一句话: {extract.get('one_liner', '')}\n"
        f"  - 问题: {extract.get('problem', '')}\n"
        f"  - 方法: {extract.get('method', '')}\n"
        f"  - 数据: {extract.get('data', '')}\n"
        f"  - 发现: {extract.get('findings', '')}\n"
        f"  - 局限: {extract.get('limitations', '')}\n"
        f"  - 相关性: {extract.get('relevance', '')}"
    )


def _default_extract(paper: dict) -> dict:
    """模型抽取失败时的兜底卡片：至少保留标题和摘要要点。"""
    abstract = (paper.get("abstract") or "").strip()
    return {
        "one_liner": (abstract[:100] or paper.get("title", "") or "摘要缺失"),
        "problem": "（模型解析失败，见摘要原文）",
        "method": "",
        "data": "",
        "findings": abstract[:200],
        "limitations": "",
        "relevance": "",
    }


# --------------------------------------------------------------------------
# 建图
# --------------------------------------------------------------------------
def build_graph(settings: Settings, llm: ChatLLM, log: Log = print):
    """构造并编译 LangGraph 图。"""
    http = Http()

    # ---------------------------------------------------------------- plan
    def plan_queries(state: ReviewState) -> dict:
        log("① 规划检索式 ...")
        started = time.perf_counter()
        try:
            data = llm.chat_json(
                prompts.QUERY_PLANNER_SYSTEM,
                prompts.query_planner_user(state.topic),
                max_tokens=320,
            )
            raw_queries = data.get("queries") or []
            queries = [q for q in (_english_query(_normalize_query(x)) for x in raw_queries) if q][:3]
            keywords = [k.strip() for k in (data.get("keywords") or []) if k and k.strip()]
            core_terms = [
                c.strip() for c in (data.get("core_terms") or [])
                if c and c.strip() and re.search(r"[A-Za-z]", c)
            ]
            domain_terms = [
                c.strip() for c in (data.get("domain_terms") or [])
                if c and c.strip() and re.search(r"[A-Za-z]", c)
            ]
            zh_query = _normalize_query(data.get("zh_query") or "")
        except (LLMError, ValueError) as exc:
            log(f"   ! 检索式规划失败，改用原主题检索：{exc}")
            queries, keywords, zh_query, core_terms, domain_terms = [], [state.topic], "", [], []

        # 模型没给出可用的英文检索式时兜底：用核心概念 / 原主题
        if not queries:
            base = core_terms[0] if core_terms else _english_query(state.topic)
            queries = [base or state.topic]

        # 硬约束：每条检索式都必须包含核心概念，否则补上。
        # 否则模型容易生成 "financial risk management" 这种丢掉主题词的泛检索式，
        # 搜回来一堆与主题无关的论文（原版网页工具踩过的坑）。
        if core_terms:
            fixed: list[str] = []
            for q in queries:
                if not _has_core_term(q, core_terms):
                    q = _normalize_query(f"{core_terms[0]} {q}")
                    log(f"   修正检索式（缺少核心概念）: {q}")
                fixed.append(q)
            queries = fixed

        # 补齐到 3 条英文检索式
        if len(queries) < 3 and core_terms:
            base = core_terms[0]
            for kw in keywords:
                kw_en = _english_query(kw)
                if not kw_en:
                    continue
                candidate = f"{base} {kw_en}"
                if candidate not in queries:
                    queries.append(candidate)
                if len(queries) >= 3:
                    break

        if zh_query and state.language == "zh":
            queries.append(zh_query)
        if not queries:
            queries = [state.topic]
        if not keywords:
            keywords = [w for w in state.topic.replace("，", " ").replace(",", " ").split() if len(w) > 1][:6]

        log(f"   检索式: {queries}")
        log(f"   核心概念: {core_terms}")
        log(f"   应用领域: {domain_terms}")
        log(f"   关键词: {keywords}")
        log(f"   耗时 {time.perf_counter() - started:.1f}s")
        return {
            "queries": queries,
            "all_queries": queries,
            "keywords": keywords,
            "core_terms": core_terms,
            "domain_terms": domain_terms,
            "round": 0,
        }

    # --------------------------------------------------------------- search
    def search(state: ReviewState) -> dict:
        round_no = state.round + 1
        log(f"② 第 {round_no} 轮检索（{len(state.queries)} 条检索式）...")
        started = time.perf_counter()

        found: list[Paper] = []
        disabled: set[str] = set()

        def try_source(name: str, func, query: str) -> list[Paper]:
            try:
                batch = func(query)
            except SourceRateLimited as exc:
                disabled.add(name)
                log(f"     - {name:12s} 跳过（{str(exc)[:60]}）")
                return []
            except Exception as exc:  # noqa: BLE001
                log(f"     - {name:12s} 出错：{exc}")
                return []
            log(f"     - {name:12s} {len(batch)} 篇")
            return batch

        for i, query in enumerate(state.queries):
            log(f"   检索式：{query}")
            if "Crossref" not in disabled:
                found += try_source("Crossref", lambda q: crossref_search(
                    q, rows=settings.openalex_per_query, year_from=settings.year_from,
                    mailto=settings.mailto, http=http), query)
            if "DOAJ" not in disabled:
                found += try_source("DOAJ", lambda q: doaj_search(
                    q, page_size=settings.openalex_per_query, year_from=settings.year_from,
                    http=http), query)
            if "arXiv" not in disabled and settings.include_preprints:
                found += try_source("arXiv", lambda q: arxiv_search(
                    q, max_results=settings.arxiv_per_query, http=http), query)
            if settings.openalex_key and "OpenAlex" not in disabled:
                found += try_source("OpenAlex", lambda q: openalex_search(
                    q, per_page=settings.openalex_per_query, year_from=settings.year_from,
                    mailto=settings.mailto, api_key=settings.openalex_key, http=http), query)
            if i < len(state.queries) - 1:
                time.sleep(3.0)  # arXiv 官方建议两次请求间隔 3 秒

        if not settings.include_preprints:
            log("   （默认不检索 arXiv 预印本；综述引用只用正式发表来源）")
        if not settings.openalex_key:
            log("   （未配置 LIT_OPENALEX_KEY，已跳过 OpenAlex；Crossref+DOAJ 已覆盖）")

        log(f"   共拿到 {len(found)} 条结果，去重 ...")
        unique = dedupe(found)

        seen = set(state.seen_ids)
        fresh = [p for p in unique if not (set(_paper_ids(p)) & seen)]

        terms = list(state.keywords) + [state.topic]
        # v2：同义词扩展 + 软惩罚，避免"换了个说法就被淘汰"
        required = expand_synonyms(state.core_terms)
        domain = expand_synonyms(state.domain_terms)
        candidates = select_candidates(
            fresh, terms, settings.candidates_per_round,
            required_terms=required,
            domain_terms=domain,
            mode=settings.constraint_mode,
        )

        log(f"   去重后 {len(unique)} 篇，新增 {len(fresh)} 篇，候选 {len(candidates)} 篇，"
            f"约束模式 {settings.constraint_mode}，耗时 {time.perf_counter() - started:.1f}s")
        return {
            "round": round_no,
            "candidates": [p.to_dict() for p in candidates],
            "seen_ids": state.seen_ids + [pid for p in unique for pid in _paper_ids(p)],
            "all_queries": state.all_queries + [
                q for q in state.queries if q not in state.all_queries
            ],
        }

    # --------------------------------------------------------------- screen
    def screen(state: ReviewState) -> dict:
        candidates = state.candidates
        want = settings.papers_per_round
        log(f"③ 筛选：从 {len(candidates)} 篇候选里挑 {want} 篇精读 ...")
        started = time.perf_counter()

        if not candidates:
            log("   ! 本轮没有候选论文")
            return {"selected": []}

        if len(candidates) <= want:
            selected = candidates
        else:
            catalog = "\n".join(
                f"[{i + 1}] {c.get('title', '')[:110]} | {c.get('year') or '?'} | "
                f"{c.get('venue', '')[:40]} | {c.get('source', '')} | "
                f"引用 {c.get('citations', 0)} | {(c.get('abstract') or '')[:240]}"
                for i, c in enumerate(candidates)
            )
            system = (
                "你是文献筛选助手。根据用户的研究主题，从候选论文里挑出最相关的论文。"
                f"规则：只挑 {want} 篇；论文必须同时满足两个条件："
                f"① 讨论核心概念（{'、'.join(state.core_terms) or state.topic}）；"
                f"② 落在应用领域内（{'、'.join(state.domain_terms) or state.topic}）。"
                "只涉及核心概念但领域完全无关的论文不要选；"
                "优先正式发表的期刊论文/综述/原创研究；"
                "优先近五年、有 DOI、期刊来源清楚、摘要与主题直接相关的论文；"
                "只输出 JSON：{\"selected\": [编号, 编号, ...]}，编号从 1 开始。"
            )
            user = f"研究主题：{state.topic}\n\n候选论文：\n{catalog}"
            try:
                data = llm.chat_json(system, user, max_tokens=120)
                indices = [int(i) - 1 for i in (data.get("selected") or [])]
                picked = [candidates[i] for i in indices if 0 <= i < len(candidates)]
                if not picked:
                    raise ValueError("模型没有选出有效编号")
                # 用规则排序补齐，保证数量
                picked_ids = {id(x) for x in picked}
                rest = [c for c in candidates if id(c) not in picked_ids]
                selected = (picked + rest)[:want]
            except (LLMError, ValueError, TypeError) as exc:
                log(f"   ! 模型筛选失败，改用规则排序：{exc}")
                selected = candidates[:want]

        for i, paper in enumerate(selected, 1):
            log(f"   [{i}] {paper.get('title', '')[:80]} ({paper.get('year') or '?'}, "
                f"{paper.get('cited_by_count', paper.get('citations', 0))} 引用)")
        log(f"   耗时 {time.perf_counter() - started:.1f}s")
        return {"selected": selected}

    # ----------------------------------------------------------------- read
    def read_papers(state: ReviewState) -> dict:
        selected = state.selected
        log(f"④ 逐篇精读 {len(selected)} 篇 ...")
        started = time.perf_counter()

        offset = len(state.extracts)
        new_extracts: list[dict] = []
        for i, paper in enumerate(selected):
            index = offset + i + 1
            title = paper.get("title", "")
            log(f"   [{index}] {title[:70]} ...")
            t0 = time.perf_counter()
            abstract = (paper.get("abstract") or "").strip()
            if len(abstract) < 80:
                log("     ! 没有可用摘要，跳过模型抽取（可能是付费墙论文）")
                extract = _default_extract(paper)
            else:
                try:
                    data = llm.chat_json(
                        prompts.EXTRACT_SYSTEM_ZH if settings.is_chinese else prompts.EXTRACT_SYSTEM_EN,
                        prompts.extract_user(
                            state.topic, title, paper.get("venue", ""), paper.get("year"),
                            abstract, settings.language,
                        ),
                        max_tokens=420,
                    )
                    extract = {k: str(data.get(k, "") or "").strip() for k in (
                        "one_liner", "problem", "method", "data", "findings",
                        "limitations", "relevance",
                    )}
                    if not extract["one_liner"]:
                        extract["one_liner"] = abstract[:80]
                    if not any(extract.values()):
                        raise ValueError("空抽取结果")
                except (LLMError, ValueError) as exc:
                    log(f"     ! 抽取失败，使用兜底卡片：{exc}")
                    extract = _default_extract(paper)

            extract.update({
                "index": index,
                "paper_id": paper.get("paper_id", ""),
                "title": title,
                "year": paper.get("year"),
                "venue": paper.get("venue", ""),
                "doi": paper.get("doi", ""),
                "url": paper.get("url", ""),
                "authors": paper.get("authors", []),
                "citations": paper.get("citations", 0),
                # 保留摘要，供"引用核验"环节作为证据使用
                "abstract": abstract[:1200],
            })
            new_extracts.append(extract)
            log(f"     ✓ {extract['one_liner'][:60]}  ({time.perf_counter() - t0:.1f}s)")

        log(f"   耗时 {time.perf_counter() - started:.1f}s")
        return {
            "papers": state.papers + selected,
            "extracts": state.extracts + new_extracts,
        }

    # -------------------------------------------------------------- reflect
    def reflect(state: ReviewState) -> dict:
        if state.round >= settings.rounds:
            log("⑤ 已达轮数上限，进入综述合成")
            return {"queries": []}

        log(f"⑤ 反思：找知识缺口（第 {state.round}/{settings.rounds} 轮之后）...")
        started = time.perf_counter()
        digest = "\n".join(
            f"[{e['index']}] {e.get('one_liner', '')} | 发现: {e.get('findings', '')}"
            for e in state.extracts[-settings.papers_per_round:]
        )
        titles = [e.get("title", "") for e in state.extracts]

        try:
            data = llm.chat_json(
                prompts.REFLECT_SYSTEM_ZH if settings.is_chinese else prompts.REFLECT_SYSTEM_EN,
                prompts.reflect_user(state.topic, digest, titles, settings.language),
                max_tokens=300,
            )
            gaps = [str(g).strip() for g in (data.get("gaps") or []) if str(g).strip()]
            queries = [q for q in (_english_query(_normalize_query(x))
                                   for x in (data.get("queries") or [])) if q]
        except (LLMError, ValueError) as exc:
            log(f"   ! 反思失败，跳过追加检索：{exc}")
            gaps, queries = [], []

        # 同样的硬约束：补检检索式必须同时含核心概念和领域词，否则会搜回无关论文
        fixed: list[str] = []
        for q in queries[:2]:
            if state.core_terms and not _has_core_term(q, state.core_terms):
                q = _normalize_query(f"{state.core_terms[0]} {q}")
            if state.domain_terms and not any(t.lower() in q.lower() for t in state.domain_terms):
                q = _normalize_query(f"{q} {state.domain_terms[0]}")
            fixed.append(q)
        queries = fixed
        if not queries:
            log("   没有产生新检索式，进入综述合成")
        else:
            log(f"   知识缺口: {gaps}")
            log(f"   下一轮检索式: {queries}")
        log(f"   耗时 {time.perf_counter() - started:.1f}s")
        return {"gaps": state.gaps + gaps, "queries": queries}

    def route_after_reflect(state: ReviewState) -> str:
        if state.queries and state.round < settings.rounds:
            return "search"
        return "synthesize"

    # ------------------------------------------------------------ synthesize
    def synthesize(state: ReviewState) -> dict:
        if not state.extracts:
            return {"draft": "（没有检索到可用论文，无法生成综述。）"}
        log("⑥ 合成综述 ...")
        started = time.perf_counter()
        cards = [_card_text(e["index"], e) for e in state.extracts]
        system = prompts.SYNTH_SYSTEM_ZH if settings.is_chinese else prompts.SYNTH_SYSTEM_EN
        user = prompts.synth_user(state.topic, cards, settings.language)

        try:
            draft = llm.chat(system, user, max_tokens=1800, temperature=0.3)
            # 小模型有时会一个引用都不写，这里强制补一次
            if not re.search(r"\[\d+\]", draft):
                log("   ! 正文完全没有 [编号] 引用，要求模型重写一次 ...")
                draft = llm.chat(
                    system,
                    user + "\n\n注意：上一次生成的正文没有任何 [编号] 引用。"
                           "请重写，并在每个具体事实、方法、数字后面标注 [编号]。",
                    max_tokens=1800,
                    temperature=0.2,
                )
        except LLMError as exc:
            log(f"   ! 综述合成失败：{exc}")
            draft = "（综述合成失败，请查看下方论文卡片。）"
        log(f"   耗时 {time.perf_counter() - started:.1f}s，正文 {len(draft)} 字")
        return {"draft": draft.strip()}
    # --------------------------------------------------- verify (引用核验)
    def verify_refs(state: ReviewState) -> dict:
        if not settings.verify_citations:
            log("⑦ 跳过引用核验（LIT_VERIFY_CITATIONS=false）")
            return {"verification": {"ok": False, "reason": "已关闭"}}

        log("⑦ 引用核验：编号有效性 + 数字回查" + (" + 语义抽检" if settings.verify_semantic else "") + " ...")
        started = time.perf_counter()
        try:
            result = verify_citations(
                state.draft,
                state.extracts,
                llm if settings.verify_semantic else None,
                log,
                semantic_sentences=settings.verify_max_sentences if settings.verify_semantic else 0,
                language=settings.language,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"   ! 引用核验失败：{exc}")
            return {"verification": {"ok": False, "reason": str(exc)}}

        metrics = result.get("metrics") or {}
        if metrics:
            log(
                f"   引用 {metrics.get('citation_marks', 0)} 处"
                f"（有效 {metrics.get('valid_citation_marks', 0)} / "
                f"无效 {metrics.get('invalid_citation_marks', 0)}），"
                f"数字回查 {metrics.get('numbers_found', 0)}/{metrics.get('numbers_checked', 0)}"
                + (f"，数字可追溯率 {metrics.get('number_rate')}%" if metrics.get("number_rate") is not None else "")
            )
        log(f"   耗时 {time.perf_counter() - started:.1f}s")
        return {"verification": result}

    # ------------------------------------------------------------- finalize
    def finalize(state: ReviewState) -> dict:
        log("⑧ 生成 Markdown 报告 ...")
        return {"report": _render_report(settings, state)}

    builder = StateGraph(ReviewState)
    builder.add_node("plan_queries", plan_queries)
    builder.add_node("search", search)
    builder.add_node("screen", screen)
    builder.add_node("read", read_papers)
    builder.add_node("reflect", reflect)
    builder.add_node("synthesize", synthesize)
    builder.add_node("verify_refs", verify_refs)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "plan_queries")
    builder.add_edge("plan_queries", "search")
    builder.add_edge("search", "screen")
    builder.add_edge("screen", "read")
    builder.add_edge("read", "reflect")
    builder.add_conditional_edges(
        "reflect", route_after_reflect,
        {"search": "search", "synthesize": "synthesize"},
    )
    builder.add_edge("synthesize", "verify_refs")
    builder.add_edge("verify_refs", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


# --------------------------------------------------------------------------
# 报告渲染
# --------------------------------------------------------------------------
def _md_cell(text: str, limit: int = 60) -> str:
    text = (text or "").replace("|", "／").replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _render_report(settings: Settings, state: ReviewState) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    extracts = state.extracts
    years = [e.get("year") for e in extracts if e.get("year")]

    lines: list[str] = []
    lines.append(f"# {state.topic} —— 文献综述")
    lines.append("")
    metrics = (state.verification or {}).get("metrics") or {}
    rate = metrics.get("number_rate")
    extra = f" ｜ 数字可追溯率 {rate}%" if rate is not None else ""
    lines.append(
        f"> 自动生成于 {now} ｜ 模型 `{settings.provider}/{settings.model}` ｜ "
        f"覆盖 {len(extracts)} 篇文献"
        + (f" ｜ 年份 {min(years)}–{max(years)}" if years else "")
        + extra
    )
    lines.append("")

    lines.append("## 一、综述正文")
    lines.append("")
    lines.append(state.draft or "（无内容）")
    lines.append("")

    lines.append("## 二、核心文献对比")
    lines.append("")
    lines.append("| # | 论文 | 年份 | 来源 | 方法 | 主要发现 |")
    lines.append("|---|------|------|------|------|----------|")
    for e in extracts:
        lines.append(
            f"| [{e['index']}] | {_md_cell(e.get('title'), 46)} | {e.get('year') or '—'} | "
            f"{_md_cell(e.get('venue'), 26)} | {_md_cell(e.get('method'), 34)} | "
            f"{_md_cell(e.get('findings'), 44)} |"
        )
    lines.append("")

    lines.append("## 三、研究空白与下一步")
    lines.append("")
    if state.gaps:
        for gap in dict.fromkeys(state.gaps):
            lines.append(f"- {gap}")
    else:
        lines.append("- （本轮未生成明确的知识缺口描述，建议人工补充）")
    lines.append("")

    lines.append("## 四、参考文献")
    lines.append("")
    for e in extracts:
        paper = Paper(
            paper_id=e.get("paper_id", ""),
            title=e.get("title", ""),
            authors=e.get("authors", []) or [],
            year=e.get("year"),
            venue=e.get("venue", ""),
            doi=e.get("doi", ""),
            url=e.get("url", ""),
        )
        lines.append(f"[{e['index']}] {paper.reference()}")
    lines.append("")

    # 五、引用核验（有结果才渲染）
    verification = state.verification or {}
    if verification.get("ok"):
        lines.append(render_verification_section(verification, extracts).rstrip())
        lines.append("")

    lines += [
        "---",
        "",
        "### 方法与局限说明",
        "",
        f"- **检索来源**：Crossref（期刊）、DOAJ（开放获取期刊）、arXiv（预印本）"
        + ("、OpenAlex（全学科）" if settings.openalex_key else ""),
        f"- **年份范围**：{settings.year_from or '不限'} 至今",
        f"- **检索式**：{'；'.join(state.all_queries)}",
        f"- **流程**：检索 → 相关性筛选 → 逐篇结构化精读 → 主题合成 → 反思补检"
        f"（共 {state.round} 轮）→ 引用核验",
        "- **引用核验**：编号有效性与数字回查均为**确定性检查**（不依赖模型判断）；"
        + (f"另对 {settings.verify_max_sentences} 句做语义抽检（小模型判定，仅供参考）。"
           if settings.verify_semantic else "语义抽检默认关闭。"),
        "- **局限**：精读基于论文摘要，可能遗漏正文细节；AI 抽取的结论需回到原文核对；"
        "未覆盖知网等需要机构权限的中文数据库。",
        "",
    ]
    return "\n".join(lines)
