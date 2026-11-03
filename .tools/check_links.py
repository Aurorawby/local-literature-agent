"""检查作品集与文档里的相对链接是否都存在，并列出将被提交的文件。"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FILES = [
    "README.md",
    "ATTRIBUTION.md",
    "index.html",
    "requirements.txt",
    "使用指南.md",
    "使用场景与工作流.md",
    "给朋友的一页说明.md",
    "docs/作品集说明.md",
    "docs/上游README-原版.md",
    "portfolio/case-study.md",
    "eval/README.md",
    "eval/RESULTS.md",
    "eval/MODEL_COMPARISON.md",
    "user_test/README.md",
    "user_test/task_sheet.md",
]

MD_LINK = re.compile(r"\]\(([^)#]+?)\)")
HTML_LINK = re.compile(r'(?:href|src)="([^"]+)"')

bad = []
for name in FILES:
    path = ROOT / name
    if not path.exists():
        bad.append((name, "FILE MISSING"))
        continue
    text = path.read_text(encoding="utf-8")
    links = [m.group(1) for m in MD_LINK.finditer(text)]
    links += [m.group(1) for m in HTML_LINK.finditer(text)]
    for link in links:
        if link.startswith(("http", "#", "mailto", "data:")):
            continue
        if not (path.parent / link).exists():
            bad.append((name, link))

print("坏链接：")
if not bad:
    print("  无")
for name, link in bad:
    print(f"  {name} -> {link}")

print("\n目录文件数：")
for directory in ("portfolio", "eval", "user_test"):
    base = ROOT / directory
    files = [p for p in base.rglob("*") if p.is_file()]
    print(f"  {directory}/  {len(files)} 个文件")

print("\n仓库里不应出现的高风险文件检查：")
risky = []
for pattern in ("*.docx", "~$*", ".env", "*.pdf"):
    for path in ROOT.rglob(pattern):
        if any(part in {".venv", ".tmp", ".cache", "papers", "node_modules"} for part in path.parts):
            continue
        risky.append(path.relative_to(ROOT))
if risky:
    for path in risky:
        print(f"  ⚠️  {path}")
else:
    print("  无")

print("\n关键文件是否存在：")
for name in ("README.md", "requirements.txt", ".env.example", "LICENSE",
             "pyproject.toml", "index.html", "启动界面.bat"):
    print(f"  {'✓' if (ROOT / name).exists() else '✗'} {name}")
