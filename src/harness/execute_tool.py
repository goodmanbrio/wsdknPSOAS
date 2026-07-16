"""
execute_tool.py — Dispatcher that maps tool names to Python functions.

Bridges the orchestrator LLM's tool calls to actual code. Handles
opaque handle resolution, registry storage, disk persistence, and
label injection.

Usage:
    from src.harness.execute_tool import execute_tool, set_session_dir
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

from src.harness.opaque_registry import registry
from src.harness.terminal_router import register


# ── Session state (set once per run_harness call) ─────────────────────
_session_dir: Path | None = None

# ── Module-level channels ─────────────────────────────────────────────
_orchestrator_channel = register("ORCHESTRATOR")

# ── S2C invocation counter (per-call labels: S2C-1, S2C-2) ───────────
_s2c_counter = 0
_s2c_lock = threading.Lock()


def _next_s2c_label() -> str:
    global _s2c_counter
    with _s2c_lock:
        _s2c_counter += 1
        return f"S2C-{_s2c_counter}"


def set_session_dir(path: Path) -> None:
    global _session_dir
    _session_dir = path


def reset_counters() -> None:
    """Reset per-session state. Called by run_harness."""
    global _s2c_counter
    with _s2c_lock:
        _s2c_counter = 0


# ── Handle resolution ─────────────────────────────────────────────────

def _resolve_handles(params: dict) -> dict:
    """Replace $var_N strings with actual data from registry."""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$var_"):
            resolved[key] = registry.resolve(value)
        else:
            resolved[key] = value
    return resolved


# ── Disk persistence ──────────────────────────────────────────────────

def _dump_asset(filename: str, data: Any) -> None:
    """Write data to session assets dir as JSON."""
    assets = _session_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    with open(assets / filename, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _sanitize(firm: str) -> str:
    """Best Buy → Best_Buy. Strips chars unsafe for filenames."""
    return re.sub(r"[^\w\-]", "_", firm)


# ── Branch handlers ───────────────────────────────────────────────────

def _exec_pms1(params: dict) -> str:
    from src.tools.tool_pms1 import run_pms1_pipeline

    firm = params["firm"]
    query = params["query"]
    channel = register(f"PMS1-{firm}")

    stencil = run_pms1_pipeline(firm, query, channel=channel)

    handle = registry.store(stencil, f"{firm} stencil")
    _dump_asset(
        f"{_sanitize(firm)}_stencil_{handle.lstrip('$')}.json", stencil
    )

    n_rows = len(stencil.get("rows", []))
    n_periods = len(stencil.get("periods", []))
    return (
        f"PMS1 complete. {firm} stencil stored as {handle}. "
        f"{n_rows} rows, {n_periods} periods."
    )


def _exec_pteca(params: dict) -> str:
    from src.tools.tool_pteca import run_pteca

    stencil = params["stencil"]
    query = params["query"]
    firm = params["firm"]
    channel = register(f"PTECA-{firm}")

    chart_inputs = run_pteca(stencil, query, firm, channel=channel)

    handles = []
    last_handle = ""
    for i, ci in enumerate(chart_inputs):
        n_series = len(ci.get("series", []))
        desc = f"{firm} chart {i + 1} ({n_series} series)"
        handle = registry.store(ci, desc)
        handles.append(f"{handle}: {desc}")
        last_handle = handle

    _dump_asset(
        f"{_sanitize(firm)}_chart_inputs_{last_handle.lstrip('$')}.json",
        chart_inputs,
    )

    return (
        f"PTECA complete. {len(chart_inputs)} chart(s):\n"
        + "\n".join(handles)
    )


def _exec_stencil2chart(params: dict) -> str:
    from src.stencil2chart import stencil2chart

    chart_input = params["chart_input"]
    channel = register(_next_s2c_label())

    output_dir = _session_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = stencil2chart(chart_input, output_dir, channel=channel)

    if output_path is None:
        return "stencil2chart cancelled by user (gap prompt)."
    return f"Saved: {output_path}"


def _exec_ask_user(params: dict) -> str:
    question = params["question"]
    answer = _orchestrator_channel.input(question)
    return f"User answered: {answer}"


def _exec_inspect_var(params: dict) -> str:
    mode = params["mode"]
    if mode == "list":
        return registry.list_vars()
    handle = params.get("handle", "")
    if not handle:
        return "Error: handle required for preview/full mode."
    try:
        if mode == "preview":
            return registry.preview(handle)
        elif mode == "full":
            return registry.dump(handle)
        else:
            return f"Error: unknown mode '{mode}'."
    except KeyError:
        return (
            f"Error: {handle} does not exist. "
            f"Available:\n{registry.list_vars()}"
        )


# ── Dispatch table ────────────────────────────────────────────────────

_dispatch: dict[str, Callable] = {
    "run_pms1":          _exec_pms1,
    "run_pteca":         _exec_pteca,
    "run_stencil2chart": _exec_stencil2chart,
    "ask_user":          _exec_ask_user,
    "inspect_var":       _exec_inspect_var,
}


# ── Public API ────────────────────────────────────────────────────────

def execute_tool(name: str, params: dict) -> str:
    handler = _dispatch.get(name)
    if handler is None:
        available = ", ".join(_dispatch.keys())
        return f"Error: unknown tool '{name}'. Available: {available}"

    # Resolve handles before dispatching.
    # Skip for inspect_var — it needs raw handle strings.
    if name == "inspect_var":
        return handler(params)

    resolved = _resolve_handles(params)
    return handler(resolved)
