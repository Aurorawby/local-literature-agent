"""用合成数据验证 analysis.py 能正确计算（合成数据只在本地，不进入仓库）。"""

import csv
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".tmp" / "fake_user_test"
if TMP.exists():
    shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)

records = [
    # 手工 40min/8篇 -> 工具 25min/9篇；等待 6 分钟；观点 3 条用 2 条
    ["P1", "llm_finance", "manual_first", "2026-11-01", 40, 8, 25, 9, 6, 3, 2, "不知道输入什么主题", "概念", 4, "yes", "检索比我自己找快"],
    ["P2", "esg", "tool_first", "2026-11-01", 35, 7, 22, 8, 5, 3, 3, "找不到上传 PDF 的入口", "界面", 4, "yes", "观点卡有用"],
    ["P3", "supply_chain", "manual_first", "2026-11-02", 50, 9, 30, 9, 8, 3, 2, "等待时间长", "等待", 3, "yes", "等得有点久"],
    ["P4", "medical_seg", "tool_first", "2026-11-02", 45, 8, 28, 7, 7, 3, 2, "无", "无", 4, "yes", "综述结构不错"],
    ["P5", "carbon", "manual_first", "2026-11-03", 55, 6, 35, 6, 10, 3, 1, "综述里数字看不懂", "概念", 3, "yes", "数字要核对"],
]
with (TMP / "records.csv").open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(["participant_id", "theme", "order", "date", "manual_minutes", "manual_relevant",
                     "tool_minutes", "tool_relevant", "tool_wait_minutes", "claims_total",
                     "claims_usable", "first_stuck", "stuck_category", "would_use", "consent",
                     "open_feedback"])
    writer.writerows(records)

sus = [
    ["P1", 4, 2, 4, 2, 4, 2, 4, 2, 4, 2],
    ["P2", 4, 2, 4, 3, 5, 2, 4, 2, 4, 2],
    ["P3", 3, 3, 3, 3, 4, 3, 4, 3, 3, 3],
    ["P4", 4, 2, 4, 2, 4, 2, 5, 2, 4, 2],
    ["P5", 3, 3, 4, 3, 4, 3, 4, 3, 3, 3],
]
with (TMP / "sus.csv").open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(["participant_id"] + [f"q{i}" for i in range(1, 11)])
    writer.writerows(sus)

result = subprocess.run(
    [sys.executable, str(ROOT / "user_test" / "analysis.py"), str(TMP)],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
print(result.stdout[-2500:])
print("STDERR:", result.stderr[-800:])
print("\n---- RESULTS.md 全文 ----")
print((TMP / "RESULTS.md").read_text(encoding="utf-8"))
print("✓ 合成数据验证完成（数据在 .tmp，未进入仓库）")
