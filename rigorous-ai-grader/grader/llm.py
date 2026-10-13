"""Thin, provider-agnostic LLM client (Gemini, OpenAI or Anthropic).

Every provider is wrapped behind one method, complete(system, user, json_mode), so the
grading pipeline never needs to know which API is underneath.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Protocol, TypeVar

T = TypeVar("T")


class LLM(Protocol):
    def complete(self, system: str, user: str, json_mode: bool = False) -> str: ...


# Default model names can be overridden in the sidebar or with GEMINI_MODEL, OPENAI_MODEL
# or ANTHROPIC_MODEL. Model names change often; confirm them in your provider's dashboard.
PROVIDERS: dict[str, dict[str, str]] = {
    "Gemini": {"env": "GEMINI_API_KEY", "model": "gemini-2.5-flash"},
    "OpenAI": {"env": "OPENAI_API_KEY", "model": "gpt-4.1-mini"},
    "Anthropic": {"env": "ANTHROPIC_API_KEY", "model": "claude-sonnet-5-5"},
}

_TRANSIENT = ("429", "resource_exhausted", "rate limit", "overloaded", "503", "unavailable", "timeout")


def default_model(provider: str) -> str:
    return os.environ.get(f"{provider.upper()}_MODEL") or PROVIDERS[provider]["model"]


def _retry(call: Callable[[], T], attempts: int = 4, delay: float = 5.0) -> T:
    """Retry rate-limit and overload errors with exponential backoff."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - SDK exception types differ per provider
            transient = any(marker in str(exc).lower() for marker in _TRANSIENT)
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


class GeminiLLM:
    def __init__(self, api_key: str, model: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json" if json_mode else None,
        )
        response = _retry(
            lambda: self._client.models.generate_content(model=self._model, contents=user, config=config)
        )
        return response.text or ""


class OpenAILLM:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = _retry(
            lambda: self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **kwargs,
            )
        )
        return response.choices[0].message.content or ""


class AnthropicLLM:
    def __init__(self, api_key: str, model: str):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, json_mode: bool = False) -> str:
        response = _retry(
            lambda: self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        )
        return "".join(block.text for block in response.content if block.type == "text")


_CLIENTS = {"Gemini": GeminiLLM, "OpenAI": OpenAILLM, "Anthropic": AnthropicLLM}


def make_llm(provider: str, api_key: str, model: str) -> LLM:
    if provider not in _CLIENTS:
        raise ValueError(f"Unknown provider: {provider}")
    return _CLIENTS[provider](api_key, model or default_model(provider))
