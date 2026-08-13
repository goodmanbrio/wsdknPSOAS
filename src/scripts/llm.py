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
from dotenv import load_dotenv
from openai import OpenAI

from src.config import Config

# Load .env from project root (parent of src/)
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

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
            b["type"] in ("thinking", "redacted_thinking", "reasoning")
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
    def complete(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> str: ...

    def complete_with_usage(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> tuple[str, dict]:
        """Like complete(), but also returns a usage dict.

        Default: delegates to complete() with empty usage.
        Subclasses that can report token counts override this.
        """
        return self.complete(prompt, system_prompt=system_prompt, label=label), {}

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
        label: str = "",
        web_search: bool = False,
    ) -> LLMResponse:
        """Multi-turn tool calling. Override in backends that support tools."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support tool calling. "
            f"Check that the profile assigned to this role uses a provider "
            f"with tool support."
        )

    def structured_complete(
        self,
        prompt: str,
        schema: dict,
        system_prompt: str | None = None,
        label: str = "",
        web_search: bool = False,
    ) -> dict:
        """Return structured JSON matching schema. Override in backends."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support structured_complete. "
            f"Override in AnthropicLLM or OpenAICompatibleLLM."
        )


# ── OpenAI-compatible backend ───────────────────────────────────────────
# Works for: DeepSeek, OpenAI, Kimi/Moonshot, Groq, Together, local vLLM

