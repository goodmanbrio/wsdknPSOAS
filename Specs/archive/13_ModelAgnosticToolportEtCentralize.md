# Model-Agnostic Tool Calling + LLM Centralization

Extend `LLMBackend` with a normalized `call_with_tools()` method so
agent_loop, PTECA, and PUMBA Dailo route through the same multi-provider
factory that PMS1 internals already use. Eliminate **all** direct
`anthropic.Anthropic()` usage from the entire codebase.

## Architecture

### Before (current state)

```
                     PMS1 pipeline (CLEAN — complete() only)
                     ───────────────────────────────────────
sekei.py          →  get_sekei_llm(config)       →  LLMBackend.complete()
pto.py            →  get_pto_judge_llm(config)    →  LLMBackend.complete()
pto.py            →  get_pto_hyde_llm(config)     →  LLMBackend.complete()
pumba.py          →  get_pumba_gulei_llm(config)  →  LLMBackend.complete()
pumba.py          →  get_pumba_leng_llm(config)   →  LLMBackend.complete()

                     HARDCODED ANTHROPIC (tool calling)
                     ──────────────────────────────────
agent_loop.py     →  anthropic.Anthropic()        →  client.messages.create(tools=...)
tool_pteca.py     →  anthropic.Anthropic()        →  client.messages.create(tools=...)
pumba.py (Dailo)  →  anthropic.Anthropic()        →  client.messages.create(tools=...)
```

Problem: agent_loop, tool_pteca, and PUMBA Dailo bypass llm.py because
`LLMBackend` has no tool calling method. They hardcode `import anthropic`,
Anthropic-specific response parsing (`response.stop_reason`, `block.type`,
`tb.id`, `tb.name`, `tb.input`), and Anthropic-specific message
accumulation format. Dailo was initially missed because the spec
assumed "pumba.py uses LLMBackend.complete()" — true for Gulei/Leng,
but Dailo has its own agentic tool-calling loop.

### After (implemented state)

```
                     complete() roles (unchanged)
                     ────────────────────────────
sekei.py          →  get_sekei_llm(config)          →  LLMBackend.complete()
pto.py            →  get_pto_judge_llm(config)       →  LLMBackend.complete()
pto.py            →  get_pto_hyde_llm(config)        →  LLMBackend.complete()
pumba.py          →  get_pumba_gulei_llm(config)     →  LLMBackend.complete()
pumba.py          →  get_pumba_leng_llm(config)      →  LLMBackend.complete()

                     call_with_tools() roles (all migrated)
                     ──────────────────────────────────────
agent_loop.py     →  get_orchestrator_llm(config)    →  LLMBackend.call_with_tools()
tool_pteca.py     →  get_pteca_llm(config)           →  LLMBackend.call_with_tools()
pumba.py (Dailo)  →  get_pumba_dailo_llm(config)     →  LLMBackend.call_with_tools()
```

All LLM access goes through llm.py. Zero `import anthropic` outside
of `AnthropicLLM` in llm.py. Provider selection is config-only.

### Component diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  config.py                                                       │
│    orchestrator_profile: str = "anthropic_orchestrator"           │
│    pteca_profile: str = "anthropic_sonnetmed"                    │
│    sekei_profile: str = "anthropic_opushighthink"                │
│    ...                                                           │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  llm.py                                                          │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  LLMBackend (ABC)                                           │ │
│  │    .complete(prompt, system_prompt) → str      ← PMS1 uses  │ │
│  │    .call_with_tools(messages, system, tools,   ← NEW        │ │
│  │                     max_tokens=None) → LLMResponse           │ │
│  └──────┬───────────┬──────────────┬───────────────────────────┘ │
│         │           │              │                             │
│    AnthropicLLM  OpenAICompatLLM  GeminiLLM                     │
│    (overrides    (overrides       (overrides                     │
│     both)         both)            both)                         │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Normalized data types                                      │ │
│  │    ToolCall(id, name, input)                                │ │
│  │    LLMResponse(stop_reason, text, tool_calls, raw_content)  │ │
│  │    STOP_REASONS = {"end_turn", "tool_use", "max_tokens"}    │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Factories:                                                      │
│    get_orchestrator_llm(config) → LLMBackend  ← NEW             │
│    get_pteca_llm(config)        → LLMBackend  ← NEW             │
│    get_sekei_llm(config)        → LLMBackend  (existing)        │
│    get_pto_hyde_llm(config)     → LLMBackend  (existing)        │
│    get_pto_judge_llm(config)    → LLMBackend  (existing)        │
│    get_pumba_gulei_llm(config)  → LLMBackend  (existing)        │
│    get_pumba_leng_llm(config)   → LLMBackend  (existing)        │
└──────────────────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  llm_profiles.yaml                                               │
│    anthropic_orchestrator:                                        │
│      provider: anthropic                                         │
│      model: claude-sonnet-4-6                                    │
│      max_tokens: 4096                                            │
│    anthropic_sonnetmed:                                          │
│      provider: anthropic                                         │
│      model: claude-sonnet-4-6                                    │
│      max_tokens: 3000                                            │
│    deepseek_orchestrator:        ← add when ready                │
│      provider: deepseek                                          │
│      model: deepseek-chat                                        │
│      temperature: 0.1                                            │
│    gemini_orchestrator:          ← add when ready                │
│      provider: gemini                                            │
│      model: gemini-2.5-flash                                     │
│      temperature: 0.1                                            │
└──────────────────────────────────────────────────────────────────┘
```

### How a tool-calling turn flows (provider-agnostic)

```
agent_loop.py
    │
    │  backend = get_orchestrator_llm(config)
    │
    │  response: LLMResponse = backend.call_with_tools(
    │      messages=messages,
    │      system_prompt=SYSTEM_PROMPT,
    │      tools=TOOL_DEFINITIONS,
    │  )   # max_tokens from profile (self._max_tokens)
    │
    │  ── Inside AnthropicLLM.call_with_tools(): ──────────────────
    │  │  Convert tools: pass-through (Anthropic format = universal)
    │  │  Convert messages: _messages_to_anthropic(messages)
    │  │  Call: self._client.messages.create(...)
    │  │  Parse: _parse_anthropic_response(raw_response)
    │  │  Return: LLMResponse(
    │  │      stop_reason="tool_use",
    │  │      text="Running PMS1 for both firms.",
    │  │      tool_calls=[ToolCall(id="toolu_01X", name="run_pms1", input={...}), ...],
    │  │      raw_content=[...],     ← for message accumulation
    │  │  )
    │  └────────────────────────────────────────────────────────────
    │
    │  ── Inside OpenAICompatibleLLM.call_with_tools(): ───────────
    │  │  Convert tools: _tools_to_openai(tools)
    │  │  Convert messages: _messages_to_openai(messages)
    │  │  Call: self._client.chat.completions.create(...)
    │  │  Parse: _parse_openai_response(raw_response)
    │  │  Return: LLMResponse(same normalized shape)
    │  └────────────────────────────────────────────────────────────
    │
    │  ── Inside GeminiLLM.call_with_tools(): ─────────────────────
    │  │  Convert tools: _tools_to_gemini(tools)
    │  │  Convert messages: _messages_to_gemini(messages)
    │  │  Call: self._client.models.generate_content(tools=...)
    │  │  Parse: _parse_gemini_response(raw_response)
    │  │  Return: LLMResponse(same normalized shape)
    │  └────────────────────────────────────────────────────────────
    │
    ▼
agent_loop uses response.stop_reason, response.text, response.tool_calls
    — never touches provider SDK objects
```

### Message accumulation (provider-agnostic)

```
BEFORE (Anthropic-specific):
    messages.append({"role": "assistant", "content": response.content})
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tb.id, "content": result_str},
    ]})

AFTER (normalized):
    messages.append(response.to_assistant_message())
    messages.append(LLMResponse.make_tool_results_message([
        {"id": tc.id, "name": tc.name, "content": result_str},
        ...
    ]))
```

Normalized message format (stored in `messages` list, converted
per-provider inside `call_with_tools()`):

```python
# User message (unchanged)
{"role": "user", "content": "Chart Best Buy margins FY2022-2023"}

