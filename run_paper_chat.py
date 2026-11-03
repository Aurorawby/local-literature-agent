#!/usr/bin/env python
"""论文对话 Agent —— 加载一篇论文，然后围绕它连续提问。

用法：
    .venv\\Scripts\\python.exe run_paper_chat.py 论文.pdf
    .venv\\Scripts\\python.exe run_paper_chat.py 2305.10601
    .venv\\Scripts\\python.exe run_paper_chat.py --question "核心方法是什么" 2305.10601

进入对话后可以用这些命令：
    /claims [关注点]   提炼可以直接写进论文的观点
    /critique [关注点] 以审稿人视角挑方法与局限的毛病
    /quote 关键词      在原文里找包含关键词的句子（用于直接引用）
    /save              保存对话记录到 output/
    /help              查看命令
    /exit              退出（会自动保存）
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.chat import PaperChat, load_paper  # noqa: E402
from lit_agent.cli import apply_overrides, build_llm, ensure_utf8_stdout, make_logger  # noqa: E402
from lit_agent.config import Settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="和一篇论文对话：提问、提炼可写观点、摘原文引文。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target", nargs="+",
        help="PDF 路径 / arXiv 链接或编号 / DOI（路径含空格时可以直接粘贴，会自动拼接）",
    )
    parser.add_argument("--question", "-q", help="只问一个问题就退出（不进入交互）")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--provider", choices=["ollama", "openai"])
    parser.add_argument("--lang", choices=["zh", "en"])
    return parser.parse_args()


def print_sources(sources: list[dict], snippet_chars: int = 420) -> None:
    if not sources:
        return
    pages = sorted({s["page"] for s in sources})
    print(f"   📎 依据页码：{', '.join('p%d' % p for p in pages)}")
    for i, source in enumerate(sources, 1):
        text = source.get("text") or source.get("snippet") or ""
        text = " ".join(text.split())
        shown = text[:snippet_chars] + (" …（输入 /sources 看完整片段）" if len(text) > snippet_chars else "")
        print(f"      [{i}] p{source['page']}  {shown}")


def main() -> int:
    ensure_utf8_stdout()
    args = parse_args()
    log = make_logger(quiet=True)
    settings = apply_overrides(Settings.load(), args)

    print("=" * 70)
    print(settings.describe())
    print("=" * 70)

    llm = build_llm(settings, log)

    # 路径可能被空格拆成多个参数（例如直接粘贴带空格的路径、或路径里有括号），这里拼回去
    target = " ".join(args.target).strip().strip('"').strip("'")
    try:
        index, paper = load_paper(target, log)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 加载论文失败：{type(exc).__name__}: {exc}")
        return 1

    chat = PaperChat(index, paper, settings, llm, log)
    print(f"\n📄 {paper.title}")
    if paper.year:
        print(f"   {paper.year} ｜ {paper.venue}")
    print(f"   已切分为 {len(index.chunks)} 个片段，共 {index.meta.get('pages', '?')} 页")

    # 单次问答模式
    if args.question:
        result = chat.ask(args.question)
        print(f"\n❓ {args.question}\n")
        print(result["answer"])
        print_sources(result["sources"])
        path = chat.save(f"对话-{paper.title[:30]}")
        print(f"\n✓ 已保存：{path}")
        return 0

    print("\n可以开始提问了。输入 /help 查看命令，/exit 退出（会自动保存记录）。\n")
    while True:
        try:
            line = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue

        lowered = line.lower()
        if lowered in {"/exit", "exit", "quit", "/quit"}:
            break
        if lowered in {"/help", "help", "?"}:
            print(
                "\n  /claims [关注点]   提炼可写进论文的观点\n"
                "  /critique [关注点] 审稿人视角挑毛病\n"
                "  /quote 关键词      找原文里的句子（直接引用用）\n"
                "  /sources [N]       查看上一轮依据片段（N 只看第 N 条）\n"
                "  /save              保存对话记录\n"
                "  /exit              退出\n"
                "\n  提示：问题里带「详细」「展开」会得到更长的回答。\n"
            )
            continue
        if lowered.startswith("/sources"):
            if not chat.last_sources:
                print("\n  还没有检索记录，先问一个问题。\n")
                continue
            arg = line[len("/sources"):].strip()
            picks = chat.last_sources
            if arg.isdigit():
                idx = int(arg) - 1
                picks = [chat.last_sources[idx]] if 0 <= idx < len(chat.last_sources) else []
            if not picks:
                print("\n  编号超出范围。\n")
                continue
            for i, source in enumerate(picks, 1):
                print(f"\n  ── 片段 {i} ｜ 第 {source['page']} 页 ｜ "
                      f"{source.get('chars', '?')} 字 ──")
                print(textwrap.fill(" ".join((source.get('text') or '').split()), width=100))
            print()
            continue

        try:
            if lowered.startswith("/claims"):
                focus = line[len("/claims"):].strip()
                result = chat.claims(focus)
            elif lowered.startswith("/critique"):
                focus = line[len("/critique"):].strip()
                result = chat.critique(focus)
            elif lowered.startswith("/quote"):
                keyword = line[len("/quote"):].strip()
                hits = chat.quote(keyword)
                if not hits:
                    print(f"\n  没有找到包含「{keyword}」的句子。\n")
                else:
                    print(f"\n  「{keyword}」在原文中的出现（可用于直接引用）：\n")
                    for hit in hits:
                        print(f"  [p{hit['page']}] {hit['sentence']}\n")
                continue
            elif lowered.startswith("/save"):
                path = chat.save(f"对话-{paper.title[:30]}")
                print(f"\n  ✓ 已保存：{path}\n")
                continue
            else:
                result = chat.ask(line)
        except Exception as exc:  # noqa: BLE001
            print(f"\n✗ 出错：{type(exc).__name__}: {exc}\n")
            continue

        print(f"\n助手 >\n{result['answer']}\n")
        print_sources(result["sources"])
        print()

    path = chat.save(f"对话-{paper.title[:30]}")
    print(f"\n✓ 对话已保存：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
