"""PDF 文本提取与长文本切分。

依赖 PyMuPDF（import 名 ``fitz``）。只在真正解析 PDF 时才 import，
这样即使没装也不影响在线检索功能。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "pdfs"

_PAGE_NUMBER_RE = re.compile(r"^\s*(?:-?\s*\d{1,3}\s*-?|第\s*\d+\s*页)\s*$")
_REF_HEAD_RE = re.compile(
    r"\n\s*(references|bibliography|参考文献)\s*\n", re.IGNORECASE
)


def _fitz():
    """PyMuPDF 1.x 的 import 名是 fitz，2.x 改成了 pymupdf，这里都兼容。"""
    try:
        import pymupdf  # type: ignore
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz  # type: ignore
        return fitz
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 PDF 解析库 PyMuPDF。请运行：\n"
            "  .venv\\Scripts\\python.exe -m pip install pymupdf"
        ) from exc


def _repeated_lines(page_texts: list[str], *, threshold_ratio: float = 0.5,
                    max_len: int = 60) -> set[str]:
    """找出在多数页面重复出现的行（期刊名、栏目名、日期等页眉/页脚）。

    知网导出的中文期刊 PDF 几乎每页都有"金融理论与政策 / Financial Theory & Policy /
    国际金融研究 / 2022·11"这类固定行，它们对检索和阅读都是噪声，应该先剔除。
    """
    if len(page_texts) < 3:
        return set()
    counts: dict[str, int] = {}
    for text in page_texts:
        seen = {
            line.strip()
            for line in text.splitlines()
            if 4 <= len(line.strip()) <= max_len
        }
        for line in seen:
            counts[line] = counts.get(line, 0) + 1
    threshold = max(2, int(len(page_texts) * threshold_ratio))
    return {line for line, count in counts.items() if count >= threshold}


def _strip_repeated_lines(page_texts: list[str]) -> list[str]:
    repeated = _repeated_lines(page_texts)
    if not repeated:
        return page_texts
    return [
        "\n".join(line for line in text.splitlines() if line.strip() not in repeated)
        for text in page_texts
    ]


def clean_text(text: str) -> str:
    """去掉页码、多余空行，并截掉参考文献部分（对精读没有帮助、还特别长）。"""
    lines = []
    for line in text.splitlines():
        if _PAGE_NUMBER_RE.match(line):
            continue
        lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    match = _REF_HEAD_RE.search(text)
    if match and match.start() > len(text) * 0.4:
        text = text[: match.start()]
    return text.strip()


def extract_pdf(path: str | Path) -> tuple[str, dict[str, Any]]:
    """提取 PDF 全文与元数据，返回 ``(text, meta)``。"""
    fitz = _fitz()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")

    doc = fitz.open(path)
    try:
        pages = [page.get_text("text") or "" for page in doc]
        meta = dict(doc.metadata or {})
    finally:
        doc.close()

    pages = _strip_repeated_lines(pages)
    text = clean_text("\n".join(pages))

    title = (meta.get("title") or "").strip()
    if not title and pages:
        # 很多 PDF 没有写元数据，用首页第一行当标题
        for line in pages[0].splitlines():
            line = line.strip()
            if 10 < len(line) < 200:
                title = line
                break

    # 中文期刊 PDF（知网导出）首行常是期刊名/栏目名，且每页页眉都重复。
    # 如果标题在多数页面出现，说明它是页眉，后面会用文件名兜底。
    if title and len(pages) >= 3:
        occurrences = sum(1 for page_text in pages if title in page_text)
        if occurrences >= max(2, len(pages) // 2):
            title = ""

    author = (meta.get("author") or "").strip()
    if author.lower() in {"cnki", "cnki.net", "microsoft", "administrator", "user", "unknown"}:
        author = ""

    meta = {
        "title": title,
        "author": author,
        "pages": len(pages),
        "chars": len(text),
        "path": str(path),
    }
    return text, meta


def download_pdf(url: str, *, timeout: float = 90.0) -> Path:
    """下载 PDF 到本地缓存，返回路径（同一 URL 只下载一次）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    target = CACHE_DIR / f"{digest}.pdf"
    if target.exists() and target.stat().st_size > 10_000:
        return target

    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": "lit-agent/1.0"}) as client:
        response = client.get(url)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise RuntimeError(f"返回内容不是 PDF：{url}")
        target.write_bytes(response.content)
    return target