# Assistant message with tool calls (normalized)
{"role": "assistant", "content": [
    {"type": "text", "text": "Running PMS1..."},
    {"type": "tool_use", "id": "tc_1", "name": "run_pms1", "input": {"firm": "Best Buy", ...}},
]}

# Tool results (normalized — NEW role)
{"role": "tool_results", "content": [
    {"id": "tc_1", "name": "run_pms1", "content": "PMS1 complete. Best Buy stencil stored as $var_1."},
]}

# Pure text assistant message (end_turn)
{"role": "assistant", "content": "Done. 2 charts saved."}
```

Conversion responsibility:

| Message type | Anthropic | OpenAI/DeepSeek | Gemini |
|---|---|---|---|
| `user` text | pass-through | pass-through | `Content(role="user", parts=[Part(text=...)])` |
| `assistant` with tool_calls | `content` list of dicts (API accepts dicts) | Split: `.content=text`, `.tool_calls=[ChatCompletionMessageToolCall(...)]` | `Content(role="model", parts=[Part(text=...), Part(function_call=...)])` |
| `tool_results` | `{"role":"user","content":[{"type":"tool_result","tool_use_id":id,"content":...}]}` | N separate `{"role":"tool","tool_call_id":id,"content":...}` messages | `Content(role="user", parts=[FunctionResponse(name=name, response={"result":...})])` |


## Input contract

### LLMBackend.call_with_tools()

Base class provides a default that raises `NotImplementedError`.
Backends that support tool calling override it.

```python
# LLMBackend (ABC) — add this concrete method (NOT @abstractmethod):
def call_with_tools(
    self,
    messages: list[dict],
    system_prompt: str,
    tools: list[dict],
    max_tokens: int | None = None,
) -> "LLMResponse":
    """Multi-turn tool calling. Override in backends that support tools."""
    raise NotImplementedError(
        f"{type(self).__name__} does not support tool calling. "
        f"Check that the profile assigned to this role uses a provider "
        f"with tool support."
    )
```

Signature (same on all overrides):

```python
def call_with_tools(
    self,
    messages: list[dict],
    system_prompt: str,
    tools: list[dict],
    max_tokens: int | None = None,
) -> LLMResponse:
```

| Param | Type | Description |
|---|---|---|
| `messages` | `list[dict]` | Normalized message list. Roles: `"user"`, `"assistant"`, `"tool_results"`. See format above. |
| `system_prompt` | `str` | System prompt text. Provider converts as needed (Anthropic: `system=`, OpenAI: `role="system"`, Gemini: `system_instruction=`). |
| `tools` | `list[dict]` | Tool schemas in Anthropic format: `{"name", "description", "input_schema"}`. Backend converts to provider format. |
| `max_tokens` | `int \| None` | Max output tokens. Defaults to `self._max_tokens` (from profile). Override only when caller needs a different budget than the profile (e.g. checkpoint summary with `max_tokens=1024`). All backends store `self._max_tokens` from construction. |

Tools format is Anthropic-native because that's what `system_prompt.py`
already stores. Converting FROM Anthropic to others is simpler than
converting FROM a custom universal format to all three. Rename
would add churn for zero benefit.

### Tool schema stored in system_prompt.py (UNCHANGED)

```python
TOOL_DEFINITIONS = [
    {
        "name": "run_pms1",
        "description": "...",
        "input_schema": {
            "type": "object",
            "properties": {...},
            "required": [...],
        },
    },
    ...
]
```

No changes. This format is already the canonical representation.
Each backend converts to its native format inside `call_with_tools()`.

Conversion:

```python
# Anthropic: pass-through
tools_native = tools

# OpenAI/DeepSeek:
tools_native = [
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

# Gemini:
from google.genai import types
tools_native = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name=t["name"],
        description=t["description"],
        parameters=t["input_schema"],
    )
    for t in tools
])
```

## Output contract

### ToolCall

```python
@dataclass
class ToolCall:
    id: str         # Provider-assigned. Echoed back in tool_results.
                    # Anthropic: "toolu_01Xq...". OpenAI: "call_abc123".
                    # Gemini: synthesized "gemini_call_0" (Gemini has no IDs).
    name: str       # Tool function name, e.g. "run_pms1".
    input: dict     # Parsed input arguments, e.g. {"firm": "Best Buy", "query": "..."}.
```

### LLMResponse

```python
@dataclass
class LLMResponse:
    stop_reason: str
    text: str
    tool_calls: list[ToolCall]
    raw_content: list[dict]       # Normalized content blocks for message accumulation
```

| Field | Type | Description |
|---|---|---|
| `stop_reason` | `str` | Normalized. One of: `"end_turn"`, `"tool_use"`, `"max_tokens"`. See mapping table below. |
| `text` | `str` | All text blocks concatenated with `\n`. Empty string if no text blocks. |
| `tool_calls` | `list[ToolCall]` | Empty list if `stop_reason != "tool_use"`. |
| `raw_content` | `list[dict]` | Normalized content blocks: `[{"type":"text","text":"..."},{"type":"tool_use","id":"...","name":"...","input":{...}}]`. Used by `to_assistant_message()` for message accumulation. |

### Stop reason mapping

| Provider raw value | Normalized value |
|---|---|
| Anthropic `"end_turn"` | `"end_turn"` |
| Anthropic `"tool_use"` | `"tool_use"` |
| Anthropic `"max_tokens"` | `"max_tokens"` |
| OpenAI/DeepSeek `"stop"` | `"end_turn"` |
| OpenAI/DeepSeek `"tool_calls"` | `"tool_use"` |
| OpenAI/DeepSeek `"length"` | `"max_tokens"` |
| Gemini `"STOP"` | `"end_turn"` |
| Gemini `"MAX_TOKENS"` | `"max_tokens"` |
| Gemini (response has `function_call` parts) | `"tool_use"` (inferred from parts, Gemini has no explicit tool stop reason) |

### LLMResponse methods

```python
def to_assistant_message(self) -> dict:
    """Build normalized assistant message for appending to messages list.

    If raw_content has only text blocks, collapses to a plain string
    content (simpler for providers that don't need structured blocks).
    If it has tool_use or thinking blocks, returns the full content list
    (Anthropic API requires thinking/redacted_thinking echoed back).
    """
    has_structured = self.tool_calls or any(
        b["type"] in ("thinking", "redacted_thinking")
        for b in self.raw_content
    )
    if has_structured:
        return {"role": "assistant", "content": self.raw_content}
    return {"role": "assistant", "content": self.text}

@staticmethod
def make_tool_results_message(
    results: list[dict],
) -> dict:
    """Build normalized tool_results message.

    results: [{"id": "tc_1", "name": "run_pms1", "content": "PMS1 complete..."}]

    The `name` field is required for Gemini (uses name, not id).
    Anthropic and OpenAI ignore it (they match by id).
    """
    return {
        "role": "tool_results",
        "content": results,
    }
```

## Per-backend implementation

### AnthropicLLM.call_with_tools()

```python
def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
    effective_max = max_tokens if max_tokens is not None else self._max_tokens

    # ── Convert messages ──────────────────────────────────────
    api_messages = self._messages_to_anthropic(messages)

    # ── Build kwargs ──────────────────────────────────────────
    kwargs = {
        "model": self._model,
        "system": system_prompt,
        "messages": api_messages,
        "tools": tools,              # pass-through (native format)
        "max_tokens": effective_max,
    }

    # ── Thinking config ─────────────────────────────────────────
    # Same thinking kwargs as _build_kwargs() uses for complete().
    # System prompt handling differs: call_with_tools() always uses
    # system= (above), _build_kwargs() folds into user msg for
    # thinking mode (see spec 13b for that fix).
    # Only enable thinking when budget fits within max_tokens.
    # Caller overriding max_tokens below thinking_budget (e.g.
    # checkpoint summary with 1024) signals "short response" →
    # thinking silently skipped for that call.
    if self._thinking and effective_max > self._thinking_budget:
        if "opus-4-8" in self._model:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": "high"}
        else:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

    # ── API call ──────────────────────────────────────────────
    resp = self._client.messages.create(**kwargs)

    # ── Parse response ────────────────────────────────────────
    return self._parse_anthropic_response(resp)


