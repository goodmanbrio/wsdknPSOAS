# Tool Contract: PTECA

Poony Table Et Chart Agent. Internal agent loop that takes a stencil
dict + user query, decides which metrics to keep, groups by unit,
and outputs one or more chart_input dicts for stencil2chart.

PTECA is NOT a dumb function. It has its own while loop, its own
messages list, its own LLM calls, and its own tools (pteca_ask_user +
finalize). The orchestrator LLM never sees PTECA's internal
conversation.

## JSON schema

Source of truth. 04_system_prompt.md copies this.

```json
{
    "name": "run_pteca",
    "description": "Trim and split a stencil into chart-ready data. Takes a stencil handle and returns a chart_input handle.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stencil": {
                "type": "string",
                "description": "Opaque handle to a stencil, e.g. '$var_1'"
            },
            "query": {
                "type": "string",
                "description": "What the user wants charted. Used to decide which metrics to keep/drop."
            },
            "firm": {
                "type": "string",
                "description": "Company name. Used for asset naming. e.g. 'Best Buy'"
            }
        },
        "required": ["stencil", "query", "firm"]
    }
}
```

## Architecture

```
execute_tool("run_pteca", {"stencil": <resolved dict>, "query": "...", "firm": "Best Buy"})
    │
    ▼
run_pteca(stencil_dict, query, firm, channel)
    │                                         ← in tool_pteca.py (NEW)
    │
    ├── Format stencil into human-readable text for LLM
    │
    ├── messages = [{"role": "user", "content": <formatted stencil + query>}]
    │
    ▼
┌────────────────────────────────────────────────────────────────┐
│  PTECA INTERNAL WHILE LOOP                                     │
│                                                                │
│  client.messages.create(                                       │
│      model = "claude-sonnet-4-6"  (from anthropic_sonnetmed)   │
│      system = PTECA_SYSTEM_PROMPT                              │
│      messages = messages                                       │
│      tools = [pteca_ask_user, finalize]                              │
│  )                                                             │
│                                                                │
│  stop_reason == "tool_use":                                    │
│      ┌─ "pteca_ask_user" ───────────────────────────────────┐        │
│      │  answer = channel.input(question)               │        │
│      │  tool_result = "User answered: {answer}"        │        │
│      │  append to messages, continue loop              │        │
│      └─────────────────────────────────────────────────┘        │
│      ┌─ "finalize" ───────────────────────────────────┐        │
│      │  decisions = block.input["charts"]              │        │
│      │  chart_inputs = _build_chart_inputs(            │        │
│      │      stencil_dict, decisions)                   │        │
│      │  BREAK loop                                     │        │
│      └─────────────────────────────────────────────────┘        │
│                                                                │
│  stop_reason == "end_turn":                                    │
│      ERROR — must call finalize, not end_turn                  │
│      inject error message, continue loop                       │
│                                                                │
│  Guard rail: max 4 turns                                       │
└────────────────────────────────────────────────────────────────┘
    │
    ▼
return list[dict]   ← list of chart_input dicts
```

### What the LLM decides vs what Python builds

```
LLM decides (via finalize tool):          Python builds (mechanically):
─────────────────────────────             ─────────────────────────────
which metrics to keep                     chart_input["series"][i]["values"]
how to group into charts                    → looked up from stencil rows
title for each chart                      chart_input["periods"]
denomination_label for each chart           → copied from stencil["periods"]
                                          full chart_input dict structure

LLM NEVER touches numbers. Values flow stencil → chart_input without
passing through the LLM.
```

## PTECA's internal tools

### pteca_ask_user

```json
{
    "name": "pteca_ask_user",
    "description": "Ask the user a clarifying question about what they want charted. Use when the query is ambiguous about which metrics to include or exclude.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask."
            }
        },
        "required": ["question"]
    }
}
```

Handled by `channel.input(question)`. User sees `[PTECA-Best Buy] Keep Revenue?`

### finalize

