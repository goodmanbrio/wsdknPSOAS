"""
llm.py — Pluggable LLM backend abstraction, driven by llm_profiles.yaml.

Model specs (provider, model name, thinking budget, temperature, etc.) live
in llm_profiles.yaml.  Config.py maps pipeline roles to profile names.
To swap models, edit profile names in config.py.  To tweak model params,
edit llm_profiles.yaml.  No code changes needed for either.

Factory functions, one per pipeline role:
    get_sekei_llm(config)       — Sekei planning (Opus + high thinking)
    get_pto_hyde_llm(config)    — PTO HyDE keyword generation (DeepSeek temp=0)
    get_pto_judge_llm(config)   — PTO judge extraction (Opus + med thinking)

API keys are resolved from env vars by provider — see PROVIDER_CONFIG.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

import yaml
from openai import OpenAI

from src.config import Config

# ── Provider registry ────────────────────────────────────────────────────
# Maps provider name → env var for API key + base_url for OpenAI-compat.
# base_url=None means the provider uses its own SDK (not OpenAI-compat).

PROVIDER_CONFIG = {
    "anthropic": {"env_var": "ANTHROPIC_API_KEY", "base_url": None},
    "deepseek":  {"env_var": "DEEPSEEK_API_KEY",  "base_url": "https://api.deepseek.com/v1"},
    "openai":    {"env_var": "OPENAI_API_KEY",     "base_url": "https://api.openai.com/v1"},
    "kimi":      {"env_var": "MOONSHOT_API_KEY",   "base_url": "https://api.moonshot.cn/v1"},
    "gemini":    {"env_var": "GEMINI_API_KEY",     "base_url": None},
}


# ── Profile loading ─────────────────────────────────────────────────────

def _load_profiles(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _resolve_profile(profiles: dict, name: str) -> dict:
    """Look up profile by name. All params come from the profile — no overrides."""
    if name not in profiles:
        raise ValueError(
            f"LLM profile '{name}' not found. "
            f"Available: {list(profiles.keys())}"
        )
    return profiles[name]


def _get_api_key(provider: str) -> str:
    info = PROVIDER_CONFIG.get(provider)
    if not info:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Known: {list(PROVIDER_CONFIG.keys())}"
        )
    key = os.getenv(info["env_var"], "")
    if not key:
        raise ValueError(
            f"{info['env_var']} not set. "
            f"Export it or add it to .env for provider '{provider}'."
        )
    return key


# ── Abstract backend ────────────────────────────────────────────────────

class LLMBackend(ABC):
    """Minimal interface for any chat-completion LLM."""

    @abstractmethod
    def complete(self, prompt: str, system_prompt: str | None = None) -> str: ...

    def complete_with_usage(
        self, prompt: str, system_prompt: str | None = None
    ) -> tuple[str, dict]:
        """Like complete(), but also returns a usage dict.

        Default: delegates to complete() with empty usage.
        Subclasses that can report token counts override this.
        """
        return self.complete(prompt, system_prompt=system_prompt), {}

    @abstractmethod
    def stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]: ...

    @abstractmethod
    def model_name(self) -> str: ...


# ── OpenAI-compatible backend ───────────────────────────────────────────
# Works for: DeepSeek, OpenAI, Kimi/Moonshot, Groq, Together, local vLLM

class OpenAICompatibleLLM(LLMBackend):
    """Generic OpenAI-compatible chat completions backend."""

    def __init__(self, model: str, api_key: str, base_url: str, temperature: float):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = self._build_messages(prompt, system_prompt)
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            stream=False,
        )
        return resp.choices[0].message.content or ""

    def stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]:
        messages = self._build_messages(prompt, system_prompt)
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def model_name(self) -> str:
        return self._model

    def _build_messages(
        self, prompt: str, system_prompt: str | None
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages


# ── Anthropic backend ───────────────────────────────────────────────────

class AnthropicLLM(LLMBackend):
    """Anthropic Claude backend with configurable extended thinking.

    When thinking=True:
    - temperature is forced to 1 (Anthropic requirement)
    - system prompt is folded into user message
    - budget_tokens caps internal reasoning
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        thinking: bool = True,
        thinking_budget: int = 10000,
        max_tokens: int = 16000,
    ):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._thinking = thinking
        self._thinking_budget = thinking_budget
        self._max_tokens = max_tokens

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        text, _ = self.complete_with_usage(prompt, system_prompt=system_prompt)
        return text

    def complete_with_usage(
        self, prompt: str, system_prompt: str | None = None
    ) -> tuple[str, dict]:
        kwargs = self._build_kwargs(prompt, system_prompt)
        resp = self._client.messages.create(**kwargs)
        parts = [b.text for b in resp.content if b.type == "text"]
        text = "\n".join(parts)

        # Extract thinking tokens if present
        thinking_tokens = 0
        for b in resp.content:
            if b.type == "thinking":
                thinking_tokens += len(b.thinking) // 4  # rough estimate from chars

        usage = {}
        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }

        return text, usage

    def stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]:
        kwargs = self._build_kwargs(prompt, system_prompt)
        with self._client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text

    def model_name(self) -> str:
        return self._model

    def _build_kwargs(self, prompt: str, system_prompt: str | None) -> dict:
        if self._thinking:
            # Thinking mode: fold system into user, force temp=1
            user_text = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
            kwargs: dict = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": user_text}],
            }
            # opus-4-8 uses adaptive thinking; older models use enabled
            if "opus-4-8" in self._model:
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": "high"}
            else:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._thinking_budget,
                }
            return kwargs
        else:
            # No thinking: normal system message, configurable temp
            kwargs = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system_prompt:
                kwargs["system"] = system_prompt
            return kwargs


