"""文献库：把多篇论文放在一起检索、问答、对比。

和单篇对话（``chat.py``）的区别
------------------------------
* 单篇对话关心"这篇论文说了什么"；
* 文献库关心"这几篇论文之间是什么关系"——谁的结论一致、谁的方法不同、共同的空白在哪。

实现要点
--------
* 所有论文的片段放进**同一个 BM25 索引**，这样分数可比；
* 引用格式统一成 ``[文献编号-p页码]``（如 ``[2-p7]``），读者能直接翻到那篇论文的那一页；
* 检索时限制**每篇论文最多命中 3 个片段**，避免某一篇长论文把其他论文挤掉；
* 对比表是"逐篇结构化抽取 → 程序拼表 → 模型写综述"，表格内容不靠模型一次生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .chat import (
    BM25,
    PaperChat,
    PaperIndex,
    _readable_ratio,
    has_cjk,
    split_sentences,
    tokenize,
    TRANSLATE_SYSTEM,
)
from .config import Settings
from .llm import ChatLLM, LLMError
from .pdf import extract_pages
from . import prompts
from .prompts import EXTRACT_SYSTEM_EN, EXTRACT_SYSTEM_ZH, extract_user
from .reader import arxiv_metadata
from .search import Paper

Log = Callable[[str], None]

LIBRARY_QA_SYSTEM = """你是文献综述助手。下面是从多篇论文里检索到的片段，每条前面有编号，
格式为 [文献编号-p页码]，例如 [2-p7] 表示第 2 篇论文的第 7 页。

规则：
1. 只依据给出的片段回答，片段里没有的就说"这些片段中没有提到"；
2. 引用必须用 [文献编号-p页码]，例如 [1-p3][2-p5]；
3. 如果用户问的是对比，请分点说明每篇论文的异同，不要混在一起讲；
4. 用中文回答，不超过 400 字，除非用户要求详细展开。"""

EXTRACT_SYSTEM = """你是文献信息抽取员。根据论文片段，抽取结构化信息，用中文填写。

只输出 JSON：
{"problem": "研究问题",
 "method": "方法与技术路线",
 "data": "数据与样本",
 "findings": "主要发现与结论",
 "limitations": "局限"}

要求：
- 写真实内容，**不要把字段说明、括号里的字数要求抄进答案**；
- 片段里没有的信息写"未提及"；
- 只依据片段，不要编造。"""

COMPARE_SYSTEM = """你是文献综述作者。下面是一组论文的结构化信息。
请直接写一段 400-600 字的中文对比综述。

硬性要求：
1. **第一句直接给结论**（例如"这组论文在 X 上一致，在 Y 上存在分歧"）；
2. 禁止复述输入的结构化信息、禁止输出"问题: … 方法: …"这类列表；
3. 按"研究问题—方法—数据—结论"的比较线索组织，指出**共识**和**分歧**；
4. 用 [编号] 标注来源；指出哪些结论有实验支撑、哪些只是作者主张；
5. 只输出 Markdown 正文，不要表格，不要小标题。"""

GAPS_SYSTEM = """你是研究规划者。下面是一组论文的结构化信息（每行一篇，带 [编号]）。
请找出这组论文**共同没有解决**的问题。

严格按以下格式输出**恰好 3 条**（不要多），每条都必须是"这几篇都没有做/没有回答"的事情：

1. **空白**：一句话说清缺什么
   - 依据：哪几篇没有涉及（用 [编号]）
   - 为什么重要：一句话

