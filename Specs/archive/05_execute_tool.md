# Execute Tool

Dispatcher that maps tool names to Python functions. Bridges the
orchestrator LLM's tool calls to actual code. Handles opaque handle
resolution, registry storage, disk persistence, and label injection.

## Architecture

```
agent_loop while loop
    │
    │  LLM returns ToolUseBlock:
    │    name="run_pms1"
    │    input={"firm": "Best Buy", "query": "margins FY2022-2023"}
    │
    ▼
execute_tool("run_pms1", {"firm": "Best Buy", "query": "..."})
    │
    │  ── PHASE 1: resolve handles ──────────────────────────
    │  Scan all string values in params for "$var_" prefix.
    │  Replace with real data from registry.
    │
    │  (no handles in this call — skip)
    │
    │  ── PHASE 2: derive label + create channel ────────────
    │  label = f"PMS1-{params['firm']}"  → "PMS1-Best Buy"
    │  channel = register("PMS1-Best Buy")
    │
    │  ── PHASE 3: call tool code ───────────────────────────
    │  result = run_pms1_pipeline(firm, query, channel=channel)
    │  returns: stencil dict (big)
    │
    │  ── PHASE 4: store in registry ────────────────────────
    │  handle = registry.store(result, "Best Buy stencil")
    │  returns: "$var_1"
    │
    │  ── PHASE 5: persist to disk ──────────────────────────
    │  path = session_dir / "assets" / "var_1_Best_Buy_stencil.json"
    │  json.dump(result, path)
    │
    │  ── PHASE 6: compose result string ────────────────────
    │  return "PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods."
    │
    ▼
agent_loop puts this string into tool_result message
```

### Full dispatch flow (multi-tool turn)

```
LLM response contains 2 ToolUseBlocks:
    [run_pms1(firm="Best Buy", ...), run_pms1(firm="Amcor", ...)]
              │                                │
              ▼                                ▼
    ThreadPoolExecutor                ThreadPoolExecutor
    thread 1                          thread 2
              │                                │
    execute_tool("run_pms1",          execute_tool("run_pms1",
      {"firm":"Best Buy",...})          {"firm":"Amcor",...})
              │                                │
    channel = register(               channel = register(
      "PMS1-Best Buy")                  "PMS1-Amcor")
              │                                │
    run_pms1_pipeline(                run_pms1_pipeline(
      ..., channel=channel)             ..., channel=channel)
              │                                │
    [PMS1-Best Buy] Loading...        [PMS1-Amcor] Loading...
    [PMS1-Best Buy] Sekei done.       [PMS1-Amcor] Sekei done.
              │                                │
    registry.store(stencil,           registry.store(stencil,
      "Best Buy stencil")              "Amcor stencil")
    → "$var_1"                        → "$var_2"
              │                                │
    dump var_1_Best_Buy_stencil.json  dump var_2_Amcor_stencil.json
              │                                │
              ▼                                ▼
    "PMS1 complete. Best Buy          "PMS1 complete. Amcor
     stencil stored as $var_1."        stencil stored as $var_2."
```

### Connection to other components

```
execute_tool.py
    │
    ├── imports from: opaque_registry (03)    registry.store/resolve
    │                 terminal_router (01)     register() for channels
    │
    ├── calls: tool entry functions (lazy imports inside handlers)
    │            run_pms1_pipeline()   (06) — from src.tools.tool_pms1
    │            run_pteca()           (07) — from src.tools.tool_pteca
    │            stencil2chart()       (08) — from src.stencil2chart
    │
    ├── called by: agent_loop (02)    _safe_execute → execute_tool
    │
    ├── reads: _session_dir           set once by agent_loop
    │
    └── no deps on: system_prompt (04), anthropic SDK
```

Note: `from src.XXX` imports resolve via `src/__init__.py`'s `__path__` extension — see D1/D8 in ClaudenoDiscretion.md.

## API

### execute_tool(name: str, params: dict) → str

| Param | Type | Description |
|---|---|---|
| `name` | `str` | Tool name from ToolUseBlock. e.g. `"run_pms1"` |
| `params` | `dict` | Tool input from ToolUseBlock. e.g. `{"firm": "Best Buy", "query": "..."}` |

