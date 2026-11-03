"""对比 B1 / B2 的前 10 篇标题，找出硬约束"误杀"了哪些相关论文。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

data = json.loads((ROOT / "eval" / "regress.json").read_text(encoding="utf-8"))
for row in data["rows"]:
    print("=" * 78)
    print(f"{row['topic']}  B1={row['b1']['precision@10']}%  B2={row['b2']['precision@10']}%")
    print(f"检索式: {row['queries']}")
    b1 = set(row["b1_titles"])
    b2 = set(row["b2_titles"])
    only_b1 = b1 - b2
    print(f"\nB1 有、B2 没有（疑似被硬约束误杀）{len(only_b1)} 篇：")
    for title in list(only_b1)[:6]:
        print(f"  - {title[:95]}")
    only_b2 = b2 - b1
    print(f"\nB2 新增 {len(only_b2)} 篇：")
    for title in list(only_b2)[:6]:
        print(f"  + {title[:95]}")
