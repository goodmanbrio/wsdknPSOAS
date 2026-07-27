# Tool Contract: PTECA

Poony Table Et Chart Agent. Internal agent loop that takes one or more
stencil dicts + user query, always asks the user about chart layout
preferences (with ASCII sketches), and outputs one or more chart_input
dicts for stencil2chart.

PTECA is NOT a dumb function. It has its own while loop, its own
messages list, its own LLM calls, and its own tools (pteca_ask_user +
finalize). The orchestrator LLM never sees PTECA's internal
conversation.

## JSON schema

Source of truth. 04_system_prompt.md copies this.

```json
{
    "name": "run_pteca",
    "description": "Plan charts from one or more stencils. Pass ALL stencil handles in one call. PTECA interacts with the user to decide chart layout — do not pre-decide yourself.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stencils": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of stencil handles, e.g. ['$var_1', '$var_2']. One per firm."
            },
            "query": {
                "type": "string",
                "description": "What the user wants charted. Passed to PTECA for context."
            }
        },
        "required": ["stencils", "query"]
    }
}
```

## Architecture

```
execute_tool("run_pteca", {"stencils": [<resolved dict>, ...], "query": "..."})
    │
    ▼
run_pteca(stencils_list, query, channel)
    │                                         ← in tool_pteca.py (NEW)
    │
    ├── Format stencils (one per firm) into LLM input text
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
│      │  channel.print(question, markdown=True)          │        │
│      │  answer = channel.input("")                     │        │
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
│  Guard rail: max 8 turns                                       │
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
    "description": "Ask the user about chart layout preferences. Present options with ASCII chart sketches showing the approximate shape of the data.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask, including ASCII chart previews and options."
            }
        },
        "required": ["question"]
    }
}
```

Handled by `channel.print(question, markdown=True)` then `channel.input("")`. User sees the ASCII chart sketch and options printed, then an input prompt.

### finalize

```json
{
    "name": "finalize",
    "description": "Output final chart decisions after user confirms layout. Each chart groups metrics with compatible units.",
    "input_schema": {
        "type": "object",
        "properties": {
            "charts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string"
                        },
                        "denomination_label": {
                            "type": "string"
                        },
                        "metrics": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "firm": {"type": "string"},
                                    "metric": {"type": "string"}
                                },
                                "required": ["firm", "metric"]
                            }
                        },
                        "periods": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Periods for x-axis, e.g. ['FY2022', 'FY2023']"
                        }
                    },
                    "required": ["title", "denomination_label", "metrics", "periods"]
                }
            }
        },
        "required": ["charts"]
    }
}
```

## PTECA system prompt

```
You are PTECA — a chart planning agent. You receive one or more
financial data stencils (each from a different company) and a user
query. Your job is to decide how to chart the data.

## What you receive

One or more stencils, each with:
- firm name
- periods (e.g. FY2020, FY2021, FY2022)
- rows of financial metrics, each with: metric name, values per
  period, unit (e.g. "%", "USD"), denomination (e.g. "bn", "mn")

And the user's query describing what they want charted.

## What you MUST do

1. Analyze the stencils. Note:
   - How many firms
   - Which metrics each firm has (highlight near-matches like
     "Net profit margin" vs "Net margin")
   - Period coverage per firm (highlight mismatches)
   - Unit compatibility (% vs USD — cannot share a Y-axis)

2. ALWAYS present options to the user using pteca_ask_user.
   Even for single-firm, single-metric cases — always ask.

   Present 2-3 layout options. For each option, show a data preview
   table of what each chart would contain. Use aligned columns with
   spaces (not pipe tables). Example:

   **OPTION A — Comparison by metric**

   Chart 1: "Gross Margin Comparison"
                    FY2020    FY2021
   Best Buy          23.0%     22.4%
   Boeing            -9.8%      4.8%

   Chart 2: "Net Margin Comparison"
   (same format with net margin values)

   **OPTION B — Per firm**
   (tables showing all metrics per firm)

   For multi-firm cases, typical options:
   A) Comparison by metric — both firms on same chart per metric
   B) Per-firm — all metrics per firm on separate charts
   C) Everything on one chart (only if units are compatible)

   If periods don't fully overlap across firms, state the mismatch
   and offer: intersect only, pad missing with 0, or leave gaps.

   End with: "Or describe your own layout."

3. Read the user's response. They may:
   - Pick an option (A/B/C) → finalize immediately, no reconfirm needed
   - Describe a custom layout → redraw ASCII preview, ask to confirm,
     iterate until user says yes
   - Adjust your suggestion → same as custom, redraw + confirm

4. Call finalize with the confirmed chart decisions.

## Finalize format

Each chart in finalize has:
- title: chart title string
- periods: explicit list of periods for the X-axis
- denomination_label: Y-axis label (e.g. "(%)", "(USD bn)")
- metrics: list of {firm, metric} objects — firm and metric names
  must match stencil data EXACTLY

For multi-firm charts, the legend will automatically show
"Firm - Metric" (e.g. "Best Buy - Gross margin"). For single-firm
charts, just the metric name is shown.

## Rules

- You MUST call pteca_ask_user at least once before finalize.
- You MUST call finalize to complete. Do not end without calling it.
- If the user says "cancel", "skip", "nevermind", or otherwise
  wants to abort charting, call finalize with an empty charts list.
  This exits cleanly — do not keep asking.
- Metric names must match stencil row names EXACTLY.
- Firm names must match stencil firm names EXACTLY.
- Do not invent metrics or firms not in the stencils.
- Use markdown pipe tables for data previews, not ASCII art.
- Do not call finalize in the same turn as pteca_ask_user.
- No emojis except 💦. No others. Ever.
```

