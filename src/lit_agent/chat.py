"""论文对话 Agent：加载一篇论文，然后可以围绕它提问、提炼可写观点、摘原文引文。

为什么不用向量检索
------------------
* 一篇论文切完只有几十个片段，**词法检索（BM25）足够**，纯 Python 几十行，
  不引入 embedding 模型和额外内存；
* 中英混排用「英文词 + 中文字符二元组」分词，对中文论文同样有效；
* 检索是确定性的——同样的提问永远命中同样的原文，方便调试和引用页码。
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .llm import ChatLLM, LLMError
from .pdf import _readable_ratio, download_pdf, extract_pages
from .reader import arxiv_metadata, doi_metadata, parse_arxiv_id
from .search import Paper

Log = Callable[[str], None]

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；.!?;])\s+|\n+")
# 常见缩写里的点不是句号，切句前先保护起来
_ABBREVIATIONS = (
    "e.g.", "i.e.", "et al.", "Fig.", "Figs.", "Eq.", "Eqs.", "vs.", "etc.",
    "cf.", "Sec.", "Tab.", "No.", "approx.", "resp.", "w.r.t.",
)
_ABBR_PLACEHOLDER = "\u0000"


def split_sentences(text: str) -> list[str]:
    """切句，保护 e.g. / et al. 这类缩写不被误切。"""
    flat = " ".join((text or "").split())
    protected = flat
    for abbr in _ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", _ABBR_PLACEHOLDER))
    parts = [p.replace(_ABBR_PLACEHOLDER, ".").strip()
             for p in _SENT_SPLIT_RE.split(protected)]
    return [p for p in parts if p]


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


TRANSLATE_SYSTEM = """你是学术检索助手。把用户的中文问题转换成用于检索英文论文的英文关键词。
要求：只输出英文关键词，用空格分隔，6 个词以内；不要输出句子、标点或解释。
例：输入"这篇论文的核心方法是什么" → 输出"core method approach proposed framework"
例：输入"实验结果比之前好多少" → 输出"experimental results improvement comparison" """


# --------------------------------------------------------------------------
# 检索：分词 + BM25
# --------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """中英混排分词：英文/数字按词，中文按二元组。"""
    lowered = (text or "").lower()
    tokens = _WORD_RE.findall(lowered)
    cjk = _CJK_RE.findall(lowered)
    tokens += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return tokens


class BM25:
    """极简 BM25（Okapi），够用且没有依赖。"""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.doc_len = [len(d) for d in docs]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter = Counter()
        for counter in self.tf:
            df.update(counter.keys())
        self.idf = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n
        for term in query_tokens:
            idf = self.idf.get(term)
            if not idf:
                continue
            for i, counter in enumerate(self.tf):
                freq = counter.get(term)
                if not freq:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * self.doc_len[i] / (self.avgdl or 1))
                scores[i] += idf * freq * (self.k1 + 1) / denom
        return scores


@dataclass
class Chunk:
    cid: int
    page: int
    text: str


@dataclass
class PaperIndex:
    """一篇论文的可检索索引。"""

    chunks: list[Chunk]
    bm25: BM25
    title: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ build
    @classmethod
    def from_pages(cls, pages: list[tuple[int, str]], *, max_chars: int = 1100,
                   overlap: int = 140) -> "PaperIndex":
        chunks: list[Chunk] = []
        cid = 0
        for number, text in pages:
            pieces = _split_for_chat(text, max_chars=max_chars, overlap=overlap)
            # 丢掉 PDF 里图表/嵌入字体产生的乱码块（"GĮŔũƜ" 之类）
            readable = [p for p in pieces if _readable_ratio(p) >= 0.75]
            for piece in (readable or pieces):
                chunks.append(Chunk(cid=cid, page=number, text=piece))
                cid += 1
        bm25 = BM25([tokenize(c.text) for c in chunks])
        return cls(chunks=chunks, bm25=bm25, meta={"pages": len(pages)})

    @classmethod
    def from_text(cls, text: str) -> "PaperIndex":
        return cls.from_pages([(1, text)])

    # ----------------------------------------------------------------- search
    def search(self, query: str, k: int = 5) -> list[tuple[float, Chunk]]:
        if not self.chunks or not (query or "").strip():
            return []
        query_tokens = tokenize(query)
        scores = self.bm25.scores(query_tokens)
        # 精确短语命中给一点额外加分（对中文关键词尤其有效）
        needle = query.strip().lower()
        if len(needle) >= 2:
            for i, chunk in enumerate(self.chunks):
                if needle in chunk.text.lower():
                    scores[i] += 1.5
        ranked = sorted(
            ((scores[i], self.chunks[i]) for i in range(len(self.chunks)) if scores[i] > 0),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return ranked[:k]

    def snippet(self, chunk: Chunk, limit: int = 160) -> str:
        text = " ".join(chunk.text.split())
        return text[:limit] + ("…" if len(text) > limit else "")


def _split_for_chat(text: str, *, max_chars: int = 900, overlap: int = 120) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(buffer) + len(para) + 2 <= max_chars:
            buffer = f"{buffer}\n{para}" if buffer else para
            continue
        if buffer:
            pieces.append(buffer)
        if len(para) <= max_chars:
            buffer = para
        else:
            start = 0
            while start < len(para):
                pieces.append(para[start : start + max_chars])
                start += max_chars - overlap
            buffer = ""
    if buffer:
        pieces.append(buffer)
    return pieces


# --------------------------------------------------------------------------
# 加载论文
# --------------------------------------------------------------------------
_DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s\"<>]+)")


def detect_kind(target: str) -> tuple[str, str]:
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
    raise ValueError(f"无法识别输入：{target}")


def load_paper(target: str, log: Log = print) -> tuple[PaperIndex, Paper]:
    """把 PDF / arXiv / DOI 加载成可检索的论文。"""
    kind, value = detect_kind(target)
    if kind == "pdf":
        log(f"   解析 PDF：{Path(value).name}")
        pages, meta = extract_pages(value)
        if meta["chars"] < 500:
            raise RuntimeError("提取到的文字太少，可能是扫描版 PDF，需要先 OCR。")
        paper = Paper(
            paper_id=f"FILE:{Path(value).name}",
            title=meta["title"] or Path(value).stem,
            authors=[a for a in re.split(r"[;；,，]", meta["author"]) if a.strip()],
            venue="本地 PDF",
            url=str(Path(value)),
        )
        index = PaperIndex.from_pages(pages)
        index.title = paper.title
        return index, paper

    if kind == "arxiv":
        log(f"   获取 arXiv 元数据：{value}")
        paper = arxiv_metadata(value)
        log(f"   下载 PDF：{paper.title[:60]}")
        pdf_path = download_pdf(paper.pdf_url)
        pages, _ = extract_pages(pdf_path)
        index = PaperIndex.from_pages(pages)
        index.title = paper.title
        return index, paper

    log(f"   获取 DOI 元数据：{value}")
    paper = doi_metadata(value)
    if not paper.abstract:
        raise RuntimeError("Crossref 没有这篇论文的摘要，无法对话；建议直接下载 PDF 再上传。")
    text = f"标题：{paper.title}\n\n摘要：{paper.abstract}"
    index = PaperIndex.from_text(text)
    index.title = paper.title
    return index, paper


# --------------------------------------------------------------------------
# 对话
# --------------------------------------------------------------------------
QA_SYSTEM = """你是严谨的论文阅读助手。你只能依据用户提供的原文片段回答问题。

