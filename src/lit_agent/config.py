"""Literature Agent —— 配置读取。

所有配置都来自项目根目录的 ``.env``，也可以用环境变量覆盖（环境变量优先）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """读取项目根目录的 .env（python-dotenv 未安装时静默跳过）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _str(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return (value or "").strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


@dataclass
class Settings:
    """运行一次文献综述所需的全部参数。"""

    provider: str = "ollama"
    model: str = "qwen2.5:3b"
    ollama_base_url: str = "http://localhost:11434"
    api_base: str = "https://api.deepseek.com/v1"
    api_key: str = ""

    year_from: int | None = 2019
    include_preprints: bool = False
    mailto: str = ""
    openalex_key: str = ""
    papers_per_round: int = 5
    rounds: int = 2
    read_fulltext: bool = False
    language: str = "zh"
    constraint_mode: str = "soft"
    verify_citations: bool = True
    verify_semantic: bool = False
    verify_max_sentences: int = 6

    openalex_per_query: int = 20
    arxiv_per_query: int = 20
    candidates_per_round: int = 12

    temperature: float = 0.2
    num_ctx: int = 8192
    request_timeout: float = 600.0

    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls) -> "Settings":
        _load_dotenv()
        provider = (_str("LLM_PROVIDER", "ollama") or "ollama").lower()
        if provider not in {"ollama", "openai"}:
            provider = "ollama"

        year_raw = _str("LIT_YEAR_FROM")
        year_from: int | None
        if year_raw.lower() in {"", "0", "none", "all"}:
            year_from = None
        else:
            try:
                year_from = int(year_raw)
            except ValueError:
                year_from = 2019

        language = (_str("LIT_LANGUAGE", "zh") or "zh").lower()
        if language.startswith("en"):
            language = "en"
        else:
            language = "zh"

        return cls(
            provider=provider,
            model=_str("LIT_MODEL") or _str("LOCAL_LLM", "qwen2.5:3b"),
            ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            api_base=_str("LIT_API_BASE", "https://api.deepseek.com/v1").rstrip("/"),
            api_key=_str("LIT_API_KEY") or _str("DEEPSEEK_API_KEY") or _str("OPENAI_API_KEY"),
            year_from=year_from,
            include_preprints=_bool("LIT_INCLUDE_PREPRINTS", False),
            mailto=_str("LIT_MAILTO"),
            openalex_key=_str("LIT_OPENALEX_KEY"),
            papers_per_round=max(1, _int("LIT_PAPERS_PER_ROUND", 5)),
            rounds=max(1, min(4, _int("LIT_ROUNDS", 2))),
            read_fulltext=_bool("LIT_READ_FULLTEXT", False),
            language=language,
            constraint_mode=(
                _str("LIT_CONSTRAINT_MODE", "soft").lower()
                if _str("LIT_CONSTRAINT_MODE", "soft").lower() in {"soft", "hard", "off"}
                else "soft"
            ),
            verify_citations=_bool("LIT_VERIFY_CITATIONS", True),
            verify_semantic=_bool("LIT_VERIFY_SEMANTIC", False),
            verify_max_sentences=max(1, min(20, _int("LIT_VERIFY_SENTENCES", 6))),
            output_dir=PROJECT_ROOT / "output",
        )

    # --------------------------------------------------------------- helpers
    @property
    def is_chinese(self) -> bool:
        return self.language == "zh"

    def describe(self) -> str:
        """人类可读的配置摘要，运行时打印。"""
        model_info = f"{self.provider}/{self.model}"
        if self.provider == "ollama":
            model_info += f" @ {self.ollama_base_url}"
        else:
            model_info += f" @ {self.api_base}"
        years = f"{self.year_from} 年至今" if self.year_from else "不限年份"
        sources = "Crossref + DOAJ"
        if self.include_preprints:
            sources += " + arXiv（预印本，默认不纳入参考文献）"
        if self.openalex_key:
            sources += " + OpenAlex"
        return (
            f"模型: {model_info}\n"
            f"检索: {sources} | {years}\n"
            f"规模: 每轮 {self.papers_per_round} 篇 × {self.rounds} 轮 | "
            f"全文精读: {'开' if self.read_fulltext else '关'} | "
            f"引用核验: {'开' if self.verify_citations else '关'} | "
            f"约束模式: {self.constraint_mode} | "
            f"输出: {'中文' if self.is_chinese else 'English'}"
        )
