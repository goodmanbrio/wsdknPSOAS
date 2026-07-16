"""
tool_pteca.py — PTECA: Poony Table Et Chart Agent.

Internal agent loop that takes a stencil dict + user query, decides
which metrics to keep, groups by unit, and outputs chart_input dicts
for stencil2chart. Has its own while loop, messages, LLM calls, and
tools (pteca_ask_user + finalize). The orchestrator never sees PTECA's
internal conversation.

Usage:
    from src.tool_pteca import run_pteca
    chart_inputs = run_pteca(stencil_dict, "gross margins", "Best Buy", channel=ch)
"""

from __future__ import annotations

import anthropic

from src.harness.terminal_router import register, ToolChannel, _router
from src.config import Config

_default_channel = register("PTECA")

PTECA_TOOLS = [
    {
        "name": "pteca_ask_user",
        "description": (
            "Ask the user a clarifying question about what they want charted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "finalize",
        "description": (
            "Output your final chart decisions. Each chart groups "
            "metrics with the same unit."
        ),
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
                        "required": [
                            "title",
                            "denomination_label",
                            "metrics",
                        ],
                    },
                },
            },
            "required": ["charts"],
        },
    },
]

PTECA_SYSTEM_PROMPT = """\
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
- If the user's intent is clear, skip pteca_ask_user and finalize directly."""

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
    ch.print(
        f"Stencil has {len(stencil['rows'])} rows: "
        + ", ".join(r["metric"] for r in stencil["rows"])
        + "."
    )

    messages = [{"role": "user", "content": user_msg}]
    turn_counter = 0

    while turn_counter < MAX_TURNS:
        _router.start_spinner(f"PTECA-{firm}")
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

        # ── end_turn (error — must call finalize) ─────────────
        if response.stop_reason == "end_turn":
            messages.append(
                {"role": "assistant", "content": response.content}
            )
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

        # ── tool_use ──────────────────────────────────────────
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
                # Reject if pteca_ask_user was also called this turn
                asked_user = any(
                    b.name == "pteca_ask_user" for b in tool_blocks
                )
                if asked_user:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": (
                            "Error: cannot finalize in the same turn as "
                            "pteca_ask_user. See the user's answer first, "
                            "then call finalize."
                        ),
                    })
                else:
                    charts_decisions = tb.input["charts"]
                    chart_inputs = _build_chart_inputs(
                        stencil, charts_decisions
                    )
                    if not chart_inputs:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": (
                                "Error: no valid charts produced. All "
                                "metric names were invalid. Check "
                                "stencil row names."
                            ),
                        })
                    else:
                        finalize_result = chart_inputs
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": (
                                f"Finalized. {len(chart_inputs)} chart(s) "
                                "built."
                            ),
                        })

            else:
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


def _build_chart_inputs(
    stencil: dict, charts: list[dict]
) -> list[dict]:
    """Build chart_input dicts from finalize decisions + stencil data.
    Values looked up mechanically — never LLM-generated."""
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
