"""统一的 LLM 接口：本地 Ollama 或任意 OpenAI 兼容接口。

设计要点
--------
* 只用 httpx 直连 HTTP API，不依赖任何 SDK 的版本行为；
* 统一支持 "JSON 模式"（Ollama: ``format=json``；OpenAI: ``response_format``）；
* 统一的重试 + JSON 容错解析，方便小模型（3B）使用；
* 所有调用都有超时和友好的错误信息。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import Settings


class LLMError(RuntimeError):
    """LLM 调用失败（网络、模型不存在、返回格式异常等）。"""


# --------------------------------------------------------------------------
# JSON 容错解析
# --------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里尽可能抠出 JSON 对象。

    小模型常见问题：外面包着 ```json 代码块、前后有多余解释、括号不配对。
    依次尝试：整体解析 -> 代码块 -> 第一个平衡花括号片段。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("模型返回为空")

    candidates: list[str] = [text]
    fence = _FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())

    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break

    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    raise ValueError(f"无法解析 JSON: {text[:200]}")


# --------------------------------------------------------------------------
# Chat 客户端
# --------------------------------------------------------------------------
class ChatLLM:
    """极简 chat 客户端，封装 Ollama 与 OpenAI 兼容接口。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = settings.provider
        self._client = httpx.Client(
            timeout=httpx.Timeout(settings.request_timeout, connect=15.0),
            headers={"User-Agent": "lit-agent/1.0"},
        )

    # ---------------------------------------------------------------- public
    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int = 800,
        temperature: float | None = None,
        retries: int = 2,
    ) -> str:
        """发一轮对话，返回纯文本内容。失败会重试，最终抛 :class:`LLMError`。"""
        temp = self.settings.temperature if temperature is None else temperature
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                if self.provider == "ollama":
                    return self._ollama(system, user, json_mode, max_tokens, temp)
                return self._openai(system, user, json_mode, max_tokens, temp)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败（{self.provider}/{self.settings.model}）：{last_error}")

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        """要求模型输出 JSON，并解析成 dict；解析失败会带着纠正提示重试一次。"""
        text = self.chat(
            system, user, json_mode=True, max_tokens=max_tokens,
            temperature=temperature, retries=retries,
        )
        try:
            return extract_json(text)
        except ValueError:
            pass

        retry_user = (
            f"{user}\n\n你上一次的回答不是合法 JSON。请只输出一个 JSON 对象，"
            f"不要任何解释、不要 Markdown 代码块。"
        )
        text = self.chat(
            system, retry_user, json_mode=True, max_tokens=max_tokens,
            temperature=0, retries=retries,
        )
        return extract_json(text)

    def ping(self) -> str:
        """检查模型是否可用，返回一句话说明。"""
        if self.provider == "ollama":
            try:
                r = self._client.get(f"{self.settings.ollama_base_url}/api/tags", timeout=10)
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
            except Exception as exc:  # noqa: BLE001
                raise LLMError(
                    f"连不上本地 Ollama（{self.settings.ollama_base_url}）：{exc}\n"
                    "请先启动 Ollama（开始菜单里打开 Ollama 即可）。"
                ) from exc
            if self.settings.model not in names:
                raise LLMError(
                    f"Ollama 里没有模型 {self.settings.model}。已安装：{names or '（无）'}\n"
                    f"请先运行：ollama pull {self.settings.model}"
                )
            return f"Ollama 正常，模型 {self.settings.model} 已就绪"
        # 云端：真的调一次接口，尽早暴露 Key / 模型名 / 余额问题，
        # 避免一路静默降级到最后才出现"综述合成失败"的废稿。
        if not self.settings.api_key:
            raise LLMError("云端模型缺少 API Key，请在左侧填写")
        models = self._list_models()
        if models and self.settings.model not in models:
            raise LLMError(
                f"模型名 `{self.settings.model}` 在 {self.settings.api_base} 上不存在。\n"
                f"该接口可用的模型：{', '.join(models[:12])}\n"
                "DeepSeek 常用的是 deepseek-chat / deepseek-reasoner / deepseek-flash。"
            )
        return f"云端接口可用：{self.settings.api_base} / {self.settings.model}"

    def _list_models(self) -> list[str]:
        """拿到服务商的模型列表；接口不存在时返回空列表（不做名单校验，让后续调用自己报错）。"""
        try:
            r = self._client.get(
                f"{self.settings.api_base}/models",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"连不上模型接口 {self.settings.api_base}：{exc}") from exc
        if r.status_code in (401, 403):
            raise LLMError(
                f"API Key 无效或没有权限（{r.status_code}）。请检查左侧填写的 API Key 是否正确。"
            )
        if r.status_code == 402:
            raise LLMError("API 账户余额不足（402），请到服务商后台充值后再试。")
        if r.status_code >= 400:
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        ids = []
        for item in data.get("data", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
        return ids

    # -------------------------------------------------------------- internal
    def _ollama(
        self, system: str, user: str, json_mode: bool, max_tokens: int, temp: float
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": temp,
                "num_predict": max_tokens,
                "num_ctx": self.settings.num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"

        r = self._client.post(f"{self.settings.ollama_base_url}/api/chat", json=payload)
        if r.status_code == 404:
            raise LLMError(
                f"Ollama 找不到模型 {self.settings.model}，请先运行：ollama pull {self.settings.model}"
            )
        r.raise_for_status()
        data = r.json()
        content = (data.get("message") or {}).get("content", "")
        if not content.strip():
            raise LLMError(f"Ollama 返回空内容：{data}")
        return content

    def _openai(
        self, system: str, user: str, json_mode: bool, max_tokens: int, temp: float
    ) -> str:
        if not self.settings.api_key:
            raise LLMError("LLM_PROVIDER=openai 但没有配置 LIT_API_KEY")
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        r = self._client.post(
            f"{self.settings.api_base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
        )
        if r.status_code >= 400:
            raise LLMError(f"API 返回 {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"API 返回了无法解析的结构：{str(data)[:300]}") from None
        if not content.strip():
            raise LLMError("模型返回了空内容（可能输出被截断或触发了内容审查）")
        return content
