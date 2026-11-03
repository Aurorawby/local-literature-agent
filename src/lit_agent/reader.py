"""单篇精读 / 多篇本地 PDF 对比。

支持三种输入：
* 本地 PDF 文件（例如从知网、学校图书馆下载的中文文献）；
* arXiv 链接或编号（会自动下载 PDF 全文）；
* DOI（用 Crossref 取元数据，只有摘要时按摘要精读）。
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx

from . import prompts
from .config import Settings
from .llm import ChatLLM, LLMError
from .pdf import (
    clean_text,
    download_pdf,
    extract_pdf,
    pick_important_chunks,
    split_chunks,
)
from .search import Paper, crossref_search, dedupe

Log = Callable[[str], None]

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)")
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")


# --------------------------------------------------------------------------
# 元数据获取
# --------------------------------------------------------------------------
def parse_arxiv_id(text: str) -> str | None:
    match = _ARXIV_ID_RE.search(text)
    return match.group(1) if match else None


def detect_kind(target: str) -> tuple[str, str]:
    """识别输入类型：本地 PDF / arXiv / DOI。"""
    path = Path(target)
    if path.exists() and path.suffix.lower() == ".pdf":
        return "pdf", str(path)
    doi = _DOI_RE.search(target)
    if doi and "arxiv" not in target.lower():
        return "doi", doi.group(1)
    arxiv_id = parse_arxiv_id(target)
    if arxiv_id:
        return "arxiv", arxiv_id
    if path.exists():
        return "pdf", str(path)
    raise ValueError(
        f"无法识别输入：{target}\n"
        "支持：本地 .pdf 文件路径 / arXiv 链接或编号（如 2305.10601）/ DOI（如 10.1038/xxx）"
    )


def arxiv_metadata(arxiv_id: str) -> Paper:
    """通过 arXiv API 取标题、作者、摘要。"""
    url = "https://export.arxiv.org/api/query"
    with httpx.Client(timeout=30, headers={"User-Agent": "lit-agent/1.0"}) as client:
        r = client.get(url, params={"id_list": arxiv_id, "max_results": 1})
        r.raise_for_status()
        root = ET.fromstring(r.text)

    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        raise RuntimeError(f"arXiv 上找不到 {arxiv_id}")
    authors = [
        (a.findtext(f"{_ATOM}name") or "").strip()
        for a in entry.findall(f"{_ATOM}author")
    ]
    published = entry.findtext(f"{_ATOM}published") or ""
    return Paper(
        paper_id=f"ARXIV:{arxiv_id}",
        title=" ".join((entry.findtext(f"{_ATOM}title") or "").split()),
        abstract=" ".join((entry.findtext(f"{_ATOM}summary") or "").split()),
        authors=[a for a in authors if a],
        year=int(published[:4]) if published[:4].isdigit() else None,
        venue="arXiv",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        source="arxiv",
        type="preprint",
    )


def doi_metadata(doi: str) -> Paper:
    """通过 Crossref 取 DOI 对应元数据。"""
    with httpx.Client(timeout=30, headers={"User-Agent": "lit-agent/1.0"}) as client:
        r = client.get(f"https://api.crossref.org/works/{doi}")
        r.raise_for_status()
        item = r.json()["message"]

    title = " ".join((item.get("title") or [""])[0].split())
    authors = []
    for a in item.get("author", []) or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x)
        if name:
            authors.append(name)
    issued = (item.get("issued") or {}).get("date-parts") or [[None]]
    year = int(issued[0][0]) if issued and issued[0] and issued[0][0] else None
    abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
    return Paper(
        paper_id=f"DOI:{doi}",
        title=title,
        abstract=" ".join(abstract.split())[:4000],
        authors=authors,
        year=year,
        venue=" ".join((item.get("container-title") or [""])[0].split()),
        doi=doi,
        url=f"https://doi.org/{doi}",
        source="crossref",
        type=item.get("type", "article"),
    )


# --------------------------------------------------------------------------
# 核心：把全文变成精读笔记
# --------------------------------------------------------------------------
def _chunk_points(llm: ChatLLM, chunks: list[str], log: Log, language: str) -> str:
    """长文分块提炼（map 阶段）。"""
    system = prompts.CHUNK_SYSTEM_ZH if language == "zh" else prompts.CHUNK_SYSTEM_EN
    points: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        t0 = time.perf_counter()
        try:
            text = llm.chat(system, chunk, max_tokens=260, temperature=0.1)
            text = text.strip()
        except LLMError as exc:
            log(f"     ! 第 {i}/{len(chunks)} 块失败：{exc}")
            continue
        points.append(f"[片段 {i}]\n{text}")
        log(f"     片段 {i}/{len(chunks)} 完成（{time.perf_counter() - t0:.1f}s）")
    return "\n\n".join(points)


def read_text_paper(
    text: str,
    paper: Paper,
    settings: Settings,
    llm: ChatLLM,
    log: Log = print,
) -> str:
    """给定全文文本 + 元数据，生成 Markdown 精读笔记。"""
    language = settings.language
    if len(text) <= 9000:
        log(f"   全文 {len(text)} 字，单次精读")
        try:
            body = llm.chat(
                prompts.PAPER_NOTE_SYSTEM_ZH if language == "zh" else prompts.PAPER_NOTE_SYSTEM_EN,
                prompts.paper_note_user(
                    paper.title, paper.venue, paper.year,
                    "论文全文：\n\n" + text, language,
                ),
                max_tokens=1400,
                temperature=0.3,
            )
        except LLMError as exc:
            raise RuntimeError(f"精读失败：{exc}") from exc
    else:
        chunks = pick_important_chunks(split_chunks(text, max_chars=3000), limit=6)
        log(f"   全文 {len(text)} 字，切 {len(chunks)} 块分别提炼")
        points = _chunk_points(llm, chunks, log, language)
        body = llm.chat(
            prompts.PAPER_NOTE_SYSTEM_ZH if language == "zh" else prompts.PAPER_NOTE_SYSTEM_EN,
            prompts.paper_note_user(paper.title, paper.venue, paper.year, points, language),
            max_tokens=1400,
            temperature=0.3,
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = [
        f"# 精读笔记：{paper.title}",
        "",
        f"> {', '.join(paper.authors[:6]) or '作者未知'} ｜ {paper.venue or '来源未知'} ｜ "
        f"{paper.year or 'n.d.'}",
    ]
    if paper.doi:
        header.append(f"> DOI: {paper.doi}")
    if paper.url:
        header.append(f"> 链接: {paper.url}")
    header += [f"> 由 {settings.provider}/{settings.model} 生成于 {now}", ""]
    return "\n".join(header) + "\n" + body.strip() + "\n"


def read_pdf_file(
    path: str | Path,
    settings: Settings,
    llm: ChatLLM,
    log: Log = print,
) -> str:
    """精读一个本地 PDF 文件。"""
    path = Path(path)
    log(f"   解析 PDF：{path.name}")
    text, meta = extract_pdf(path)
    if len(text) < 500:
        raise RuntimeError(
            f"{path.name} 提取到的文字太少（{len(text)} 字），可能是扫描版 PDF，需要先做 OCR。"
        )
    title = meta.get("title") or path.stem
    paper = Paper(
        paper_id=f"FILE:{path.name}",
        title=title,
        authors=[a for a in re.split(r"[;；,，]", meta.get("author", "")) if a.strip()],
        venue="本地 PDF",
        url=str(path),
    )
    log(f"   共 {meta['pages']} 页，{meta['chars']} 字")
    return read_text_paper(text, paper, settings, llm, log)


def read_arxiv(
    arxiv_id: str,
    settings: Settings,
    llm: ChatLLM,
    log: Log = print,
) -> str:
    log(f"   获取 arXiv 元数据：{arxiv_id}")
    paper = arxiv_metadata(arxiv_id)
    log(f"   {paper.title[:70]}")
    log("   下载 PDF ...")
    pdf_path = download_pdf(paper.pdf_url)
    text, _ = extract_pdf(pdf_path)
    return read_text_paper(text, paper, settings, llm, log)


def read_doi(doi: str, settings: Settings, llm: ChatLLM, log: Log = print) -> str:
    log(f"   获取 DOI 元数据：{doi}")
    paper = doi_metadata(doi)
    log(f"   {paper.title[:70]}（仅能拿到摘要，按摘要精读）")
    if not paper.abstract:
        raise RuntimeError("Crossref 没有这篇论文的摘要，无法精读；建议直接下载 PDF。")
    text = f"标题：{paper.title}\n摘要：{paper.abstract}"
    return read_text_paper(text, paper, settings, llm, log)


# --------------------------------------------------------------------------
# 多篇本地 PDF 对比
# --------------------------------------------------------------------------
def compare_pdfs(
    paths: list[str | Path],
    topic: str,
    settings: Settings,
    llm: ChatLLM,
    log: Log = print,
) -> tuple[str, list[Paper]]:
    """批量精读本地 PDF 并生成对比综述。"""
    papers: list[Paper] = []
    cards: list[str] = []
    for i, path in enumerate(paths, 1):
        path = Path(path)
        log(f"[{i}/{len(paths)}] {path.name}")
        try:
            text, meta = extract_pdf(path)
            paper = Paper(
                paper_id=f"FILE:{path.name}",
                title=meta.get("title") or path.stem,
                venue="本地 PDF",
                url=str(path),
            )
            papers.append(paper)
            data = llm.chat_json(
                prompts.EXTRACT_SYSTEM_ZH if settings.is_chinese else prompts.EXTRACT_SYSTEM_EN,
                prompts.extract_user(
                    topic or "本地文献集合", paper.title, "本地 PDF", None,
                    text[:4000], settings.language,
                ),
                max_tokens=420,
            )
        except (LLMError, ValueError, RuntimeError) as exc:
            log(f"   ! 跳过：{exc}")
            continue

        card = (
            f"[{len(cards) + 1}] {paper.title}（{path.name}）\n"
            + "\n".join(f"  - {k}: {data.get(k, '')}" for k in (
                "one_liner", "problem", "method", "data", "findings", "limitations"))
        )
        cards.append(card)

    if not cards:
        raise RuntimeError("没有成功解析任何 PDF（注意：扫描版 PDF 需要先 OCR）。")

    log(f"合成对比综述（{len(cards)} 篇）...")
    body = llm.chat(
        prompts.COMPARE_SYSTEM_ZH,
        f"研究主题：{topic or '（未指定，请根据文献内容归纳）'}\n\n" + "\n\n".join(cards),
        max_tokens=1800,
        temperature=0.3,
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 本地文献对比：{topic or '（未指定主题）'}",
        "",
        f"> {len(papers)} 篇 PDF ｜ {now} ｜ {settings.provider}/{settings.model}",
        "",
        body.strip(),
        "",
        "## 文献清单",
        "",
    ]
    for i, path in enumerate(paths, 1):
        lines.append(f"{i}. `{Path(path).name}`")
    return "\n".join(lines) + "\n", papers