Returns a string that becomes the `content` field of the
`tool_result` message sent back to the orchestrator LLM.

### set_session_dir(path: Path) → None

Set the session directory for disk persistence. Called once by
`run_harness()` at session start.

```python
# in agent_loop.py, inside run_harness():
execute_tool_mod.set_session_dir(session_dir)
```

### reset_counters() → None

Reset per-session state (`_s2c_counter`). Called by `run_harness()`
between sessions so S2C labels restart at S2C-1.

```python
def reset_counters() -> None:
    """Reset per-session state. Called by run_harness."""
    global _s2c_counter
    with _s2c_lock:
        _s2c_counter = 0
```

## Handle resolution (Phase 1)

Before dispatching to any tool branch, scan all string values
in `params` for the `$var_` prefix and resolve them via
`registry.resolve()`.

**Exception:** `inspect_var` is skipped — it needs raw handle
strings to look up in the registry, not resolved data.

```python
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
```

Scans top-level string values and arrays of strings (needed for
PTECA v2 `stencils: [str]`). No recursive scanning of nested
dicts. For array parameters (e.g. `stencils: [$var_1, $var_2]`),
each element is resolved individually.

If `registry.resolve()` raises `KeyError`, the exception
propagates to `_safe_execute` in agent_loop, which catches it
and returns the error string (with available handles) as the
tool_result.

## Label injection (Phase 2)

For parallel execution, each tool invocation gets a unique
ToolChannel label so the user can distinguish output.

`execute_tool` derives the label from tool name + params, creates
a ToolChannel via `register()`, and passes it to the tool's entry
function as the `channel` keyword argument.

```python
# Per-branch label derivation:
"run_pms1":          f"PMS1-{params['firm']}"
"run_pteca":         "PTECA-" + "+".join(firms)   # firms extracted from stencil dicts
"run_stencil2chart": _next_s2c_label()    # "S2C-1", "S2C-2", etc.
"ask_user":          "ORCHESTRATOR"
"inspect_var":       (no channel needed — pure data, no terminal I/O)
"write_session_md":  (no channel needed — file I/O only, no terminal output)
"read_session_md":   (no channel needed — read-only, no terminal output)
```

Tool entry functions accept an optional `channel` param:

```python
# In PMS1's entry function (06_tool_pms1.md):
def run_pms1_pipeline(firm, query, channel=None):
    if channel is None:
        channel = _default_channel   # module-level fallback
    channel.print("Loading index...")
    ...
```

This is E1 from the forensics review: explicit param, no magic.

## Dispatch table (Phase 3)

Dict mapping tool names to handler functions. Each handler
receives resolved params and returns a result string.

```python
_dispatch: dict[str, Callable] = {
    "run_pms1":          _exec_pms1,
    "run_pteca":         _exec_pteca,
    "run_stencil2chart": _exec_stencil2chart,
    "ask_user":          _exec_ask_user,
    "inspect_var":       _exec_inspect_var,
    "write_session_md":  _exec_write_session_md,
    "read_session_md":   _exec_read_session_md,
}
```

Unknown tool name → error string with dynamically generated list:
`available = ", ".join(_dispatch.keys())`
`f"Error: unknown tool '{name}'. Available: {available}"`

## Per-branch details

### run_pms1

```python
def _exec_pms1(params: dict) -> str:
    firm = params["firm"]
    query = params["query"]
    channel = register(f"PMS1-{firm}")

    # Call PMS1 pipeline
    stencil = run_pms1_pipeline(firm, query, channel=channel)

    # Store in registry
    handle = registry.store(stencil, f"{firm} stencil")

    # Persist to disk
    _dump_asset(f"{handle.lstrip('$')}_{_sanitize(firm)}_stencil.json", stencil)

    # Count rows + periods for result message
    n_rows = len(stencil.get("rows", []))
    n_periods = len(stencil.get("periods", []))
    return (
        f"PMS1 complete. {firm} stencil stored as {handle}. "
        f"{n_rows} rows, {n_periods} periods."
    )
```