class OpenAICompatibleLLM(LLMBackend):
    """Generic OpenAI-compatible chat completions backend."""

    def __init__(
        self, model: str, api_key: str, base_url: str, temperature: float,
        max_tokens: int = 4096, thinking: bool = False,
    ):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._thinking = thinking

    def complete(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> str:
        return self.complete_with_usage(prompt, system_prompt=system_prompt, label=label)[0]

    def complete_with_usage(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> tuple[str, dict]:
        messages = self._build_messages(prompt, system_prompt)
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "stream": False,
        }
        if self._thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        resp = self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""

        usage = {}
        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "output_tokens": getattr(resp.usage, "completion_tokens", 0),
            }

        # Trace recording
        from src.harness.trace import get_current_trace
        trace = get_current_trace()
        if trace:
            trace.record_llm_call(
                messages=[{"role": "user", "content": prompt}],
                response=text,
                model=self._model,
                label=label,
            )

        return text, usage

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

    _TOOL_TEXT_MAX_RETRIES = 3  # for V4 Pro tool-as-text bug

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None, label=""):
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        api_tools = self._tools_to_openai(tools)
        api_messages = self._messages_to_openai(messages, system_prompt)

        kwargs: dict = {
            "model": self._model,
            "messages": api_messages,
            "tools": api_tools if tools else None,
            "temperature": self._temperature,
            "max_tokens": effective_max,
        }
        if self._thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        # Retry loop: DeepSeek V4 Pro intermittently emits tool calls as
        # plain text in content instead of structured tool_calls array
        # (deepseek-ai/DeepSeek-V3#1244). Detect and retry.
        tool_names = {t["name"] for t in tools} if tools else set()
        result = None
        for _attempt in range(self._TOOL_TEXT_MAX_RETRIES):
            resp = self._client.chat.completions.create(**kwargs)
            result = self._parse_openai_response(resp)
            if not (result.stop_reason == "end_turn" and tool_names
                    and any(name in result.text for name in tool_names)):
                break
        # Trace recording
        if result is not None:
            from src.harness.trace import get_current_trace
            trace = get_current_trace()
            if trace:
                trace.record_llm_call(
                    messages=messages,
                    response=self._serialize_llm_response(result),
                    model=self._model,
                    label=label,
                )
        return result  # give up after retries

    @staticmethod
    def _serialize_llm_response(result: LLMResponse) -> str:
        """Serialize LLMResponse to string for trace recording."""
        parts = []
        for block in result.raw_content:
            if block["type"] == "text":
                parts.append(block["text"])
            elif block["type"] == "tool_use":
                args = json.dumps(block["input"], ensure_ascii=False)
                parts.append(f"[tool_use: {block['name']}({args})]")
            elif block["type"] == "thinking":
                parts.append(f"[thinking: {block['thinking'][:500]}...]")
            elif block["type"] == "reasoning":
                parts.append(f"[reasoning: {block['reasoning'][:500]}...]")
        return "\n".join(parts) if parts else result.text

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
                    # DeepSeek API requires content or tool_calls on assistant
                    # messages. Use "" not None for empty text-only turns.
                    result.append({"role": "assistant", "content": content or ""})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    reasoning = None
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
                        elif block["type"] == "reasoning":
                            reasoning = block["reasoning"]
                    oai_msg: dict = {
                        "role": "assistant",
                        "content": "\n".join(text_parts) or None,
                    }
                    if tool_calls:
                        oai_msg["tool_calls"] = tool_calls
                    # DeepSeek V4: echo reasoning_content for multi-turn.
                    # Empty string (not null) required on tool-call turns.
                    if reasoning is not None:
                        oai_msg["reasoning_content"] = reasoning
                    elif tool_calls and self._thinking:
                        oai_msg["reasoning_content"] = ""
                    # DeepSeek API requires content or tool_calls to be set
                    # on assistant messages. reasoning_content alone is not
                    # enough. When a thinking-only turn produces no text and
                    # no tool calls, set content="" to pass validation.
                    if not oai_msg.get("content") and not oai_msg.get("tool_calls"):
                        oai_msg["content"] = ""
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

        # DeepSeek V4 thinking mode: capture reasoning_content for
        # multi-turn echo-back. Must be echoed as "" (not null) when
        # tool_calls are present (deepseek-ai/DeepSeek-V3#1376).
        reasoning = getattr(message, "reasoning_content", None) or ""
        if reasoning:
            raw_content.append({"type": "reasoning", "reasoning": reasoning})

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

    # ── Structured complete (Leng extraction) ──────────────────────────

    _STRUCTURED_MAX_RETRIES = 3  # 3 attempts total (2 retries)

    def structured_complete(
        self, prompt, schema, system_prompt=None, label="",
        web_search=False,
    ) -> dict:
        """Structured JSON via tool-call shim (OpenAI-compatible).

        No thinking — extraction call. web_search silently ignored.

        NOTE: tool_choice forcing omitted. DeepSeek V4 Pro has
        server-side thinking always-on, which is incompatible with
        tool_choice. The model reliably calls the tool without forcing
        when a single tool is provided. The retry loop + text-recovery
        handle the ~11% tool-as-text failure rate.
        """
        tool = {
            "type": "function",
            "function": {
                "name": "structured_output",
                "description": "Return structured data matching schema",
                "parameters": schema,
            },
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        for attempt in range(self._STRUCTURED_MAX_RETRIES):
            kwargs = {
                "model": self._model,
                "messages": messages,
                "tools": [tool],
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }

            resp = self._client.chat.completions.create(**kwargs)

            if not resp.choices:
                continue

            message = resp.choices[0].message

            # Happy path: tool_calls array present
            if message.tool_calls:
                for tc in message.tool_calls:
                    if tc.function.name == "structured_output":
                        try:
                            result = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError):
                            break  # retry
                        from src.harness.trace import get_current_trace
                        trace = get_current_trace()
                        if trace:
                            trace.record_llm_call(
                                messages=[{"role": "user", "content": prompt}],
                                response=json.dumps(result),
                                model=self._model,
                                label=label,
                            )
                        return result

            # Failure mode: tool-as-text (DeepSeek V4 Pro ~11%).
            content = message.content or ""
            if "structured_output" in content or "{" in content:
                try:
                    start = content.index("{")
                    depth = 0
                    end = len(content) - 1
                    for i, c in enumerate(content[start:], start):
                        if c == "{":
                            depth += 1
                        elif c == "}":
                            depth -= 1
                        if depth == 0:
                            end = i
                            break
                    json_str = content[start:end + 1]
                    result = json.loads(json_str)
                    if isinstance(result, dict):
                        from src.harness.trace import get_current_trace
                        trace = get_current_trace()
                        if trace:
                            trace.record_llm_call(
                                messages=[{"role": "user", "content": prompt}],
                                response=json.dumps(result),
                                model=self._model,
                                label=f"{label}:text-recovery",
                            )
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass  # retry

        raise ValueError(
            f"structured_complete: no valid tool call after "
            f"{self._STRUCTURED_MAX_RETRIES} attempts "
            f"(model: {self._model})"
        )


