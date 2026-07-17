"""
tool_pteca.py — PTECA: Poony Table Et Chart Agent.

Internal agent loop that takes one or more stencil dicts + user query,
presents charting options with ASCII previews, iterates with the user,
and outputs chart_input dicts for stencil2chart. Has its own while
loop, messages, LLM calls, and tools (pteca_ask_user + finalize).
The orchestrator never sees PTECA's internal conversation.

Usage:
    from src.tools.tool_pteca import run_pteca
    chart_inputs = run_pteca([stencil_bby, stencil_ba], "compare margins", channel=ch)
"""

from __future__ import annotations

from src.harness.terminal_router import register, ToolChannel, _router
from src.config import Config
from src.llm import get_pteca_llm, LLMResponse, ToolCall

_default_channel = register("PTECA")

PTECA_TOOLS = [
    {
        "name": "pteca_ask_user",
        "description": (
            "Ask the user about chart layout preferences. Present "
            "options with ASCII chart sketches showing the approximate "
            "shape of the data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The question to ask, including ASCII chart "
                        "previews and options."
                    ),
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "finalize",
        "description": (
            "Output final chart decisions after user confirms layout. "
            "Each chart groups metrics with compatible units."
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
                            "periods": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Explicit period list for this "
                                    "chart's X-axis."
                                ),
                            },
                            "denomination_label": {"type": "string"},
                            "metrics": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "firm": {
                                            "type": "string",
                                            "description": (
                                                "Firm name — must match "
                                                "stencil firm exactly."
                                            ),
                                        },
                                        "metric": {
                                            "type": "string",
                                            "description": (
                                                "Metric name — must match "
                                                "stencil row name exactly."
                                            ),
                                        },
                                    },
                                    "required": ["firm", "metric"],
                                },
                            },
                        },
                        "required": [
                            "title",
                            "periods",
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

MAX_TURNS = 8


def run_pteca(
    stencils: list[dict],
    query: str,
    channel: ToolChannel | None = None,
    config: Config | None = None,
) -> list[dict]:
    """Run PTECA agent loop. Returns list of chart_input dicts."""
    ch = channel or _default_channel

    config = config or Config.from_env()
    backend = get_pteca_llm(config)

    # Format input
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
            response = backend.call_with_tools(
                messages=messages,
                system_prompt=PTECA_SYSTEM_PROMPT,
                tools=PTECA_TOOLS,
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

    # Exceeded max turns
    ch.print("Error: exceeded max turns without finalize.")
    raise RuntimeError("PTECA exceeded max turns without calling finalize.")


def _format_pteca_input(stencils: list[dict], query: str) -> str:
    """Format multiple stencils + pre-built option tables for PTECA."""
    lines = [f"Number of firms: {len(stencils)}", ""]

    # Raw stencil data
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

            # Align values to chart_periods
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
