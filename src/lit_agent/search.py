"""学术检索：Crossref / DOAJ / arXiv / OpenAlex。

为什么用这几个源（2026-10 实测）
-------------------------------
* **Crossref**：期刊论文的权威元数据（DOI/期刊/卷期），免 key、限流宽松，
  是"找正式发表的期刊论文"的主源；
* **DOAJ**：开放获取期刊目录，免 key，覆盖各学科开放期刊，补充 Crossref 拿不到的 OA 论文；
* **arXiv**：计算机 / 金融 / 物理预印本，免 key，能拿全文 PDF，最新进展都在这里；
* **OpenAlex**：元数据最全（摘要、引用数、引用关系），但 2025 年后无 key 会消耗
  共享 IP 配额，用尽即 429。填了 ``LIT_OPENALEX_KEY`` 才会启用，否则自动跳过。

四者都不需要付费，也不像知网那样必须机构登录，适合做个人作品集。
"""

from __future__ import annotations

import html
import math
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Iterable
from urllib.parse import quote

import httpx

USER_AGENT = "lit-agent/1.0 (local literature review; https://github.com/)"

_BOILERPLATE = re.compile(
    r"<[^>]+>|^\s*(abstract|摘要)\s*[:：]?\s*", re.IGNORECASE
)


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------
@dataclass
class Paper:
    """统一之后的论文元数据。"""

    paper_id: str
    title: str
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    pdf_url: str = ""
    citations: int = 0
    source: str = ""
    type: str = "article"

    def short_cite(self) -> str:
        """参考文献里的简短引用，如 ``Zhang et al. (2024)``。"""
        if not self.authors:
            who = "Anon."
        elif len(self.authors) == 1:
            who = self.authors[0]
        else:
            who = f"{self.authors[0]} 等" if _has_cjk(self.authors[0]) else f"{self.authors[0]} et al."
        return f"{who} ({self.year or 'n.d.'})"

    def reference(self) -> str:
        """完整参考文献条目（接近 GB/T 7714 / APA 的混合体）。"""
        parts = []
        if self.authors:
            names = ", ".join(self.authors[:6])
            if len(self.authors) > 6:
                names += " 等"
            parts.append(f"{names}.")
        else:
            parts.append("佚名.")
        parts.append(f"{self.title}.")
        if self.venue:
            parts.append(f"{self.venue},")
        parts.append(f"{self.year or 'n.d.'}.")
        if self.doi:
            parts.append(f"DOI: {self.doi}")
        elif self.url:
            parts.append(self.url)
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)  # Crossref/DOAJ 会返回 &amp; 这类 HTML 转义
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())


def _abstract_from_inverted(index: dict[str, list[int]] | None) -> str:
    """OpenAlex 的摘要以"词 -> 位置"倒排索引存储，这里还原成正常句子。"""
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        for pos in spots:
            positions.append((pos, word))
    positions.sort()
    return _clean(" ".join(w for _, w in positions))[:4000]


# --------------------------------------------------------------------------
# HTTP 小工具
# --------------------------------------------------------------------------
class SourceRateLimited(RuntimeError):
    """该检索源当前不可用（配额用尽 / 限流 / 需要 key）。调用方应跳过而不是重试。"""