# ── Anthropic backend ───────────────────────────────────────────────────

class AnthropicLLM(LLMBackend):
    """Anthropic Claude backend with configurable extended thinking.

    When thinking=True:
    - 4.6+ models: adaptive thinking (model decides depth via effort)
    - 4.5 and earlier: enabled + budget_tokens (fixed ceiling)
    - temperature forced to 1 (Anthropic requirement)
    - system prompt folded into user message
    """

    @staticmethod
    def _supports_adaptive(model: str) -> bool:
        """True if model supports adaptive thinking (4.6+).

        4.5 and earlier only support type=enabled + budget_tokens.
        4.6+ support adaptive (and deprecate enabled).
        4.7+ require adaptive (enabled returns 400).
        """
        import re
        m = re.search(r"claude-\w+-(\d+)-(\d+)", model)
        if not m:
            return False
        return (int(m.group(1)), int(m.group(2))) >= (4, 6)

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

    def complete(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> str:
        text, _ = self.complete_with_usage(prompt, system_prompt=system_prompt, label=label)
        return text

    def complete_with_usage(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
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

        # Trace recording
        from src.harness.trace import get_current_trace
        trace = get_current_trace()
        if trace:
            trace.record_llm_call(
                messages=[{"role": "user", "content": prompt}],
                response=text,
                model=self._model,
                label=label,
            )

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
            if self._supports_adaptive(self._model):
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

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None, label="", web_search=False):
        effective_max = max_tokens if max_tokens is not None else self._max_tokens
        api_messages = self._messages_to_anthropic(messages)

        api_tools = list(tools)
        if web_search:
            api_tools.append({
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 1,
            })

        kwargs = {
            "model": self._model,
            "system": system_prompt,
            "messages": api_messages,
            "tools": api_tools,
            "max_tokens": effective_max,
        }

        if self._thinking:
            if self._supports_adaptive(self._model):
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": "high"}
            elif effective_max > self._thinking_budget:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": self._thinking_budget,
                }

        resp = self._client.messages.create(**kwargs)
        result = self._parse_anthropic_response(resp)

        # Trace recording
        from src.harness.trace import get_current_trace
        trace = get_current_trace()
        if trace:
            trace.record_llm_call(
                messages=messages,
                response=OpenAICompatibleLLM._serialize_llm_response(result),
                model=self._model,
                label=label,
            )

        return result

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
            else:
                # Preserve unknown block types (server_tool_use,
                # web_search_tool_result, future block types) in
                # raw_content for message accumulation.
                try:
                    raw_content.append(block.model_dump())
                except Exception:
                    raw_content.append({"type": getattr(block, "type", "unknown")})

        return LLMResponse(
            stop_reason=resp.stop_reason,
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            raw_content=raw_content,
        )

    # ── Structured complete ─────────────────────────────────────────

    _STRUCTURED_MAX_RETRIES = 2  # 1 retry on parse failure

    def structured_complete(self, prompt, schema, system_prompt=None, label="", web_search=False):
        """Return structured JSON matching schema via tool-call shim.

        Single tool call with schema as input_schema, parse tool_use block.
        No thinking — cheap extraction call.
        """
        for attempt in range(self._STRUCTURED_MAX_RETRIES):
            tool = {
                "name": "structured_output",
                "description": "Return structured data matching schema",
                "input_schema": schema,
            }
            tools = [tool]
            if web_search:
                tools.append({
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 1,
                })
            kwargs = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                "tools": tools,
            }
            # Force tool use when web_search is off (Leng, Validator).
            # Can't force when web_search is on — model needs to call
            # web_search first (FiscalCalResolver).
            if not web_search:
                kwargs["tool_choice"] = {"type": "tool", "name": "structured_output"}
            if system_prompt:
                kwargs["system"] = system_prompt
            # No thinking block — even if instance has thinking enabled.
            resp = self._client.messages.create(**kwargs)
            for block in resp.content:
                if block.type == "tool_use" and block.name == "structured_output":
                    from src.harness.trace import get_current_trace
                    trace = get_current_trace()
                    if trace:
                        trace.record_llm_call(
                            messages=[{"role": "user", "content": prompt}],
                            response=json.dumps(block.input),
                            model=self._model,
                            label=label,
                        )
                    return block.input
            # No tool_use block found — parse failure, retry
            if attempt == self._STRUCTURED_MAX_RETRIES - 1:
                raise ValueError(
                    f"structured_complete: no tool_use block after "
                    f"{self._STRUCTURED_MAX_RETRIES} attempts"
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

    def complete(
        self, prompt: str, system_prompt: str | None = None, label: str = "",
    ) -> str:
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
        text = resp.text or ""

        # Trace recording
        from src.harness.trace import get_current_trace
        trace = get_current_trace()
        if trace:
            trace.record_llm_call(
                messages=[{"role": "user", "content": prompt}],
                response=text,
                model=self._model,
                label=label,
            )

        return text

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

    def call_with_tools(self, messages, system_prompt, tools, max_tokens=None, label=""):
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
        result = self._parse_gemini_response(resp)

        # Trace recording
        from src.harness.trace import get_current_trace
        trace = get_current_trace()
        if trace:
            trace.record_llm_call(
                messages=messages,
                response=OpenAICompatibleLLM._serialize_llm_response(result),
                model=self._model,
                label=label,
            )

        return result

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
        thinking=profile.get("thinking", False),
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

def get_pumba_dailo_llm(config: Config) -> LLMBackend:
    """PUMBA Dailo LLM — agentic file search + extraction (tool calling)."""
    return _get(config, "pumba_dailo_profile")


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


# ── PMS2 factories ──────────────────────────────────────────────────────

def get_pms2_sekei_llm(config: Config) -> LLMBackend:
    """PMS2 Sekei LLM — stencil design, metric decomposition (tool calling)."""
    return _get(config, "pms2_sekei_profile")


def get_pms2_mapper_llm(config: Config) -> LLMBackend:
    """PMS2 Mapper LLM — dir navigation, fuzzy firm matching (tool calling)."""
    return _get(config, "pms2_mapper_profile")


def get_pms2_fiscal_cal_llm(config: Config) -> LLMBackend:
    """PMS2 FiscalCalResolver LLM — FY end lookup via web_search."""
    return _get(config, "pms2_fiscal_cal_profile")


def get_pms2_batch_planner_llm(config: Config) -> LLMBackend:
    """PMS2 Batch Planner LLM — file routing reasoning (tool calling)."""
    return _get(config, "pms2_batch_planner_profile")


def get_pms2_leng_llm(config: Config) -> LLMBackend:
    """PMS2 Leng LLM — cheap per-chunk extraction (structured_complete)."""
    return _get(config, "pms2_leng_profile")


def get_pms2_validator_llm(config: Config) -> LLMBackend:
    """PMS2 Validator LLM — per-chunk verdict (tool calling)."""
    return _get(config, "pms2_validator_profile")


# ── Research factories ──────────────────────────────────────────────────

def get_research_decomposer_llm(config: Config) -> LLMBackend:
    """Research decomposer LLM — splits open-ended question into sub-questions."""
    return _get(config, "research_decomposer_profile")


def get_research_synthesizer_llm(config: Config) -> LLMBackend:
    """Research synthesizer LLM — cites retrieved chunks into coherent answer."""
    return _get(config, "research_synthesizer_profile")


def get_zako_bunragman_llm(config: Config) -> LLMBackend:
    """Zako Bunragman LLM — names-only source-group discovery."""
    return _get(config, "zako_bunragman_profile")