### run_pteca (D14 rewrite)

```python
def _exec_pteca(params: dict) -> str:
    stencils = params["stencils"]     # list of dicts (resolved from $var_N handles)
    query = params["query"]
    firms = [s["firm"] for s in stencils]
    channel = register("PTECA-" + "+".join(firms))

    # Call PTECA (internal agent loop)
    chart_inputs = run_pteca(stencils, query, channel=channel)

    # Early return if user cancelled
    if not chart_inputs:
        return "PTECA cancelled by user. No charts to render. Move on."

    # Store each chart_input as a separate handle
    handles = []
    last_handle = ""
    for i, ci in enumerate(chart_inputs):
        n_series = len(ci.get("series", []))
        desc = f"chart {i+1} ({n_series} series)"
        handle = registry.store(ci, desc)
        handles.append(f"{handle}: {desc}")
        last_handle = handle

    # Persist all to disk
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

### run_stencil2chart

```python
def _exec_stencil2chart(params: dict) -> str:
    chart_input = params["chart_input"]   # already resolved from $var_N
    channel = register(_next_s2c_label())

    output_dir = _session_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Call stencil2chart (returns Path or None)
    output_path = stencil2chart(chart_input, output_dir, channel=channel)

    if output_path is None:
        return "stencil2chart cancelled by user (gap prompt)."

    # No registry store — result is a file path (small string)
    # No disk dump — stencil2chart already writes the SVG
    return f"Saved: {output_path}"
```

### ask_user

```python
def _exec_ask_user(params: dict) -> str:
    question = params["question"]
    answer = _orchestrator_channel.input(question, markdown=True)
    return f"User answered: {answer}"
```

### inspect_var

```python
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
        available = registry.list_vars()
        return f"Error: {handle} does not exist. Available:\n{available}"
```

### write_session_md

```python
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
```

### read_session_md

```python
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
```

## Disk persistence (Phase 5)

Handled per-branch, not by the registry. Only tools that produce
large structured output persist to disk.

| Tool | Persist? | Filename | Format |
|---|---|---|---|
| run_pms1 | Yes | `{var_N}_{Firm}_stencil.json` | JSON |
| run_pteca | Yes | `{firms_slug}_chart_inputs_{var_N}.json` | JSON |
| run_stencil2chart | No (already writes SVG) | — | — |
| ask_user | No | — | — |
| write_session_md | Yes (direct file write) | user-specified | Markdown |
| read_session_md | No (read-only) | — | — |
| inspect_var | No | — | — |

Asset directory: `{session_dir}/assets/`

```python
_session_dir: Path | None = None

def set_session_dir(path: Path):
    global _session_dir
    _session_dir = path

