"""只测「提炼观点」在两种输入下的表现：空关注点（全文）与具体关注点。"""

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

print("=" * 70)
print("A) 空关注点（应覆盖全文，不只 p9）")
r = chat.claims("")
print(r["answer"][:1800])
print("页码:", sorted({s["page"] for s in r["sources"]}))

print("=" * 70)
print("B) 具体关注点：实验结果")
r2 = chat.claims("实验结果和对比基线")
print(r2["answer"][:1200])
print("页码:", sorted({s["page"] for s in r2["sources"]}))

print("=" * 70)
print("C) 引文摘录（检查缩写切句）")
for hit in chat.quote("e.g.")[:3]:
    print(f"  [p{hit['page']}] {hit['sentence'][:180]}")
