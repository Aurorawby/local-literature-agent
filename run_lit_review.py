#!/usr/bin/env python
"""文献综述 Agent —— 输入一个研究主题，自动检索学术论文并生成中文综述。

用法：
    .venv\\Scripts\\python.exe run_lit_review.py "大语言模型在金融风控中的应用"
    .venv\\Scripts\\python.exe run_lit_review.py            # 交互式输入
    .venv\\Scripts\\python.exe run_lit_review.py "生成式AI" --rounds 1 --papers 3   # 快速试跑
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.cli import (  # noqa: E402
    apply_overrides,
    build_llm,
    ensure_utf8_stdout,
    make_logger,
    write_output,
)
from lit_agent.config import Settings  # noqa: E402
from lit_agent.graph import build_graph  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="根据研究主题检索学术文献，生成带参考文献的中文综述。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("topic", nargs="*", help="研究主题（不加参数则进入交互输入）")
    parser.add_argument("--rounds", type=int, help="研究轮数，1=快 2=标准 3=深挖")
    parser.add_argument("--papers", type=int, help="每轮精读篇数")
    parser.add_argument("--model", help="覆盖模型名，例如 qwen2.5:7b")
    parser.add_argument("--provider", choices=["ollama", "openai"], help="模型提供方")
    parser.add_argument("--lang", choices=["zh", "en"], help="输出语言")
    parser.add_argument("--year-from", type=int, dest="year_from", help="检索年份下限，0 表示不限")
    parser.add_argument("--fulltext", action="store_true", help="下载 arXiv 全文精读（更慢）")
    parser.add_argument("--quiet", action="store_true", help="少打印中间过程")
    return parser.parse_args()


def main() -> int:
    ensure_utf8_stdout()
    args = parse_args()
    log = make_logger(quiet=args.quiet)

    topic = " ".join(args.topic).strip().strip('"').strip("'")
    if not topic:
        try:
            topic = input("请输入研究主题（例如：大语言模型在金融风控中的应用）：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
    if not topic:
        print("没有输入主题，退出。")
        return 1

    settings = apply_overrides(Settings.load(), args)

    print("=" * 70)
    print(f"研究主题：{topic}")
    print(settings.describe())
    print("=" * 70)

    llm = build_llm(settings, log)
    graph = build_graph(settings, llm, log)

    initial = {"topic": topic, "language": settings.language}
    merged: dict = {}
    try:
        for event in graph.stream(initial, {"recursion_limit": 200}):
            for node, update in event.items():
                if isinstance(update, dict):
                    merged.update(update)
    except KeyboardInterrupt:
        print("\n\n已中断。已完成的精读卡片仍会保存。")
    except Exception as exc:  # noqa: BLE001
        print(f"\n运行出错：{type(exc).__name__}: {exc}")
        if not merged.get("extracts"):
            print("提示：检查 Ollama 是否启动、模型是否已下载（ollama list）、网络是否可用。")
            return 1

    report = merged.get("report") or ""
    if not report:
        print("没有得到报告内容，无法保存。")
        return 1

    verification = merged.get("verification") or {}
    metrics = verification.get("metrics") or {}

    md_path = write_output(f"{topic}-文献综述", report, sidecar={
        "topic": topic,
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "model": f"{settings.provider}/{settings.model}",
        "settings": {
            "rounds": settings.rounds,
            "papers_per_round": settings.papers_per_round,
            "year_from": settings.year_from,
            "language": settings.language,
            "verify_citations": settings.verify_citations,
        },
        "queries": merged.get("all_queries", []),
        "gaps": merged.get("gaps", []),
        "papers": merged.get("extracts", []),
        "draft": merged.get("draft", ""),
        "verification": {
            "metrics": metrics,
            "items": verification.get("items", []),
        },
    })

    print()
    print("=" * 70)
    print(f"✓ 综述已保存：{md_path}")
    print(f"✓ 结构化数据：{md_path.with_suffix('.json').name}")
    if metrics.get("citation_marks") is not None:
        print(
            f"✓ 引用核验：{metrics.get('citation_marks', 0)} 处引用，"
            f"有效 {metrics.get('valid_citation_marks', 0)} 处，"
            f"数字回查 {metrics.get('numbers_found', 0)}/{metrics.get('numbers_checked', 0)}"
            + (f"，数字可追溯率 {metrics.get('number_rate')}%"
               if metrics.get("number_rate") is not None else "")
        )
    print("=" * 70)
    print()
    print(report[:1500] + ("\n...（完整内容见文件）" if len(report) > 1500 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
