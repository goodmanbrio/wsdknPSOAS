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

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
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


# ── Normalized data types ───────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str         # Provider-assigned. Echoed back in tool_results.
    name: str       # Tool function name, e.g. "run_pms1".
    input: dict     # Parsed input arguments.


@dataclass
class LLMResponse:
    stop_reason: str            # "end_turn", "tool_use", "max_tokens"
    text: str                   # All text blocks concatenated with \n.
    tool_calls: list[ToolCall]  # Empty if stop_reason != "tool_use".
    raw_content: list[dict]     # Normalized content blocks for message accumulation.

    def to_assistant_message(self) -> dict:
        """Build normalized assistant message for appending to messages list."""
        has_structured = self.tool_calls or any(
            b["type"] in ("thinking", "redacted_thinking")
            for b in self.raw_content
        )
        if has_structured:
            return {"role": "assistant", "content": self.raw_content}
        return {"role": "assistant", "content": self.text}

    @staticmethod
    def make_tool_results_message(results: list[dict]) -> dict:
        """Build normalized tool_results message.

        results: [{"id": "tc_1", "name": "run_pms1", "content": "..."}]
        name is required for Gemini (uses name, not id).
        """
        return {"role": "tool_results", "content": results}


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

    def call_with_tools(
        self,
        messages: list[dict],
        system_prompt: str,
        tools: list[dict],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Multi-turn tool calling. Override in backends that support tools."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support tool calling. "
            f"Check that the profile assigned to this role uses a provider "
            f"with tool support."
        )


# ── OpenAI-compatible backend ───────────────────────────────────────────
# Works for: DeepSeek, OpenAI, Kimi/Moonshot, Groq, Together, local vLLM

class OpenAICompatibleLLM(LLMBackend):
    """Generic OpenAI-compatible chat completions backend."""

    def __init__(
        self, model: str, api_key: str, base_url: str, temperature: float,
        max_tokens: int = 4096,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

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

    # ── Tool calling ─────────────────────────────────────────────────

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        api_tools = self._tools_to_openai(tools)
        api_messages = self._messages_to_openai(messages, system_prompt)

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=api_messages,
            tools=api_tools if tools else None,
            temperature=self._temperature,
            max_tokens=effective_max,
        )
        return self._parse_openai_response(resp)

    def _tools_to_openai(self, tools: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

    def _messages_to_openai(
        self, messages: list[dict], system_prompt: str,
    ) -> list[dict]:
        result = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            if msg["role"] == "tool_results":
                for r in msg["content"]:
                    result.append({
                        "role": "tool",
                        "tool_call_id": r["id"],
                        "content": r["content"],
                    })
            elif msg["role"] == "assistant":
                content = msg["content"]
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        if block["type"] == "text":
                            text_parts.append(block["text"])
                        elif block["type"] == "tool_use":
                            tool_calls.append({
                                "id": block["id"],
                                "type": "function",
                                "function": {
                                    "name": block["name"],
                                    "arguments": json.dumps(
                                        block["input"], ensure_ascii=False
                                    ),
                                },
                            })
                    oai_msg: dict = {
                        "role": "assistant",
                        "content": "\n".join(text_parts) or None,
                    }
                    if tool_calls:
                        oai_msg["tool_calls"] = tool_calls
                    result.append(oai_msg)
                else:
                    result.append(msg)
            else:
                result.append(msg)
        return result

    def _parse_openai_response(self, resp) -> LLMResponse:
        if not resp.choices:
            return LLMResponse(
                stop_reason="end_turn", text="", tool_calls=[], raw_content=[],
            )

        choice = resp.choices[0]
        message = choice.message

        STOP_MAP = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop_reason = STOP_MAP.get(choice.finish_reason, choice.finish_reason)

        text = message.content or ""
        tool_calls = []
        raw_content = []

        if text:
            raw_content.append({"type": "text", "text": text})

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    input_dict = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    input_dict = {}
                tool_call = ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=input_dict,
                )
                tool_calls.append(tool_call)
                raw_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": input_dict,
                })

        return LLMResponse(
            stop_reason=stop_reason,
            text=text,
            tool_calls=tool_calls,
            raw_content=raw_content,
        )


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

    # ── Tool calling ─────────────────────────────────────────────────

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        api_messages = self._messages_to_anthropic(messages)

        kwargs = {
            "model": self._model,
            "system": system_prompt,
            "messages": api_messages,
            "tools": tools,
            "max_tokens": effective_max,
        }

        if self._thinking and effective_max > self._thinking_budget:
            if "opus-4-8" in self._model:
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": "high"}
            else:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._thinking_budget,
                }

        resp = self._client.messages.create(**kwargs)
        return self._parse_anthropic_response(resp)

    def _messages_to_anthropic(self, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            if msg["role"] == "tool_results":
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r["id"],
                            "content": r["content"],
                        }
                        for r in msg["content"]
                    ],
                })
            else:
                result.append(msg)
        return result

    def _parse_anthropic_response(self, resp) -> LLMResponse:
        text_parts = []
        tool_calls = []
        raw_content = []

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                raw_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
                raw_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif block.type == "thinking":
                raw_content.append({
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                })
            elif block.type == "redacted_thinking":
                raw_content.append({
                    "type": "redacted_thinking",
                    "data": block.data,
                })

        return LLMResponse(
            stop_reason=resp.stop_reason,
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            raw_content=raw_content,
        )


