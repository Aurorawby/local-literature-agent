"""用本地假服务器验证"云端 OpenAI 兼容接口"的请求格式是否正确。

不需要真实 API Key：起一个本地 HTTP 服务模拟 /chat/completions，
检查请求路径、鉴权头、response_format，以及返回解析。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

captured: dict = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        captured["path"] = self.path
        captured["auth"] = self.headers.get("Authorization")
        captured["body"] = body
        payload = json.dumps({
            "choices": [{"message": {"content": '{"verdict": "支持", "reason": "模拟返回"}'}}]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # 静音
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

from lit_agent.config import Settings  # noqa: E402
from lit_agent.llm import ChatLLM  # noqa: E402

settings = Settings.load()
settings.provider = "openai"
settings.api_base = "http://127.0.0.1:8765/v1"
settings.api_key = "sk-fake-key-for-test"
settings.model = "deepseek-chat"

llm = ChatLLM(settings)
data = llm.chat_json("系统提示", "用户问题", max_tokens=100)
print("返回解析:", data)

ok = True
checks = {
    "请求路径正确": captured.get("path") == "/v1/chat/completions",
    "带 Bearer 鉴权": captured.get("auth") == "Bearer sk-fake-key-for-test",
    "JSON 模式生效": captured.get("body", {}).get("response_format") == {"type": "json_object"},
    "模型名正确": captured.get("body", {}).get("model") == "deepseek-chat",
}
for name, passed in checks.items():
    print(("  ✓ " if passed else "  ✗ ") + name)
    ok = ok and passed

print("\n请求体摘要:", {
    k: captured["body"].get(k) for k in ("model", "temperature", "max_tokens", "response_format")
})
server.shutdown()
print("\n" + ("✓ 云端接口代码路径验证通过" if ok else "✗ 有问题"))
sys.exit(0 if ok else 1)
