"""生成作品集用的架构图（SVG），不依赖任何绘图库。"""

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "portfolio" / "architecture.svg"
OUT.parent.mkdir(parents=True, exist_ok=True)

W, H = 1240, 700
BG = "#ffffff"
INK = "#0f172a"
MUTED = "#64748b"
ACCENT = "#2563eb"
ACCENT_SOFT = "#eff6ff"
BORDER = "#cbd5e1"
CARD = "#f8fafc"
GREEN = "#059669"
GREEN_SOFT = "#ecfdf5"
AMBER = "#b45309"
AMBER_SOFT = "#fffbeb"

parts: list[str] = []


def rect(x, y, w, h, fill=CARD, stroke=BORDER, rx=10, dash=None, sw=1.2):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>'
    )


def text(x, y, s, size=13, fill=INK, weight="400", anchor="start", family="inherit"):
    parts.append(
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
        f'text-anchor="{anchor}" font-family="{family}">{escape(s)}</text>'
    )


def arrow(x1, y1, x2, y2, color=MUTED, width=1.4, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="{width}" marker-end="url(#arrow)"{d}/>'
    )


def chip(x, y, label, fill=ACCENT_SOFT, stroke="#bfdbfe", text_fill=ACCENT, w=None, size=12):
    width = w or max(58, len(label) * (7.2 if any("\u4e00" <= c <= "\u9fff" for c in label) else 6.4) + 22)
    rect(x, y, width, 28, fill=fill, stroke=stroke, rx=14, sw=1)
    text(x + width / 2, y + 18.5, label, size=size, fill=text_fill, anchor="middle")
    return width


# ------------------------------------------------------------------ header
parts.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
text(40, 46, "Local Literature Agent", size=22, weight="700")
text(40, 72, "本地优先的中文学术文献 Agent  ·  LangGraph + Ollama  ·  检索 / 精读 / 综述 / 对话 / 引用核验",
     size=12.5, fill=MUTED)

# ------------------------------------------------------------------ layer 1: 入口
text(40, 112, "① 用户入口", size=12, fill=MUTED, weight="600")
entries = [
    ("Streamlit 网页界面", "4 个标签页 · 实时进度 · 指标卡", 40),
    ("命令行", "run_lit_review / run_paper_read / run_paper_chat / run_library_chat", 360),
    ("一键启动 .bat", "双击即用 · chcp 65001", 940),
]
for title, desc, x in entries:
    w = 300 if x == 360 else 280
    rect(x, 124, w, 56, fill="#ffffff")
    text(x + 14, 147, title, size=13, weight="600")
    text(x + 14, 167, desc, size=10.5, fill=MUTED)

# ------------------------------------------------------------------ layer 2: 编排
text(40, 214, "② Agent 编排（LangGraph）", size=12, fill=MUTED, weight="600")
rect(40, 226, 1160, 96, fill=ACCENT_SOFT, stroke="#bfdbfe")
nodes = [
    ("规划检索式", 62),
    ("多源检索", 212),
    ("相关性筛选", 362),
    ("逐篇精读", 512),
    ("反思补检", 662),
    ("综述合成", 812),
    ("引用核验", 962),
]
for label, x in nodes:
    w = 130
    rect(x, 252, w, 44, fill="#ffffff", stroke="#93c5fd")
    text(x + w / 2, 279, label, size=12.5, anchor="middle", weight="600")
for i in range(len(nodes) - 1):
    x1 = nodes[i][1] + 130
    x2 = nodes[i + 1][1]
    arrow(x1 + 2, 274, x2 - 2, 274)
# reflect loop
parts.append(
    f'<path d="M 727 300 L 727 316 L 277 316 L 277 300" fill="none" stroke="{MUTED}" '
    f'stroke-width="1.3" stroke-dasharray="5 4" marker-end="url(#arrow)"/>'
)
text(500, 332, "未达轮数则继续检索", size=10.5, fill=MUTED)

# ------------------------------------------------------------------ layer 3: 能力
text(40, 372, "③ 能力模块", size=12, fill=MUTED, weight="600")
modules = [
    ("学术检索", "Crossref / DOAJ / arXiv / OpenAlex"),
    ("PDF 解析", "PyMuPDF · 按页切块 · 乱码过滤"),
    ("BM25 检索", "中英混排分词 · 确定性可复现"),
    ("结构化抽取", "问题 / 方法 / 数据 / 发现 / 局限"),
    ("引用核验", "编号有效性 · 数字回查"),
]
x = 40
for title, desc in modules:
    rect(x, 384, 222, 62, fill="#ffffff")
    text(x + 14, 408, title, size=12.5, weight="600")
    text(x + 14, 428, desc, size=10.2, fill=MUTED)
    x += 236

# ------------------------------------------------------------------ layer 4: 数据源
text(40, 486, "④ 外部数据与模型", size=12, fill=MUTED, weight="600")
rect(40, 498, 560, 66, fill="#ffffff")
text(56, 522, "免费学术数据源（无需 key）", size=12, weight="600")
chip(56, 530, "Crossref")
chip(136, 530, "DOAJ")
chip(190, 530, "arXiv")
chip(248, 530, "OpenAlex（可选 key）")
chip(400, 530, "本地 PDF", fill="#f1f5f9", stroke=BORDER, text_fill=INK, w=78)

rect(620, 498, 580, 66, fill=GREEN_SOFT, stroke="#a7f3d0")
text(636, 522, "模型：本地优先，一键切云端", size=12, weight="600", fill=GREEN)
chip(636, 530, "Ollama / qwen2.5（默认）", fill="#ffffff", stroke="#a7f3d0", text_fill=GREEN, w=170)
chip(818, 530, "OpenAI 兼容：DeepSeek / 通义 / GPT", fill="#ffffff", stroke="#a7f3d0", text_fill=GREEN, w=290)

# ------------------------------------------------------------------ layer 5: 输出
text(40, 600, "⑤ 产出", size=12, fill=MUTED, weight="600")
outputs = [
    ("中文文献综述", "带 DOI 参考文献 + 对比表 + 核验指标"),
    ("结构化精读笔记", "问题 / 方法 / 数据 / 结果 / 局限 / 启发"),
    ("论文对话记录", "多轮问答 · 可写观点卡 · 原文引文"),
    ("文献库报告", "跨文献对比表 + 共同研究空白"),
]
x = 40
for title, desc in outputs:
    rect(x, 612, 278, 58, fill=AMBER_SOFT, stroke="#fde68a")
    text(x + 14, 635, title, size=12.5, weight="600", fill=AMBER)
    text(x + 14, 655, desc, size=10.2, fill="#92400e")
    x += 292

# 层间箭头
for x in (190, 620, 1090):
    arrow(x, 188, x, 224)
for x in (190, 620, 1090):
    arrow(x, 352, x, 382)
for x in (190, 620, 1090):
    arrow(x, 452, x, 496)

defs = f'''<defs>
<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
  <path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/>
</marker>
</defs>'''

svg = (
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
    f'viewBox="0 0 {W} {H}" role="img" aria-label="Local Literature Agent architecture">'
    + defs
    + "\n".join(parts)
    + "</svg>"
)
OUT.write_text(svg, encoding="utf-8")
print("已生成:", OUT, f"({len(svg)} 字节)")