# ── Gemini backend ──────────────────────────────────────────────────────

class GeminiLLM(LLMBackend):
    """Google Gemini backend via google-genai SDK."""

    def __init__(
        self, model: str, api_key: str, temperature: float = 0.1,
        max_tokens: int = 4096,
    ):
        from google import genai
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._call_counter = 0

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

    # ── Tool calling ─────────────────────────────────────────────────

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
        from google.genai import types

        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        contents = self._messages_to_gemini(messages)

        config_kwargs = {
            "temperature": self._temperature,
            "system_instruction": system_prompt,
            "max_output_tokens": effective_max,
        }
        if tools:
            config_kwargs["tools"] = [self._tools_to_gemini(tools)]

        config = types.GenerateContentConfig(**config_kwargs)
        resp = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        return self._parse_gemini_response(resp)

    def _tools_to_gemini(self, tools: list[dict]):
        from google.genai import types
        declarations = []
        for t in tools:
            schema = dict(t["input_schema"])
            declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=schema,
            ))
        return types.Tool(function_declarations=declarations)

    def _messages_to_gemini(self, messages: list[dict]) -> list:
        from google.genai import types
        contents = []

        for msg in messages:
            role = msg["role"]

            if role == "user":
                parts = [types.Part(text=msg["content"])]
                gemini_role = "user"

            elif role == "assistant":
                content = msg["content"]
                parts = []
                if isinstance(content, str):
                    parts.append(types.Part(text=content))
                elif isinstance(content, list):
                    for block in content:
                        if block["type"] == "text":
                            parts.append(types.Part(text=block["text"]))
                        elif block["type"] == "tool_use":
                            parts.append(types.Part(
                                function_call=types.FunctionCall(
                                    name=block["name"],
                                    args=block["input"],
                                )
                            ))
                gemini_role = "model"

            elif role == "tool_results":
                parts = []
                for r in msg["content"]:
                    parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=r["name"],
                            response={"result": r["content"]},
                        )
                    ))
                gemini_role = "user"

            else:
                continue

            if not parts:
                continue

            # Merge with previous if same role (Gemini requires alternation)
            if contents and contents[-1].role == gemini_role:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=gemini_role, parts=parts))

        return contents

    def _parse_gemini_response(self, resp) -> LLMResponse:
        if not resp.candidates:
            return LLMResponse(
                stop_reason="end_turn", text="", tool_calls=[], raw_content=[],
            )
        candidate = resp.candidates[0]
        if not candidate.content or not candidate.content.parts:
            raw_reason = str(getattr(candidate, "finish_reason", "STOP"))
            STOP_MAP = {
                "STOP": "end_turn", "MAX_TOKENS": "max_tokens",
                "FinishReason.STOP": "end_turn",
                "FinishReason.MAX_TOKENS": "max_tokens",
                "SAFETY": "end_turn", "RECITATION": "end_turn",
                "FinishReason.SAFETY": "end_turn",
                "FinishReason.RECITATION": "end_turn",
            }
            return LLMResponse(
                stop_reason=STOP_MAP.get(raw_reason, "end_turn"),
                text="", tool_calls=[], raw_content=[],
            )
        parts = candidate.content.parts

        text_parts = []
        tool_calls = []
        raw_content = []

        for part in parts:
            if part.text:
                text_parts.append(part.text)
                raw_content.append({"type": "text", "text": part.text})
            elif part.function_call:
                fc = part.function_call
                call_id = f"gemini_call_{self._call_counter}"
                self._call_counter += 1
                tool_calls.append(ToolCall(
                    id=call_id,
                    name=fc.name,
                    input=dict(fc.args) if fc.args else {},
                ))
                raw_content.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": fc.name,
                    "input": dict(fc.args) if fc.args else {},
                })

        if tool_calls:
            stop_reason = "tool_use"
        else:
            raw_reason = str(getattr(candidate, "finish_reason", "STOP"))
            STOP_MAP = {
                "STOP": "end_turn",
                "MAX_TOKENS": "max_tokens",
                "FinishReason.STOP": "end_turn",
                "FinishReason.MAX_TOKENS": "max_tokens",
            }
            stop_reason = STOP_MAP.get(raw_reason, "end_turn")

        return LLMResponse(
            stop_reason=stop_reason,
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            raw_content=raw_content,
        )


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
            max_tokens=profile.get("max_tokens", 4096),
        )

    # OpenAI-compatible (deepseek, openai, kimi, groq, together, etc.)
    base_url = PROVIDER_CONFIG[provider]["base_url"]
    return OpenAICompatibleLLM(
        model=profile["model"],
        api_key=api_key,
        base_url=base_url,
        temperature=profile.get("temperature", 0.1),
        max_tokens=profile.get("max_tokens", 4096),
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


def get_orchestrator_llm(config: Config) -> LLMBackend:
    """Orchestrator agent loop LLM — tool calling required."""
    return _get(config, "orchestrator_profile")


def get_pteca_llm(config: Config) -> LLMBackend:
    """PTECA chart planning agent LLM — tool calling required."""
    return _get(config, "pteca_profile")