# --------------------------------------------------------------------------
# 同义词扩展：硬约束误杀同义表达的解药
# --------------------------------------------------------------------------
SYNONYM_MAP: dict[str, tuple[str, ...]] = {
    "large language model": ("llm", "language model", "gpt", "chatgpt", "foundation model"),
    "llm": ("large language model", "language model", "gpt"),
    "gpt": ("large language model", "llm", "language model"),
    "transformer": ("bert", "attention", "language model"),
    "supply chain resilience": ("supply chain robustness", "resilient supply chain", "resilience"),
    "resilience": ("robustness", "adaptive capacity", "recovery"),
    "esg": ("environmental social governance", "sustainability", "csr"),
    "sustainability": ("esg", "sustainable development"),
    "carbon neutrality": ("net zero", "carbon neutral", "decarbonization", "carbon emission"),
    "carbon emission": ("carbon neutrality", "co2 emission", "emission reduction"),
    "medical image segmentation": ("image segmentation", "lesion segmentation", "segmentation"),
    "credit risk": ("credit scoring", "default prediction", "loan default"),
    "fraud detection": ("financial fraud", "fraud", "anomaly detection"),
    "international finance": ("global finance", "cross-border finance", "international financial"),
    "data element": ("data market", "data trading", "data governance", "data pricing"),
    "semiconductor": ("chip", "integrated circuit", "wafer"),
    "solid-state battery": ("solid electrolyte", "all-solid-state battery", "solid state battery"),
    "education": ("learning", "teaching", "student", "classroom"),
}


def expand_synonyms(terms: list[str] | None) -> list[str]:
    """给核心概念/领域词加同义表达，避免"换了个说法就被淘汰"。

    例如 ``supply chain resilience`` 会扩展出 ``supply chain robustness`` /
    ``resilient supply chain`` / ``resilience``；这样打分的召回口径更接近人的判断。
    """
    if not terms:
        return []
    expanded: list[str] = []
    for term in terms:
        term = (term or "").strip()
        if not term:
            continue
        if term.lower() not in [t.lower() for t in expanded]:
            expanded.append(term)
        for synonym in SYNONYM_MAP.get(term.lower(), ()):
            if synonym.lower() not in [t.lower() for t in expanded]:
                expanded.append(synonym)
    return expanded


class Http:
    """带重试的轻量 HTTP 客户端。"""

    def __init__(self, timeout: float = 30.0) -> None:
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def _get(self, url: str, params: dict[str, Any] | None, retries: int) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self.client.get(url, params=params)
            except Exception as exc:  # noqa: BLE001 网络层错误才重试
                last_error = exc
                if attempt >= retries:
                    raise RuntimeError(f"网络请求失败 {url}: {exc}") from exc
                time.sleep(1.5 * (attempt + 1))
                continue

            if r.status_code == 429:
                raise SourceRateLimited(f"{url} 返回 429（配额/限流）：{r.text[:160]}")
            if r.status_code >= 500 and attempt < retries:
                last_error = RuntimeError(f"{r.status_code}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{url} 返回 {r.status_code}: {r.text[:200]}")
            return r
        raise RuntimeError(f"请求失败 {url}: {last_error}")

    def get_json(self, url: str, params: dict[str, Any] | None = None,
                 retries: int = 2) -> dict[str, Any]:
        return self._get(url, params, retries).json()

    def get_text(self, url: str, params: dict[str, Any] | None = None,
                 retries: int = 2) -> str:
        return self._get(url, params, retries).text


# --------------------------------------------------------------------------
# OpenAlex
# --------------------------------------------------------------------------
OPENALEX_SELECT = (
    "id,doi,title,publication_year,cited_by_count,authorships,primary_location,"
    "abstract_inverted_index,type"
)