# ── Gemini backend ──────────────────────────────────────────────────────

class GeminiLLM(LLMBackend):
    """Google Gemini backend via google-genai SDK."""

    def __init__(self, model: str, api_key: str, temperature: float = 0.1):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature

    def complete(self, prompt: str, system_prompt: str | None = None) -> str:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=self._temperature,
            system_instruction=system_prompt,
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=config,
        )
        return resp.text or ""

    def stream(self, prompt: str, system_prompt: str | None = None) -> Iterator[str]:
        from google.genai import types
        config = types.GenerateContentConfig(
            temperature=self._temperature,
            system_instruction=system_prompt,
        )
        for chunk in self._client.models.generate_content_stream(
            model=self._model,
            contents=prompt,
            config=config,
        ):
            if chunk.text:
                yield chunk.text

    def model_name(self) -> str:
        return self._model


# ── Factory: profile → backend instance ─────────────────────────────────

def _make_llm(profile: dict) -> LLMBackend:
    """Construct the right LLMBackend from a resolved profile dict."""
    provider = profile["provider"]
    api_key = _get_api_key(provider)

    if provider == "anthropic":
        return AnthropicLLM(
            model=profile["model"],
            api_key=api_key,
            thinking=profile.get("thinking", False),
            thinking_budget=profile.get("thinking_budget", 10000),
            max_tokens=profile.get("max_tokens", 16000),
        )

    if provider == "gemini":
        return GeminiLLM(
            model=profile["model"],
            api_key=api_key,
            temperature=profile.get("temperature", 0.1),
        )

    # OpenAI-compatible (deepseek, openai, kimi, groq, together, etc.)
    base_url = PROVIDER_CONFIG[provider]["base_url"]
    return OpenAICompatibleLLM(
        model=profile["model"],
        api_key=api_key,
        base_url=base_url,
        temperature=profile.get("temperature", 0.1),
    )


# ── Role factories (one per pipeline role) ──────────────────────────────

def _get(config: Config, profile_attr: str) -> LLMBackend:
    """Generic factory: load profile by role, build LLM."""
    profiles = _load_profiles(config.llm_profiles_path)
    name = getattr(config, profile_attr)
    prof = _resolve_profile(profiles, name)
    return _make_llm(prof)



def get_sekei_llm(config: Config) -> LLMBackend:
    """Sekei (設計) planning LLM — multi-retrieval query decomposition."""
    return _get(config, "sekei_profile")


def get_pto_hyde_llm(config: Config) -> LLMBackend:
    """PTO HyDE LLM — keyword generation for non-standard table retrieval."""
    return _get(config, "pto_hyde_profile")


def get_pto_judge_llm(config: Config) -> LLMBackend:
    """PTO judge LLM — extraction + sufficiency check on retrieved chunks."""
    return _get(config, "pto_judge_profile")


# ── PUMBA factories ──────────────────────────────────────────────────────

def get_pumba_gulei_llm(config: Config) -> LLMBackend:
    """PUMBA GuleiPai/GuleiSau LLM — chunk selection and review."""
    return _get(config, "pumba_gulei_profile")


def get_pumba_leng_llm(config: Config) -> LLMBackend:
    """PUMBA Leng LLM — chunk screening (cheapest model)."""
    return _get(config, "pumba_leng_profile")
