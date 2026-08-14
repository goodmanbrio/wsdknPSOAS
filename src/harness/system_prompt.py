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
    # research (open-ended QA)
    {
        "name": "run_research",
        "description": (
            "Answer an open-ended qualitative question by searching ingested "
            "documents. Decomposes the question into sub-questions, retrieves "
            "relevant chunks via BM25 keyword search, and synthesizes a cited "
            "answer. Use for questions like 'What is LITE's competitive "
            "outlook?', 'Summarize the bull case for Coherent', or 'How does "
            "Furukawa Electric view the optical fiber market?'. Do NOT use "
            "for tabular data extraction — use run_pms2 for metrics like "
            "revenue, EPS, margins with specific periods. For a follow-up, "
            "you must first call read_turn_memory with turn_id='latest'. "
            "Then inspect any saved research artifacts named in that turn's "
            "tool activity using read_session_md. Decide whether the answer "
            "or those existing artifacts already contain the requested detail. "
            "If they do, answer from them without calling run_research. Only "
            "if the existing artifacts are insufficient should you call "
            "run_research, and then only for a self-contained, targeted "
            "missing-information question; do not rerun the whole prior broad "
            "query. Separate independent missing items into parallel calls "
            "when useful."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The open-ended question to research. Pass the user's "
                        "query as-is for a standalone request. For a follow-up, "
                        "pass a self-contained rewritten question using the "
                        "active turn memory. The research pipeline handles "
                        "decomposition internally."
                    ),
                },
                "missing_information": {
                    "type": "string",
                    "description": (
                        "For a follow-up, briefly state the specific evidence "
                        "gap identified after reading the latest turn memory. "
                        "Omit this for a standalone first-turn request."
                    ),
                },
            },
            "required": ["question"],
        },
    },
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
            "file content as a string. On a follow-up, use this to inspect "
            "saved research_*.md or source-summary artifacts named in the "
            "latest turn memory before deciding whether new research is "
            "necessary."
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
    {
        "name": "read_turn_memory",
        "description": (
            "Read the structured memory page for a completed conversation turn. "
            "Use turn_id='latest' for the most recent turn, or a numeric id such "
            "as '1'. On a follow-up, call this before run_research so the "
            "orchestrator can identify what information is actually missing, "
            "then inspect any saved research artifacts named in its tool "
            "activity with read_session_md. Cited original documents remain "
            "the evidence source."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "turn_id": {
                    "type": "string",
                    "description": "'latest' or a turn number such as '1'.",
                },
            },
            "required": ["turn_id"],
        },
    },
]