禁止：
- 不要重复论文自己的研究问题；
- 不要写"需要更多研究"这类空话；
- 不要出现两条意思相同的空白。"""


@dataclass
class LibraryEntry:
    number: int
    paper: Paper
    index: PaperIndex
    extract: dict[str, str] = field(default_factory=dict)


class PaperLibrary:
    """多篇论文的集合：检索、问答、对比、找空白。"""

    def __init__(self, settings: Settings, llm: ChatLLM, log: Log = print) -> None:
        self.settings = settings
        self.llm = llm
        self.log = log
        self.entries: list[LibraryEntry] = []
        self._bm25: BM25 | None = None
        self._rows: list[tuple[int, int, str]] = []  # (entry_idx, page, text)
        self._translation_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ 构建
    def add_pdf(self, path: str | Path) -> LibraryEntry:
        path = Path(path)
        pages, meta = extract_pages(path)
        if meta["chars"] < 500:
            raise RuntimeError(f"{path.name} 提取到的文字太少，可能是扫描版 PDF。")
        paper = Paper(
            paper_id=f"FILE:{path.name}",
            title=meta["title"] or path.stem,
            authors=[a for a in re.split(r"[;；,，]", meta["author"]) if a.strip()],
            venue="本地 PDF",
            url=str(path),
        )
        return self._add(paper, PaperIndex.from_pages(pages))

    def add_arxiv(self, arxiv_id: str) -> LibraryEntry:
        from .pdf import download_pdf

        paper = arxiv_metadata(arxiv_id)
        pdf_path = download_pdf(paper.pdf_url)
        pages, _ = extract_pages(pdf_path)
        return self._add(paper, PaperIndex.from_pages(pages))

    def _add(self, paper: Paper, index: PaperIndex) -> LibraryEntry:
        index.title = paper.title
        entry = LibraryEntry(number=len(self.entries) + 1, paper=paper, index=index)
        self.entries.append(entry)
        self._bm25 = None  # 下次检索时重建
        self.log(f"   [{entry.number}] {paper.title[:70]}（{len(index.chunks)} 片段）")
        return entry

    # ------------------------------------------------------------------ 检索
    def _rebuild(self) -> None:
        self._rows = []
        for entry_pos, entry in enumerate(self.entries):
            for chunk in entry.index.chunks:
                self._rows.append((entry_pos, chunk.page, chunk.text))
        self._bm25 = BM25([tokenize(text) for _, _, text in self._rows])

    def _translate(self, question: str) -> str:
        if question in self._translation_cache:
            return self._translation_cache[question]
        try:
            text = self.llm.chat(TRANSLATE_SYSTEM, question, max_tokens=60, temperature=0)
        except LLMError:
            return ""
        cleaned = " ".join(re.sub(r"[^A-Za-z0-9\s]", " ", text).split())[:120]
        self._translation_cache[question] = cleaned
        return cleaned

    def search(self, query: str, k: int = 8, per_paper: int = 3) -> list[dict[str, Any]]:
        """跨文献检索，返回带文献编号与页码的片段。"""
        if not self.entries:
            return []
        if self._bm25 is None:
            self._rebuild()

        def run(text: str) -> list[tuple[float, int]]:
            scores = self._bm25.scores(tokenize(text))  # type: ignore[union-attr]
            needle = text.strip().lower()
            if len(needle) >= 2:
                for i, (_, _, chunk) in enumerate(self._rows):
                    if needle in chunk.lower():
                        scores[i] += 1.5
            return [(scores[i], i) for i in range(len(self._rows)) if scores[i] > 0]

        ranked = run(query)
        translated = ""
        if len(ranked) < 2 and has_cjk(query):
            translated = self._translate(query)
            if translated:
                self.log(f"   中文问题自动转英文检索：{translated}")
                ranked = run(translated)

        ranked.sort(reverse=True)
        picked: list[dict[str, Any]] = []
        used_per_paper: dict[int, int] = {}
        for score, row_idx in ranked:
            entry_pos, page, text = self._rows[row_idx]
            if used_per_paper.get(entry_pos, 0) >= per_paper:
                continue
            used_per_paper[entry_pos] = used_per_paper.get(entry_pos, 0) + 1
            entry = self.entries[entry_pos]
            picked.append({
                "score": round(score, 2),
                "entry": entry.number,
                "title": entry.paper.title,
                "page": page,
                "text": text,
            })
            if len(picked) >= k:
                break
        if translated:
            for item in picked:
                item["query_en"] = translated
        return picked

    # ------------------------------------------------------------------ 问答
    def _context(self, hits: list[dict[str, Any]], max_chars: int = 5600) -> str:
        blocks: list[str] = []
        total = 0
        for hit in hits:
            block = f"[{hit['entry']}-p{hit['page']}] {hit['text']}"
            if total + len(block) > max_chars:
                break
            blocks.append(block)
            total += len(block)
        return "\n\n".join(blocks)

    def ask(self, question: str, k: int = 8) -> dict[str, Any]:
        # 对比类问题需要更多片段，否则每篇只能看到一两段
        compare_intent = any(word in question for word in
                             ("对比", "比较", "不同", "差异", "区别", "谁更", "compare"))
        hits = self.search(question, k=12 if compare_intent else k,
                           per_paper=4 if compare_intent else 3)
        if not hits:
            return {"answer": "文献库里没有找到相关片段，换个术语或关键词试试。", "sources": []}
        note = ""
        if hits[0].get("query_en"):
            note = f"\n\n（检索时已把问题转成英文关键词：{hits[0]['query_en']}）"
        user = (
            f"<片段>\n{self._context(hits)}\n</片段>\n\n"
            f"<问题>{question}</问题>\n\n请只依据片段回答，并标注 [文献编号-p页码]。"
        )
        try:
            answer = self.llm.chat(LIBRARY_QA_SYSTEM, user, max_tokens=900, temperature=0.2)
        except LLMError as exc:
            answer = f"模型调用失败：{exc}"
        return {"answer": answer.strip() + note, "sources": hits}

    # -------------------------------------------------------------- 对比表
    @staticmethod
    def _is_bad_value(value: str) -> bool:
        """判断抽取结果是不是把提示词里的字段说明/字段名抄回来了。"""
        text = (value or "").strip()
        if not text:
            return False
        markers = (
            "字内", "不要", "例如", "字段", "JSON",
            "研究问题", "方法与技术路线", "数据与样本", "主要发现与结论",
            "局限（", "非提及",
        )
        return any(marker in text for marker in markers)

    def _extract_entry(self, entry: LibraryEntry) -> dict[str, str]:
        """对单篇论文做结构化抽取（结果缓存）。

        用综述流水线里验证过的"标题 + 摘要/开头 + 结论"抽卡片方式：
        3B 模型对"自由片段"容易把字段说明当答案，对"摘要 + 固定格式"则稳定得多。
        """
        if entry.extract:
            return entry.extract
        index = entry.index
        head = index.chunks[0].text[:1600] if index.chunks else ""
        tail = index.chunks[-1].text[:700] if len(index.chunks) > 1 else ""
        body = f"{head}\n\n…\n\n{tail}"
        keys = ("problem", "method", "data", "findings", "limitations")

        def ask(extra: str = "") -> dict[str, str]:
            user = (
                prompts.extract_user(
                    entry.paper.title, entry.paper.title, entry.paper.venue,
                    entry.paper.year, body, "zh",
                )
                + extra
            )
            try:
                data = self.llm.chat_json(
                    prompts.EXTRACT_SYSTEM_ZH, user, max_tokens=520, temperature=0.1
                )
            except (LLMError, ValueError) as exc:
                self.log(f"   ! 第 {entry.number} 篇抽取失败：{exc}")
                data = {}
            mapping = {
                "problem": data.get("problem", ""),
                "method": data.get("method", ""),
                "data": data.get("data", ""),
                "findings": data.get("findings", ""),
                "limitations": data.get("limitations", ""),
            }
            return {key: str(mapping[key] or "未提及").strip() for key in keys}

        extract = ask()
        bad = [key for key in keys if self._is_bad_value(extract[key])]
        if bad:
            self.log(f"   ! 第 {entry.number} 篇抽取到字段说明（{bad}），重试一次")
            fixed = ask(
                "\n\n注意：上一次你把字段名或字段说明当成了答案。"
                "请写这篇论文的真实内容（中文），不要出现「研究问题」「主要发现与结论」这类字段名。"
            )
            for key in bad:
                extract[key] = fixed[key] if not self._is_bad_value(fixed[key]) else "未提及（抽取失败）"

        entry.extract = extract
        return entry.extract

    def compare(self, aspect: str = "") -> dict[str, Any]:
        """生成跨文献对比表 + 对比综述。"""
        if not self.entries:
            return {"answer": "文献库是空的。", "table": "", "sources": []}

        self.log(f"   逐篇抽取结构化信息（{len(self.entries)} 篇）...")
        rows = [self._extract_entry(entry) for entry in self.entries]

        table_lines = ["| # | 文献 | 研究问题 | 方法 | 数据 | 主要发现 | 局限 |",
                       "|---|---|---|---|---|---|---|"]
        for entry, row in zip(self.entries, rows):
            title = entry.paper.title.replace("|", "／")[:46]
            table_lines.append(
                f"| [{entry.number}] | {title} | {row['problem']} | {row['method']} | "
                f"{row['data']} | {row['findings']} | {row['limitations']} |"
            )
        table = "\n".join(table_lines)

        cards = "\n\n".join(
            f"[{entry.number}] {entry.paper.title}\n" + "\n".join(
                f"- {label}: {row[key]}" for label, key in (
                    ("问题", "problem"), ("方法", "method"), ("数据", "data"),
                    ("发现", "findings"), ("局限", "limitations"),
                )
            )
            for entry, row in zip(self.entries, rows)
        )
        aspect_note = f"用户特别关注的方面：{aspect}\n\n" if aspect else ""
        self.log("   生成对比综述 ...")
        try:
            synthesis = self.llm.chat(
                COMPARE_SYSTEM,
                f"{aspect_note}论文信息：\n\n{cards}",
                max_tokens=1200, temperature=0.3,
            )
        except LLMError as exc:
            synthesis = f"对比综述生成失败：{exc}"

        answer = f"{synthesis.strip()}\n\n### 对比表\n\n{table}"
        return {
            "answer": answer,
            "table": table,
            "synthesis": synthesis.strip(),
            "sources": [
                {"entry": entry.number, "title": entry.paper.title}
                for entry in self.entries
            ],
        }

    # -------------------------------------------------------------- 研究空白
    def gaps(self) -> dict[str, Any]:
        if not self.entries:
            return {"answer": "文献库是空的。", "sources": []}
        rows = [self._extract_entry(entry) for entry in self.entries]
        cards = "\n\n".join(
            f"[{entry.number}] {entry.paper.title}\n"
            f"- 问题: {row['problem']}\n- 方法: {row['method']}\n"
            f"- 发现: {row['findings']}\n- 局限: {row['limitations']}"
            for entry, row in zip(self.entries, rows)
        )
        try:
            answer = self.llm.chat(
                GAPS_SYSTEM, f"论文信息：\n\n{cards}",
                max_tokens=900, temperature=0.2,
            )
        except LLMError as exc:
            answer = f"研究空白生成失败：{exc}"
        answer = answer.strip()
        answer += (
            "\n\n> 以上空白由模型基于逐篇卡片推断，"
            "**请人工确认是否真的未被这些论文覆盖**（模型可能误判某篇已经做过）。"
        )
        return {
            "answer": answer,
            "sources": [
                {"entry": entry.number, "title": entry.paper.title}
                for entry in self.entries
            ],
        }

    # ------------------------------------------------------------------ 导出
    def transcript(self, title: str = "文献库") -> str:
        lines = [f"# {title}：文献库记录", ""]
        lines.append("## 文献清单")
        lines.append("")
        for entry in self.entries:
            paper = entry.paper
            lines.append(
                f"[{entry.number}] {paper.title}（{paper.year or 'n.d.'}"
                f"{', ' + paper.venue if paper.venue else ''}）"
            )
        lines.append("")
        return "\n".join(lines)

    def save(self, name: str, extra: str = "") -> Path:
        from .cli import write_output

        content = self.transcript(name)
        if extra:
            content += "\n" + extra
        return write_output(name, content, sidecar={
            "papers": [
                {"number": e.number, "title": e.paper.title, "doi": e.paper.doi,
                 "file": e.paper.url}
                for e in self.entries
            ],
        })
