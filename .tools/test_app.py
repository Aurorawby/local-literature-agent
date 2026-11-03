"""用 Streamlit 官方 AppTest 无头执行 app.py，检查首屏渲染是否报错。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
at.run()

print("exception:", at.exception)
print("标题/正文元素数:", len(at.markdown))
print("按钮:", [b.label for b in at.button])
print("标签页数量:", len(at.tabs) if hasattr(at, "tabs") else "n/a")
print("侧边栏 radio:", [r.options for r in at.sidebar.radio])
print("侧边栏 slider:", [(s.label, s.value) for s in at.sidebar.slider])
print("text_input:", [t.label for t in at.text_input])
if at.exception:
    for e in at.exception:
        print("!!! 异常:", e.value)
        raise SystemExit(1)
print("\n✓ 首屏渲染无异常")
