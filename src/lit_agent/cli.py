"""命令行入口的公共逻辑。"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .llm import ChatLLM, LLMError


def ensure_utf8_stdout() -> None:
    """控制台编码兼容。

    Windows 控制台常见两种编码：
    * 中文系统默认 GBK/CP936 —— 本身能显示中文，不能强行改成 UTF-8，否则乱码；
    * 英文系统 / 被重定向时可能是 ASCII —— 这时改成 UTF-8。
    保存到文件的内容始终是 UTF-8，不受这里影响。
    """
    encoding = (sys.stdout.encoding or "").lower().replace("-", "")
    if any(key in encoding for key in ("utf8", "utf16", "gbk", "gb18030", "cp936", "big5")):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def make_logger(quiet: bool = False):
    started = time.perf_counter()

    def log(message: str = "") -> None:
        if quiet and message.startswith("   "):
            return
        elapsed = time.perf_counter() - started
        stamp = f"[{int(elapsed // 60):02d}:{elapsed % 60:04.1f}]"
        print(f"{stamp} {message}", flush=True)

    return log


def safe_filename(text: str, max_len: int = 50) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text or "").strip(" ._")
    return name[:max_len] or "untitled"


def write_output(stem: str, markdown: str, sidecar: dict | None = None) -> Path:
    """把结果写到 output/ 目录，返回 Markdown 路径。"""
    out_dir = PROJECT_ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = out_dir / f"{safe_filename(stem)}-{stamp}.md"
    md_path.write_text(markdown, encoding="utf-8")
    if sidecar is not None:
        json_path = md_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return md_path


def build_llm(settings: Settings, log=print) -> ChatLLM:
    """创建 LLM 客户端并做一次可用性检查（失败时给出可操作的提示）。"""
    llm = ChatLLM(settings)
    try:
        status = llm.ping()
    except LLMError as exc:
        log("✗ 模型不可用")
        log(str(exc))
        raise SystemExit(1) from exc
    log(f"✓ {status}")
    return llm


def apply_overrides(settings: Settings, args) -> Settings:
    """把命令行参数覆盖到配置上。"""
    if getattr(args, "rounds", None):
        settings.rounds = max(1, min(4, args.rounds))
    if getattr(args, "papers", None):
        settings.papers_per_round = max(1, args.papers)
    if getattr(args, "model", None):
        settings.model = args.model
    if getattr(args, "provider", None):
        settings.provider = args.provider
    if getattr(args, "lang", None):
        settings.language = args.lang
    if getattr(args, "fulltext", False):
        settings.read_fulltext = True
    if getattr(args, "year_from", None) is not None:
        settings.year_from = args.year_from or None
    return settings
