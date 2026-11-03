"""用已保存的 JSON 重新渲染 Markdown 报告（改了排版/参考文献格式后不用重跑模型）。"""

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.config import Settings  # noqa: E402
from lit_agent.graph import ReviewState, _render_report  # noqa: E402


def _sanitize(value):
    """修掉历史数据里的 HTML 转义（&amp; 等）。"""
    if isinstance(value, str):
        return html.unescape(value)
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    return value


def rerender(json_path: Path) -> Path:
    data = _sanitize(json.loads(json_path.read_text(encoding="utf-8")))
    settings = Settings.load()
    opts = data.get("settings", {})
    state = ReviewState(
        topic=data["topic"],
        language=opts.get("language", "zh"),
        all_queries=data.get("queries", []),
        gaps=data.get("gaps", []),
        extracts=data.get("papers", []),
        draft=data.get("draft", ""),
        round=opts.get("rounds", settings.rounds),
    )
    md = _render_report(settings, state)
    out = json_path.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    return out


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print("重写:", rerender(Path(arg)))