```json
{
    "name": "finalize",
    "description": "Output your final decisions. Call this when you know which metrics to keep and how to group them. Each chart groups metrics with the same unit.",
    "input_schema": {
        "type": "object",
        "properties": {
            "charts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Chart title. e.g. 'Best Buy Margins (FY2022-2023)'"
                        },
                        "denomination_label": {
                            "type": "string",
                            "description": "Y-axis label. e.g. '(%)', '(millions USD)', '(count)'"
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Metric names to include. Must match stencil row names exactly."
                        }
                    },
                    "required": ["title", "denomination_label", "metrics"]
                }
            }
        },
        "required": ["charts"]
    }
}
```

## PTECA system prompt

```
You are PTECA — a chart planning agent. You receive a financial
data stencil and a user query. Your job is to decide which metrics
to chart, how to group them, and what to title each chart.

## What you receive

A stencil with rows of financial metrics for a company. Each row has:
- metric name (e.g. "Revenue", "Gross Margin")
- values per period
- unit (e.g. "USD", "%")
- denomination (e.g. "bn", "mn", "unit")

And the user's original query describing what they want charted.

## What you do

1. Read the stencil rows and the user's query.
2. Decide which metrics the user wants charted.
   - If the query is clear (e.g. "gross margins only"), decide directly.
   - If ambiguous, use pteca_ask_user to clarify.
3. Group metrics by unit compatibility:
   - % metrics go in one chart (denomination_label: "(%)")
   - USD metrics go in another chart (include denomination in label)
   - Different units cannot share a Y-axis.
4. Call finalize with your decisions.

## Rules

- You MUST call finalize to complete. Do not end without calling it.
- Metric names in finalize must match stencil row names EXACTLY.
- Do not invent metrics that aren't in the stencil.
- Keep it concise. Ask at most 1-2 questions.
- If the user's intent is clear, skip pteca_ask_user and finalize directly.
```

## Input to PTECA's LLM

The first user message is a formatted representation of the stencil
+ the user's query:

```python
def _format_pteca_input(stencil: dict, query: str) -> str:
    lines = [f"Firm: {stencil['firm']}"]
    lines.append(f"Periods: {', '.join(stencil['periods'])}")
    lines.append("")
    lines.append("Stencil rows:")
    for row in stencil["rows"]:
        vals = ", ".join(str(v) for v in row["values"])
        unit_info = ""
        if row["unit"]:
            unit_info = f" [{row['unit']}"
            if row["denomination"]:
                unit_info += f", {row['denomination']}"
            unit_info += "]"
        lines.append(f"  - {row['metric']}{unit_info}: {vals}")
    lines.append("")
    lines.append(f"User query: {query}")
    return "\n".join(lines)
```

Example output:

```
Firm: Best Buy
Periods: FY2022, FY2023

Stencil rows:
  - Revenue [USD, bn]: 51761000000, 46298000000
  - Gross Profit [USD, bn]: 15005000000, 13610000000
  - Gross Margin [%]: 29, 29
  - Net Income [USD, bn]: 2454000000, 1419000000
  - Net Profit Margin [%]: 4.7, 3.1

User query: gross margins only
```

## _build_chart_inputs

Python function that constructs chart_input dicts from the LLM's
finalize decisions + the stencil data. Values are looked up
mechanically — never LLM-generated.

```python
def _build_chart_inputs(stencil: dict, charts: list[dict]) -> list[dict]:
    """Build chart_input dicts from finalize decisions + stencil data."""
    # Index stencil rows by metric name for lookup
    row_by_metric = {row["metric"]: row for row in stencil["rows"]}

    result = []
    for chart in charts:
        series = []
        for metric_name in chart["metrics"]:
            row = row_by_metric.get(metric_name)
            if row is None:
                # LLM hallucinated a metric name — skip it
                continue
            series.append({
                "metric": metric_name,
                "values": row["values"],
            })

        if not series:
            continue

        result.append({
            "title": chart["title"],
            "periods": stencil["periods"],
            "denomination_label": chart["denomination_label"],
            "series": series,
        })

    return result
```

## Multi-handle return

PTECA returns `list[dict]`. execute_tool stores each chart_input
separately in the registry:

