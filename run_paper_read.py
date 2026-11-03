#!/usr/bin/env python
"""文献精读 Agent —— 把一篇论文（PDF / arXiv / DOI）读成中文精读笔记。

用法：
    .venv\\Scripts\\python.exe run_paper_read.py 论文.pdf
    .venv\\Scripts\\python.exe run_paper_read.py https://arxiv.org/abs/2305.10601
    .venv\\Scripts\\python.exe run_paper_read.py 10.1038/nature12373
    .venv\\Scripts\\python.exe run_paper_read.py --folder papers --topic "金融风控"   # 多篇对比

中文文献（知网 / 万方 / 学校图书馆下载的 PDF）直接放进来读即可，不需要联网检索权限。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.cli import (  # noqa: E402
    apply_overrides,
    build_llm,
    ensure_utf8_stdout,
    make_logger,
    safe_filename,
    write_output,
)
from lit_agent.config import Settings  # noqa: E402
from lit_agent.reader import (  # noqa: E402
    compare_pdfs,
    detect_kind,
    parse_arxiv_id,
    read_arxiv,
    read_doi,
    read_pdf_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把论文读成中文精读笔记（支持本地 PDF / arXiv / DOI）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="*", help="PDF 路径、arXiv 链接/编号、或 DOI")
    parser.add_argument("--folder", help="批量读取文件夹里的所有 PDF，并生成对比综述")
    parser.add_argument("--topic", default="", help="批量对比时的研究主题")
    parser.add_argument("--model", help="覆盖模型名")
    parser.add_argument("--provider", choices=["ollama", "openai"])
    parser.add_argument("--lang", choices=["zh", "en"])
    return parser.parse_args()


def main() -> int:
    ensure_utf8_stdout()
    args = parse_args()
    log = make_logger()
    settings = apply_overrides(Settings.load(), args)

    print("=" * 70)
    print(settings.describe())
    print("=" * 70)
    llm = build_llm(settings, log)

    # ---------------- 批量读取本地 PDF ----------------
    if args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            print(f"找不到文件夹：{folder}")
            return 1
        pdfs = sorted(p for p in folder.rglob("*.pdf") if p.is_file())
        if not pdfs:
            print(f"{folder} 里没有 PDF 文件。")
            return 1
        log(f"发现 {len(pdfs)} 个 PDF，开始批量精读 ...")
        markdown, papers = compare_pdfs(pdfs, args.topic, settings, llm, log)
        stem = f"{args.topic or folder.name}-本地文献对比"
        path = write_output(stem, markdown, sidecar={
            "topic": args.topic,
            "files": [p.name for p in pdfs],
        })
        print(f"\n✓ 对比综述已保存：{path}")
        return 0

    if not args.target:
        print("请提供至少一个 PDF 路径 / arXiv 链接 / DOI，或者用 --folder 指定文件夹。")
        return 1

    # 防御：粘贴带空格的路径时，可能被拆成多个参数；如果拼起来是个真实文件就拼回去
    targets = [t.strip().strip('"').strip("'") for t in args.target]
    if len(targets) > 1:
        joined = " ".join(targets)
        if Path(joined).exists():
            targets = [joined]

    exit_code = 0
    for raw in targets:
        try:
            kind, value = detect_kind(raw)
            print(f"\n---- 处理：{raw} （类型：{kind}）----")
            if kind == "pdf":
                note = read_pdf_file(value, settings, llm, log)
                stem = f"精读-{Path(value).stem}"
            elif kind == "arxiv":
                note = read_arxiv(value, settings, llm, log)
                stem = f"精读-arXiv-{value}"
            else:
                note = read_doi(value, settings, llm, log)
                stem = f"精读-DOI-{safe_filename(value, 30)}"
            path = write_output(stem, note)
            print(f"✓ 精读笔记已保存：{path}")
        except KeyboardInterrupt:
            print("\n已中断。")
            return 1
        except Exception as exc:  # noqa: BLE001
            exit_code = 1
            print(f"✗ 处理 {raw} 失败：{type(exc).__name__}: {exc}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
