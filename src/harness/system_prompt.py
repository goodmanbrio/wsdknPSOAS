"""
system_prompt.py — Orchestrator tool definitions.

The system prompt text is now externalized to sysprompts/orchestrator/.
This module only contains TOOL_DEFINITIONS (JSON schemas for API tools).

Usage:
    from src.harness.system_prompt import TOOL_DEFINITIONS
"""

TOOL_DEFINITIONS = [
    # PMS2
    {
        "name": "run_pms2",
        "description": (
            "Extract financial data for one or more firms into stencils. "
            "Handles stencil creation, user confirmation, file discovery, "
            "batch planning, parallel extraction via chunk-level Leng "
            "workers, and validation. Returns stencil(s) as opaque handles."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "firms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Firm names/tickers. e.g. ['LITE', 'Innolight']"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "What to extract. e.g. "
                        "'Revenue, Laser Rev, OpProfit, EPS, MktCap, P/Rev, P/E'"
                    ),
                },
                "periods": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Fiscal periods. e.g. ['FY2025', 'FY2026', 'FY2027']"
                    ),
                },
                "granularity": {
                    "type": "string",
                    "enum": ["annual", "quarterly", "half"],
                    "description": (
                        "Period granularity. annual=full FY, "
                        "quarterly=per quarter, half=per half."
                    ),
                },
            },
            "required": ["firms", "query", "periods", "granularity"],
        },
    },
    # 06_tool_pms1 — COMMENTED OUT: PMS2 replaces PMS1. Code stays in tree.
    # {
    #     "name": "run_pms1",
    #     "description": (
    #         "Run the PMS1 pipeline to extract financial data for a "
    #         "single firm. Returns a stencil as an opaque handle."
    #     ),
    #     "input_schema": {
    #         "type": "object",
    #         "properties": {
    #             "firm": {
    #                 "type": "string",
    #                 "description": "Company name. e.g. 'Best Buy', 'Amcor'",
    #             },
    #             "query": {
    #                 "type": "string",
    #                 "description": (
    #                     "The user's original query or the relevant "
    #                     "portion for this firm."
    #                 ),
    #             },
    #         },
    #         "required": ["firm", "query"],
    #     },
    # },
    # 07_tool_pteca
    {
        "name": "run_pteca",
        "description": (
            "Plan charts from one or more stencils. Pass ALL stencil "
            "handles in one call. PTECA interacts with the user to "
            "decide chart layout — do not pre-decide yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stencils": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "List of stencil handles, e.g. "
                        "['$var_1', '$var_2']. One per firm."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "What the user wants charted. Passed to PTECA "
                        "for context."
                    ),
                },
            },
            "required": ["stencils", "query"],
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
    # session file I/O
    {
        "name": "write_session_md",
        "description": (
            "Write a markdown file to the session directory. Use "
            "{{embed:$var_N}} in content to inline a variable's "
            "full JSON data as a code block."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Filename relative to session directory, "
                        "e.g. 'report.md' or 'notes/analysis.md'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Markdown content. Use {{embed:$var_N}} to "
                        "inline a variable's data as a JSON block."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["write", "append"],
                    "description": (
                        "write = create or overwrite (default). "
                        "append = add to existing file."
                    ),
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "read_session_md",
        "description": (
            "Read a file from the session directory. Returns the "
            "file content as a string."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Filename relative to session directory."
                    ),
                },
            },
            "required": ["filename"],
        },
    },
]