```python
def _exec_pteca(params: dict) -> str:
    stencil = params["stencil"]       # already resolved
    query = params["query"]
    firm = params["firm"]
    channel = register(f"PTECA-{firm}")

    chart_inputs = run_pteca(stencil, query, firm, channel=channel)

    # Store each chart_input as a separate handle
    handles = []
    for i, ci in enumerate(chart_inputs):
        n_series = len(ci.get("series", []))
        desc = f"{firm} chart {i+1} ({n_series} series)"
        handle = registry.store(ci, desc)
        handles.append(f"{handle}: {desc}")

    # Persist all to disk
    _dump_asset(f"{_sanitize(firm)}_chart_inputs_{handle.lstrip('$')}.json", chart_inputs)

    return f"PTECA complete. {len(chart_inputs)} chart(s):\n" + "\n".join(handles)
```

Orchestrator sees:
```
PTECA complete. 2 chart(s):
$var_3: Best Buy chart 1 (2 series)
$var_4: Best Buy chart 2 (2 series)
```

Then calls `stencil2chart($var_3)` and `stencil2chart($var_4)`.

## LLM profile

Uses `anthropic_sonnetmed` profile via `Config`:

```yaml
# In llm_profiles.yaml (accessed through Config):
anthropic_sonnetmed:
  provider: anthropic
  model: claude-sonnet-4-6
  thinking: false
  max_tokens: 3000
```

No thinking needed. PTECA's task is straightforward — filter rows,
group by unit, title charts. Sonnet is sufficient.

PTECA uses `anthropic.Anthropic()` directly (not `LLMBackend`) because
it needs `tools=` in the API call. Profile is read for model name +
max_tokens only.

```python
config = Config.from_env()
profile = config.get_llm_profile("anthropic_sonnetmed")
model = profile["model"]       # "claude-sonnet-4-6"
max_tokens = profile["max_tokens"]  # 3000
```

## Guard rails

### Max turns: 4

PTECA's task is simple — 4 turns is generous. Typical flow:
- Turn 1: LLM reads stencil, calls pteca_ask_user or finalize
- Turn 2: If pteca_ask_user, gets answer, calls finalize
- Done in 1-2 turns.

After 4 turns without finalize, PTECA returns error:
```
"PTECA error: exceeded 4 turns without calling finalize."
```

### end_turn without finalize

If LLM returns `end_turn` instead of calling `finalize`, inject
error message and continue loop:

```python
if response.stop_reason == "end_turn":
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": "Error: you must call the finalize tool with your decisions. Do not end without calling finalize.",
    })
    turn_counter += 1
    continue
```

### Invalid metric names

If finalize references a metric name not in the stencil,
`_build_chart_inputs` silently skips it. If ALL metrics in a chart
are invalid, the chart is dropped. If zero valid charts remain,
PTECA returns error:
```
"PTECA error: no valid charts produced. All metric names were invalid."
```

## Entry function

### run_pteca(stencil: dict, query: str, firm: str, channel: ToolChannel | None = None) → list[dict]

| Param | Type | Description |
|---|---|---|
| `stencil` | `dict` | Stencil dict from PMS1 (serialize_stencil output) |
| `query` | `str` | What the user wants charted |
| `firm` | `str` | Company name (for channel label) |
| `channel` | `ToolChannel \| None` | Per-invocation channel. Falls back to `register("PTECA")` |

Returns list of chart_input dicts.

## Implementation sketch