def _messages_to_anthropic(self, messages: list[dict]) -> list[dict]:
    """Convert normalized messages to Anthropic API format.

    Handles:
    - user/assistant text: pass-through
    - assistant with tool_use content blocks: pass-through (API accepts dicts)
    - tool_results: convert to {"role":"user", "content":[{"type":"tool_result",...}]}
    """
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
            # Preserve for Anthropic multi-turn (API requires echoing).
            # Non-Anthropic backends filter these out in _messages_to_*.
            # signature is REQUIRED — API rejects requests without it.
            raw_content.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
        elif block.type == "redacted_thinking":
            # MUST echo back — API rejects requests missing these.
            raw_content.append({
                "type": "redacted_thinking",
                "data": block.data,
            })

    return LLMResponse(
        stop_reason=resp.stop_reason,    # already normalized names
        text="\n".join(text_parts),
        tool_calls=tool_calls,
        raw_content=raw_content,
    )
```

### OpenAICompatibleLLM.call_with_tools()

Covers: DeepSeek, OpenAI, Kimi/Moonshot, any OpenAI-compatible API.

```python
def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
    effective_max = max_tokens if max_tokens is not None else self._max_tokens

    # ── Convert tools ─────────────────────────────────────────
    api_tools = self._tools_to_openai(tools)

    # ── Convert messages ──────────────────────────────────────
    api_messages = self._messages_to_openai(messages, system_prompt)

    # ── API call ──────────────────────────────────────────────
    resp = self._client.chat.completions.create(
        model=self._model,
        messages=api_messages,
        tools=api_tools if tools else None,   # omit when empty
        temperature=self._temperature,
        max_tokens=effective_max,
    )

    # ── Parse response ────────────────────────────────────────
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
    self, messages: list[dict], system_prompt: str
) -> list[dict]:
    """Convert normalized messages to OpenAI API format.

    Handles:
    - system_prompt: prepended as {"role":"system",...}
    - user text: pass-through
    - assistant text: pass-through
    - assistant with tool_use blocks: split into content + tool_calls
    - tool_results: expand to N separate {"role":"tool",...} messages
    """
    result = [{"role": "system", "content": system_prompt}]

    for msg in messages:
        if msg["role"] == "tool_results":
            # Each tool result is a separate message
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
                # Extract text and tool_calls from content blocks
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
    # Guard: provider returned no choices (content filter, API error)
    if not resp.choices:
        return LLMResponse(
            stop_reason="end_turn", text="", tool_calls=[], raw_content=[],
        )

    choice = resp.choices[0]
    message = choice.message

    # Stop reason
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
```

### GeminiLLM.call_with_tools()

```python
def call_with_tools(self, messages, system_prompt, tools, max_tokens=None):
    from google.genai import types

    effective_max = max_tokens if max_tokens is not None else self._max_tokens

    # ── Convert messages ──────────────────────────────────────
    contents = self._messages_to_gemini(messages)

    # ── Build config ──────────────────────────────────────────
    config_kwargs = {
        "temperature": self._temperature,
        "system_instruction": system_prompt,
        "max_output_tokens": effective_max,
    }
    # Omit tools when empty — Gemini may error on empty tool list
    if tools:
        config_kwargs["tools"] = [self._tools_to_gemini(tools)]

    config = types.GenerateContentConfig(**config_kwargs)

    # ── API call ──────────────────────────────────────────────
    resp = self._client.models.generate_content(
        model=self._model,
        contents=contents,
        config=config,
    )

    # ── Parse response ────────────────────────────────────────
    return self._parse_gemini_response(resp)


def _tools_to_gemini(self, tools: list[dict]):
    """Convert universal tool schemas to Gemini FunctionDeclarations."""
    from google.genai import types

    declarations = []
    for t in tools:
        # Shallow-clone schema to avoid mutating the shared
        # TOOL_DEFINITIONS dict. Gemini's FunctionDeclaration accepts
        # OpenAPI-style schemas with top-level "required" arrays.
        schema = dict(t["input_schema"])
        declarations.append(types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=schema,
        ))
    return types.Tool(function_declarations=declarations)


def _messages_to_gemini(self, messages: list[dict]) -> list:
    """Convert normalized messages to Gemini Contents.

    Gemini requires strict role alternation (user ↔ model). If two
    consecutive messages have the same effective role, merge them
    into one Content with multiple Parts.

    Role mapping:
      "user"         → "user"
      "assistant"    → "model"
      "tool_results" → "user" (with FunctionResponse parts)
    """
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

        # Guard: skip if no parts (e.g. assistant message with only
        # thinking/redacted_thinking blocks — these are filtered out
        # above). Empty parts would crash Gemini's Content constructor.
        if not parts:
            continue

        # Merge with previous if same role (Gemini requires alternation)
        if contents and contents[-1].role == gemini_role:
            contents[-1].parts.extend(parts)
        else:
            contents.append(types.Content(role=gemini_role, parts=parts))

    return contents


def _parse_gemini_response(self, resp) -> LLMResponse:
    from google.genai import types

    # Guard: safety filter or empty response
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

    # Gemini has no explicit "tool_use" stop reason — infer from parts
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
```

## Config changes

### config.py — role → profile mapping (all roles, current defaults)

```python
@dataclass
class Config:
    # ── PMS1 pipeline roles ────────────────────────────────────
    sekei_profile: str = "deepseek_v4pro_highalloc"
    pto_hyde_profile: str = "deepseek_v4flash_temp0"
    pto_judge_profile: str = "deepseek_v4pro_highalloc_temp0"

    # ── Harness roles ──────────────────────────────────────────
    orchestrator_profile: str = "deepseek_v4pro_orchestrator"
    pteca_profile: str = "deepseek_v4pro_pteca"

    # ── PUMBA roles ────────────────────────────────────────────
    pumba_dailo_profile: str = "deepseek_v4pro_highalloc_temp0"
    pumba_gulei_profile: str = "deepseek_v4pro_med"
    pumba_leng_profile: str = "deepseek_v4flash_leng"
```

All defaults point to DeepSeek V4. To revert any role to Anthropic,
change the string (e.g. `orchestrator_profile: str = "anthropic_orchestrator"`).
All original Anthropic profiles preserved in llm_profiles.yaml.

### llm.py new imports

```python
# ADD to top of llm.py:
import json
from dataclasses import dataclass
```

These are needed for `ToolCall`/`LLMResponse` dataclasses and
`json.loads()`/`json.dumps()` in OpenAI message/response conversion.

### llm.py `_build_kwargs` — NOT changed here

`_build_kwargs()` system prompt folding fix is in **spec 13b**
(`13b_KwargsBullshite.md`), separated for independent rollback.
`call_with_tools()` always uses `system=` regardless.

### llm.py constructor changes

`OpenAICompatibleLLM` needs `max_tokens` and `thinking` in `__init__`.
`GeminiLLM` needs `max_tokens`.

```python
# OpenAICompatibleLLM — add max_tokens + thinking params
class OpenAICompatibleLLM(LLMBackend):
    def __init__(self, model, api_key, base_url, temperature,
                 max_tokens=4096, thinking=False):
        # ... existing init ...
        self._max_tokens = max_tokens
        self._thinking = thinking  # DeepSeek V4 thinking mode

# GeminiLLM — add max_tokens param + call counter
class GeminiLLM(LLMBackend):
    def __init__(self, model, api_key, temperature=0.1,
                 max_tokens=4096):
        # ... existing init ...
        self._max_tokens = max_tokens
        self._call_counter = 0   # session-scoped ID for synthesized tool call IDs
```

Update `_make_llm()` to pass `max_tokens` + `thinking` to backends:

```python
# OpenAI-compatible path (add max_tokens + thinking):
return OpenAICompatibleLLM(
    model=profile["model"],
    api_key=api_key,
    base_url=base_url,
    temperature=profile.get("temperature", 0.1),
    max_tokens=profile.get("max_tokens", 4096),
    thinking=profile.get("thinking", False),
)

