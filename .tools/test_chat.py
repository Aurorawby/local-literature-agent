"""测试论文对话 Agent：多轮问答 / 提炼观点 / 原文引文。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.chat import PaperChat, load_paper  # noqa: E402
from lit_agent.config import Settings  # noqa: E402
from lit_agent.llm import ChatLLM  # noqa: E402

settings = Settings.load()
llm = ChatLLM(settings)
index, paper = load_paper(str(ROOT / ".cache" / "pdfs" / "59fbcc09d3cbd369.pdf"))
chat = PaperChat(index, paper, settings, llm)
print(f"论文：{paper.title}")
print(f"片段数：{len(index.chunks)}（已过滤乱码块）")

print("\n" + "=" * 70)
print("1) 多轮问答：第一个问题")
r1 = chat.ask("这篇论文的核心方法是什么？")
print(r1["answer"][:800])
print("页码：", sorted({s["page"] for s in r1["sources"]}))

print("\n" + "=" * 70)
print("2) 追问（依赖上下文）")
r2 = chat.ask("它在 Game of 24 上比 CoT 好多少？具体数字是多少？")
print(r2["answer"][:800])
print("页码：", sorted({s["page"] for s in r2["sources"]}))

print("\n" + "=" * 70)
print("3) 提炼可写进论文的观点")
r3 = chat.claims("这篇论文的主要贡献和适用边界")
print(r3["answer"][:1600])
print("页码：", sorted({s["page"] for s in r3["sources"]}))

print("\n" + "=" * 70)
print("4) 原文引文摘录（确定性，不调用模型）")
for hit in chat.quote("Tree of Thoughts")[:4]:
    print(f"  [p{hit['page']}] {hit['sentence'][:150]}")

print("\n" + "=" * 70)
print("5) 保存对话")
path = chat.save("对话测试-TreeOfThoughts")
print("  ->", path)
