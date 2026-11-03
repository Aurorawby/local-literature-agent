"""测试 PDF 下载与解析（不调用模型，纯本地）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from lit_agent.pdf import download_pdf, extract_pdf, pick_important_chunks, split_chunks

url = "https://arxiv.org/pdf/2305.10601"
t0 = time.time()
path = download_pdf(url)
print(f"下载完成：{path} ({path.stat().st_size / 1024:.0f} KB, {time.time() - t0:.1f}s)")

text, meta = extract_pdf(path)
print(f"页数={meta['pages']} 字数={meta['chars']} 标题={meta['title'][:60]!r}")
print("开头 300 字：")
print(text[:300].replace("\n", " "))

chunks = split_chunks(text, max_chars=3000)
picked = pick_important_chunks(chunks, limit=6)
print(f"\n切块：{len(chunks)} 块，取样 {len(picked)} 块")
for i, c in enumerate(picked, 1):
    print(f"  块{i}: {len(c)} 字 | {c[:60]!r}")
