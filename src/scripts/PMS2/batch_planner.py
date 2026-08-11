"""Batch Planner agent loop — file routing + extraction dispatch.

M4: run_leng_caller live — calls real leng_caller.run_leng_caller().
override_firm_currency handler is live (mutates job stencil).
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel, _router
from src.harness.trace import (
    TraceBuffer,
    set_current_trace,
    clear_current_trace,
)
from src.scripts.config import Config
from src.scripts.llm import LLMResponse, get_pms2_batch_planner_llm

_MAX_TURNS = 4

# ── BP tool schemas (spec 17 § Batch Planner tools) ──────────────────

BATCH_PLANNER_TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "Ask user about routing preferences. "
            "e.g. 'D&A detail - prefer analyst model or 10K?' "
            "Use when unsure which files to search for specific cells. "
            "Cannot be called in the same turn as run_leng_caller."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "run_leng_caller",
        "description": (
            "Execute the extraction plan. Sends the full plan to "
            "LengCaller, which fetches chunks, fans out Lengs and "
            "Validators in parallel, and writes results directly "
            "to the job stencil. Returns 'complete' when done. "
            "TERMINAL TOOL - calling this ends the Batch Planner."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {
                                "type": "string",
                                "description": (
                                    "File path relative to data/files_ingested/."
                                ),
                            },
                            "cells": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Cell IDs to search for in this file."
                                ),
                            },
                        },
                        "required": ["file", "cells"],
                    },
                    "description": (
                        "File-centric extraction plan. Each entry = "
                        "one file + which cells to search for in it. "
                        "A cell can appear in multiple entries (different "
                        "files) - first validated write wins."
                    ),
                },
            },
            "required": ["plan"],
        },
    },
    {
        "name": "report_exhausted",
        "description": (
            "Declare further searching pointless for this firm. "
            "Call after ask_user confirms user wants to stop. "
            "TERMINAL TOOL - exits the Batch Planner. "
            "Dispatcher will stop looping for this firm."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Why further searching is pointless. "
                        "e.g. 'All files tried, remaining cells are "
                        "forward estimates not in any source.'"
                    ),
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "override_firm_currency",
        "description": (
            "Override reporting currency for ALL non-float rows of a "
            "firm (retrieve AND compute). Atomic: all or nothing. "
            "Use when searched_files shows systematic unit-mismatch "
            "rejections (e.g. all Innolight values found in USD but "
            "stencil expects CNY). Call AFTER ask_user confirms. "
            "Cannot be called in the same turn as run_leng_caller. "
            "NOT a terminal tool - call run_leng_caller after to "
            "re-extract with the corrected unit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "firm": {
                    "type": "string",
                    "description": "Firm whose rows to override.",
                },
                "new_unit": {
                    "type": "string",
                    "description": (
                        "New currency from units.json. e.g. 'USD'. "
                        "Cannot be 'float'."
                    ),
                },
            },
            "required": ["firm", "new_unit"],
        },
    },
]

TERMINAL_TOOLS = {"run_leng_caller", "report_exhausted"}
EXPLORATION_TOOLS = {"override_firm_currency"}


# ── override_firm_currency handler ───────────────────────────────────


def _handle_override_firm_currency(
    params: dict,
    job_stencil: dict,
    valid_units: list[str],
) -> str:
    """Override currency for ALL non-float rows of a firm. Atomic."""
    firm = params["firm"]
    new_unit = params["new_unit"]

    if new_unit not in valid_units:
        return f"Error: '{new_unit}' not in {valid_units}"
    if new_unit == "float":
        return "Error: cannot override currency to 'float'"

    targets = [
        (rn, row) for rn, row in job_stencil["rows"].items()
        if row["firm"] == firm
        and row["unit"] != "float"
    ]

    if not targets:
        return f"Error: no currency rows for {firm}"

    # Guard: reject if any non-float values already written
    for rn, row in targets:
        for col in job_stencil["col_letters"]:
            cell_id = f"{col}{rn}"
            if job_stencil["values"].get(cell_id) is not None:
                return (
                    f"Error: {cell_id} ({row['metric']}) already "
                    f"filled. Cannot override currency after partial "
                    f"extraction - values would be wrong."
                )

    old_unit = targets[0][1]["unit"]
    for rn, row in targets:
        row["unit"] = new_unit

    return (
        f"Overrode {len(targets)} rows for {firm}: "
        f"{old_unit} -> {new_unit}"
    )


# ── run_leng_caller handler ─────────────────────────────────────────


def _handle_run_leng_caller(
    params: dict,
    searched_files: dict,
    job_stencil: dict,
    stencil_lock: threading.Lock,
    fiscal_calendar: dict | None,
    docstore,
    file_path_index: dict,
    config: Config,
    channel: ToolChannel,
    firm: str,
    debug_dir: Path | None,
) -> tuple[str | None, str]:
    """Real run_leng_caller: delegates to leng_caller.run_leng_caller().

    Returns (result, status_string).
      result = "complete" on valid plan (terminal succeeds).
      result = None on validation error (LLM retries).
    """
    from src.scripts.PMS2.leng_caller import run_leng_caller

    plan = params.get("plan", [])
    if not plan:
        return None, "Error: plan is empty."

    result = run_leng_caller(
        plan=plan,
        job_stencil=job_stencil,
        stencil_lock=stencil_lock,
        searched_files=searched_files,
        fiscal_calendar=fiscal_calendar,
        docstore=docstore,
        file_path_index=file_path_index,
        config=config,
        channel=channel,
        firm=firm,
        debug_dir=debug_dir,
    )

    # Summarize for LLM
    total_filled = sum(
        1 for v in job_stencil["values"].values() if v is not None
    )
    total_cells = len(job_stencil["values"])
    status = (
        f"Leng complete: {total_filled}/{total_cells} cells filled."
    )
    return result, status


# ── Batch Planner agent loop ─────────────────────────────────────────


def run_batch_planner(
    firm: str,
    job_stencil: dict,
    active_cells: list[str],
    file_inventory: list[dict],
    searched_files: dict,
    iteration: int,
    stencil_lock: threading.Lock,
    fiscal_calendar: dict | None,
    channel: ToolChannel,
    config: Config,
    docstore,
    file_path_index: dict,
    debug_dir: Path | None = None,
) -> str:
    """Run Batch Planner agent loop for one Dispatcher iteration.

    Returns "complete" (plan executed) or "exhausted" (BP gave up).
    """
    valid_units = json.loads(
        (Path(__file__).parent / "hardcode_dependencies" / "units.json")
        .read_text()
    )

    # Build cell descriptions for BP context
    cell_descriptions = {}
    for cell_id in active_cells:
        row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        col = cell_id[: len(cell_id) - len(row_num)]
        row = job_stencil["rows"].get(row_num, {})
        col_idx = job_stencil["col_letters"].index(col) if col in job_stencil["col_letters"] else -1
        period = job_stencil["periods"][col_idx] if 0 <= col_idx < len(job_stencil["periods"]) else "?"
        cell_descriptions[cell_id] = (
            f"{row.get('metric', '?')} {period} "
            f"({row.get('type', '?')}, {row.get('unit', '?')})"
        )

    sys_prompt = load_sysprompt(
        "pms2_batch_planner",
        config.pms2_batch_planner_profile,
        firm=firm,
        job_stencil=json.dumps({
            "rows": job_stencil["rows"],
            "col_letters": job_stencil["col_letters"],
            "periods": job_stencil["periods"],
            "values": {k: v for k, v in job_stencil["values"].items() if k in active_cells or v is not None},
        }, indent=2, ensure_ascii=False),
        file_inventory=json.dumps(file_inventory, indent=2, ensure_ascii=False),
        searched_files=json.dumps(searched_files, indent=2, ensure_ascii=False),
        active_cells=json.dumps(cell_descriptions, indent=2, ensure_ascii=False),
        iteration=str(iteration),
    )

    backend = get_pms2_batch_planner_llm(config)

    trace = TraceBuffer(f"PMS2-bp-{firm}-#{iteration}")
    set_current_trace(trace)

    messages: list[dict] = [{"role": "user", "content": "Begin."}]
    turn_counter = 0

    try:
        while turn_counter < _MAX_TURNS:
            _router.start_spinner(f"PMS2-disp-{firm}")
            try:
                response = backend.call_with_tools(
                    messages=messages,
                    system_prompt=sys_prompt,
                    tools=BATCH_PLANNER_TOOLS,
                    label=f"PMS2-bp-{firm}-#{iteration}",
                )
            finally:
                _router.stop_spinner()

            # end_turn without tool call = error
            if response.stop_reason == "end_turn":
                messages.append(response.to_assistant_message())
                messages.append({
                    "role": "user",
                    "content": (
                        "Error: you must call run_leng_caller, "
                        "report_exhausted, ask_user, or "
                        "override_firm_currency. "
                        "Do not end your turn without calling a tool."
                    ),
                })
                turn_counter += 1
                continue

            # Classify tool calls for same-turn guards
            has_ask_user = any(
                t.name == "ask_user" for t in response.tool_calls
            )
            has_explore = any(
                t.name in EXPLORATION_TOOLS for t in response.tool_calls
            )
            terminal_count = sum(
                1 for t in response.tool_calls
                if t.name in TERMINAL_TOOLS
            )

            def _dispatch_tc(tc):
                if tc.name == "ask_user":
                    answer = channel.input(tc.input["question"], markdown=True)
                    trace.record_user_interaction(
                        tc.input["question"], answer
                    )
                    return tc, f"User answered: {answer}", None

                elif tc.name == "run_leng_caller":
                    if has_ask_user or has_explore:
                        return tc, (
                            "Error: cannot run_leng_caller in same turn "
                            "as ask_user or override_firm_currency."
                        ), None
                    if terminal_count > 1:
                        return tc, (
                            "Error: cannot call multiple terminal tools "
                            "in the same turn."
                        ), None

                    data, status = _handle_run_leng_caller(
                        tc.input, searched_files, job_stencil,
                        stencil_lock, fiscal_calendar,
                        docstore, file_path_index,
                        config, channel, firm, debug_dir,
                    )
                    return tc, status, data

                elif tc.name == "report_exhausted":
                    if has_ask_user or has_explore:
                        return tc, (
                            "Error: cannot report_exhausted in same turn "
                            "as ask_user or exploration tools."
                        ), None
                    if terminal_count > 1:
                        return tc, (
                            "Error: cannot call multiple terminal tools "
                            "in the same turn."
                        ), None
                    reason = tc.input.get("reason", "")
                    channel.print(f"exhausted: {reason}")
                    return tc, f"Exhausted: {reason}", "exhausted"

                elif tc.name == "override_firm_currency":
                    # Guard: must not fire in same turn as ask_user
                    if has_ask_user:
                        return tc, (
                            "Error: cannot override_firm_currency in "
                            "same turn as ask_user."
                        ), None
                    result = _handle_override_firm_currency(
                        tc.input, job_stencil, valid_units,
                    )
                    return tc, result, None

                return tc, f"Error: unknown tool '{tc.name}'.", None

            # Dispatch all tool calls
            tool_results = []
            terminal_result = None

            with ThreadPoolExecutor() as pool:
                futures = {
                    pool.submit(_dispatch_tc, tc): tc
                    for tc in response.tool_calls
                }
                for fut in as_completed(futures):
                    tc, content, t_result = fut.result()
                    tool_results.append({
                        "id": tc.id,
                        "name": tc.name,
                        "content": content,
                    })
                    if tc.name == "ask_user":
                        turn_counter = 0
                    if t_result is not None:
                        terminal_result = t_result

            messages.append(response.to_assistant_message())
            messages.append(
                LLMResponse.make_tool_results_message(tool_results)
            )

            if terminal_result is not None:
                return terminal_result

            turn_counter += 1

        # MAX_TURNS exhausted — treat as exhausted
        channel.print(
            f"iter {iteration}: "
            f"exhausted {_MAX_TURNS} turns without terminal tool."
        )
        return "exhausted"

    finally:
        flush_dir = debug_dir or Path("tests/debug")
        try:
            trace.flush_to_disk(flush_dir)
        except Exception:
            pass
        clear_current_trace()
