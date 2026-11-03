"""测试引用核验（默认只跑确定性检查，不调用模型，秒级完成）。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.verify import (  # noqa: E402
    extract_numbers,
    render_section,
    split_cited_sentences,
    verify_citations,
)

print("== 数字抽取 ==")
for text in [
    "Transformer 模型在金融预测任务中 AUC 提升 3.08%，KS 增加 10.3%[4]。",
    "在 2024 年发表的 1,362 篇论文中，样本量为 120 万条记录，准确率 87.8%[2]。",
    "研究使用了 3 种模型和 4 个数据集[1]。",
]:
    print(f"  {extract_numbers(text)}  <- {text[:50]}")

path = ROOT / "output" / "大语言模型在金融风控中的应用-文献综述-20261026-222936.json"
data = json.loads(path.read_text(encoding="utf-8"))
draft, extracts = data["draft"], data["papers"]

sentences = split_cited_sentences(draft)
print(f"\n== 正文 ==")
print(f"{len(draft)} 字，{len(sentences)} 个带引用句子，{len(extracts)} 篇文献")

result = verify_citations(draft, extracts, llm=None, semantic_sentences=0)
print("\n== 指标 ==")
print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
print("\n== 渲染 ==")
print(render_section(result, extracts))
