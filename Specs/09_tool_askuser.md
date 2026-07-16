# Tool Contract: ask_user

Orchestrator-level tool for asking the user a clarifying question.
Thin passthrough to `channel.input()`. No logic, no state.

## JSON schema

Source of truth. 04_system_prompt.md copies this.

```json
{
    "name": "ask_user",
    "description": "Ask the user a clarifying question. Use for high-level ambiguity only (which firms, which metrics, which years). Do not use for sub-tool interactions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user."
            }
        },
        "required": ["question"]
    }
}
```

## Architecture

```
execute_tool("ask_user", {"question": "Which firms should I run?"})
    │
    ▼
_exec_ask_user(params)
    │
    ├── answer = _orchestrator_channel.input("Which firms should I run?")
    │       │
    │       ├── TerminalRouter displays:
    │       │     [ORCHESTRATOR] Which firms should I run?
    │       │     > _
    │       │
    │       └── user types "Best Buy and Amcor"
    │
    └── return "User answered: Best Buy and Amcor"
```

## execute_tool branch

```python
def _exec_ask_user(params: dict) -> str:
    question = params["question"]
    answer = _orchestrator_channel.input(question)
    return f"User answered: {answer}"
```

`_orchestrator_channel` is module-level in `execute_tool.py` — created
once via `register("ORCHESTRATOR")`, reused across calls.

## Scope

Orchestrator-level ONLY. This tool is for the orchestrator LLM to
ask the user high-level questions before or during execution:

- "Which firms should I run?"
- "Do you want charts or just the stencil data?"
- "FY2022 and FY2023, or all available years?"

Sub-tool user interaction (PMS1's "Relax FY filter?", PTECA's
"Keep Revenue?") goes through each tool's own ToolChannel — NOT
through this tool. The orchestrator LLM never sees sub-tool
conversations.

## ToolChannel label

Always `"ORCHESTRATOR"`. Uses module-level `_orchestrator_channel`
in `execute_tool.py` — created once via `register("ORCHESTRATOR")`,
reused across calls.

## Internal user interaction

The entire tool IS user interaction. One `channel.input()` call,
one answer returned.

## No registry, no disk, no state

- No `registry.store()` — the answer is a short string, returned
  inline as tool_result content.
- No disk persistence — answer appears in the transcript via
  the normal tool_result logging in agent_loop.
- No state across calls — each call is independent.

## Dependencies

| Component | What it provides |
|---|---|
| `src/harness/terminal_router.py` | `register()`, `ToolChannel` |

Nothing else.

## File location

No separate file. The `_exec_ask_user` function lives in
`execute_tool.py` directly — it's 4 lines.

## Ideal demo

```
[ORCHESTRATOR] PSOAS reporting for duty sir!

Two firms mentioned but I want to confirm the time range.

                                 ← LLM calls ask_user

[ORCHESTRATOR] FY2022 and FY2023 only, or all available fiscal years?
> just 2022 and 2023

                                 ← tool_result: "User answered: just 2022 and 2023"
                                 ← LLM proceeds with run_pms1 calls
```

## Resolved questions

- **Scope.** Orchestrator-level only. High-level clarification.
  Sub-tool interaction is separate.
- **No separate file.** Lives in execute_tool.py as a 4-line
  handler function.
- **Label.** "ORCHESTRATOR" — module-level channel, reused across calls.
- **Return format.** `"User answered: {answer}"` — plain string.
