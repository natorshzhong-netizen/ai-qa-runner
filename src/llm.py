"""最小可用的 LLM 客户端：DeepSeek / 任意 OpenAI 兼容端点 / Ollama / offline。

不引入 SDK，只保留一次 POST。provider=auto 时的选择顺序：
显式配置 → 有 DEEPSEEK_API_KEY → 本机 Ollama 可达 → offline（用例目录里的固定用例）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

DEFAULT_MODELS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5-coder:7b",
}
CLOUD_PROVIDERS = {"deepseek", "openai"}
ALL_PROVIDERS = {"auto", "deepseek", "openai", "ollama", "offline"}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 120.0

    @property
    def label(self) -> str:
        if self.provider == "offline":
            return "offline"
        return f"{self.provider}:{self.model}"


def _ollama_reachable(host: str) -> bool:
    try:
        resp = requests.get(f"{host.rstrip('/')}/api/tags", timeout=1.5)
        return resp.ok
    except requests.RequestException:
        return False


def resolve_config(provider: str | None = None, model: str | None = None) -> LLMConfig:
    requested = (provider or os.getenv("AIQA_PROVIDER") or "auto").lower()
    if requested not in ALL_PROVIDERS:
        raise LLMError(f"未知 provider: {requested}（可选 {sorted(ALL_PROVIDERS)}）")

    if requested == "auto":
        if os.getenv("DEEPSEEK_API_KEY"):
            requested = "deepseek"
        elif os.getenv("OPENAI_API_KEY"):
            requested = "openai"
        elif _ollama_reachable(os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")):
            requested = "ollama"
        else:
            requested = "offline"

    if requested == "offline":
        return LLMConfig(provider="offline")

    chosen_model = model or os.getenv("AIQA_MODEL") or DEFAULT_MODELS.get(requested)
    if requested == "deepseek":
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise LLMError("使用 deepseek 需要设置 DEEPSEEK_API_KEY；或加 --provider offline")
        return LLMConfig(
            provider="deepseek",
            model=chosen_model,
            api_key=key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
    if requested == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise LLMError("使用 openai 需要设置 OPENAI_API_KEY")
        return LLMConfig(
            provider="openai",
            model=chosen_model,
            api_key=key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
    return LLMConfig(
        provider="ollama",
        model=chosen_model,
        base_url=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
    )


def _chat_cloud(cfg: LLMConfig, messages: list[dict], json_mode: bool, temperature: float) -> str:
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"  # type: ignore[union-attr]
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=cfg.timeout,
        )
    except requests.RequestException as exc:
        raise LLMError(f"请求 {cfg.provider} 失败: {exc}") from exc
    if not resp.ok:
        raise LLMError(f"{cfg.provider} 返回 HTTP {resp.status_code}: {resp.text[:400]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"{cfg.provider} 响应结构异常: {resp.text[:400]}") from exc


def _chat_ollama(cfg: LLMConfig, messages: list[dict], json_mode: bool, temperature: float) -> str:
    url = f"{cfg.base_url.rstrip('/')}/api/chat"  # type: ignore[union-attr]
    payload: dict = {
        "model": cfg.model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if json_mode:
        payload["format"] = "json"
    try:
        resp = requests.post(url, json=payload, timeout=cfg.timeout)
    except requests.RequestException as exc:
        raise LLMError(f"连接 Ollama 失败: {exc}") from exc
    if not resp.ok:
        raise LLMError(f"Ollama 返回 HTTP {resp.status_code}: {resp.text[:400]}")
    try:
        return resp.json()["message"]["content"]
    except (KeyError, ValueError) as exc:
        raise LLMError(f"Ollama 响应结构异常: {resp.text[:400]}") from exc


class LLMClient:
    def __init__(self, config: LLMConfig):
        if config.provider == "offline":
            raise LLMError("offline provider 不发请求，请直接使用固定用例")
        self.config = config

    @property
    def label(self) -> str:
        return self.config.label

    def chat(
        self,
        messages: list[dict],
        *,
        json_mode: bool = True,
        temperature: float = 0.2,
    ) -> str:
        if self.config.provider in CLOUD_PROVIDERS:
            return _chat_cloud(self.config, messages, json_mode, temperature)
        return _chat_ollama(self.config, messages, json_mode, temperature)


def make_client(provider: str | None = None, model: str | None = None) -> tuple[LLMConfig, LLMClient | None]:
    cfg = resolve_config(provider, model)
    if cfg.provider == "offline":
        return cfg, None
    return cfg, LLMClient(cfg)
