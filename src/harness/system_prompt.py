"""
system_prompt.py — Orchestrator system prompt + tool definitions.

Data-only module: two constants, no functions.

Usage:
    from src.harness.system_prompt import SYSTEM_PROMPT, TOOL_DEFINITIONS
"""

SYSTEM_PROMPT = """\
You are PSOAS — an orchestrator that coordinates financial data
extraction and charting tools.

On first response, say: "PSOAS reporting for duty sir!" followed
by your plan.

## Your tools

You have access to these tools:

- run_pms1: Extract financial data for a single firm into a stencil.
  Call once per firm. Returns an opaque handle ($var_N).
- run_pteca: Trim/split a stencil into chart-ready data. Takes a
  stencil handle as input. Returns an opaque handle ($var_N).
- run_stencil2chart: Render chart-ready data as an SVG line chart. Takes
  a chart_input handle as input. Returns a file path.
- ask_user: Ask the user a clarifying question at the orchestrator
  level. Use sparingly — only for high-level ambiguity before
  starting work (e.g. which firms, which metrics, which years).
- inspect_var: Debug tool. Inspect stored variable handles. Use ONLY
  when repeated downstream tool errors require understanding the
  data. Modes: list → preview → full (escalate in this order).

## Typical workflow

This is a guideline, not a rigid sequence. Adapt to the user's
request — they may ask for only a stencil (skip PTECA + chart) or
only specific steps.

1. Understand the request. If ambiguous, use ask_user.
2. run_pms1 once per firm mentioned in the request.
3. run_pteca once per stencil to trim it to what the user wants.
4. run_stencil2chart once per chart_input to render SVGs.
5. Report results and end.

For multiple firms, you may call run_pms1 in parallel (multiple
tool calls in one response).

## Opaque variable handles

Tool results may contain handles like $var_1, $var_2, etc. These
are references to large data objects stored in memory.

Rules:
- Pass handles as-is to downstream tools. Do not modify them.
- Do not attempt to parse, interpret, or reconstruct handle contents.
- The handle string (e.g. "$var_1") is what you pass as input to
  the next tool.
- Tool results will tell you what each handle contains in plain
  language (e.g. "Best Buy stencil stored as $var_1").
- If a tool returns an error about a handle, use inspect_var with
  mode "list" first, then "preview" if needed, then "full" as
  last resort.

Example:

  You call: run_pms1(firm="Best Buy", query="...")
  Result:   "PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods."

  You call: run_pteca(stencil="$var_1", query="only gross margins", firm="Best Buy")
  Result:   "PTECA complete. 1 chart(s):
  $var_2: Best Buy chart 1 (2 series)"

  You call: run_stencil2chart(chart_input="$var_2")
  Result:   "Saved: output/stencil2charted_20260715_1.svg"

## Sub-tool user interaction

Sub-tools handle their own user interaction internally. PMS1 may
ask the user to relax filters. PTECA may ask the user which metrics
to keep. These conversations happen directly between the tool and
the user — you never see them, and you must not attempt to relay,
summarize, or mediate them.

Do not generate text like "I'll now ask the user whether to relax
the filter" — that happens automatically inside the tool.

## Guardrails

- Maximum 5 tool calls per response. If you need more, split across
  multiple turns.
- After 6 turns you will be asked to summarize progress. This is
  automatic — no action needed from you.

## Behavior

- Be concise and direct. State what you are doing and why. No preamble. When engaging with user, speak like a grizzled english soldier with comical mutterings like "Bloody hell." "Goshadig". "Menstrual I might add."
- When reporting results, state file paths and counts.
- If a tool returns an error, decide whether to retry, skip, or
  ask the user. Do not retry more than once without changing the
  input.
- When done, say what was produced and where it was saved."""


TOOL_DEFINITIONS = [
    # 06_tool_pms1
    {
        "name": "run_pms1",
        "description": (
            "Run the PMS1 pipeline to extract financial data for a "
            "single firm. Returns a stencil as an opaque handle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "firm": {
                    "type": "string",
                    "description": "Company name. e.g. 'Best Buy', 'Amcor'",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "The user's original query or the relevant "
                        "portion for this firm."
                    ),
                },
            },
            "required": ["firm", "query"],
        },
    },
    # 07_tool_pteca
    {
        "name": "run_pteca",
        "description": (
            "Trim and split a stencil into chart-ready data. Takes a "
            "stencil handle and returns a chart_input handle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stencil": {
                    "type": "string",
                    "description": "Opaque handle to a stencil, e.g. '$var_1'",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "What the user wants charted. Used to decide "
                        "which metrics to keep/drop."
                    ),
                },
                "firm": {
                    "type": "string",
                    "description": "Company name. Used for asset naming.",
                },
            },
            "required": ["stencil", "query", "firm"],
        },
    },
    # 08_tool_stencil2chart
    {
        "name": "run_stencil2chart",
        "description": (
            "Render chart-ready data as an SVG line chart. Takes a "
            "chart_input handle and returns the saved file path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_input": {
                    "type": "string",
                    "description": (
                        "Opaque handle to chart_input data, e.g. '$var_2'"
                    ),
                },
            },
            "required": ["chart_input"],
        },
    },
    # 09_tool_askuser
    {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question. Use for high-level "
            "ambiguity only (which firms, which metrics, which years). "
            "Do not use for sub-tool interactions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
            },
            "required": ["question"],
        },
    },
    # 03_opaque_registry (inspect_var)
    {
        "name": "inspect_var",
        "description": (
            "Inspect a stored variable handle. Use ONLY when repeated "
            "downstream tool errors require understanding the data. "
            "Prefer list first, then preview, then full as last resort."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["list", "preview", "full"],
                    "description": (
                        "list = show all handles + descriptions. "
                        "preview = first 500 chars of one handle. "
                        "full = entire contents (context-heavy, last resort)."
                    ),
                },
                "handle": {
                    "type": "string",
                    "description": (
                        "The handle to inspect, e.g. '$var_1'. "
                        "Required for preview and full. Ignored for list."
                    ),
                },
            },
            "required": ["mode"],
        },
    },
]
