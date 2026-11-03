"""用户测试结果分析：读 records.csv + sus.csv，输出 RESULTS.md。

用法：
    .venv\\Scripts\\python.exe user_test/analysis.py            # 默认读 user_test/results
    .venv\\Scripts\\python.exe user_test/analysis.py 某目录

口径见 user_test/README.md：
* 任务 1：找 10 篇相关文献，比较手工 vs 工具的耗时与相关篇数；
* 任务 2：3 条观点里能直接用的比例；
* SUS：标准 10 题换算 0-100。
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as handle:
        return [row for row in csv.DictReader(handle) if (row.get("participant_id") or "").strip()]


def number(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def sus_score(row: dict[str, str]) -> float:
    total = 0.0
    for i in range(1, 11):
        value = number(row.get(f"q{i}"), 3)
        total += (value - 1) if i % 2 == 1 else (5 - value)
    return round(total * 2.5, 1)


def fmt(values: list[float], digits: int = 1) -> str:
    if not values:
        return "—"
    return f"{statistics.mean(values):.{digits}f}"


def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "user_test" / "results"
    records = read_csv(directory / "records.csv")
    sus_rows = read_csv(directory / "sus.csv")

    if not records:
        print(f"没找到 {directory / 'records.csv'}")
        print("请先用 user_test/timer.py 记录被试数据；列格式见 user_test/record_template.csv。")
        return 1

    n = len(records)
    manual_minutes = [number(r["manual_minutes"]) for r in records]
    tool_minutes = [number(r["tool_minutes"]) for r in records]
    manual_relevant = [number(r["manual_relevant"]) for r in records]
    tool_relevant = [number(r["tool_relevant"]) for r in records]
    wait_minutes = [number(r.get("tool_wait_minutes", 0)) for r in records]
    claims_total = [number(r.get("claims_total", 3)) for r in records]
    claims_usable = [number(r.get("claims_usable", 0)) for r in records]

    manual_mean = statistics.mean(manual_minutes)
    tool_mean = statistics.mean(tool_minutes)
    saving = (manual_mean - tool_mean) / manual_mean * 100 if manual_mean else 0

    def success(relevant: list[float]) -> float:
        return statistics.mean([min(1.0, v / 10) for v in relevant]) * 100

    usable_rate = (
        sum(claims_usable) / sum(claims_total) * 100 if sum(claims_total) else 0
    )

    scores = [sus_score(row) for row in sus_rows] if sus_rows else []
    stuck = {}
    for row in records:
        key = (row.get("stuck_category") or "未记录").strip() or "未记录"
        stuck[key] = stuck.get(key, 0) + 1

    lines = [
        "# 用户测试结果",
        "",
        f"- 被试数：**{n}** 人",
        f"- 数据日期：{records[0].get('date', '—')} ~ {records[-1].get('date', '—')}",
        f"- 主题：{'、'.join(sorted({r.get('theme', '') for r in records}))}",
        "",
        "## 一、任务 1：找 10 篇相关文献",
        "",
        "| 条件 | 平均耗时 | 中位数耗时 | 平均相关篇数 | 成功率 |",
        "|---|---|---|---|---|",
        f"| 手工 | {fmt(manual_minutes)} 分钟 | {statistics.median(manual_minutes):.1f} 分钟 | "
        f"{fmt(manual_relevant)} / 10 | {success(manual_relevant):.0f}% |",
        f"| 工具 | {fmt(tool_minutes)} 分钟 | {statistics.median(tool_minutes):.1f} 分钟 | "
        f"{fmt(tool_relevant)} / 10 | {success(tool_relevant):.0f}% |",
        "",
        f"- **平均节省时间：{saving:.0f}%**（工具比手工快 {manual_mean - tool_mean:.1f} 分钟）",
        f"- 工具阶段中，等待模型/检索的平均时间：{fmt(wait_minutes)} 分钟",
        "",
        "## 二、任务 2：可写进论文的观点",
        "",
        f"- 每人挑 {fmt(claims_total, 0)} 条，其中能直接用的 {fmt(claims_usable)} 条",
        f"- **观点可用率：{usable_rate:.0f}%**",
        "",
        "## 三、主观可用性（SUS）",
        "",
    ]
    if scores:
        lines += [
            f"- 平均 SUS：**{statistics.mean(scores):.1f}**（范围 {min(scores):.0f}–{max(scores):.0f}）",
            "",
            "| 被试 | SUS |",
            "|---|---|",
        ]
        lines += [f"| {row['participant_id']} | {sus_score(row):.0f} |" for row in sus_rows]
    else:
        lines.append("（未提交 sus.csv，跳过）")
    lines += [
        "",
        "> 参考：SUS > 68 通常视为高于平均水平；> 80 为优秀。样本量 5，仅作方向性参考。",
        "",
        "## 四、卡点分布",
        "",
        "| 类别 | 人数 |",
        "|---|---|",
    ]
    lines += [f"| {key} | {value} |" for key, value in sorted(stuck.items(), key=lambda kv: -kv[1])]
    stuck_details = [r for r in records if (r.get("first_stuck") or "无").strip() not in {"", "无"}]
    if stuck_details:
        lines += ["", "具体卡点：", ""]
        lines += [f"- {r['participant_id']}：{r['first_stuck']}" for r in stuck_details]

    feedback = [r for r in records if (r.get("open_feedback") or "").strip()]
    if feedback:
        lines += ["", "## 五、原话反馈", ""]
        lines += [f"- **{r['participant_id']}**：{r['open_feedback']}" for r in feedback]

    lines += [
        "",
        "## 六、结论（自动生成，需人工确认）",
        "",
    ]
    if n < 5:
        lines.append(f"- ⚠️ 样本量只有 {n} 人，不足 5 人，结论仅供参考，不要写进作品集。")
    else:
        lines.append(f"- {n} 人中有 {sum(1 for r in records if number(r['tool_relevant']) >= 7)} 人"
                     "用工具找到 ≥7 篇相关文献。")
        lines.append(f"- 工具平均{'更快' if saving > 0 else '更慢'} {abs(saving):.0f}%"
                     f"（{manual_mean:.1f} → {tool_mean:.1f} 分钟）。")
        if scores:
            mean_sus = statistics.mean(scores)
            verdict = "达到或高于" if mean_sus >= 68 else "低于"
            lines.append(f"- SUS {mean_sus:.0f}：{verdict}常用基准 68。")
        if wait_minutes and tool_mean:
            lines.append(f"- 等待时间占工具阶段 {statistics.mean(wait_minutes) / tool_mean * 100:.0f}%，"
                         "这是明确的优化方向（换 7B / 云端模型 / 缓存）。")
        lines.append("- ⚠️ 5 人测试不做显著性检验；结论是方向性的，需在作品集里如实标注样本量。")

    directory.mkdir(parents=True, exist_ok=True)
    out = directory / "RESULTS.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已生成：{out}")
    print("\n".join(lines[:18]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
