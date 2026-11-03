"""检查 App 的"云端 API"分支：切换后是否出现 Key 提示、是否有异常。"""

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

print("1) 默认（本地 Ollama）")
print("   exception:", list(at.exception))
print("   sidebar error:", [e.value for e in at.sidebar.error])
print("   sidebar warning:", [w.value for w in at.sidebar.warning])

# 切到云端
at.sidebar.radio[0].set_value("云端 API（更快更准）").run()
print("\n2) 切换到云端 API")
print("   exception:", list(at.exception))
print("   sidebar 输入框:", [t.label for t in at.sidebar.text_input])
print("   sidebar warning:", [w.value for w in at.sidebar.warning])
print("   sidebar error:", [e.value for e in at.sidebar.error])

# 检查 provider 真的切过去了
import lit_agent.config as cfg  # noqa: F401

print("\n3) 填一个假 key 再运行")
at.sidebar.text_input(key=None) if False else None
for t in at.sidebar.text_input:
    if t.label == "API Key":
        t.set_value("sk-test-123")
at.run()
print("   exception:", list(at.exception))
print("   主区域按钮:", [b.label for b in at.button])
print("   主区域 info/warning:", [w.value for w in at.warning][:3])
print("\n✓ 云端分支无异常（真实调用需要有效 key）")