## Input to PTECA's LLM

The first user message is a formatted representation of all stencils
+ the user's query. Each firm gets a `### {firm}` section:

```python
def _format_pteca_input(stencils: list[dict], query: str) -> str:
    lines = [f"Number of firms: {len(stencils)}", ""]
    for stencil in stencils:
        lines.append(f"### {stencil['firm']}")
        lines.append(f"Periods: {', '.join(stencil['periods'])}")
        lines.append("Rows:")
        for row in stencil["rows"]:
            vals = ", ".join(str(v) for v in row["values"])
            unit_info = ""
            if row.get("unit"):
                unit_info = f" [{row['unit']}"
                if row.get("denomination"):
                    unit_info += f", {row['denomination']}"
                unit_info += "]"
            lines.append(f"  - {row['metric']}{unit_info}: {vals}")
        lines.append("")
    lines.append(f"User query: {query}")
    lines.append("")
    return "\n".join(lines)
```

Example output:

```
Number of firms: 1

### Best Buy
Periods: FY2022, FY2023
Rows:
  - Revenue [USD, bn]: 51761000000, 46298000000
  - Gross Profit [USD, bn]: 15005000000, 13610000000
  - Gross Margin [%]: 29, 29
  - Net Income [USD, bn]: 2454000000, 1419000000
  - Net Profit Margin [%]: 4.7, 3.1

User query: gross margins only

```

## _build_chart_inputs

Python function that constructs chart_input dicts from the LLM's
finalize decisions + multi-stencil data. Builds a two-level index
`{firm: {"rows": {metric: row}, "periods": [...]}}` for cross-stencil
lookup. Uses index-based period alignment (0-padded for missing) and
multi-firm legend prefixing with hyphen separator.
Values are looked up mechanically — never LLM-generated.

```python
def _build_chart_inputs(stencils: list[dict], charts: list[dict]) -> list[dict]:
    """Build chart_input dicts from finalize decisions + stencil data.

    Handles multi-stencil period alignment. Missing values are padded
    with 0 (dummy value for plotting). For multi-firm charts, series
    metric names are prefixed with firm name.
    """
    multi_firm = len(stencils) > 1

    # Index: {firm_name: {"rows": {metric: row}, "periods": [...]}}
    by_firm: dict[str, dict] = {}
    for s in stencils:
        firm = s["firm"]
        by_firm[firm] = {
            "rows": {row["metric"]: row for row in s["rows"]},
            "periods": s["periods"],
        }

    result = []
    for chart in charts:
        chart_periods = chart["periods"]
        series = []
        for metric_spec in chart["metrics"]:
            firm = metric_spec["firm"]
            metric_name = metric_spec["metric"]

            firm_data = by_firm.get(firm)
            if firm_data is None:
                continue
            row = firm_data["rows"].get(metric_name)
            if row is None:
                continue

            # Align values to chart_periods via index lookup
            firm_periods = firm_data["periods"]
            period_to_idx = {p: i for i, p in enumerate(firm_periods)}
            aligned_values = []
            for p in chart_periods:
                idx = period_to_idx.get(p)
                if idx is not None:
                    aligned_values.append(row["values"][idx])
                else:
                    aligned_values.append(0)  # pad missing

            # Legend name
            if multi_firm:
                legend = f"{firm} - {metric_name}"
            else:
                legend = metric_name

            series.append({
                "metric": legend,
                "values": aligned_values,
            })

        if not series:
            continue
        result.append({
            "title": chart["title"],
            "periods": chart_periods,
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
    stencils = params["stencils"]       # already resolved (list of dicts)
    query = params["query"]
    firms = [s["firm"] for s in stencils]
    label = "PTECA-" + "+".join(firms)
    channel = register(label)

    chart_inputs = run_pteca(stencils, query, channel=channel)

    # Cancel path — empty chart_inputs means user cancelled
    if not chart_inputs:
        return "PTECA cancelled by user. No charts to render. Move on."

    # Store each chart_input as a separate handle
    handles = []
    last_handle = ""
    for i, ci in enumerate(chart_inputs):
        n_series = len(ci.get("series", []))
        desc = f"chart {i + 1} ({n_series} series)"
        handle = registry.store(ci, desc)
        handles.append(f"{handle}: {desc}")
        last_handle = handle

    firms_slug = "_".join(_sanitize(f) for f in firms)
    _dump_asset(
        f"{firms_slug}_chart_inputs_{last_handle.lstrip('$')}.json",
        chart_inputs,
    )

    return (
        f"PTECA complete. {len(chart_inputs)} chart(s):\n"
        + "\n".join(handles)
    )
```