# Gemini path (add max_tokens):
return GeminiLLM(
    model=profile["model"],
    api_key=api_key,
    temperature=profile.get("temperature", 0.1),
    max_tokens=profile.get("max_tokens", 4096),
)

# Anthropic path: unchanged (already passes max_tokens + thinking)
```

### llm.py factory additions

```python
def get_orchestrator_llm(config: Config) -> LLMBackend:
    """Orchestrator agent loop LLM — tool calling required."""
    return _get(config, "orchestrator_profile")

def get_pteca_llm(config: Config) -> LLMBackend:
    """PTECA chart planning agent LLM — tool calling required."""
    return _get(config, "pteca_profile")

def get_pumba_dailo_llm(config: Config) -> LLMBackend:
    """PUMBA Dailo LLM — agentic file search + extraction (tool calling)."""
    return _get(config, "pumba_dailo_profile")
```

### llm_profiles.yaml

All Anthropic profiles preserved. DeepSeek V4 profiles added and set
as defaults. Key design:

- **V4 Pro** ($0.435/M in, $0.87/M out) for all high-capability roles.
  `thinking: true` on all Pro profiles — backend passes
  `extra_body={"thinking": {"type": "enabled"}}` to the API.
- **V4 Flash** ($0.14/M in, $0.28/M out) for cheap screening/keyword roles.

**CRITICAL: `deepseek-chat` and `deepseek-reasoner` deprecated
2026-07-24 15:59 UTC.** Legacy profiles updated to `deepseek-v4-flash`.

```yaml
# ── DeepSeek V4 Pro (Opus-tier) ──────────────────────────────
deepseek_v4pro_orchestrator:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.1
  thinking: true
  max_tokens: 8192

deepseek_v4pro_pteca:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.1
  thinking: true
  max_tokens: 4096

deepseek_v4pro_highalloc:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.1
  thinking: true
  max_tokens: 16384

deepseek_v4pro_highalloc_temp0:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.0
  thinking: true
  max_tokens: 16384

deepseek_v4pro_med:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.1
  thinking: true
  max_tokens: 8192

# ── DeepSeek V4 Flash (Sonnet/Haiku-tier) ────────────────────
deepseek_v4flash_temp0:
  provider: deepseek
  model: deepseek-v4-flash
  temperature: 0.0
  max_tokens: 4096

deepseek_v4flash_leng:
  provider: deepseek
  model: deepseek-v4-flash
  temperature: 0.0
  max_tokens: 3000
```

To switch providers, change one line in `config.py`:
```python
orchestrator_profile: str = "anthropic_orchestrator"
```

Or override at runtime:
```python
config = Config.from_env(orchestrator_profile="gemini_orchestrator")
```

## Changes to agent_loop.py

### Imports

```python
# REMOVE:
import anthropic

# ADD:
from src.llm import get_orchestrator_llm, LLMResponse, ToolCall

# KEEP (still needed):
from src.config import Config
```

### run_harness() changes

```python
def run_harness(
    first_query: str | None = None,
    config: Config | None = None,
) -> None:
    # REMOVE these 3 lines:
    # client = anthropic.Anthropic()
    # orch_profile = config.get_llm_profile("anthropic_orchestrator")
    # model = orch_profile["model"]

    # ⚠ PRESERVE (these sit between removed lines in current code):
    #   registry.reset()
    #   reset_counters()
    # They must remain at the top of run_harness(), before config.

    # ADD (after registry.reset / reset_counters):
    config = config or Config.from_env()
    backend = get_orchestrator_llm(config)

    # ... session_dir, transcript, banner, set_session_dir unchanged ...

    # Propagate config to execute_tool dispatchers (PTECA reads it).
    # Same pattern as set_session_dir(). Place AFTER set_session_dir().
    from src.harness.execute_tool import set_config
    set_config(config)

    # ... messages init, welcome banner, outer REPL unchanged ...

    # ── Inner agent loop ─────────────────────────────────────
    while True:
        # REMOVE:
        # response = client.messages.create(
        #     model=model,
        #     system=SYSTEM_PROMPT,
        #     messages=messages,
        #     tools=TOOL_DEFINITIONS,
        #     max_tokens=4096,
        # )

        # ADD (preserve spinner wrapping):
        _router.start_spinner("ORCHESTRATOR")
        try:
            response = backend.call_with_tools(
                messages=messages,
                system_prompt=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                # max_tokens omitted — uses profile default
            )
        finally:
            _router.stop_spinner()

        # ── end_turn ─────────────────────────────────────────
        if response.stop_reason == "end_turn":
            # REMOVE: for block in response.content: if hasattr(block, "text"): ...
            # ADD:
            if response.text:
                orchestrator_out.print(response.text, markdown=True)
            messages.append(response.to_assistant_message())
            transcript_turn += 1
            _dump_turn(transcript, transcript_turn, response, None)
            break

        # ── tool_use ─────────────────────────────────────────
        # REMOVE: tool_blocks = [b for b in response.content if b.type == "tool_use"]
        # ADD:
        tool_calls = response.tool_calls

        if not tool_calls:
            if response.stop_reason == "max_tokens":
                # truncation recovery — unchanged logic
                orchestrator_out.print(
                    "Response truncated (max_tokens). Retrying..."
                )
                messages.append(response.to_assistant_message())
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was truncated "
                        "(max_tokens). Please continue or reduce "
                        "tool calls."
                    ),
                })
                turn_counter += 1
                transcript_turn += 1
                _dump_turn(
                    transcript, transcript_turn, response, None
                )
                continue
            break

        # Print LLM text
        # REMOVE: for block in response.content: if hasattr(block, "text") ...
        # ADD:
        if response.text.strip():
            orchestrator_out.print(response.text, markdown=True)

        # Guard rail: max tool calls per turn
        if len(tool_calls) > MAX_TOOL_CALLS_PER_TURN:
            tool_results_msg = LLMResponse.make_tool_results_message([
                {
                    "id": tc.id,
                    "name": tc.name,
                    "content": (
                        f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool "
                        f"calls per turn exceeded "
                        f"({len(tool_calls)} requested)."
                    ),
                }
                for tc in tool_calls
            ])
            console.print(
                f"[bold red]\\[ERROR][/bold red] "
                f"Max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
                f"exceeded ({len(tool_calls)} requested)."
            )
        else:
            # Dispatch — tool_calls are ToolCall objects
            raw_results = _dispatch_parallel(tool_calls)
            tool_results_msg = LLMResponse.make_tool_results_message(
                raw_results
            )

        # Harvest
        tool_logs = harvest_logs()
        stored_vars = registry.harvest_recent()

        # Accumulate messages
        messages.append(response.to_assistant_message())
        messages.append(tool_results_msg)

        # Transcript
        turn_counter += 1
        transcript_turn += 1
        _dump_turn(
            transcript, transcript_turn, response,
            tool_results_msg["content"],
            tool_logs=tool_logs, stored_vars=stored_vars,
        )

        # Guard rail: checkpoint
        if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
            messages.append({
                "role": "user",
                "content": (
                    "You have run 6 turns. Summarize what you have "
                    "done so far and what remains."
                ),
            })

            _router.start_spinner("ORCHESTRATOR")
            try:
                summary = backend.call_with_tools(
                    messages=messages,
                    system_prompt=SYSTEM_PROMPT,
                    tools=[],          # no tools for summary
                    max_tokens=1024,
                )
            finally:
                _router.stop_spinner()

            messages.append(summary.to_assistant_message())
            text = summary.text

            # ... Panel, prompt, transcript unchanged ...
```

### _dispatch_parallel changes

```python
# BEFORE: receives ToolUseBlock objects from Anthropic SDK
def _safe_execute(tb) -> str:
    try:
        return execute_tool(tb.name, tb.input)
    except Exception as exc:
        return f"Error in {tb.name}: {type(exc).__name__}: {exc}"

