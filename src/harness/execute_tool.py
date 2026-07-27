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
from src.harness.terminal_router import register, console
from src.harness.trace import get_current_trace, clear_current_trace
from src.config import Config


# ── Session state (set once per run_harness call) ─────────────────────
_session_dir: Path | None = None
_debug_dir: Path | None = None

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


# ── Config state (set once per run_harness call) ─────────────────────
_config: Config | None = None


def set_config(config: Config) -> None:
    """Store active config. Called by run_harness()."""
    global _config
    _config = config


def set_session_dir(path: Path) -> None:
    global _session_dir
    _session_dir = path


def set_debug_dir(path: Path) -> None:
    global _debug_dir
    _debug_dir = path


def reset_counters() -> None:
    """Reset per-session state. Called by run_harness."""
    global _s2c_counter, _config, _debug_dir
    with _s2c_lock:
        _s2c_counter = 0
    _config = None
    _debug_dir = None


# ── Handle resolution ─────────────────────────────────────────────────

def _resolve_handles(params: dict) -> dict:
    """Replace $var_N strings with actual data from registry.
    Handles both top-level strings and lists of strings."""
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$var_"):
            resolved[key] = registry.resolve(value)
        elif isinstance(value, list):
            resolved[key] = [
                registry.resolve(item)
                if isinstance(item, str) and item.startswith("$var_")
                else item
                for item in value
            ]
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

    stencil = run_pms1_pipeline(firm, query, channel=channel, debug_dir=_debug_dir, config=_config)

    handle = registry.store(stencil, f"{firm} stencil")
    _dump_asset(
        f"{handle.lstrip('$')}_{_sanitize(firm)}_stencil.json", stencil
    )

    n_rows = len(stencil.get("rows", []))
    n_periods = len(stencil.get("periods", []))
    return (
        f"PMS1 complete. {firm} stencil stored as {handle}. "
        f"{n_rows} rows, {n_periods} periods."
    )


def _exec_pteca(params: dict) -> str:
    from src.tools.tool_pteca import run_pteca

    stencils = params["stencils"]
    query = params["query"]
    firms = [s["firm"] for s in stencils]
    label = "PTECA-" + "+".join(firms)
    channel = register(label)

    chart_inputs = run_pteca(stencils, query, channel=channel, config=_config, debug_dir=_debug_dir)

    if not chart_inputs:
        return "PTECA cancelled by user. No charts to render. Move on."

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
    answer = _orchestrator_channel.input(question, markdown=True)
    return f"User answered: {answer}"


def _exec_write_session_md(params: dict) -> str:
    if _session_dir is None:
        return "Error: no active session."

    filename = params["filename"]
    content = params["content"]
    mode = params.get("mode", "write")

    # Path traversal safety
    target = (_session_dir / filename).resolve()
    if not target.is_relative_to(_session_dir.resolve()):
        return f"Error: path '{filename}' escapes session directory."

    # Resolve {{embed:$var_N}} markers
    def _embed_replacer(match: re.Match) -> str:
        handle = match.group(1)
        try:
            data = registry.resolve(handle)
            desc = registry._registry[handle]["description"]
            json_str = json.dumps(
                data, indent=2, ensure_ascii=False, default=str
            )
            return f"**{handle}** ({desc}):\n```json\n{json_str}\n```"
        except KeyError:
            return f"[Error: {handle} not found]"

    resolved_content = re.sub(
        r"\{\{embed:(\$var_\d+)\}\}", _embed_replacer, content
    )

    # Write or append
    target.parent.mkdir(parents=True, exist_ok=True)
    open_mode = "a" if mode == "append" else "w"
    with open(target, open_mode) as f:
        f.write(resolved_content)

    return f"Written: {_session_dir / filename} (mode={mode})"


def _exec_read_session_md(params: dict) -> str:
    if _session_dir is None:
        return "Error: no active session."

    filename = params["filename"]

    # Path traversal safety
    target = (_session_dir / filename).resolve()
    if not target.is_relative_to(_session_dir.resolve()):
        return f"Error: path '{filename}' escapes session directory."

    if not target.exists():
        return f"Error: '{filename}' does not exist in session directory."

    return target.read_text()


def _exec_pms2(params: dict) -> str:
    from src.scripts.PMS2.pms2 import run_pms2_pipeline

    if _session_dir is None:
        return "Error: no active session (run_pms2 called outside harness)."

    firms = params["firms"]
    query = params["query"]
    periods = params["periods"]
    granularity = params["granularity"]
    channel = register("PMS2")

    display_stencils = run_pms2_pipeline(
        firms=firms, query=query, periods=periods,
        granularity=granularity,
        session_dir=_session_dir,
        channel=channel, config=_config, debug_dir=_debug_dir,
    )

    handles = []
    for stencil in display_stencils:
        firm = stencil["firm"]
        handle = registry.store(stencil, f"{firm} stencil")
        _dump_asset(
            f"{handle.lstrip('$')}_{_sanitize(firm)}_stencil.json",
            stencil,
        )
        handles.append((firm, handle))

    return (
        f"PMS2 complete. {len(handles)} firms extracted. "
        f"Handles: {', '.join(f'{f}={h}' for f, h in handles)}."
    )


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
    # "run_pms1":          _exec_pms1,  # PMS2 replaces PMS1. Code stays in tree.
    "run_pms2":          _exec_pms2,
    "run_pteca":         _exec_pteca,
    "run_stencil2chart": _exec_stencil2chart,
    "ask_user":          _exec_ask_user,
    "inspect_var":       _exec_inspect_var,
    "write_session_md":  _exec_write_session_md,
    "read_session_md":   _exec_read_session_md,
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
    try:
        return handler(resolved)
    except Exception as e:
        # Flush whatever the trace captured before the crash
        trace = get_current_trace()
        if trace and _debug_dir:
            try:
                filepath = trace.flush_to_disk(_debug_dir)
                console.print(
                    f"[bold red]\\[BUMMER][/bold red] "
                    f"{name} failure logged → {filepath}"
                )
            except OSError:
                pass
        clear_current_trace()
        return f"Error: {e}"
