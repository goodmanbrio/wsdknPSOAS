"""Mapper: per-firm file inventory builder.

T0 scope: _walk_dirs only.
M2: full agent loop (list_dir + ask_user + report_dirs).
"""

import json
import os
from pathlib import Path

from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel, _router
from src.harness.trace import TraceBuffer, set_current_trace, clear_current_trace
from src.scripts.config import Config
from src.scripts.llm import LLMResponse, get_pms2_mapper_llm

_MAX_TURNS = 6

# ── Mapper tool schemas (spec 17 § Mapper tools) ─────────────────────

MAPPER_TOOLS = [
    {
        "name": "list_dir",
        "description": "List files and subdirectories at a path under data/files_ingested/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under data/files_ingested/. Use '' for root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask user to disambiguate directory selection. "
            "e.g. 'LITE could be LITE/ or Lumentum/. Which?'"
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
        "name": "report_dirs",
        "description": (
            "Report final directory selections for this firm. "
            "Call when confident about which directories to walk."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dirs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Directory paths relative to data/files_ingested/. "
                        "e.g. ['LITE/', '0 Optical/']"
                    ),
                }
            },
            "required": ["dirs"],
        },
    },
]

TERMINAL_TOOLS = {"report_dirs"}
EXPLORATION_TOOLS = {"list_dir"}


# ── list_dir handler ─────────────────────────────────────────────────

def _handle_list_dir(rel_path: str, data_dir: Path) -> str:
    """List contents of a directory under data_dir. Returns newline-separated listing."""
    target = data_dir / rel_path
    if not target.exists():
        return f"Error: path '{rel_path}' does not exist."
    if not target.is_dir():
        return f"Error: '{rel_path}' is not a directory."

    entries = []
    for name in sorted(os.listdir(target)):
        if name.startswith("."):
            continue
        full = target / name
        if full.is_dir():
            entries.append(f"{name}/")
        elif not name.endswith(".json"):
            entries.append(name)
    if not entries:
        return "(empty directory)"
    return "\n".join(entries)


# ── report_dirs handler ──────────────────────────────────────────────

def _handle_report_dirs(
    dirs: list[str], data_dir: Path, manifest: dict, firm: str,
) -> tuple[list[dict] | None, str]:
    """Validate dirs, walk, return (file_inventory, status).

    Returns (inventory, status_string).
      inventory = list of file dicts on success, None on error.
    """
    if not dirs:
        return None, "Error: dirs list is empty."

    # Validate all dirs exist
    for d in dirs:
        target = data_dir / d
        if not target.exists():
            return None, f"Error: directory '{d}' does not exist under data/files_ingested/."
        if not target.is_dir():
            return None, f"Error: '{d}' is not a directory."

    inventory = _walk_dirs(dirs, data_dir, manifest)
    return inventory, (
        f"Mapper complete for {firm}: {len(inventory)} files catalogued "
        f"from {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}."
    )


# ── Per-firm mapper agent loop ───────────────────────────────────────

def run_mapper_for_firm(
    firm: str,
    query: str,
    notes: str,
    config: Config,
    data_dir: Path,
    manifest: dict,
    channel: ToolChannel,
    debug_dir: Path | None = None,
) -> list[dict]:
    """Run mapper agent loop for a single firm.

    Returns file inventory (list of {path, filetype, filesize} dicts).
    Raises RuntimeError if MAX_TURNS exhausted without report_dirs.
    """
    sys_prompt = load_sysprompt(
        "pms2_mapper",
        config.pms2_mapper_profile,
        firm=firm,
        query=query,
        notes=notes or "No specific instructions.",
    )

    backend = get_pms2_mapper_llm(config)

    trace = TraceBuffer(f"PMS2-map-{firm}")
    set_current_trace(trace)

    messages: list[dict] = [{"role": "user", "content": "Begin."}]
    turn_counter = 0

    try:
        while turn_counter < _MAX_TURNS:
            _router.start_spinner(f"PMS2-map-{firm}")
            try:
                response = backend.call_with_tools(
                    messages=messages,
                    system_prompt=sys_prompt,
                    tools=MAPPER_TOOLS,
                    label=f"PMS2-map-{firm}",
                )
            finally:
                _router.stop_spinner()

            # end_turn without tool call = error
            if response.stop_reason == "end_turn":
                messages.append(response.to_assistant_message())
                messages.append({
                    "role": "user",
                    "content": (
                        "Error: you must call list_dir, ask_user, or "
                        "report_dirs. Do not end your turn without "
                        "calling a tool."
                    ),
                })
                turn_counter += 1
                continue

            # Classify tool calls for same-turn guards
            has_ask_user = any(
                t.name == "ask_user" for t in response.tool_calls
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

                elif tc.name == "list_dir":
                    # Guard: must not fire in same turn as ask_user
                    if has_ask_user:
                        return tc, (
                            "Error: cannot run list_dir in same turn "
                            "as ask_user. Call list_dir in a separate "
                            "turn after receiving the user's answer."
                        ), None
                    result = _handle_list_dir(tc.input["path"], data_dir)
                    return tc, result, None

                elif tc.name == "report_dirs":
                    # Guard: reject if ask_user or exploration also called
                    if has_ask_user:
                        return tc, (
                            "Error: cannot report_dirs in same turn as "
                            "ask_user. Call report_dirs in a separate turn."
                        ), None
                    if terminal_count > 1:
                        return tc, (
                            "Error: cannot call multiple terminal tools "
                            "in the same turn."
                        ), None
                    inventory, status = _handle_report_dirs(
                        tc.input["dirs"], data_dir, manifest, firm,
                    )
                    return tc, status, inventory

                return tc, f"Error: unknown tool '{tc.name}'.", None

            # Dispatch tool calls
            from concurrent.futures import ThreadPoolExecutor, as_completed

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
                channel.print(
                    f"{len(terminal_result)} files found."
                )
                return terminal_result

            turn_counter += 1

        raise RuntimeError(
            f"Mapper agent loop for {firm} exhausted {_MAX_TURNS} turns "
            f"without reporting directories."
        )

    finally:
        flush_dir = debug_dir or Path("tests/debug")
        try:
            trace.flush_to_disk(flush_dir)
        except Exception:
            pass
        clear_current_trace()


# ── _walk_dirs (T0, unchanged) ───────────────────────────────────────

def _walk_dirs(
    dirs: list[str], data_dir: Path, manifest: dict,
) -> list[dict]:
    """Walk directories, collect file inventory with filetype."""
    files = []
    for d in dirs:
        target = data_dir / d
        for root, _, filenames in os.walk(target):
            for fname in filenames:
                if fname.startswith(".") or fname.endswith(".json"):
                    continue
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(data_dir))
                filetype = manifest.get(rel, "unknown")
                files.append({
                    "path": rel,
                    "filetype": filetype,
                    "filesize": fpath.stat().st_size,
                })
    return files