def openalex_search(query: str, *, per_page: int = 20, year_from: int | None = None,
                    mailto: str = "", api_key: str = "",
                    http: Http | None = None) -> list[Paper]:
    """OpenAlex 全文检索。

    无 API key 时共享 IP 配额很容易用尽（返回 429，抛 :class:`SourceRateLimited`）。
    免费申请：https://openalex.org/ 注册后即可拿到 key，填到 ``.env`` 的
    ``LIT_OPENALEX_KEY``。
    """
    http = http or Http()
    params: dict[str, Any] = {
        "search": query,
        "per-page": per_page,
        "sort": "relevance_score:desc",
        "select": OPENALEX_SELECT,
    }
    filters = ["type:article|review"]
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    params["filter"] = ",".join(filters)
    if mailto:
        params["mailto"] = mailto
    if api_key:
        params["api_key"] = api_key

    data = http.get_json("https://api.openalex.org/works", params)
    papers: list[Paper] = []
    for item in data.get("results", []):
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in item.get("authorships", []) or []
        ]
        authors = [a for a in authors if a]
        venue = ""
        loc = item.get("primary_location") or {}
        if loc.get("source"):
            venue = loc["source"].get("display_name", "") or ""
        doi = (item.get("doi") or "").replace("https://doi.org/", "")
        oa_url = ((item.get("open_access") or {}).get("oa_url")) or ""
        papers.append(
            Paper(
                paper_id=item.get("id", "").replace("https://openalex.org/", "OA:"),
                title=_clean(item.get("title")),
                abstract=_abstract_from_inverted(item.get("abstract_inverted_index")),
                authors=authors[:20],
                year=item.get("publication_year"),
                venue=venue,
                doi=doi,
                url=doi and f"https://doi.org/{doi}" or (item.get("id") or ""),
                pdf_url=oa_url,
                citations=item.get("cited_by_count") or 0,
                source="openalex",
                type=item.get("type") or "article",
            )
        )
    return [p for p in papers if p.title]


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------
_ATOM = "{http://www.w3.org/2005/Atom}"


def _arxiv_query(terms: str) -> str:
    """把自然语言检索式变成 arXiv 的字段检索。"""
    words = [w for w in re.split(r"[\s,;]+", terms.strip()) if w]
    if not words:
        return "all:all"
    escaped = " AND ".join(f'all:"{w}"' if len(words) > 1 else f"all:{w}" for w in words[:8])
    return escaped


