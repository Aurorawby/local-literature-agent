"""模型对比：同一个抽取任务，本地 3B vs 7B 在"结构化输出可靠性"上的差异。

为什么只比抽取环节
------------------
完整综述跑一次 7B 在 CPU 上要十几分钟，不适合反复对比；而**结构化抽取**是
整条流水线里最容易暴露小模型问题的一步（字段名回显、空值、JSON 不合法），
也是后面综述质量的输入。用 3 篇论文 × 2 个模型做同口径对比，成本可控、结论可解释。

指标：
* 单篇耗时；
* 字段回显次数（把"研究问题"这类字段名当答案输出——3B 的典型问题）；
* 有效字段率（非空且非"未提及"的字段占比）；
* JSON 解析失败次数。

用法：
    .venv\\Scripts\\python.exe eval\\compare_models.py
    .venv\\Scripts\\python.exe eval\\compare_models.py --models qwen2.5:3b,qwen2.5:7b
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.config import Settings  # noqa: E402
from lit_agent.library import PaperLibrary  # noqa: E402
from lit_agent.llm import ChatLLM, LLMError  # noqa: E402

PAPERS = ["2305.10601", "2201.11903", "2203.11171"]
FIELDS = ("problem", "method", "data", "findings", "limitations")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="qwen2.5:3b,qwen2.5:7b")
    parser.add_argument("--papers", default=",".join(PAPERS))
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    papers = [p.strip() for p in args.papers.split(",") if p.strip()]
    base = Settings.load()

    results: dict[str, list[dict]] = {}
    for model in models:
        print(f"\n=== 模型 {model} ===")
        settings = Settings.load()
        settings.model = model
        settings.provider = "ollama"
        llm = ChatLLM(settings)
        try:
            print(llm.ping())
        except LLMError as exc:
            print(f"  ✗ 模型不可用，跳过：{exc}")
            continue

        library = PaperLibrary(settings, llm, lambda *_: None)
        rows: list[dict] = []
        for arxiv_id in papers:
            try:
                entry = library.add_arxiv(arxiv_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 加载 {arxiv_id} 失败：{exc}")
                continue
            started = time.perf_counter()
            extract = library._extract_entry(entry)
            elapsed = round(time.perf_counter() - started, 1)
            echo = [key for key in FIELDS if library._is_bad_value(extract.get(key, ""))]
            blank = [key for key in FIELDS if extract.get(key, "").strip() in {"", "未提及"}]
            valid = len(FIELDS) - len(set(echo) | set(blank))
            rows.append({
                "arxiv_id": arxiv_id,
                "title": entry.paper.title[:70],
                "elapsed_s": elapsed,
                "echo_fields": echo,
                "blank_fields": blank,
                "valid_fields": valid,
                "extract": extract,
            })
            print(f"  {arxiv_id}  {elapsed:>5.1f}s  有效字段 {valid}/5  "
                  f"回显 {echo or '无'}")
        results[model] = rows

    # ---------- 输出 ----------
    if not results:
        print("没有可用的模型结果。")
        return 1

    lines = [
        "# 模型对比：结构化抽取可靠性（3B vs 7B）",
        "",
        f"- 论文：{', '.join(f'`{p}`' for p in papers)}（同一批 PDF、同一套提示词、temperature=0.1）",
        "- 任务：逐篇抽取「研究问题 / 方法 / 数据 / 发现 / 局限」五个字段",
        "- 判定：字段回显 = 输出里出现字段名或说明文字（小模型典型失败模式）；有效字段 = 非空且非「未提及」",
        "",
        "## 总览",
        "",
        "| 模型 | 平均耗时/篇 | 有效字段率 | 字段回显次数 | 有回显的篇数 |",
        "|---|---|---|---|---|",
    ]

    summary_rows = []
    for model, rows in results.items():
        if not rows:
            continue
        times = [r["elapsed_s"] for r in rows]
        valid = sum(r["valid_fields"] for r in rows)
        echo_count = sum(len(r["echo_fields"]) for r in rows)
        echo_papers = sum(1 for r in rows if r["echo_fields"])
        summary_rows.append((model, statistics.mean(times), valid / (len(rows) * 5) * 100,
                             echo_count, echo_papers, len(rows)))
        lines.append(
            f"| {model} | {statistics.mean(times):.1f}s | {valid / (len(rows) * 5) * 100:.0f}% | "
            f"{echo_count} | {echo_papers}/{len(rows)} |"
        )

    lines += ["", "## 逐篇明细", "",
              "| 模型 | 论文 | 耗时 | 有效字段 | 回显字段 |",
              "|---|---|---|---|---|"]
    for model, rows in results.items():
        for row in rows:
            lines.append(
                f"| {model} | {row['arxiv_id']} | {row['elapsed_s']}s | {row['valid_fields']}/5 | "
                f"{'、'.join(row['echo_fields']) or '无'} |"
            )

    lines += ["", "## 抽取结果原文（便于人工核对）", ""]
    for model, rows in results.items():
        for row in rows:
            lines.append(f"### {model} · {row['arxiv_id']} {row['title']}")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(row["extract"], ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")

    lines += [
        "## 结论（自动生成，需人工确认）",
        "",
    ]
    if len(summary_rows) >= 2:
        (m1, t1, v1, e1, ep1, n1), (m2, t2, v2, e2, ep2, n2) = summary_rows[0], summary_rows[1]
        lines.append(f"- 有效字段率：{m1} {v1:.0f}% vs {m2} {v2:.0f}%")
        lines.append(f"- 平均耗时：{m1} {t1:.1f}s vs {m2} {t2:.1f}s（{t2 / t1:.1f}×）")
        lines.append(f"- 字段回显：{m1} {e1} 次 / {m2} {e2} 次")
        lines.append("- ⚠️ 样本仅 3 篇，结论是方向性的；7B 在 CPU 上明显更慢，"
                     "是否值得取决于用户对质量的容忍度。")
    lines += ["", "> 复跑：`.venv\\Scripts\\python.exe eval/compare_models.py`", ""]

    out = ROOT / "eval" / "MODEL_COMPARISON.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "eval" / "model_comparison.json").write_text(
        json.dumps({model: [{k: v for k, v in r.items()} for r in rows]
                    for model, rows in results.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n" + "=" * 70)
    for model, t, v, e, ep, n in summary_rows:
        print(f"{model}: 平均 {t:.1f}s/篇，有效字段 {v:.0f}%，回显 {e} 次（{ep}/{n} 篇）")
    print("=" * 70)
    print(f"已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