def _dispatch_parallel(tool_blocks: list) -> list[dict]:
    results = [None] * len(tool_blocks)
    with ThreadPoolExecutor(max_workers=len(tool_blocks)) as pool:
        future_to_idx = {
            pool.submit(_safe_execute, tb): i
            for i, tb in enumerate(tool_blocks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            tb = tool_blocks[idx]
            results[idx] = {
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": future.result(),
            }
    return results

# AFTER: receives ToolCall objects from LLMResponse
def _safe_execute(tc: ToolCall) -> str:
    """Execute one tool call, catch exceptions."""
    try:
        return execute_tool(tc.name, tc.input)
    except Exception as exc:
        return f"Error in {tc.name}: {type(exc).__name__}: {exc}"


def _dispatch_parallel(tool_calls: list[ToolCall]) -> list[dict]:
    """Execute tool calls in parallel. Returns result dicts in order.

    Result format: [{"id": "tc_1", "name": "run_pms1", "content": "..."}]
    These are passed to LLMResponse.make_tool_results_message().
    """
    results = [None] * len(tool_calls)

    with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
        future_to_idx = {
            pool.submit(_safe_execute, tc): i
            for i, tc in enumerate(tool_calls)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            tc = tool_calls[idx]
            results[idx] = {
                "id": tc.id,
                "name": tc.name,
                "content": future.result(),
            }

    return results
```

### _dump_turn changes

```python
# BEFORE: accepts raw Anthropic response object
def _dump_turn(path, turn_num, response, tool_results, ...):
    for block in response.content:
        if block.type == "text": ...
        elif block.type == "tool_use": ...

# AFTER: accepts LLMResponse
def _dump_turn(
    path: Path,
    turn_num: int,
    response: LLMResponse,
    tool_results: list[dict] | None,
    tool_logs: dict[str, list[str]] | None = None,
    stored_vars: list | None = None,
) -> None:
    with open(path, "a") as f:
        f.write(f"\n## Turn {turn_num}\n\n")

        # Content blocks — preserve interleaving order (text ↔ tool_use)
        for block in response.raw_content:
            if block["type"] == "text":
                f.write(f"{block['text']}\n\n")
            elif block["type"] == "tool_use":
                args_str = json.dumps(block["input"], ensure_ascii=False)
                f.write(f"**Tool call:** `{block['name']}({args_str})`\n")
            # thinking/redacted_thinking blocks not written to transcript

        # Stream A: tool logs
        if tool_logs:
            f.write("\n### Tool Logs\n")
            for label, lines in tool_logs.items():
                f.write(f"\n#### {label}\n```\n")
                for line in lines:
                    f.write(f"{line}\n")
                f.write("```\n")

        # Stream C: tool results
        if tool_results:
            f.write("\n")
            for tr in tool_results:
                content = tr["content"]
                f.write(f"**Result:** `{content}`\n")

        # Stream B: stored variables
        if stored_vars:
            f.write("\n### Stored Variables\n\n")
            for handle, desc, data in stored_vars:
                f.write(f"**{handle}** ({desc}):\n```json\n")
                f.write(json.dumps(
                    data, indent=2, ensure_ascii=False, default=str
                ))
                f.write("\n```\n\n")

        f.write("\n")
```

## Changes to execute_tool.py

Config must flow from `run_harness` to dispatchers so PTECA sees
runtime overrides. Same pattern as `_session_dir` / `set_session_dir()`.

### Imports

```python
# ADD to existing imports in execute_tool.py:
from src.config import Config
```

### Additions

**Ordering:** The `_config` declaration and `set_config()` function
must appear BEFORE `reset_counters()` in the file, because
`reset_counters()` references `_config` in its `global` statement.
Place after the existing `_s2c_lock` / `_next_s2c_label` block.

```python
# ── Config state (set once per run_harness call) ─────────────────
_config: Config | None = None


def set_config(config: Config) -> None:
    """Store active config. Called by run_harness()."""
    global _config
    _config = config
```

### _exec_pteca() change

```python
def _exec_pteca(params: dict) -> str:
    from src.tools.tool_pteca import run_pteca

    stencils = params["stencils"]
    query = params["query"]
    firms = [s["firm"] for s in stencils]
    label = "PTECA-" + "+".join(firms)
    channel = register(label)

    # Pass config so PTECA uses same config as orchestrator
    chart_inputs = run_pteca(stencils, query, channel=channel, config=_config)

    # ... rest unchanged ...
```

### reset_counters() change

```python
def reset_counters() -> None:
    """Reset per-session state. Called by run_harness."""
    global _s2c_counter, _config
    with _s2c_lock:
        _s2c_counter = 0
    _config = None
```


## Changes to tool_pteca.py

Same pattern as agent_loop. Replace direct Anthropic SDK usage with
factory + `call_with_tools()`.

### Imports

```python
# REMOVE:
import anthropic

# ADD:
from src.llm import get_pteca_llm, LLMResponse, ToolCall

# KEEP (still needed):
from src.config import Config
```

### run_pteca() changes

```python
def run_pteca(stencils, query, channel=None, config=None):
    ch = channel or _default_channel

    # REMOVE:
    # client = anthropic.Anthropic()
    # config = Config.from_env()
    # profile = config.get_llm_profile("anthropic_sonnetmed")
    # model = profile["model"]
    # max_tokens = profile["max_tokens"]

    # ADD (falls back to defaults if no config propagated):
    config = config or Config.from_env()
    backend = get_pteca_llm(config)

    # ... format input unchanged ...

    messages = [{"role": "user", "content": user_msg}]
    turn_counter = 0

    while turn_counter < MAX_TURNS:
        _router.start_spinner(ch.label)
        try:
            # REMOVE:
            # response = client.messages.create(
            #     model=model,
            #     system=PTECA_SYSTEM_PROMPT,
            #     messages=messages,
            #     tools=PTECA_TOOLS,
            #     max_tokens=max_tokens,
            # )

            # ADD:
            response = backend.call_with_tools(
                messages=messages,
                system_prompt=PTECA_SYSTEM_PROMPT,
                tools=PTECA_TOOLS,
                # max_tokens omitted — uses profile default
            )
        finally:
            _router.stop_spinner()

        # ── end_turn (error — must call finalize) ─────────────
        if response.stop_reason == "end_turn":
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: you must call the finalize tool. "
                    "Do not end without it."
                ),
            })
            turn_counter += 1
            continue

        # ── no tool calls ─────────────────────────────────────
        if not response.tool_calls:
            messages.append(response.to_assistant_message())
            messages.append({
                "role": "user",
                "content": (
                    "Error: response contained no tool calls. "
                    "You must call the finalize tool."
                ),
            })
            turn_counter += 1
            continue

        # ── tool_use ──────────────────────────────────────────
        tool_results = []
        finalize_result = None

        for tc in response.tool_calls:
            if tc.name == "pteca_ask_user":
                question = tc.input["question"]
                ch.print(question, markdown=True)
                answer = ch.input("")
                tool_results.append({
                    "id": tc.id,
                    "name": tc.name,
                    "content": f"User answered: {answer}",
                })

            elif tc.name == "finalize":
                asked_user = any(
                    t.name == "pteca_ask_user"
                    for t in response.tool_calls
                )
                if asked_user:
                    tool_results.append({
                        "id": tc.id,
                        "name": tc.name,
                        "content": (
                            "Error: cannot finalize in the same turn as "
                            "pteca_ask_user. See the user's answer first, "
                            "then call finalize."
                        ),
                    })
                else:
                    charts_decisions = tc.input["charts"]
                    if not charts_decisions:
                        finalize_result = []
                        tool_results.append({
                            "id": tc.id,
                            "name": tc.name,
                            "content": "Cancelled. No charts produced.",
                        })
                    else:
                        chart_inputs = _build_chart_inputs(
                            stencils, charts_decisions
                        )
                        if not chart_inputs:
                            tool_results.append({
                                "id": tc.id,
                                "name": tc.name,
                                "content": (
                                    "Error: no valid charts produced. "
                                    "All metric/firm names were invalid. "
                                    "Check stencil data."
                                ),
                            })
                        else:
                            finalize_result = chart_inputs
                            tool_results.append({
                                "id": tc.id,
                                "name": tc.name,
                                "content": (
                                    f"Finalized. {len(chart_inputs)} "
                                    "chart(s) built."
                                ),
                            })
            else:
                tool_results.append({
                    "id": tc.id,
                    "name": tc.name,
                    "content": (
                        f"Error: unknown tool '{tc.name}'. "
                        "Available: pteca_ask_user, finalize."
                    ),
                })

        messages.append(response.to_assistant_message())
        messages.append(
            LLMResponse.make_tool_results_message(tool_results)
        )

        if finalize_result is not None:
            n = len(finalize_result)
            ch.print(f"Done. {n} chart(s).")
            return finalize_result

        turn_counter += 1

    ch.print("Error: exceeded max turns without finalize.")
    raise RuntimeError("PTECA exceeded max turns without calling finalize.")
