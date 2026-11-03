#!/usr/bin/env python
"""文献库对话 Agent —— 把多篇论文放在一起问答、对比、找研究空白。

用法：
    # 读一个文件夹里的所有 PDF
    .venv\\Scripts\\python.exe run_library_chat.py --folder papers

    # 直接给几篇 arXiv 论文
    .venv\\Scripts\\python.exe run_library_chat.py 2305.10601 2201.11903 2203.11171

    # 一次问一个问题
    .venv\\Scripts\\python.exe run_library_chat.py --folder papers -q "这几篇的方法有什么分歧？"

进入对话后的命令：
    /compare [关注点]   生成跨文献对比表 + 对比综述
    /gaps               找这几篇共同没有解决的问题
    /list               列出文献库
    /save               保存记录到 output/
    /help               查看帮助
    /exit               退出
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.cli import apply_overrides, build_llm, ensure_utf8_stdout, make_logger  # noqa: E402
from lit_agent.config import Settings  # noqa: E402
from lit_agent.library import PaperLibrary  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="多篇论文的文献库：跨文献问答、对比、找研究空白。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets", nargs="*", help="PDF 路径或 arXiv 编号（可多个）")
    parser.add_argument("--folder", help="读取文件夹里的所有 PDF")
    parser.add_argument("--question", "-q", help="只问一个问题就退出")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--provider", choices=["ollama", "openai"])
    parser.add_argument("--lang", choices=["zh", "en"])
    return parser.parse_args()


def looks_like_arxiv(value: str) -> bool:
    import re

    return bool(re.search(r"\d{4}\.\d{4,5}", value))


def print_sources(sources: list[dict]) -> None:
    if not sources:
        return
    entries = sorted({s["entry"] for s in sources if "entry" in s})
    print(f"   📎 涉及文献：{', '.join('[%d]' % e for e in entries)}")
    pages: dict[int, set[int]] = {}
    for source in sources:
        if "page" in source:
            pages.setdefault(source["entry"], set()).add(source["page"])
    for entry, plist in sorted(pages.items()):
        print(f"      [{entry}] 页码 {', '.join('p%d' % p for p in sorted(plist))}")


def main() -> int:
    ensure_utf8_stdout()
    args = parse_args()
    log = make_logger(quiet=True)
    settings = apply_overrides(Settings.load(), args)

    print("=" * 70)
    print(settings.describe())
    print("=" * 70)

    llm = build_llm(settings, log)
    library = PaperLibrary(settings, llm, log)

    # ---------------- 加载 ----------------
    print("\n加载文献 ...")
    targets: list[str] = list(args.targets)
    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            print(f"找不到文件夹：{folder}")
            return 1
        targets += [str(p) for p in sorted(folder.rglob("*.pdf")) if p.is_file()]

    if not targets:
        print("没有可加载的文献。用 --folder 指定文件夹，或直接给 PDF 路径 / arXiv 编号。")
        return 1

    for target in targets:
        try:
            if Path(target).exists() and target.lower().endswith(".pdf"):
                library.add_pdf(target)
            elif looks_like_arxiv(target):
                library.add_arxiv(target)
            else:
                print(f"   ! 跳过无法识别的输入：{target}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! 加载失败 {target}：{type(exc).__name__}: {exc}")

    if not library.entries:
        print("没有成功加载任何文献。")
        return 1
    if len(library.entries) < 2:
        print("\n⚠️  只有 1 篇文献，跨文献对比意义不大；建议至少 3 篇。")

    # ---------------- 单次提问 ----------------
    if args.question:
        print(f"\n❓ {args.question}\n")
        result = library.ask(args.question)
        print(result["answer"])
        print_sources(result["sources"])
        path = library.save(f"文献库-{len(library.entries)}篇")
        print(f"\n✓ 已保存：{path}")
        return 0

    print("\n可以开始提问了。输入 /help 看命令，/exit 退出。\n")
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
                "\n  /compare [关注点]  生成对比表 + 对比综述\n"
                "  /gaps              找共同的研究空白\n"
                "  /list              列出文献库\n"
                "  /save              保存记录\n"
                "  /exit              退出\n"
            )
            continue
        if lowered.startswith("/list"):
            for entry in library.entries:
                print(f"  [{entry.number}] {entry.paper.title[:80]}")
            print()
            continue
        if lowered.startswith("/save"):
            path = library.save(f"文献库-{len(library.entries)}篇")
            print(f"\n  ✓ 已保存：{path}\n")
            continue

        try:
            if lowered.startswith("/compare"):
                aspect = line[len("/compare"):].strip()
                print("\n  正在逐篇抽取并对比（可能需要 1-3 分钟）...\n")
                result = library.compare(aspect)
                print(f"\n助手 >\n{result['answer']}\n")
                continue
            if lowered.startswith("/gaps"):
                print("\n  正在找研究空白...\n")
                result = library.gaps()
                print(f"\n助手 >\n{result['answer']}\n")
                continue
            result = library.ask(line)
        except Exception as exc:  # noqa: BLE001
            print(f"\n✗ 出错：{type(exc).__name__}: {exc}\n")
            continue

        print(f"\n助手 >\n{result['answer']}\n")
        print_sources(result["sources"])
        print()

    path = library.save(f"文献库-{len(library.entries)}篇")
    print(f"\n✓ 记录已保存：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