def arxiv_search(query: str, *, max_results: int = 20,
                 http: Http | None = None) -> list[Paper]:
    """arXiv 预印本检索（注意：官方建议两次请求间隔 3 秒）。"""
    http = http or Http()
    params = {
        "search_query": _arxiv_query(query),
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    text = http.get_text("https://export.arxiv.org/api/query", params)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    papers: list[Paper] = []
    for entry in root.findall(f"{_ATOM}entry"):
        raw_id = (entry.findtext(f"{_ATOM}id") or "").strip()
        if not raw_id:
            continue
        arxiv_id = raw_id.rsplit("/", 1)[-1]
        title = _clean(entry.findtext(f"{_ATOM}title"))
        abstract = _clean(entry.findtext(f"{_ATOM}summary"))
        published = entry.findtext(f"{_ATOM}published") or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            _clean(a.findtext(f"{_ATOM}name"))
            for a in entry.findall(f"{_ATOM}author")
        ]
        pdf_url = ""
        for link in entry.findall(f"{_ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
        papers.append(
            Paper(
                paper_id=f"ARXIV:{arxiv_id}",
                title=title,
                abstract=abstract[:4000],
                authors=[a for a in authors if a][:20],
                year=year,
                venue="arXiv",
                url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
                citations=0,
                source="arxiv",
                type="preprint",
            )
        )
    return [p for p in papers if p.title]


# --------------------------------------------------------------------------
# Crossref
# --------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")


def crossref_search(query: str, *, rows: int = 20, year_from: int | None = None,
                    mailto: str = "", http: Http | None = None) -> list[Paper]:
    """Crossref 检索：正式发表的期刊论文（DOI / 期刊 / 卷期最权威）。"""
    http = http or Http()
    params: dict[str, Any] = {
        "query.bibliographic": query,
        "rows": rows,
        "select": "DOI,title,abstract,author,issued,container-title,"
                  "is-referenced-by-count,type,URL",
    }
    filters = ["type:journal-article"]
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    params["filter"] = ",".join(filters)
    if mailto:
        params["mailto"] = mailto

    data = http.get_json("https://api.crossref.org/works", params)
    items = (data.get("message") or {}).get("items", []) or []
    papers: list[Paper] = []
    for item in items:
        title = _clean((item.get("title") or [""])[0])
        if not title:
            continue
        authors: list[str] = []
        for a in item.get("author", []) or []:
            name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if name:
                authors.append(name)
        year = None
        issued = (item.get("issued") or {}).get("date-parts") or [[None]]
        if issued and issued[0] and issued[0][0]:
            year = int(issued[0][0])
        venue = _clean((item.get("container-title") or [""])[0])
        abstract = _clean(_TAG_RE.sub(" ", item.get("abstract") or ""))[:4000]
        doi = item.get("DOI", "")
        papers.append(
            Paper(
                paper_id=f"DOI:{doi}" if doi else f"CR:{title[:40]}",
                title=title,
                abstract=abstract,
                authors=authors[:20],
                year=year,
                venue=venue,
                doi=doi,
                url=f"https://doi.org/{doi}" if doi else item.get("URL", ""),
                citations=item.get("is-referenced-by-count") or 0,
                source="crossref",
                type=item.get("type") or "article",
            )
        )
    return papers


# --------------------------------------------------------------------------
# DOAJ（开放获取期刊目录）
# --------------------------------------------------------------------------
def doaj_search(query: str, *, page_size: int = 20, year_from: int | None = None,
                http: Http | None = None) -> list[Paper]:
    """DOAJ 检索：开放获取期刊，免 key，覆盖全学科。"""
    http = http or Http()
    url = "https://doaj.org/api/search/articles/" + quote(query, safe="")
    data = http.get_json(url, {"pageSize": page_size})
    papers: list[Paper] = []
    for item in data.get("results", []) or []:
        bib = item.get("bibjson") or {}
        title = _clean(bib.get("title"))
        if not title:
            continue
        year = None
        try:
            year = int(bib.get("year")) if bib.get("year") else None
        except (TypeError, ValueError):
            year = None
        if year_from and year and year < year_from:
            continue

        doi = ""
        for ident in bib.get("identifier", []) or []:
            if (ident.get("type") or "").lower() == "doi":
                doi = (ident.get("id") or "").strip()
                break
        authors = [
            a.get("name", "") for a in (bib.get("author") or []) if a.get("name")
        ]
        journal = (bib.get("journal") or {}).get("title", "") or ""
        links = bib.get("link") or []
        url = links[0].get("url", "") if links else ""
        papers.append(
            Paper(
                paper_id=f"DOAJ:{item.get('id', title[:30])}",
                title=title,
                abstract=_clean(bib.get("abstract"))[:4000],
                authors=authors[:20],
                year=year,
                venue=journal,
                doi=doi,
                url=f"https://doi.org/{doi}" if doi else url,
                citations=0,
                source="doaj",
                type="article",
            )
        )
    return papers


# --------------------------------------------------------------------------
# 去重 / 排序 / 挑选
# --------------------------------------------------------------------------
def dedupe(papers: Iterable[Paper]) -> list[Paper]:
    """按 DOI、arXiv ID、标题去重，同时把缺失信息合并到一起。"""
    seen: dict[str, Paper] = {}
    order: list[str] = []
    for p in papers:
        keys = [k for k in (p.doi and f"doi:{p.doi.lower()}",
                            p.paper_id and f"id:{p.paper_id.lower()}",
                            f"t:{_normalize_title(p.title)}") if k]
        hit = next((seen[k] for k in keys if k in seen), None)
        if hit is None:
            key = keys[-1] if keys else p.title
            seen[key] = p
            order.append(key)
            for k in keys:
                seen.setdefault(k, p)
            continue
        # 合并：摘要 / DOI / 期刊 / 引用数
        for k in keys:
            seen.setdefault(k, hit)
        if not hit.abstract and p.abstract:
            hit.abstract = p.abstract
        if not hit.doi and p.doi:
            hit.doi = p.doi
            hit.url = p.url
        if not hit.venue and p.venue:
            hit.venue = p.venue
        if not hit.year and p.year:
            hit.year = p.year
        hit.citations = max(hit.citations, p.citations)
        if p.pdf_url and not hit.pdf_url:
            hit.pdf_url = p.pdf_url
        if p.source == "crossref" and hit.source == "arxiv" and not hit.abstract:
            hit.abstract = p.abstract
    # 只返回每个"首次出现"的对象
    result: list[Paper] = []
    seen_ids: set[int] = set()
    for key in order:
        p = seen[key]
        if id(p) in seen_ids:
            continue
        seen_ids.add(id(p))
        result.append(p)
    return result


def relevance_score(paper: Paper, topic_terms: list[str],
                    required_terms: list[str] | None = None,
                    domain_terms: list[str] | None = None,
                    core_penalty: float = -8.0,
                    domain_penalty: float = -5.0) -> float:
    """相关性打分：关键词命中 > 核心概念命中 > 领域命中 > 引用数 > 新近度。

    ``required_terms`` 是"应该沾边"的核心概念，``domain_terms`` 是应用领域。
    缺失时**扣分而不是直接淘汰**，扣多少由 ``core_penalty`` / ``domain_penalty`` 决定——
    这样"硬约束（v1）"和"软惩罚（v2）"共用同一套打分函数，只是参数不同。
    """
    title = paper.title.lower()
    abstract = paper.abstract.lower()
    score = 0.0
    for term in topic_terms:
        t = term.lower()
        if not t:
            continue
        if t in title:
            score += 3.0
        if t in abstract:
            score += 1.0

    if required_terms:
        in_title = any(t.lower() in title for t in required_terms if t)
        in_abstract = any(t.lower() in abstract for t in required_terms if t)
        if in_title:
            score += 6.0
        elif in_abstract:
            score += 3.0
        else:
            score += core_penalty

    if domain_terms:
        in_title = any(t.lower() in title for t in domain_terms if t)
        in_abstract = any(t.lower() in abstract for t in domain_terms if t)
        if in_title:
            score += 4.0
        elif in_abstract:
            score += 2.0
        else:
            score += domain_penalty

    score += min(4.0, math.log1p(paper.citations) * 0.6)

    if paper.year:
        age = max(0, date.today().year - paper.year)
        score += max(0.0, 3.0 - age * 0.4)

    if paper.abstract:
        score += 1.0
    if paper.source in {"openalex", "crossref"}:
        score += 0.8
    return score


def _mentions(paper: Paper, terms: list[str]) -> bool:
    haystack = f"{paper.title} {paper.abstract}".lower()
    return any(t.lower() in haystack for t in terms if t)


def select_candidates(papers: list[Paper], topic_terms: list[str], limit: int,
                      required_terms: list[str] | None = None,
                      domain_terms: list[str] | None = None,
                      mode: str = "soft") -> list[Paper]:
    """排序 + 截断。

    ``mode`` 三档（对应评测里的 B1 / B2 / B3）：

    * ``off``  ：不加任何概念约束（只用关键词 / 引用数 / 新近度）；
    * ``hard`` ：缺失核心概念的论文**直接淘汰**（v1 线上逻辑，会误杀同义表达）；
    * ``soft`` ：不淘汰，只在打分时扣分（v2 逻辑，配合同义词扩展使用）。

    v2 的结论来自评测：hard 模式下"supply chain robustness"这类同义表达会被误杀，
    改 soft + 同义词扩展后，既不放进完全跑题的论文，也不误杀相关的。
    """
    if mode == "hard":
        core_penalty, domain_penalty = -8.0, -5.0
    else:
        core_penalty, domain_penalty = -4.0, -3.0

    ranked = sorted(
        papers,
        key=lambda p: relevance_score(
            p, topic_terms, required_terms, domain_terms, core_penalty, domain_penalty
        ),
        reverse=True,
    )
    if mode == "hard" and required_terms:
        strict = [p for p in ranked if _mentions(p, required_terms)]
        if len(strict) >= limit:
            ranked = strict
        elif strict:
            ranked = strict + [p for p in ranked if p not in strict]

    with_abs = [p for p in ranked if len(p.abstract) > 120]
    without = [p for p in ranked if len(p.abstract) <= 120]
    picked = with_abs[:limit]
    if len(picked) < limit:
        picked += without[: limit - len(picked)]
    return picked