```


## Failure modes

### Gemini consecutive same-role messages

Gemini requires strict user ↔ model alternation. Two situations
create same-role adjacency:

1. **tool_results (→ "user") followed by injected user message**
   (checkpoint: "You have run 6 turns..."). Both map to "user".

2. **end_turn response followed by user follow-up** in REPL. This
   is fine — it's model → user alternation.

Mitigation: `_messages_to_gemini()` merges consecutive same-role
messages into one Content with multiple Parts. See implementation
above.

### DeepSeek malformed tool arguments

DeepSeek sometimes returns `function.arguments` as invalid JSON.

Mitigation: `_parse_openai_response()` wraps `json.loads()` in
try/except. On failure, `input={}` — the error surfaces naturally
when `execute_tool` receives missing required params and the tool
returns an error string. The LLM sees the error and retries with
correct arguments.

### Provider doesn't support tool calling

Some models (old Haiku, base DeepSeek) may not support `tools=`.

Mitigation: `LLMBackend.call_with_tools()` default raises
`NotImplementedError`. If someone configures an incompatible profile
for the orchestrator, it fails at runtime with a clear message.
No silent fallback — silent fallback would corrupt the agent loop.

Future: `config.validate()` could check that profiles used for
orchestrator/PTECA roles point to providers known to support tools.
Not in MVP.

### Gemini has no tool call IDs

Gemini function calls don't have IDs — matching is by function name.

Mitigation: `_parse_gemini_response()` synthesizes IDs using
`self._call_counter` (session-scoped, monotonically increasing):
`"gemini_call_0"`, `"gemini_call_1"`, etc. IDs are unique across
turns within a session. `_messages_to_gemini()` ignores IDs in
tool_results and uses `name` from the result dict to construct
`FunctionResponse(name=...)`.

The normalized tool_results message includes BOTH `id` and `name`:
```python
{"id": "gemini_call_0", "name": "run_pms1", "content": "PMS1 complete..."}
```

Anthropic/OpenAI use `id`, ignore `name`. Gemini uses `name`,
ignores `id`. No information lost.

### Token counting differs across providers

Anthropic `max_tokens` = output tokens only (includes thinking
tokens when thinking is enabled).
OpenAI `max_tokens` = output tokens only.
Gemini `max_output_tokens` = output tokens only (parameter name differs).

Mitigation: Each backend maps `self._max_tokens` to its native
parameter name. The llm_profiles.yaml value is calibrated
per-provider. The Gemini backend passes it as `max_output_tokens=`.
All backends store `self._max_tokens` from profile at construction
time — callers rarely need to override.

### Anthropic extended thinking + tool use

`call_with_tools()` fully supports extended thinking. When
`self._thinking is True`, the thinking kwargs (`thinking`,
`output_config`) are injected into the API call — same thinking
kwargs as `_build_kwargs()` uses for `complete()`. Note: system
prompt handling differs — `call_with_tools()` always uses `system=`,
while `_build_kwargs()` currently folds into user message for
thinking mode (see spec 13b for that fix).

Guard: if the caller overrides `max_tokens` below
`self._thinking_budget` (e.g. checkpoint summary with 1024),
thinking is silently skipped for that call. The caller is
signaling "short response" — thinking would consume most of the
budget and leave almost nothing for output.

Anthropic responses with thinking enabled include
`type="thinking"` and `type="redacted_thinking"` content blocks.
The API **requires** these echoed back in subsequent multi-turn
requests — dropping them causes API errors.

Mitigation: `_parse_anthropic_response()` preserves thinking and
redacted_thinking blocks in `raw_content`. `to_assistant_message()`
returns `raw_content` (not collapsed text) when thinking blocks
are present. `_messages_to_anthropic()` passes these through
(Anthropic API accepts dicts). `_messages_to_openai()` and
`_messages_to_gemini()` silently skip blocks with unknown types
(their conversion loops only match `"text"` and `"tool_use"`) —
no pollution of non-Anthropic message history.

### Empty tool list edge case

Checkpoint summary call passes `tools=[]`. Provider behavior:

| Provider | `tools=[]` | `tools` omitted |
|---|---|---|
| Anthropic | Works (no tool use possible) | Works |
| OpenAI | Works | Works |
| Gemini | Uncertain — may error on empty list | Omit entirely |

Mitigation: all backends conditionally include tools when non-empty:

- **GeminiLLM**: `config_kwargs["tools"]` only set when `tools`
  is non-empty (see implementation above).
- **OpenAICompatibleLLM**: passes `tools=api_tools if tools else None`.
- **AnthropicLLM**: passes `tools=tools` (Anthropic handles `[]` fine).

### OpenAI `tools=None` vs omission

When `tools` is empty, `OpenAICompatibleLLM` passes `tools=None`.
The OpenAI Python SDK may serialize this as JSON `null` rather than
omitting the key entirely. OpenAI's API handles both, but exotic
OpenAI-compatible APIs (Kimi, Groq) might reject `null`. If a
specific provider errors on `tools=null`, switch to kwargs-dict
construction (like the Gemini path uses `config_kwargs`):

```python
kwargs = {"model": ..., "messages": ..., "temperature": ..., "max_tokens": ...}
if tools:
    kwargs["tools"] = api_tools
