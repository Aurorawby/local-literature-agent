"""构建一个小型文献库（3 篇 LLM 推理方向的论文）做实测。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.cli import make_logger  # noqa: E402
from lit_agent.config import Settings  # noqa: E402
from lit_agent.llm import ChatLLM  # noqa: E402
from lit_agent.library import PaperLibrary  # noqa: E402

settings = Settings.load()
llm = ChatLLM(settings)
log = make_logger(quiet=True)
settings.verify_citations = False

library = PaperLibrary(settings, llm, log)
for arxiv_id in ["2305.10601", "2201.11903", "2203.11171"]:
    library.add_arxiv(arxiv_id)

print("\n" + "=" * 70)
print("1) 跨文献问答：这几篇的方法有什么不同？")
result = library.ask("这几篇论文的方法有什么不同？")
print(result["answer"])

print("\n" + "=" * 70)
print("2) 对比表 + 对比综述")
compare = library.compare("方法差异与实验结论")
print(compare["answer"])

print("\n" + "=" * 70)
print("3) 共同的研究空白")
gaps = library.gaps()
print(gaps["answer"])

extra = (
    "\n\n## 跨文献问答示例\n\n**问：这几篇论文的方法有什么不同？**\n\n"
    + result["answer"]
    + "\n\n## 对比结果\n\n"
    + compare["answer"]
    + "\n\n## 研究空白\n\n"
    + gaps["answer"]
)
path = library.save("文献库-LLM推理三篇", extra=extra)
print("\n已保存：", path)