Orchestrator sees:
```
PTECA complete. 2 chart(s):
$var_3: chart 1 (2 series)
$var_4: chart 2 (2 series)
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

### Max turns: 8

PTECA always asks the user at least once, and multi-stencil cases
may require multiple rounds of clarification. 8 turns accommodates
this. Typical flow is 2-3 turns.

After 8 turns without finalize, PTECA returns error:
```
"PTECA error: exceeded max turns without calling finalize."
```

### end_turn without finalize

If LLM returns `end_turn` instead of calling `finalize`, inject
error message and continue loop:

```python
if response.stop_reason == "end_turn":
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": "Error: you must call the finalize tool. Do not end without it.",
    })
    turn_counter += 1
    continue
```

### Invalid metric names

If finalize references a metric name not in the stencil,
`_build_chart_inputs` silently skips it. If ALL metrics in a chart
are invalid, the chart is dropped. If zero valid charts remain,
the error is sent back as a `tool_result` into the PTECA loop
(not returned from `run_pteca`), allowing the LLM to retry:
```
"Error: no valid charts produced. All metric/firm names were invalid. Check stencil data."
```

## Entry function

### run_pteca(stencils: list[dict], query: str, channel: ToolChannel | None = None) → list[dict]

| Param | Type | Description |
|---|---|---|
| `stencils` | `list[dict]` | List of stencil dicts from PMS1 (one per firm) |
| `query` | `str` | What the user wants charted |
| `channel` | `ToolChannel \| None` | Per-invocation channel. Falls back to `register("PTECA")` |

Returns list of chart_input dicts.

## Implementation sketch

```python
"""
tool_pteca.py — PTECA: internal agent loop for stencil → chart_input.
"""
from __future__ import annotations

import anthropic

from src.harness.terminal_router import register, ToolChannel, _router
from src.config import Config

_default_channel = register("PTECA")

PTECA_TOOLS = [
    {
        "name": "pteca_ask_user",
        "description": "Ask the user about chart layout preferences. Present options with ASCII chart sketches showing the approximate shape of the data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask, including ASCII chart previews and options."}
            },
            "required": ["question"],
        },
    },
    {
        "name": "finalize",
        "description": "Output final chart decisions after user confirms layout. Each chart groups metrics with compatible units.",
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
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "firm": {"type": "string"},
                                        "metric": {"type": "string"},
                                    },
                                    "required": ["firm", "metric"],
                                },
                            },
                            "periods": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Periods for x-axis, e.g. ['FY2022', 'FY2023']",
                            },
                        },
                        "required": ["title", "denomination_label", "metrics", "periods"],
                    },
                }
            },
            "required": ["charts"],
        },
    },
]

MAX_TURNS = 8