resp = self._client.chat.completions.create(**kwargs)
```

Not doing this preemptively — adds complexity for a hypothetical.

### Provider returns empty/null response (content filter, safety block)

OpenAI can return `resp.choices = []` when the entire response is
blocked. Gemini can return `candidate.content = None` when SAFETY
or RECITATION triggers.

Mitigation: both `_parse_openai_response()` and
`_parse_gemini_response()` guard against empty/null responses at
the top of the function. They return an empty `LLMResponse` with
`stop_reason="end_turn"` and empty text/tool_calls. In the agent
loop, this causes a silent break — same as the unhandled stop
reason case below.

### Gemini tool result ordering matters

When the orchestrator calls the same tool multiple times in one
turn (e.g. `run_pms1` for two firms), tool results have the same
`name`. Gemini matches `FunctionResponse` parts to `FunctionCall`
parts by **position within the Content**, not by name or ID.

Constraint: `_dispatch_parallel()` must return results in the same
order as the input `tool_calls` list. It does this via index-based
positioning (`results[idx] = ...`). Do NOT sort, shuffle, or
reorder tool results — this would break Gemini's positional
matching.

### Unhandled stop reasons (content_filter, SAFETY)

OpenAI can return `content_filter`, Gemini can return `SAFETY` or
`RECITATION`. These fall through the stop_reason mapping and are
passed through as-is. In the agent loop, unknown stop_reasons cause
a silent `break` (no text printed).

This is acceptable for MVP — these are rare and indicate the
provider refused to generate. A future improvement could log a
warning: `"Unexpected stop_reason: {stop_reason}"`. Not adding
now to avoid scope creep.


### DeepSeek V4 Pro tool-as-text bug

DeepSeek V4 Pro intermittently emits tool calls as plain text in
`content` instead of structured `tool_calls` array (~11% of the
time, per deepseek-ai/DeepSeek-V3#1244). `finish_reason` comes
back `"stop"` instead of `"tool_calls"`.

Mitigation: `OpenAICompatibleLLM.call_with_tools()` has a retry
loop (max 3 attempts). Detection heuristic: `stop_reason == "end_turn"`
AND the response text contains a tool name from the provided tools
list. P(fail all 3) ≈ 0.1%.

Side effect on non-DeepSeek OpenAI-compatible providers: if a model
legitimately ends a turn while mentioning a tool name in text (rare),
2 extra API calls are wasted before returning the response. Bounded
and benign.

### DeepSeek V4 thinking mode — reasoning_content echo-back

DeepSeek V4 thinking mode returns chain-of-thought in a
`reasoning_content` field alongside `content`. For multi-turn
with tool calls, `reasoning_content` **must** be echoed back.
Empty string (not null) is required on tool-call turns where
thinking didn't fire.

Implementation:
- `_parse_openai_response()`: captures `reasoning_content` via
  `getattr(message, "reasoning_content", None)`, stores as
  `{"type": "reasoning", "reasoning": text}` block in `raw_content`.
- `to_assistant_message()`: checks for `"reasoning"` block type
  alongside `"thinking"` and `"redacted_thinking"` — returns
  structured `raw_content` (not collapsed text) when present.
- `_messages_to_openai()`: extracts `"reasoning"` blocks from
  assistant content, sets `reasoning_content` field on the
  message dict. When `self._thinking` is True and tool_calls are
  present but no reasoning block exists, sets `reasoning_content: ""`.
- Non-DeepSeek providers: `getattr` returns None → guard skips →
  no `reasoning_content` field added. Zero impact on Anthropic,
  Gemini, vanilla OpenAI.

### DeepSeek V4 thinking mode activation

Thinking mode is a per-request parameter, not a model flag.
`OpenAICompatibleLLM` passes `extra_body={"thinking": {"type": "enabled"}}`
to the OpenAI SDK's `create()` when `self._thinking` is True.
Profiles set `thinking: true`; `_make_llm()` passes it to the
constructor.

DeepSeek V4 thinking mode ignores `temperature`, `top_p`,
`presence_penalty`, `frequency_penalty` — no error, just no effect.
Non-thinking profiles work unchanged.

### Legacy model deprecation (deepseek-chat / deepseek-reasoner)

`deepseek-chat` and `deepseek-reasoner` are **fully retired and
inaccessible after 2026-07-24 15:59 UTC**. Both legacy profiles
(`deepseek_chattemp0`, `deepseek_chattemp01`) have been updated
to use `deepseek-v4-flash` as the model name.

### PUMBA Dailo was originally missed

The initial spec stated "pumba.py uses LLMBackend.complete() —
unchanged." This was true for Gulei and Leng, but Dailo has its
own agentic tool-calling loop that used raw `anthropic.Anthropic()`.
When config defaults were switched to DeepSeek profiles, Dailo
tried to call `deepseek-v4-pro` via the Anthropic SDK → auth error.
Fixed by migrating Dailo to `get_pumba_dailo_llm(config)` +
`call_with_tools()` — same pattern as agent_loop and PTECA.


## Dependencies

| Component | What it provides | Import style |
|---|---|---|
| `anthropic` SDK | `anthropic.Anthropic()` client | Inside `AnthropicLLM` (existing) |
| `openai` SDK | `OpenAI()` client | Inside `OpenAICompatibleLLM` (existing) |
| `google-genai` SDK | `genai.Client()` client | Inside `GeminiLLM` (existing, lazy import) |
| `config.py` | `Config` with profile fields | Top-level (existing) |
| `llm_profiles.yaml` | Profile definitions | Read by `Config.get_llm_profile()` (existing) |

No new dependencies. All three SDKs already exist in the codebase.

## File locations

```
MODIFIED:
  src/scripts/llm.py              Add ToolCall, LLMResponse, call_with_tools()
                                   implementations, new factory functions
                                   (incl. get_pumba_dailo_llm),
                                   OpenAICompatibleLLM: thinking param, extra_body,
                                   reasoning_content capture/echo, retry loop.
                                   add import json + dataclass
                                   (_build_kwargs NOT changed — see spec 13b)
  src/scripts/config.py            All role → profile mappings (defaults: DeepSeek V4)
  src/scripts/llm_profiles.yaml    DeepSeek V4 Pro + Flash profiles added,
                                   legacy deepseek-chat profiles updated to v4-flash.
                                   All Anthropic profiles preserved.
  src/harness/agent_loop.py        Replace anthropic SDK with factory + LLMResponse,
                                   accept config param, call set_config()
  src/tools/tool_pteca.py          Replace anthropic SDK with factory + LLMResponse,
                                   accept config param
  src/harness/execute_tool.py      Add _config + set_config(), pass config to PTECA
  src/scripts/Poony_Multiretrieval_S1/src/pumba.py
                                   Dailo agent loop: replace anthropic.Anthropic()
                                   with get_pumba_dailo_llm() + call_with_tools().
                                   Remove manual thinking kwargs. Normalize message
                                   accumulation + tool result format.

DELETED:
  src/scripts/Poony_Multiretrieval_S1/src/llm.py
                                   Dead duplicate of src/scripts/llm.py.
                                   Was only reachable from PMS1 standalone
                                   entry points (app.py, eval scripts).
                                   All PMS1 usage now goes through PSOAS,
                                   where src/__init__.py __path__ extension
                                   resolves src.llm to src/scripts/llm.py
                                   (priority 1 shadow). Remove to prevent
                                   the duplicate drifting out of sync.

UNCHANGED:
  src/scripts/llm_profiles.yaml    Structure unchanged (add profiles as needed)
  src/harness/system_prompt.py     TOOL_DEFINITIONS format is already universal
  src/harness/opaque_registry.py   Pure data store
  src/harness/terminal_router.py   Pure I/O
  src/tools/tool_pms1.py           Uses LLMBackend.complete() (unchanged path)
  src/scripts/stencil2chart.py     No LLM
```


## Ideal demo

### Default behavior (all roles on DeepSeek V4)

```python
# config.py — current defaults (no change needed)
orchestrator_profile: str = "deepseek_v4pro_orchestrator"
# All other roles also default to DeepSeek V4 Pro/Flash
```

```
$ python src/psoas.py "Chart Best Buy gross margins FY2022-2023"

╭──────────────────────────────────────────────────────────────╮
│ PSOAS — Poony Sophomore Orchestrated Analyst Strapon         │
│                                      type answers when prompted │
╰──────────────────────────────────────────────────────────────╯

⠋ ORCHESTRATOR thinking...

[ORCHESTRATOR] PSOAS reporting for duty sir! One firm, margins.
Running PMS1.

[PMS1-Best Buy] Loading index...
[PMS1-Best Buy] Sekei planning... done. 5 cells, 2 batches.
[PMS1-Best Buy] Running 2 batches...
[PMS1-Best Buy] Stencil computed. 5 cells filled.

⠋ ORCHESTRATOR thinking...

[PTECA-Best Buy] 1 firm(s), 5 total rows: Best Buy
[PTECA-Best Buy] ...
[PTECA-Best Buy] Done. 1 chart(s).

[S2C-1] Saved: temp/sessions/20260716.../output/stencil2charted_..._1.svg