def extract_pages(path: str | Path) -> tuple[list[tuple[int, str]], dict[str, Any]]:
    """逐页提取文本（保留页码），用于"论文对话"里的页码引用。

    返回 ``([(页码, 文本), ...], meta)``。会在 References / 参考文献 处截断，
    因为参考文献页对问答和观点提炼没有帮助、还特别占上下文。
    """
    fitz = _fitz()
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")

    doc = fitz.open(path)
    try:
        raw_pages = [(i + 1, page.get_text("text") or "") for i, page in enumerate(doc)]
        meta = dict(doc.metadata or {})
    finally:
        doc.close()

    # 剔掉每页重复的期刊页眉/页脚，避免它们污染检索和片段展示
    stripped = _strip_repeated_lines([text for _, text in raw_pages])
    raw_pages = [(number, stripped[i]) for i, (number, _) in enumerate(raw_pages)]

    pages: list[tuple[int, str]] = []
    for number, text in raw_pages:
        # 参考文献开始后就不再做正文检索
        if re.search(r"^\s*(references|bibliography|参考文献)\s*$", text, re.IGNORECASE | re.MULTILINE):
            if number > max(1, len(raw_pages) // 2):
                break
        cleaned = "\n".join(
            line.rstrip() for line in text.splitlines() if not _PAGE_NUMBER_RE.match(line)
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if cleaned:
            pages.append((number, cleaned))

    title = (meta.get("title") or "").strip()
    if not title and raw_pages:
        for line in raw_pages[0][1].splitlines():
            line = line.strip()
            if 10 < len(line) < 200:
                title = line
                break

    # 中文期刊 PDF（如知网导出）首行常是"期刊名/栏目名"，而且每页页眉都重复。
    # 如果猜到的标题在多数页面里都出现，说明它是页眉，改用文件名当标题。
    if title and len(pages) >= 3:
        occurrences = sum(1 for _, text in pages if title in text)
        if occurrences >= max(2, len(pages) // 2):
            title = ""

    author = (meta.get("author") or "").strip()
    if author.lower() in {"cnki", "cnki.net", "microsoft", "administrator", "user", "unknown"}:
        author = ""

    meta = {
        "title": title,
        "author": author,
        "pages": len(raw_pages),
        "chars": sum(len(t) for _, t in pages),
        "path": str(path),
    }
    return pages, meta


def split_chunks(text: str, *, max_chars: int = 3000, overlap: int = 200) -> list[str]:
    """按段落切块，尽量不切断句子。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(buffer) + len(para) + 2 <= max_chars:
            buffer = f"{buffer}\n\n{para}" if buffer else para
            continue
        if buffer:
            chunks.append(buffer)
        if len(para) <= max_chars:
            buffer = para
        else:  # 超长段落硬切
            start = 0
            while start < len(para):
                chunks.append(para[start : start + max_chars])
                start += max_chars - overlap
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def _readable_ratio(text: str) -> float:
    """粗略判断文本是否"像人话"。

    PDF 里的图表、公式、嵌入字体会被提取成 ``GĮŔũƜ`` 这类乱码块，
    这类块喂给模型只会浪费上下文，直接丢掉。
    """
    if not text:
        return 0.0
    good = 0
    for ch in text:
        if ch.isascii():
            if ch.isalnum() or ch.isspace() or ch in ".,;:%()-[]/+=*_’'\"\n\t":
                good += 1
        elif "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef":
            good += 1
    return good / len(text)


def pick_important_chunks(chunks: list[str], limit: int = 8) -> list[str]:
    """块太多时均匀取样：保留开头（问题/方法）和结尾（结论），中间均匀取。"""
    readable = [c for c in chunks if _readable_ratio(c) >= 0.75]
    if readable:
        chunks = readable
    if len(chunks) <= limit:
        return chunks
    head = chunks[:2]
    tail = chunks[-2:]
    middle_pool = chunks[2:-2]
    need = max(0, limit - len(head) - len(tail))
    if not middle_pool or need == 0:
        return head + tail
    step = max(1, len(middle_pool) // need)
    middle = middle_pool[::step][:need]
    return head + middle + tail