```python
"""
tool_pteca.py — PTECA: internal agent loop for stencil → chart_input.
"""
from __future__ import annotations

import anthropic

from src.harness.terminal_router import register, ToolChannel
from src.config import Config

_default_channel = register("PTECA")

PTECA_TOOLS = [
    {
        "name": "pteca_ask_user",
        "description": "Ask the user a clarifying question about what they want charted.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "finalize",
        "description": "Output your final chart decisions. Each chart groups metrics with the same unit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "charts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "denomination_label": {"type": "string"},
                            "metrics": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["title", "denomination_label", "metrics"],
                    },
                }
            },
            "required": ["charts"],
        },
    },
]

MAX_TURNS = 4


def run_pteca(
    stencil: dict,
    query: str,
    firm: str,
    channel: ToolChannel | None = None,
) -> list[dict]:
    """Run PTECA agent loop. Returns list of chart_input dicts."""
    ch = channel or _default_channel
    client = anthropic.Anthropic()

    # Load model config
    config = Config.from_env()
    profile = config.get_llm_profile("anthropic_sonnetmed")
    model = profile["model"]
    max_tokens = profile["max_tokens"]

    # Format input
    user_msg = _format_pteca_input(stencil, query)
    ch.print(f"Stencil has {len(stencil['rows'])} rows: "
             + ", ".join(r['metric'] for r in stencil['rows']) + ".")

    messages = [{"role": "user", "content": user_msg}]
    turn_counter = 0

    while turn_counter < MAX_TURNS:
        response = client.messages.create(
            model=model,
            system=PTECA_SYSTEM_PROMPT,
            messages=messages,
            tools=PTECA_TOOLS,
            max_tokens=max_tokens,
        )

        # ── end_turn (error — must call finalize) ─────────
        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": "Error: you must call the finalize tool. Do not end without it.",
            })
            turn_counter += 1
            continue

        # ── no tool calls (max_tokens, unexpected stop) ──
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_blocks:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": (
                    "Error: response contained no tool calls (possibly "
                    "truncated). You must call the finalize tool."
                ),
            })
            turn_counter += 1
            continue

        # ── tool_use ──────────────────────────────────────
        tool_results = []
        finalize_result = None

        for tb in tool_blocks:
            if tb.name == "pteca_ask_user":
                question = tb.input["question"]
                answer = ch.input(question)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": f"User answered: {answer}",
                })

            elif tb.name == "finalize":
                # Reject if pteca_ask_user was also called this turn —
                # LLM must see the user's answer before finalizing
                asked_user = any(b.name == "pteca_ask_user" for b in tool_blocks)
                if asked_user:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": (
                            "Error: cannot finalize in the same turn as "
                            "pteca_ask_user. See the user's answer first, then "
                            "call finalize."
                        ),
                    })
                else:
                    charts_decisions = tb.input["charts"]
                    chart_inputs = _build_chart_inputs(stencil, charts_decisions)
                    if not chart_inputs:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": (
                                "Error: no valid charts produced. All metric "
                                "names were invalid. Check stencil row names."
                            ),
                        })
                    else:
                        finalize_result = chart_inputs
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": f"Finalized. {len(chart_inputs)} chart(s) built.",
                        })

            else:
                # Unknown tool — must still provide tool_result or
                # the Anthropic API rejects the next messages.create()
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": (
                        f"Error: unknown tool '{tb.name}'. "
                        "Available: pteca_ask_user, finalize."
                    ),
                })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if finalize_result is not None:
            n = len(finalize_result)
            ch.print(f"Done. {n} chart(s).")
            return finalize_result

        turn_counter += 1

    # Exceeded max turns
    ch.print("Error: exceeded max turns without finalize.")
    raise RuntimeError("PTECA exceeded 4 turns without calling finalize.")


def _format_pteca_input(stencil: dict, query: str) -> str:
    lines = [f"Firm: {stencil['firm']}"]
    lines.append(f"Periods: {', '.join(stencil['periods'])}")
    lines.append("")
    lines.append("Stencil rows:")
    for row in stencil["rows"]:
        vals = ", ".join(str(v) for v in row["values"])
        unit_info = ""
        if row["unit"]:
            unit_info = f" [{row['unit']}"
            if row["denomination"]:
                unit_info += f", {row['denomination']}"
            unit_info += "]"
        lines.append(f"  - {row['metric']}{unit_info}: {vals}")
    lines.append("")
    lines.append(f"User query: {query}")
    return "\n".join(lines)


def _build_chart_inputs(stencil: dict, charts: list[dict]) -> list[dict]:
    row_by_metric = {row["metric"]: row for row in stencil["rows"]}
    result = []
    for chart in charts:
        series = []
        for metric_name in chart["metrics"]:
            row = row_by_metric.get(metric_name)
            if row is None:
                continue
            series.append({
                "metric": metric_name,
                "values": row["values"],
            })
        if not series:
            continue
        result.append({
            "title": chart["title"],
            "periods": stencil["periods"],
            "denomination_label": chart["denomination_label"],
            "series": series,
        })
    return result


PTECA_SYSTEM_PROMPT = """You are PTECA — a chart planning agent. You receive a financial
data stencil and a user query. Your job is to decide which metrics
to chart, how to group them, and what to title each chart.

## What you receive

A stencil with rows of financial metrics for a company. Each row has:
- metric name (e.g. "Revenue", "Gross Margin")
- values per period
- unit (e.g. "USD", "%")
- denomination (e.g. "bn", "mn", "unit")

And the user's original query describing what they want charted.

## What you do

1. Read the stencil rows and the user's query.
2. Decide which metrics the user wants charted.
   - If the query is clear (e.g. "gross margins only"), decide directly.
   - If ambiguous, use pteca_ask_user to clarify.
3. Group metrics by unit compatibility:
   - % metrics go in one chart (denomination_label: "(%)")
   - USD metrics go in another chart (include denomination in label)
   - Different units cannot share a Y-axis.
4. Call finalize with your decisions.

## Rules

- You MUST call finalize to complete. Do not end without calling it.
- Metric names in finalize must match stencil row names EXACTLY.
- Do not invent metrics that aren't in the stencil.
- Keep it concise. Ask at most 1-2 questions.
- If the user's intent is clear, skip pteca_ask_user and finalize directly."""
```

