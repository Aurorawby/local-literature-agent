"""对已保存的 JSON 重新做引用核验，并重渲染报告（改进了证据构造后不用重跑全文）。"""

import json
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
from lit_agent.graph import ReviewState, _render_report  # noqa: E402
from lit_agent.llm import ChatLLM  # noqa: E402
from lit_agent.verify import verify_citations  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit("用法: python .tools/reverify.py output/xxx.json")

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
settings = Settings.load()
llm = ChatLLM(settings)
print(llm.ping())

t0 = time.time()
result = verify_citations(
    data["draft"], data["papers"], None,
    semantic_sentences=0, language="zh",
)
print(f"核验完成，用时 {time.time() - t0:.1f}s")
print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))

data["verification"] = result
path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

opts = data.get("settings", {})
state = ReviewState(
    topic=data["topic"],
    language=opts.get("language", "zh"),
    all_queries=data.get("queries", []),
    gaps=data.get("gaps", []),
    extracts=data["papers"],
    draft=data["draft"],
    verification=result,
    round=opts.get("rounds", settings.rounds),
)
md = _render_report(settings, state)
md_path = path.with_suffix(".md")
md_path.write_text(md, encoding="utf-8")
print("已重写:", md_path)
