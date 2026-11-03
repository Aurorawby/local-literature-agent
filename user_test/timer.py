"""用户测试计时器：按回车开始/结束，自动写入 CSV，避免事后回忆记错。

用法：
    .venv\\Scripts\\python.exe user_test/timer.py
    .venv\\Scripts\\python.exe user_test/timer.py --participant P1 --theme llm_finance

数据写入 user_test/results/records.csv（列与 record_template.csv 一致）。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "user_test" / "results"
CSV_PATH = RESULTS_DIR / "records.csv"

COLUMNS = [
    "participant_id", "theme", "order", "date",
    "manual_minutes", "manual_relevant",
    "tool_minutes", "tool_relevant", "tool_wait_minutes",
    "claims_total", "claims_usable",
    "first_stuck", "stuck_category", "would_use", "consent", "open_feedback",
]


def ask(prompt: str, default: str = "") -> str:
    suffix = f"（默认 {default}）" if default else ""
    value = input(f"{prompt}{suffix}：").strip()
    return value or default


def ask_int(prompt: str, default: int) -> int:
    """读整数，输入不合法就退回默认值（主试现场手忙脚乱时不至于崩掉）。"""
    raw = ask(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"   （输入“{raw}”不是数字，按 {default} 记录）")
        return default


def ask_float(prompt: str, default: float) -> float:
    raw = ask(prompt, str(default))
    try:
        return float(raw)
    except ValueError:
        print(f"   （输入“{raw}”不是数字，按 {default} 记录）")
        return default


def wait_enter(message: str) -> float:
    input(f"\n{message} —— 按回车开始 ...")
    start = time.perf_counter()
    input("计时中，完成时按回车 ...")
    elapsed = time.perf_counter() - start
    print(f"   ⏱ 用时 {elapsed / 60:.1f} 分钟（{elapsed:.0f} 秒）")
    return round(elapsed / 60, 1)


def append_row(row: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="用户测试计时器")
    parser.add_argument("--participant", help="被试编号，如 P1")
    parser.add_argument("--theme", help="主题，如 llm_finance")
    parser.add_argument("--order", choices=["manual_first", "tool_first"], help="条件顺序")
    args = parser.parse_args()

    if not args.participant:
        print("=" * 60)
        print("  用户测试计时器（每个被试跑一次）")
        print("=" * 60)
    participant = args.participant or ask("被试编号", "P1")
    theme = args.theme or ask("主题（如 llm_finance / esg / supply_chain）", "llm_finance")
    order = args.order or ask("顺序（manual_first / tool_first）", "manual_first")

    print(f"\n被试 {participant} ｜ 主题 {theme} ｜ 顺序 {order}")
    consent = ask("被试是否口头同意（yes/no）", "yes")
    if consent.lower() != "yes":
        print("未获得同意，结束。")
        return 1

    print("\n— 条件 A：手工 —")
    manual_minutes = wait_enter("手工阶段")
    manual_relevant = ask_int("  手工阶段找到几篇确实相关的（0-10）", 0)

    print("\n— 条件 B：工具 —")
    tool_minutes = wait_enter("工具阶段")
    tool_relevant = ask_int("  工具阶段找到几篇确实相关的（0-10）", 0)
    tool_wait = ask_float("  其中等待模型/检索大约多久（分钟，可估）", 0.0)

    print("\n— 任务二：可写进论文的观点 —")
    claims_total = ask_int("  共挑了几条观点", 3)
    claims_usable = ask_int("  其中能直接用的有几条", 0)

    print("\n— 卡点与反馈 —")
    first_stuck = ask("  第一次卡在哪（一句话；没有就写 无）", "无")
    stuck_category = ask("  归类（界面/概念/等待/无）", "无")
    would_use = ask_int("  还会继续用吗（1-5）", 3)
    feedback = ask("  最有价值的一句反馈", "")

    row = {
        "participant_id": participant,
        "theme": theme,
        "order": order,
        "date": date.today().isoformat(),
        "manual_minutes": manual_minutes,
        "manual_relevant": manual_relevant,
        "tool_minutes": tool_minutes,
        "tool_relevant": tool_relevant,
        "tool_wait_minutes": tool_wait,
        "claims_total": claims_total,
        "claims_usable": claims_usable,
        "first_stuck": first_stuck,
        "stuck_category": stuck_category,
        "would_use": would_use,
        "consent": consent,
        "open_feedback": feedback,
    }
    append_row(row)

    print("\n" + "=" * 60)
    print("已写入:", CSV_PATH)
    print(f"手工 {manual_minutes} 分钟 / {manual_relevant} 篇 ｜ "
          f"工具 {tool_minutes} 分钟 / {tool_relevant} 篇")
    print("别忘了让被试填 SUS 问卷（user_test/sus_template.csv）。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