## Dependencies

| Component | What it provides |
|---|---|
| `anthropic` SDK | `client.messages.create(tools=...)` |
| `src/harness/terminal_router.py` | `register()`, `ToolChannel` |
| `src/config.py` | `Config.from_env()` — LLM profile access |

Does NOT import from: agent_loop, execute_tool, opaque_registry,
system_prompt. No circular deps.

## File location

```
PSOAS/src/tool_pteca.py
```

## Ideal demo

```
[PTECA-Best Buy] Stencil has 5 rows: Revenue, Gross Profit, Gross Margin,
                 Net Income, Net Profit Margin.

                                 ← Turn 1: LLM reads stencil, query is
                                   "gross margins" — clear enough, but
                                   asks about net margin

[PTECA-Best Buy] Include Net Profit Margin alongside Gross Margin?
> yes

                                 ← Turn 2: LLM calls finalize
                                   finalize({charts: [{
                                     title: "Best Buy Margins (FY2022-2023)",
                                     denomination_label: "(%)",
                                     metrics: ["Gross Margin", "Net Profit Margin"]
                                   }]})

[PTECA-Best Buy] Done. 1 chart(s).
```

### Multi-chart split demo

```
[PTECA-Amcor] Stencil has 5 rows: Revenue, Gross Profit, Gross Margin,
              Net Income, Net Profit Margin.

                                 ← Turn 1: query is "chart everything"
                                   LLM calls finalize directly:
                                   finalize({charts: [
                                     {title: "Amcor Revenue & Profit (FY2022-2023)",
                                      denomination_label: "(billions USD)",
                                      metrics: ["Revenue", "Gross Profit", "Net Income"]},
                                     {title: "Amcor Margins (FY2022-2023)",
                                      denomination_label: "(%)",
                                      metrics: ["Gross Margin", "Net Profit Margin"]}
                                   ]})

[PTECA-Amcor] Done. 2 chart(s).
```

## Resolved questions

- **LLM decides, Python builds.** LLM outputs metric names + titles
  via finalize tool. Python looks up values from stencil and
  constructs chart_input dicts. Values never pass through the LLM.
- **Internal agent loop.** Own while loop, own messages, own tools
  (pteca_ask_user + finalize). Orchestrator never sees PTECA's conversation.
- **Multi-handle return.** Returns list of chart_input dicts.
  execute_tool stores each separately ($var_N per chart).
  Orchestrator calls stencil2chart per handle.
- **Model.** Sonnet 4.6 via anthropic_sonnetmed profile. No thinking.
  Uses raw anthropic SDK (not LLMBackend) because needs tools=.
- **Max turns.** 4. Typical flow is 1-2 turns.
- **end_turn without finalize.** Error injected back into messages,
  loop continues.
- **Invalid metric names.** Silently skipped. If zero valid charts,
  raises error.