def run_pteca(
    stencils: list[dict],
    query: str,
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

    # Format input (multi-stencil)
    user_msg = _format_pteca_input(stencils, query)
    firms = [s["firm"] for s in stencils]
    total_rows = sum(len(s["rows"]) for s in stencils)
    ch.print(
        f"{len(firms)} firm(s), {total_rows} total rows: "
        + ", ".join(firms)
    )

    messages = [{"role": "user", "content": user_msg}]
    turn_counter = 0

    while turn_counter < MAX_TURNS:
        _router.start_spinner(ch.label)
        try:
            response = client.messages.create(
                model=model,
                system=PTECA_SYSTEM_PROMPT,
                messages=messages,
                tools=PTECA_TOOLS,
                max_tokens=max_tokens,
            )
        finally:
            _router.stop_spinner()

        # ── end_turn (error — must call finalize) ─────────
        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": "Error: you must call the finalize tool. Do not end without it.",
            })
            turn_counter += 1
            continue

        # ── no tool calls ─────────────────────────────────────
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_blocks:
            messages.append(
                {"role": "assistant", "content": response.content}
            )
            messages.append({
                "role": "user",
                "content": (
                    "Error: response contained no tool calls. "
                    "You must call the finalize tool."
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
                ch.print(question, markdown=True)
                answer = ch.input("")
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

                    # Empty charts list = user cancelled
                    if not charts_decisions:
                        finalize_result = []
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": "Cancelled. No charts produced.",
                        })
                    else:
                        chart_inputs = _build_chart_inputs(
                            stencils, charts_decisions
                        )
                        if not chart_inputs:
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tb.id,
                                "content": (
                                    "Error: no valid charts produced. "
                                    "All metric/firm names were invalid. "
                                    "Check stencil data."
                                ),
                            })
                        else:
                            finalize_result = chart_inputs
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tb.id,
                                "content": (
                                    f"Finalized. {len(chart_inputs)} "
                                    "chart(s) built."
                                ),
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
    raise RuntimeError("PTECA exceeded max turns without calling finalize.")


def _format_pteca_input(stencils: list[dict], query: str) -> str:
    """Format multiple stencils + pre-built option tables for PTECA."""
    lines = [f"Number of firms: {len(stencils)}", ""]
    for stencil in stencils:
        lines.append(f"### {stencil['firm']}")
        lines.append(f"Periods: {', '.join(stencil['periods'])}")
        lines.append("Rows:")
        for row in stencil["rows"]:
            vals = ", ".join(str(v) for v in row["values"])
            unit_info = ""
            if row.get("unit"):
                unit_info = f" [{row['unit']}"
                if row.get("denomination"):
                    unit_info += f", {row['denomination']}"
                unit_info += "]"
            lines.append(f"  - {row['metric']}{unit_info}: {vals}")
        lines.append("")
    lines.append(f"User query: {query}")
    lines.append("")
    return "\n".join(lines)


def _build_chart_inputs(
    stencils: list[dict], charts: list[dict]
) -> list[dict]:
    """Build chart_input dicts from finalize decisions + stencil data."""
    multi_firm = len(stencils) > 1

    # Index: {firm_name: {"rows": {metric: row}, "periods": [...]}}
    by_firm: dict[str, dict] = {}
    for s in stencils:
        firm = s["firm"]
        by_firm[firm] = {
            "rows": {row["metric"]: row for row in s["rows"]},
            "periods": s["periods"],
        }

    result = []
    for chart in charts:
        chart_periods = chart["periods"]
        series = []
        for metric_spec in chart["metrics"]:
            firm = metric_spec["firm"]
            metric_name = metric_spec["metric"]

            firm_data = by_firm.get(firm)
            if firm_data is None:
                continue
            row = firm_data["rows"].get(metric_name)
            if row is None:
                continue

            # Align values to chart_periods via index lookup
            firm_periods = firm_data["periods"]
            period_to_idx = {p: i for i, p in enumerate(firm_periods)}
            aligned_values = []
            for p in chart_periods:
                idx = period_to_idx.get(p)
                if idx is not None:
                    aligned_values.append(row["values"][idx])
                else:
                    aligned_values.append(0)  # pad missing

            # Legend name
            if multi_firm:
                legend = f"{firm} - {metric_name}"
            else:
                legend = metric_name

            series.append({
                "metric": legend,
                "values": aligned_values,
            })

        if not series:
            continue
        result.append({
            "title": chart["title"],
            "periods": chart_periods,
            "denomination_label": chart["denomination_label"],
            "series": series,
        })
    return result