规则：
1. 片段里没有的内容，直接说"给出的原文片段中没有提到这一点"，不要用常识补全；
2. 回答里用 [p页码] 标注依据，例如 [p3]；
3. 引用原句时用引号照抄原文（英文就保持英文），不要改写后再加引号；
4. 用中文回答，条理清楚，不超过 300 字，除非用户要求详细展开；
5. **不要自己做减法、算差值和百分比**。片段里直接写出的结论可以引用；
   没有直接写出的，就把几个数字分别陈述（例如"ToT 为 74%，CoT 为 4%，CoT-SC 为 9%"），
   让读者自己判断。"""

CLAIMS_SYSTEM = """你是论文写作助手。给你一篇论文的句子列表（每条前面有编号，如 [p3-s2]），
请提炼 3 条可以直接写进论文的观点。

只输出 JSON：
{"claims": [
  {"claim": "一句中文论断，可直接当论点",
   "evidence_ids": ["p3-s2"],
   "usage": "放在论文哪一部分，一句话说明",
   "caveat": "适用范围或局限"}
]}

规则：
- evidence_ids 必须从给你的编号里选，1-2 个，不能编造；
- 只能依据句子内容，禁止补充外部知识；
- 找不到合适依据就不要写这一条。"""

MODE_LABELS = {
    "qa": "论文问答",
    "claims": "提炼可写观点",
    "critique": "方法与局限审视",
}


class PaperChat:
    """一篇论文的对话会话（包含历史）。"""

    def __init__(self, index: PaperIndex, paper: Paper, settings: Settings,
                 llm: ChatLLM, log: Log = print) -> None:
        self.index = index
        self.paper = paper
        self.settings = settings
        self.llm = llm
        self.log = log
        self.history: list[dict[str, str]] = []
        self.started_at = time.time()
        self._translation_cache: dict[str, str] = {}
        self.last_sources: list[dict[str, Any]] = []
        self._is_chinese: bool | None = None

    # ------------------------------------------------------------------ utils
    def _translate_query(self, question: str) -> str:
        """中文问题 → 英文检索词。

        论文大多是英文的，中文提问直接做词法检索会命中 0 个片段，
        所以先用模型把问题压成英文关键词，再检索一次。
        """
        if question in self._translation_cache:
            return self._translation_cache[question]
        try:
            text = self.llm.chat(
                TRANSLATE_SYSTEM, question, max_tokens=60, temperature=0
            )
        except LLMError:
            return ""
        cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", text)
        cleaned = " ".join(cleaned.split())[:120]
        self._translation_cache[question] = cleaned
        return cleaned

    def _search(self, question: str, k: int) -> tuple[list[tuple[float, Chunk]], str]:
        """检索；中文提问且论文是英文时，自动翻译成英文再检索。"""
        hits = self.index.search(question, k=k)
        if len(hits) >= 2 or not has_cjk(question):
            return hits, ""
        if self.is_chinese_paper:
            return hits, ""  # 中文论文用中文检索即可，翻译反而降低召回
        translated = self._translate_query(question)
        if translated:
            self.log(f"   中文问题自动转英文检索：{translated}")
            hits_en = self.index.search(translated, k=k)
            if len(hits_en) > len(hits):
                return hits_en, translated
        return hits, ""

    def _even_chunks(self, k: int = 10) -> list[tuple[float, Chunk]]:
        """从全文均匀取片段（用于"提炼全文观点"时避免只看开头几页）。"""
        chunks = self.index.chunks
        if not chunks:
            return []
        k = min(k, len(chunks))
        step = max(1, len(chunks) / k)
        return [(0.0, chunks[min(len(chunks) - 1, int(i * step))]) for i in range(k)]
    def _history_text(self, max_chars: int = 1500) -> str:
        lines = []
        for turn in self.history[-6:]:
            role = "我" if turn["role"] == "user" else "助手"
            lines.append(f"{role}：{turn['content'][:300]}")
        text = "\n".join(lines)
        return text[-max_chars:]

    def _context(self, hits: list[tuple[float, Chunk]], max_chars: int = 5200) -> str:
        blocks: list[str] = []
        total = 0
        for _score, chunk in hits:
            block = f"[p{chunk.page}] {chunk.text}"
            if total + len(block) > max_chars:
                break
            blocks.append(block)
            total += len(block)
        return "\n\n".join(blocks)

    def _sources(self, hits: list[tuple[float, Chunk]]) -> list[dict[str, Any]]:
        """返回依据片段：``snippet`` 用于一行展示，``text`` 是完整片段（可展开核对）。"""
        return [
            {
                "page": chunk.page,
                "score": round(score, 2),
                "snippet": self.index.snippet(chunk),
                "text": chunk.text[:2000],
                "chars": len(chunk.text),
            }
            for score, chunk in hits
        ]

    @property
    def is_chinese_paper(self) -> bool:
        """判断论文是否以中文为主（中文论文不需要先翻译成英文再检索）。"""
        if not getattr(self, "_is_chinese", None):
            sample = " ".join(c.text for c in self.index.chunks[:5])[:3000]
            if not sample:
                self._is_chinese = False
            else:
                cjk = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
                self._is_chinese = cjk / max(1, len(sample)) > 0.2
        return self._is_chinese

    # ------------------------------------------------------------------- ask
    def ask(self, question: str, k: int = 6) -> dict[str, Any]:
        """基于原文回答问题。问句里带"详细/展开/完整"时会给更长的回答。"""
        question = (question or "").strip()
        if not question:
            return {"answer": "请先输入问题。", "sources": []}

        detailed = any(word in question for word in ("详细", "展开", "完整", "具体说说", "多说"))
        hits, translated = self._search(question, k=k)
        if not hits:
            return {
                "answer": "在论文里没有找到与这个问题相关的片段。可以换成论文里的术语再问，"
                          "或者用 /quote 关键词 直接搜原文。",
                "sources": [],
            }

        translation_note = (
            f"\n（提示：检索时已把你的问题转成英文关键词：{translated}）"
            if translated else ""
        )
        user = (
            f"<原文片段>\n{self._context(hits)}\n</原文片段>\n\n"
            f"<历史对话>\n{self._history_text()}\n</历史对话>\n\n"
            f"<问题>{question}</问题>\n\n请只依据原文片段回答。"
        )
        try:
            answer = self.llm.chat(
                QA_SYSTEM, user,
                max_tokens=1400 if detailed else 900,
                temperature=0.2,
            )
        except LLMError as exc:
            answer = f"模型调用失败：{exc}"

        sources = self._sources(hits)
        self.last_sources = sources
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        return {
            "answer": answer.strip() + translation_note,
            "sources": sources,
            "query_en": translated,
        }

    # ---------------------------------------------------------------- claims
    def _sentence_catalog(
        self, hits: list[tuple[float, Chunk]], per_chunk: int = 4
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        """把检索到的片段拆成带编号的句子，供模型按编号引用。"""
        catalog: dict[str, dict[str, Any]] = {}
        lines: list[str] = []
        for _score, chunk in hits:
            # PDF 里是硬换行，先拼成一行再按句号切，避免把一句话切成两半
            sentences = [
                s for s in split_sentences(chunk.text)
                if (len(s.split()) >= 8 or len(s) >= 30) and _readable_ratio(s) >= 0.8
            ]
            if not sentences:
                sentences = [" ".join(chunk.text.split())]
            step = max(1, len(sentences) // per_chunk)
            for i, sentence in enumerate(sentences[::step][:per_chunk], start=1):
                sid = f"p{chunk.page}-s{i}"
                if sid in catalog:
                    continue
                catalog[sid] = {"page": chunk.page, "sentence": sentence}
                lines.append(f"[{sid}] {sentence}")
        return "\n".join(lines), catalog

    def _render_claims(
        self, data: dict[str, Any], catalog: dict[str, dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        lines: list[str] = []
        rendered = 0
        claims_out: list[dict[str, Any]] = []
        for item in (data.get("claims") or [])[:3]:
            claim = str(item.get("claim", "")).strip()
            if not claim:
                continue
            ids = [str(i).strip() for i in (item.get("evidence_ids") or [])]
            quotes = [catalog[i] for i in ids if i in catalog][:3]
            rendered += 1
            usage = str(item.get("usage", "")).strip()
            caveat = str(item.get("caveat", "")).strip()
            claims_out.append({
                "claim": claim,
                "quotes": [
                    {"page": q.get("page"), "sentence": q.get("sentence", "")}
                    for q in quotes
                ],
                "usage": usage,
                "caveat": caveat,
            })
            lines.append(f"### 观点 {rendered}")
            lines.append(f"**观点**：{claim}")
            if quotes:
                lines.append("**原文依据**：")
                for quote in quotes:
                    lines.append(f"> {quote['sentence']}")
                    lines.append(f"> —— [p{quote['page']}]")
            else:
                lines.append("**原文依据**：（模型没有给出有效依据编号，请人工核对）")
            if usage:
                lines.append(f"**怎么用**：{usage}")
            if caveat:
                lines.append(f"**注意**：{caveat}")
            lines.append("")
        if not rendered:
            return "", []
        return "\n".join(lines).strip(), claims_out

    def claims(self, focus: str = "", k: int = 10) -> dict[str, Any]:
        """提炼可写进论文的观点（原文依据由程序从句子编号回填，保证是原文）。"""
        if focus.strip():
            hits, _ = self._search(focus, k=k)
        else:
            hits = self._even_chunks(k)
        if not hits:
            hits = [(0.0, c) for c in self.index.chunks[: min(8, len(self.index.chunks))]]

        catalog_text, catalog = self._sentence_catalog(hits)
        user = (
            f"论文标题：{self.paper.title}\n"
            f"用户关注点：{focus or '（未指定，请覆盖全文最值得写的观点）'}\n\n"
            f"句子列表：\n{catalog_text[:6000]}\n\n"
            "请输出 JSON，提炼 3 条观点。"
        )
        try:
            data = self.llm.chat_json(CLAIMS_SYSTEM, user, max_tokens=900, temperature=0.2)
            answer, claims_out = self._render_claims(data, catalog)
            if not answer:
                raise ValueError("没有渲染出观点")
        except (LLMError, ValueError) as exc:
            self.log(f"   ! 观点提炼失败，改用文本模式：{exc}")
            answer = "观点提炼失败，请重试或换更具体的问题。"
            claims_out = []

        self.history.append({"role": "user", "content": f"【提炼观点】{focus or '全文'}"})
        self.history.append({"role": "assistant", "content": answer})
        return {"answer": answer.strip(), "sources": self._sources(hits), "claims": claims_out}

    # -------------------------------------------------------------- critique
    def critique(self, focus: str = "") -> dict[str, Any]:
        """审视论文的方法与局限。"""
        if focus.strip():
            hits, _ = self._search(focus, k=8)
        else:
            hits = self._even_chunks(8)
        if not hits:
            hits = [(0.0, c) for c in self.index.chunks[:6]]
        system = (
            "你是严格的审稿人。只依据给出的原文片段，用中文指出这篇论文在方法、数据、"
            "评估和结论上的可疑之处，以及作者自己承认的局限。每条都要给 [p页码]。"
            "不确定的地方写“片段中未说明”，不要编造。输出 Markdown 列表。"
        )
        user = (
            f"<原文片段>\n{self._context(hits, max_chars=5200)}\n</原文片段>\n\n"
            "请给出 4-6 条「可质疑点」，并区分“作者承认的局限”和“我认为的额外问题”。"
        )
        try:
            answer = self.llm.chat(system, user, max_tokens=900, temperature=0.3)
        except LLMError as exc:
            answer = f"模型调用失败：{exc}"
        self.history.append({"role": "user", "content": f"【方法与局限】{focus}"})
        self.history.append({"role": "assistant", "content": answer})
        return {"answer": answer.strip(), "sources": self._sources(hits)}

    # ---------------------------------------------------------------- quote
    def quote(self, keyword: str, max_items: int = 8) -> list[dict[str, Any]]:
        """在原文里找包含关键词的句子（确定性，用于直接引用）。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        needle = keyword.lower()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in self.index.chunks:
            for sentence in split_sentences(chunk.text):
                sentence = " ".join(sentence.split())
                if len(sentence) < 20 or needle not in sentence.lower():
                    continue
                if sentence in seen:
                    continue
                seen.add(sentence)
                results.append({"page": chunk.page, "sentence": sentence})
                if len(results) >= max_items:
                    return results
        return results

    # ----------------------------------------------------------------- save
    def transcript(self) -> str:
        lines = [
            f"# 论文对话记录：{self.paper.title}",
            "",
            f"> 来源：{self.paper.venue or self.paper.url} ｜ "
            f"{self.paper.year or 'n.d.'} ｜ 对话轮数：{len(self.history) // 2}",
            "",
        ]
        for turn in self.history:
            role = "我" if turn["role"] == "user" else "助手"
            lines.append(f"## {role}")
            lines.append("")
            lines.append(turn["content"])
            lines.append("")
        return "\n".join(lines)

    def save(self, stem: str) -> Path:
        from .cli import write_output

        return write_output(stem, self.transcript(), sidecar={
            "title": self.paper.title,
            "doi": self.paper.doi,
            "turns": len(self.history) // 2,
            "history": self.history,
        })
