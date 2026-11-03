"""文献 Agent 环境自检：出问题时先跑这个，五秒钟定位是哪一环坏了。

用法：双击 环境自检.bat，或
      .venv\\Scripts\\python.exe .tools\\selftest_lit.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

results: list[tuple[bool, str, str]] = []


def check(name: str):
    def wrapper(func):
        try:
            detail = func() or ""
            results.append((True, name, detail))
            print(f"  ✓ {name}  {detail}")
        except Exception as exc:  # noqa: BLE001
            results.append((False, name, str(exc)))
            print(f"  ✗ {name}  {exc}")
        return func
    return wrapper


def section(title: str) -> None:
    print(f"\n[{title}]")


# ---------------------------------------------------------------- 1. 基础环境
section("1. Python 与依赖")
check("Python 版本")(lambda: f"{sys.version.split()[0]}（{sys.executable}）")


@check("核心依赖")
def _deps():
    import httpx  # noqa: F401
    import langgraph  # noqa: F401
    import pymupdf  # noqa: F401
    import streamlit  # noqa: F401
    return "langgraph / httpx / pymupdf / streamlit 均可导入"


# ---------------------------------------------------------------- 2. 配置
section("2. 配置")
from lit_agent.config import Settings  # noqa: E402


@check("配置文件 .env")
def _config():
    settings = Settings.load()
    return (f"provider={settings.provider}, model={settings.model}, "
            f"轮数={settings.rounds}, 每轮篇数={settings.papers_per_round}")


# ---------------------------------------------------------------- 3. 模型
section("3. 本地模型")
from lit_agent.llm import ChatLLM  # noqa: E402

settings = Settings.load()
llm = ChatLLM(settings)


@check("Ollama 服务")
def _ping():
    return llm.ping()


# ---------------------------------------------------------------- 4. 检索
section("4. 学术检索源")
from lit_agent.search import (  # noqa: E402
    Http,
    SourceRateLimited,
    arxiv_search,
    crossref_search,
    doaj_search,
)

http = Http()
query = "large language model credit risk"

for name, func in (
    ("Crossref", lambda: crossref_search(query, rows=3, year_from=2019, http=http)),
    ("DOAJ", lambda: doaj_search(query, page_size=3, year_from=2019, http=http)),
    ("arXiv", lambda: arxiv_search(query, max_results=3, http=http)),
):
    def _make(f=func, n=name):
        def run():
            try:
                papers = f()
            except SourceRateLimited as exc:
                return f"跳过（限流/配额）：{str(exc)[:50]}"
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
            if not papers:
                raise RuntimeError("返回 0 篇（可能网络受限）")
            return f"{len(papers)} 篇，例如：{papers[0].title[:40]}"
        return run
    check(name)(_make())


# ---------------------------------------------------------------- 5. 结构化输出
section("5. 模型结构化输出（JSON）")


@check("JSON 抽取能力")
def _json():
    data = llm.chat_json(
        "你是文献助手，只输出 JSON。",
        '把这句话结构化：LLM 在 120 万条贷款记录上把 AUC 从 0.71 提升到 0.78。'
        '输出 {"finding": "...", "number": "..."}',
        max_tokens=120,
    )
    if not data:
        raise RuntimeError("返回空 JSON")
    return f"返回键：{list(data)}"


# ---------------------------------------------------------------- 6. 引用核验
section("6. 引用核验（确定性检查）")


@check("数字回查与编号检查")
def _verify():
    from lit_agent.verify import render_section, verify_citations

    draft = "模型把 AUC 从 0.71 提升到 0.78[1]，样本量为 120 万条记录[2]。"
    extracts = [
        {"index": 1, "title": "A", "abstract": "We improve AUC from 0.71 to 0.78."},
        {"index": 2, "title": "B", "abstract": "Trained on 1.2 million loan records."},
    ]
    result = verify_citations(draft, extracts, llm=None, semantic_sentences=0)
    metrics = result["metrics"]
    if metrics["invalid_citation_marks"]:
        raise RuntimeError("编号检查异常")
    section_text = render_section(result, extracts)
    if "数字回查" not in section_text:
        raise RuntimeError("渲染异常")
    return (f"引用 {metrics['citation_marks']} 处 / 数字回查 "
            f"{metrics['numbers_found']}/{metrics['numbers_checked']}")


# ---------------------------------------------------------------- 7. 论文对话
section("7. 论文对话 / 文献库检索（BM25，确定性）")


@check("片段检索与页码")
def _chat():
    from lit_agent.chat import PaperIndex

    text = (
        "We propose Tree of Thoughts (ToT) as a new framework.\n\n"
        "Experimental results show ToT reaches 74% success rate on Game of 24.\n\n"
        "The main limitation is higher compute cost."
    )
    index = PaperIndex.from_text(text)
    hits = index.search("74% success rate Game of 24", k=2)
    if not hits:
        raise RuntimeError("检索不到相关片段")
    return f"{len(index.chunks)} 个片段，首选 p{hits[0][1].page}（得分 {hits[0][0]:.1f}）"


@check("跨文献检索（每篇上限 3 片段）")
def _library():
    from lit_agent.chat import PaperIndex
    from lit_agent.config import Settings
    from lit_agent.library import LibraryEntry, PaperLibrary
    from lit_agent.search import Paper

    class _NullLLM:
        def chat(self, *a, **k):
            raise RuntimeError("自检不应调用模型")

        def chat_json(self, *a, **k):
            raise RuntimeError("自检不应调用模型")

    library = PaperLibrary(Settings.load(), _NullLLM(), lambda *_: None)
    for i in range(2):
        paper = Paper(paper_id=f"T{i}", title=f"Test Paper {i}", year=2024)
        index = PaperIndex.from_text(
            f"Paper {i} uses method {i}. Results show 74% accuracy on dataset {i}."
        )
        library._add(paper, index)
    hits = library.search("74% accuracy")
    if not hits:
        raise RuntimeError("跨文献检索无结果")
    entries = {h["entry"] for h in hits}
    return f"{len(library.entries)} 篇文献，命中 {len(hits)} 个片段，涉及 {len(entries)} 篇"


# ---------------------------------------------------------------- 汇总
passed = sum(1 for ok, _, _ in results if ok)
total = len(results)
print("\n" + "=" * 60)
print(f"自检结果：{passed}/{total} 通过")
if passed < total:
    print("以下检查未通过：")
    for ok, name, detail in results:
        if not ok:
            print(f"  - {name}: {detail}")
    print("\n对照 使用指南.md 第 5 节“遇到问题怎么办”排查。")
else:
    print("一切正常，可以开始跑文献综述了。")
print("=" * 60)
sys.exit(0 if passed == total else 1)