[ORCHESTRATOR] Done. 1 chart saved.
```

Externally identical. Internally DeepSeek V4 Pro handles orchestration,
all PMS1 internals use their configured DeepSeek profiles.

### Swap one role back to Anthropic

```python
# config.py — one string change
pteca_profile: str = "anthropic_sonnetmed"
```

PTECA now runs on Anthropic Sonnet. Orchestrator stays on DeepSeek V4 Pro.
Both go through `call_with_tools()`, both produce normalized `LLMResponse`.

### Runtime override (no config.py change)

```python
config = Config.from_env(
    orchestrator_profile="anthropic_orchestrator",
    pteca_profile="deepseek_v4pro_pteca",
    pumba_dailo_profile="anthropic_opusmedthink",
)
run_harness(first_query="Chart Amcor margins", config=config)
```

Mixed-provider in one session: Anthropic orchestrator, DeepSeek PTECA,
Anthropic PUMBA Dailo. Config flows: `run_harness` → `set_config(config)`
in execute_tool → `_exec_pteca` passes `config=_config` → `run_pteca`
uses it for `get_pteca_llm(config)`. PUMBA receives config from
PMS1 orchestrator.py call chain. Terminal output, transcripts,
registry — all unchanged.


## What NOT to change

- `TOOL_DEFINITIONS` schema format in system_prompt.py. It's already
  the canonical representation. Backends convert from it.

- `execute_tool.py` dispatch table and `execute_tool()` public API.
  It still receives `(name: str, params: dict)`. The only additions
  are `_config` / `set_config()` for config propagation, and passing
  `config=_config` to `run_pteca` inside `_exec_pteca`.

- `opaque_registry.py`. Pure data store.

- `terminal_router.py`. Pure I/O routing.

- PMS1 internal pipeline code that uses `LLMBackend.complete()`
  (sekei.py, pto.py, orchestrator.py, stencil.py). These are
  unchanged. Note: pumba.py WAS changed — Dailo's tool-calling
  agent loop was migrated from raw Anthropic SDK to
  `call_with_tools()`. Gulei/Leng (complete()-only) unchanged.

- The `complete()` / `complete_with_usage()` / `stream()` methods on
  LLMBackend. PMS1 code depends on these. Adding `call_with_tools()`
  is purely additive.


## Resolved questions

- **Tool schema format.** Keep Anthropic-native format in
  `system_prompt.py`. It's already stored, converting FROM it is
  simpler than inventing a custom universal format. Renaming
  `input_schema` to `parameters` would add churn for zero benefit.

- **Message format.** Normalized with three roles: `"user"`,
  `"assistant"`, `"tool_results"`. Stored in `messages` list.
  Each backend converts to its native format inside
  `call_with_tools()`. Callers never see provider-specific objects.

- **Where conversion lives.** Inside each backend class, as private
  methods (`_messages_to_X`, `_parse_X_response`, `_tools_to_X`).
  Not in a separate converter module — the conversion logic is
  tightly coupled to the provider SDK.

- **LLMBackend.call_with_tools() default.** Raises
  `NotImplementedError`. PMS1 backends don't need it. If a profile
  without tool support is assigned to orchestrator/PTECA, it fails
  loudly.

- **max_tokens ownership.** All backends store `self._max_tokens`
  from profile at construction. `call_with_tools()` uses it as
  default. Callers omit `max_tokens` for normal calls; override
  only for special cases (checkpoint summary: `max_tokens=1024`).
  The old hardcoded `max_tokens=4096` in agent_loop was an
  artifact — it just happened to match the profile value.

- **Orchestrator model for checkpoint.** The checkpoint summary
  call uses the same backend instance (`backend.call_with_tools()`
  with `tools=[]`). Same provider, same model. No separate factory.

- **`raw_content` vs reconstructed messages.** `raw_content` stores
  normalized content blocks at response-parse time. This is passed
  through `to_assistant_message()` → back into messages list →
  converted per-provider on next `call_with_tools()`. No
  reconstruction from `text` + `tool_calls` (which would lose
  block ordering if text is interleaved with tool calls).

- **PMS1 internal channel overrides.** `tool_pms1.py` uses
  `override_channel("PMS1", ch)` + `clear_override("PMS1")`.
  Unaffected by this spec — it's terminal_router machinery, not
  LLM machinery.

- **Thinking blocks.** Anthropic `type="thinking"` and
  `type="redacted_thinking"` blocks are preserved in `raw_content`
  and echoed back via `to_assistant_message()`. The API requires
  this for multi-turn. Non-Anthropic backends skip them during
  message conversion (their loops only match `"text"` and
  `"tool_use"`).

- **Thinking + tool calling.** `call_with_tools()` supports
  extended thinking. Thinking kwargs are injected when
  `self._thinking is True` and `effective_max > thinking_budget`.
  Thinking kwargs mirror `_build_kwargs()` (same `thinking` dict
  and `output_config`). System prompt handling differs:
  `call_with_tools()` always uses `system=`, while `_build_kwargs()`
  folds into user message for thinking mode (see spec 13b for fix).
  When the caller overrides `max_tokens` below the thinking budget
  (checkpoint summary), thinking is silently skipped — the caller
  wants a short response.

- **All backends store max_tokens.** `OpenAICompatibleLLM` and
  `GeminiLLM` now accept `max_tokens` in `__init__` (stored as
  `self._max_tokens`). `_make_llm()` passes it from profile.
  This makes `max_tokens` default behavior uniform across all
  backends. `complete()` callers are unaffected — `complete()`
  doesn't use `self._max_tokens` on OpenAI/Gemini backends.

- **config field names.** `orchestrator_profile` and
  `pteca_profile` — same pattern as `sekei_profile`,
  `pto_judge_profile`. Consistent, discoverable.

- **Config propagation.** Config flows from a single source:
  `run_harness(config=)` → `set_config(config)` in execute_tool →
  `_exec_pteca` passes `config=_config` → `run_pteca(config=)`.
  Same pattern as `_session_dir` / `set_session_dir()`. If no
  config is passed, each module falls back to `Config.from_env()`.

- **`_build_kwargs` NOT changed in this spec.** Separated to
  spec 13b for independent rollback. `call_with_tools()` always
  uses `system=` regardless of `_build_kwargs` behavior.

- **No new files.** All changes are to existing files. No new
  modules, no new abstractions beyond ToolCall + LLMResponse
  dataclasses.

- **PUMBA Dailo migration.** Dailo was originally missed because
  the spec assumed all of pumba.py used `complete()`. Gulei and
  Leng do; Dailo has its own agentic tool-calling loop. Migrated
  with the same pattern as agent_loop and PTECA: factory +
  `call_with_tools()` + ToolCall + LLMResponse. The manual
  thinking kwargs block (Anthropic-specific `opus-4-8` detection)
  was removed — the backend handles thinking internally.

- **DeepSeek V4 thinking via OpenAI-compatible SDK.** Thinking is
  a per-request param (`extra_body={"thinking": {"type": "enabled"}}`),
  not a model-level feature. `OpenAICompatibleLLM` reads `thinking`
  from the profile via `_make_llm()`. The `complete()` and
  `call_with_tools()` methods pass `extra_body` when `self._thinking`
  is True. Non-thinking profiles are unaffected — `extra_body` is
  not added. Non-DeepSeek providers with `thinking=False` (the
  default) see zero change.

- **`reasoning_content` block type.** Stored as `"reasoning"` (not
  `"thinking"`) to distinguish from Anthropic's `"thinking"` blocks.
  `to_assistant_message()` treats both as structured content.
  `_messages_to_openai()` converts `"reasoning"` blocks to the
  `reasoning_content` field. `_messages_to_anthropic()` and
  `_messages_to_gemini()` silently skip `"reasoning"` blocks
  (their loops only match `"text"` and `"tool_use"`). `_dump_turn()`
  also silently skips them (same as `"thinking"` blocks).

- **Retry loop scope.** The tool-as-text retry applies to all
  `OpenAICompatibleLLM` instances, not just DeepSeek. The detection
  heuristic (end_turn + tool name in text) is narrow enough that
  false positives on well-behaved providers are rare. Worst case:
  2 wasted API calls, then the response is returned normally.
  Not gated on provider name to avoid brittleness.

- **DeepSeek V4 model naming.** `deepseek-v4-pro` and
  `deepseek-v4-flash` are the stable model IDs. Legacy
  `deepseek-chat` / `deepseek-reasoner` map to v4-flash
  non-thinking / thinking modes respectively, and are
  deprecated 2026-07-24. The `base_url` (`https://api.deepseek.com/v1`)
  is unchanged.

- **Provider revert is one-string.** Every role's profile is a
  single string in config.py. All original Anthropic profiles
  (`anthropic_orchestrator`, `anthropic_opusmedthink`,
  `anthropic_sonnetmed`, `anthropic_hayasui`, etc.) are preserved
  in llm_profiles.yaml. Switching back requires no code changes —
  just change the profile string. Runtime override via
  `Config.from_env(orchestrator_profile="anthropic_orchestrator")`
  also works.