def _dump_asset(filename: str, data: Any):
    """Write data to session assets dir as JSON."""
    assets = _session_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    with open(assets / filename, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

def _sanitize(firm: str) -> str:
    """Best Buy → Best_Buy. Strips chars unsafe for filenames."""
    return re.sub(r"[^\w\-]", "_", firm)
```

`set_session_dir()` is called once by `run_harness()`. Threads
only read `_session_dir`, never write — no race condition.

## Implementation sketch

```python
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable

from src.harness.opaque_registry import registry
from src.harness.terminal_router import register

# ── Session state (set once per run_harness call) ──────────
_session_dir: Path | None = None

# ── Module-level channels ─────────────────────────────────
_orchestrator_channel = register("ORCHESTRATOR")

# ── S2C invocation counter (per-call labels: S2C-1, S2C-2) ─
_s2c_counter = 0
_s2c_lock = threading.Lock()

def _next_s2c_label() -> str:
    global _s2c_counter
    with _s2c_lock:
        _s2c_counter += 1
        return f"S2C-{_s2c_counter}"


def set_session_dir(path: Path):
    global _session_dir
    _session_dir = path


def reset_counters() -> None:
    """Reset per-session state. Called by run_harness."""
    global _s2c_counter
    with _s2c_lock:
        _s2c_counter = 0


# ── Handle resolution ──────────────────────────────────────
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


# ── Disk persistence ───────────────────────────────────────
def _dump_asset(filename: str, data: Any):
    assets = _session_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    with open(assets / filename, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _sanitize(firm: str) -> str:
    """Best Buy → Best_Buy. Strips chars unsafe for filenames."""
    return re.sub(r"[^\w\-]", "_", firm)


# ── Branch handlers ────────────────────────────────────────
def _exec_pms1(params: dict) -> str:
    from src.tools.tool_pms1 import run_pms1_pipeline

    firm = params["firm"]
    query = params["query"]
    channel = register(f"PMS1-{firm}")

    stencil = run_pms1_pipeline(firm, query, channel=channel)

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
    channel = register("PTECA-" + "+".join(firms))

    chart_inputs = run_pteca(stencils, query, channel=channel)

    if not chart_inputs:
        return "PTECA cancelled by user. No charts to render. Move on."

    handles = []
    last_handle = ""
    for i, ci in enumerate(chart_inputs):
        n_series = len(ci.get("series", []))
        desc = f"chart {i+1} ({n_series} series)"
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
    target = (_session_dir / filename).resolve()
    if not target.is_relative_to(_session_dir.resolve()):
        return f"Error: path '{filename}' escapes session directory."
    def _embed_replacer(match):
        handle = match.group(1)
        try:
            data = registry.resolve(handle)
            desc = registry._registry[handle]["description"]
            json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            return f"**{handle}** ({desc}):\n```json\n{json_str}\n```"
        except KeyError:
            return f"[Error: {handle} not found]"
    resolved_content = re.sub(r"\{\{embed:(\$var_\d+)\}\}", _embed_replacer, content)
    target.parent.mkdir(parents=True, exist_ok=True)
    open_mode = "a" if mode == "append" else "w"
    with open(target, open_mode) as f:
        f.write(resolved_content)
    return f"Written: {_session_dir / filename} (mode={mode})"


def _exec_read_session_md(params: dict) -> str:
    if _session_dir is None:
        return "Error: no active session."
    filename = params["filename"]
    target = (_session_dir / filename).resolve()
    if not target.is_relative_to(_session_dir.resolve()):
        return f"Error: path '{filename}' escapes session directory."
    if not target.exists():
        return f"Error: '{filename}' does not exist in session directory."
    return target.read_text()


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


# ── Dispatch table ─────────────────────────────────────────
_dispatch: dict[str, Callable] = {
    "run_pms1":          _exec_pms1,
    "run_pteca":         _exec_pteca,
    "run_stencil2chart": _exec_stencil2chart,
    "ask_user":          _exec_ask_user,
    "inspect_var":       _exec_inspect_var,
    "write_session_md":  _exec_write_session_md,
    "read_session_md":   _exec_read_session_md,
}


# ── Public API ─────────────────────────────────────────────
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
```

## Dependencies

| Component | Spec | What it provides | Import style |
|---|---|---|---|
| opaque_registry | `03_opaque_registry.md` | `registry.store/resolve/list_vars/preview/dump` | Top-level |
| terminal_router | `01_terminal_router.md` | `register()` for per-invocation ToolChannels | Top-level |
| PMS1 pipeline | `06_tool_pms1.md` | `run_pms1_pipeline()` entry function | Lazy (inside `_exec_pms1`) |
| PTECA | `07_tool_pteca.md` | `run_pteca()` entry function | Lazy (inside `_exec_pteca`) |
| stencil2chart | `08_tool_stencil2chart.md` | `stencil2chart()` function | Lazy (inside `_exec_stencil2chart`) |

Tool imports are lazy (inside handler functions) to avoid circular
imports and defer heavy module loading until actually needed:
- `from src.tools.tool_pms1 import run_pms1_pipeline`
- `from src.tools.tool_pteca import run_pteca`
- `from src.stencil2chart import stencil2chart`

Does NOT import from: agent_loop (02), system_prompt (04),
anthropic SDK. No circular deps.

Note: PUMBA (spec 12) is currently PMS1-internal — orchestrator.py
calls `run_pumba()` when PTO fails. Not yet exposed as a
harness-level tool.

## File location

```
src/harness/execute_tool.py
```

## Ideal demo

### What execute_tool sees per call (inside thread)

```
execute_tool("run_pms1", {"firm": "Best Buy", "query": "margins FY2022-2023"})
    │
    ├── _resolve_handles: no $var_ strings, params unchanged
    ├── _exec_pms1:
    │     channel = register("PMS1-Best Buy")
    │     stencil = run_pms1_pipeline("Best Buy", "margins...", channel)
    │       │
    │       ├── [PMS1-Best Buy] Loading index...
    │       ├── [PMS1-Best Buy] Sekei planning... done.
    │       ├── [PMS1-Best Buy] PTO batch A_124... 42 chunks.
    │       ├── [PMS1-Best Buy] PTO judge... done.
    │       └── returns stencil dict
    │     handle = registry.store(stencil, "Best Buy stencil") → "$var_1"
    │     dump assets/var_1_Best_Buy_stencil.json
    │
    └── returns "PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods."

execute_tool("run_pteca", {"stencils": ["$var_1"], "query": "gross margins"})
    │
    ├── _resolve_handles: ["$var_1"] → [actual stencil dict]
    ├── _exec_pteca:
    │     firms = [s["firm"] for s in stencils] → ["Best Buy"]
    │     channel = register("PTECA-Best Buy")
    │     chart_inputs = run_pteca(stencils, "gross margins", channel=channel)
    │       │
    │       ├── [PTECA-Best Buy] Stencil has 5 rows.
    │       ├── [PTECA-Best Buy] Keep only margins?
    │       │   > yes
    │       └── returns [chart_input dict]
    │     handle = registry.store(ci, "chart 1 (2 series)") → "$var_2"
    │     dump assets/Best_Buy_chart_inputs_var_2.json
    │
    └── returns "PTECA complete. 1 chart(s):\n$var_2: chart 1 (2 series)"

execute_tool("run_stencil2chart", {"chart_input": "$var_2"})
    │
    ├── _resolve_handles: "$var_2" → actual chart_input dict
    ├── _exec_stencil2chart:
    │     channel = register(_next_s2c_label())
    │     path = stencil2chart(chart_input_dict, output_dir, channel)
    │     (no gap → no prompt)
    │
    └── returns "Saved: output/stencil2charted_20260715_1.svg"
```

## Resolved questions

- **Session dir.** Module-level variable set once via
  `set_session_dir()`. No per-call arg. Thread-safe (read-only
  after set).
- **Handle resolution.** Prefix scan on top-level string params
  and arrays of strings (for PTECA v2 `stencils`). Runs before
  dispatch. Skipped for `inspect_var` (needs raw handle strings).
  `write_session_md` passes through `_resolve_handles` (not skipped)
  — its `content` field never starts with `$var_`, so resolution is
  a no-op. The `{{embed:$var_N}}` markers inside content are resolved
  separately by `_exec_write_session_md`'s internal regex.
- **Dispatch mechanism.** Dict mapping name → handler function.
  Extensible — add one entry + one function for new tools.
- **Label injection.** E1 approach: `execute_tool` creates a
  ToolChannel per invocation, passes it as `channel` kwarg to
  tool entry function. Tool uses it if provided, falls back to
  module-level default.
- **Disk persistence.** Per-branch. PMS1 and PTECA dump to
  `{session_dir}/assets/`. stencil2chart already writes SVG.
  Filenames derived from registry handle + tool + firm (e.g.
  `var_1_Best_Buy_stencil.json`). Handle tag prevents collision
  when same firm is called twice. PTECA uses joined firms slug
  (e.g. `Best_Buy_Amcor_chart_inputs_var_3.json`).
- **ask_user channel.** Module-level `_orchestrator_channel =
  register("ORCHESTRATOR")`. Reused across calls, not created per
  invocation.
- **inspect_var.** Delegates to registry methods. No channel
  needed (pure data, no terminal I/O).
- **Unknown tool.** Returns error string with dynamically
  generated list from `_dispatch.keys()`. No crash.