PTECA_SYSTEM_PROMPT = """\
You are PTECA — a chart planning agent. You receive one or more
financial data stencils (each from a different company) and a user
query. Your job is to decide how to chart the data.

## What you receive

One or more stencils, each with:
- firm name
- periods (e.g. FY2020, FY2021, FY2022)
- rows of financial metrics, each with: metric name, values per
  period, unit (e.g. "%", "USD"), denomination (e.g. "bn", "mn")

And the user's query describing what they want charted.

## What you MUST do

1. Analyze the stencils. Note:
   - How many firms
   - Which metrics each firm has (highlight near-matches like
     "Net profit margin" vs "Net margin")
   - Period coverage per firm (highlight mismatches)
   - Unit compatibility (% vs USD — cannot share a Y-axis)

2. ALWAYS present options to the user using pteca_ask_user.
   Even for single-firm, single-metric cases — always ask.

   Present 2-3 layout options. For each option, show a data preview
   table of what each chart would contain. Use aligned columns with
   spaces (not pipe tables). Example:

   **OPTION A — Comparison by metric**

   Chart 1: "Gross Margin Comparison"
                    FY2020    FY2021
   Best Buy          23.0%     22.4%
   Boeing            -9.8%      4.8%

   Chart 2: "Net Margin Comparison"
   (same format with net margin values)

   **OPTION B — Per firm**
   (tables showing all metrics per firm)

   For multi-firm cases, typical options:
   A) Comparison by metric — both firms on same chart per metric
   B) Per-firm — all metrics per firm on separate charts
   C) Everything on one chart (only if units are compatible)

   If periods don't fully overlap across firms, state the mismatch
   and offer: intersect only, pad missing with 0, or leave gaps.

   End with: "Or describe your own layout."

3. Read the user's response. They may:
   - Pick an option (A/B/C) → finalize immediately, no reconfirm needed
   - Describe a custom layout → redraw ASCII preview, ask to confirm,
     iterate until user says yes
   - Adjust your suggestion → same as custom, redraw + confirm

4. Call finalize with the confirmed chart decisions.

## Finalize format

Each chart in finalize has:
- title: chart title string
- periods: explicit list of periods for the X-axis
- denomination_label: Y-axis label (e.g. "(%)", "(USD bn)")
- metrics: list of {firm, metric} objects — firm and metric names
  must match stencil data EXACTLY

For multi-firm charts, the legend will automatically show
"Firm - Metric" (e.g. "Best Buy - Gross margin"). For single-firm
charts, just the metric name is shown.

## Rules

- You MUST call pteca_ask_user at least once before finalize.
- You MUST call finalize to complete. Do not end without calling it.
- If the user says "cancel", "skip", "nevermind", or otherwise
  wants to abort charting, call finalize with an empty charts list.
  This exits cleanly — do not keep asking.
- Metric names must match stencil row names EXACTLY.
- Firm names must match stencil firm names EXACTLY.
- Do not invent metrics or firms not in the stencils.
- Use markdown pipe tables for data previews, not ASCII art.
- Do not call finalize in the same turn as pteca_ask_user.
- No emojis except 💦. No others. Ever."""
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
src/tools/tool_pteca.py
```

## Ideal demo

```
[PTECA-Best Buy] 1 firm(s), 5 total rows: Best Buy

                                 ← Turn 1: LLM reads stencil, query is
                                   "gross margins" — clear enough, but
                                   asks about net margin via pteca_ask_user

[PTECA-Best Buy] Include Net Profit Margin alongside Gross Margin?
> yes

                                 ← Turn 2: LLM calls finalize
                                   finalize({charts: [{
                                     title: "Best Buy Margins (FY2022-2023)",
                                     denomination_label: "(%)",
                                     metrics: [{firm:"Best Buy",metric:"Gross Margin"},
                                               {firm:"Best Buy",metric:"Net Profit Margin"}]
                                   }]})

[PTECA-Best Buy] Done. 1 chart(s).
```

### Multi-chart split demo

```
[PTECA-Amcor] 1 firm(s), 5 total rows: Amcor

                                 ← Turn 1: query is "chart everything"
                                   LLM calls pteca_ask_user with layout
                                   options (always asks, even if obvious)

[PTECA-Amcor] I'll split by unit compatibility:
              Option A: 2 charts — USD metrics + % metrics
              Option B: 1 chart per metric (5 charts)
              Or describe your own layout.
> A

                                 ← Turn 2: LLM calls finalize
                                   finalize({charts: [
                                     {title: "Amcor Revenue & Profit (FY2022-2023)",
                                      denomination_label: "(billions USD)",
                                      metrics: [{firm:"Amcor",metric:"Revenue"},
                                                {firm:"Amcor",metric:"Gross Profit"},
                                                {firm:"Amcor",metric:"Net Income"}]},
                                     {title: "Amcor Margins (FY2022-2023)",
                                      denomination_label: "(%)",
                                      metrics: [{firm:"Amcor",metric:"Gross Margin"},
                                                {firm:"Amcor",metric:"Net Profit Margin"}]}
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
- **Max turns.** 8. Always-ask behavior + multi-stencil means 2-3 typical.
- **end_turn without finalize.** Error injected back into messages,
  loop continues.
- **Invalid metric names.** Silently skipped. If zero valid charts,
  raises error.
