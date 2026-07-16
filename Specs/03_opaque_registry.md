# Opaque Registry

In-memory key-value store for large tool outputs. Prevents context
bloat by giving the orchestrator LLM opaque handles (`$var_N`)
instead of full data.

## Architecture

```
execute_tool("run_pms1", {...})
    │
    │  PMS1 returns giant stencil dict (50+ lines)
    │
    ▼
registry.store(data, description="Best Buy stencil")
    │
    ├── _counter += 1                    → 1
    ├── _registry["$var_1"] = {
    │       "data": <stencil dict>,
    │       "description": "Best Buy stencil",
    │   }
    │
    ▼
returns "$var_1"
    │
    ▼
execute_tool returns tool_result to LLM:
    "PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods."

              ─── LLM never sees the stencil dict ───

Later, LLM calls: run_pteca(stencil="$var_1", query="...")
    │
    ▼
execute_tool receives input with "$var_1"
    │
    ▼
registry.resolve("$var_1")
    │
    ├── lookup _registry["$var_1"]
    ├── return <stencil dict>             ← real data, passed to PTECA code
    │
    ▼
PTECA runs with actual stencil dict
```

### Connection to other components

```
opaque_registry.py
    │
    ├── used by: execute_tool (05)
    │              execute_tool calls store() after tool returns big data
    │              execute_tool calls resolve() before passing data to tools
    │
    ├── used by: agent_loop (02)
    │              agent_loop calls reset() at start of run_harness()
    │
    ├── exposes: inspect_var tool definition (JSON schema)
    │              agent_loop includes this in tool_definitions list
    │              execute_tool has a branch for it
    │
    └── no deps on: terminal_router, tools, system_prompt
```

## Data model

```python
_counter: int = 0
_registry: dict[str, dict] = {}

# Each entry:
_registry["$var_1"] = {
    "data": Any,              # the actual object (stencil dict, chart_input, etc.)
    "description": str,       # human-readable, set by execute_tool
}
```

## API

### store(data: Any, description: str) → str

Store data, return a handle.

| Param | Type | Description |
|---|---|---|
| `data` | `Any` | The object to store. Registry does not inspect it. |
| `description` | `str` | What this data is. Set by `execute_tool`, not the LLM. e.g. `"Best Buy stencil"`, `"Amcor chart_input"` |

Returns `"$var_N"` where N is the auto-incremented counter.

```python
handle = registry.store(stencil_dict, "Best Buy stencil")
# handle = "$var_1"
```

### resolve(handle: str) → Any

Look up data by handle.

| Param | Type | Description |
|---|---|---|
| `handle` | `str` | e.g. `"$var_1"` |

Returns the stored data object.

Raises `KeyError` if handle does not exist. Caller (`execute_tool`)
catches this and returns error string to LLM:
`"Error: handle $var_99 does not exist. Available: $var_1 (Best Buy stencil), $var_2 (Amcor stencil)"`

```python
data = registry.resolve("$var_1")
# data = <the stencil dict>
```

### reset() → None

Clear all entries and reset counter to 0. Called once at the top
of `run_harness()` so each session starts clean.

```python
registry.reset()
# _counter = 0, _registry = {}
```

### list_vars() → str

Return a formatted string listing all handles and their descriptions.
Used by the `inspect_var` tool's `list` mode.

```python
registry.list_vars()
# "$var_1: Best Buy stencil\n$var_2: Amcor stencil\n$var_3: Best Buy chart_input"
```

Returns `"(no variables stored)"` if registry is empty.

### preview(handle: str) → str

Return first 500 characters of `str(data)` for the given handle.
Used by `inspect_var` tool's `preview` mode.

```python
registry.preview("$var_1")
# "{'firm': 'Best Buy', 'periods': ['FY2022', 'FY2023'], 'rows': [{'metric': 'Revenue', 'values': [51761000000, 46298000..."
```

Raises `KeyError` if handle does not exist.

### dump(handle: str) → str

Return full `str(data)` for the given handle. Used by `inspect_var`
tool's `full` mode. Context bloat warning — this is the nuclear option.

Raises `KeyError` if handle does not exist.

## inspect_var tool definition

The orchestrator LLM can call this tool when it's confused about
handle contents. Specced here because it's tightly coupled to
the registry — not a separate tool contract.

### JSON schema

```json
{
    "name": "inspect_var",
    "description": "Inspect a stored variable handle. Use ONLY when repeated downstream tool errors require understanding the data. Prefer 'list' first, then 'preview', then 'full' as last resort.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["list", "preview", "full"],
                "description": "list = show all handles + descriptions. preview = first 500 chars of one handle. full = entire contents of one handle (context-heavy, last resort)."
            },
            "handle": {
                "type": "string",
                "description": "The handle to inspect, e.g. '$var_1'. Required for preview and full modes. Ignored for list mode."
            }
        },
        "required": ["mode"]
    }
}
```

### execute_tool branch

```python
# inside execute_tool()
if name == "inspect_var":
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
    except KeyError:
        available = registry.list_vars()
        return f"Error: handle {handle} does not exist. Available:\n{available}"
```

### Escalation tiers

```
Tier 0 (normal):      LLM sees "$var_1" only. Tool_result says
                      "Best Buy stencil stored as $var_1."
                      No inspection.

Tier 1 (list):        LLM calls inspect_var(mode="list")
                      Gets: "$var_1: Best Buy stencil\n$var_2: ..."
                      Orients which handle is which.

Tier 2 (preview):     LLM calls inspect_var(mode="preview", handle="$var_1")
                      Gets: first 500 chars of the data.
                      Enough to see structure without context bloat.

Tier 3 (full):        LLM calls inspect_var(mode="full", handle="$var_1")
                      Gets: entire stringified data.
                      Nuclear option. Only after preview is insufficient.
```

System prompt (04) instructs the LLM to escalate in this order
and never jump straight to `full`.

## Lifetime

Per `run_harness()` call. `reset()` is called at the top of
`run_harness()`:

```python
def run_harness(user_query: str):
    registry.reset()       # ← clean slate
    # ... while loop ...
```

After `run_harness()` returns, registry data is stale. No
cross-session persistence. Disk persistence of specific assets
(stencil JSON, chart_input JSON) is handled by `execute_tool`
per-tool — not by the registry.

## Disk persistence (NOT the registry's job)

The registry is in-memory only. Saving assets to disk is the
responsibility of each tool's `execute_tool` branch.

```
execute_tool("run_pms1", {...})
    │
    ├── result = run_pms1(...)               ← actual stencil dict
    ├── handle = registry.store(result, "Best Buy stencil")
    │
    ├── # Disk dump (execute_tool does this, not registry)
    ├── asset_path = session_dir / "assets" / "Best_Buy_stencil_var_1.json"
    ├── json.dump(result, open(asset_path, "w"))
    │
    └── return f"PMS1 complete. Best Buy stencil stored as {handle}."
```

Which tools persist to disk:

| Tool | Persist? | Filename |
|---|---|---|
| run_pms1 | Yes | `{firm}_stencil_{var_N}.json` |
| run_pteca | Yes | `{firm}_chart_inputs_{var_N}.json` |
| run_stencil2chart | No (already produces an SVG file) | — |
| inspect_var | No | — |
| ask_user | No | — |

Filenames are derived by `execute_tool` from tool name + input
params + registry handle tag (e.g. `Best_Buy_stencil_var_1.json`).
Handle tag prevents collision on duplicate calls. Deterministic,
not LLM-generated.

Asset dir: `PSOAS/temp/sessions/YYYYMMDDHHMMSS/assets/`
(same session dir as transcript from 02_agent_loop).

## Implementation sketch

```python
import threading
from typing import Any


class OpaqueRegistry:
    def __init__(self):
        self._counter: int = 0
        self._registry: dict[str, dict] = {}
        self._lock = threading.RLock()

    def store(self, data: Any, description: str) -> str:
        with self._lock:
            self._counter += 1
            handle = f"$var_{self._counter}"
            self._registry[handle] = {
                "data": data,
                "description": description,
            }
        return handle

    def resolve(self, handle: str) -> Any:
        with self._lock:
            if handle not in self._registry:
                available = self.list_vars()
                raise KeyError(
                    f"Handle {handle} does not exist. Available:\n{available}"
                )
            return self._registry[handle]["data"]

    def list_vars(self) -> str:
        with self._lock:
            if not self._registry:
                return "(no variables stored)"
            lines = []
            for handle, entry in self._registry.items():
                lines.append(f"{handle}: {entry['description']}")
            return "\n".join(lines)

    def preview(self, handle: str) -> str:
        data = self.resolve(handle)
        full = str(data)
        if len(full) <= 500:
            return full
        return full[:500] + "..."

    def dump(self, handle: str) -> str:
        return str(self.resolve(handle))

    def reset(self):
        with self._lock:
            self._counter = 0
            self._registry.clear()


# ── Singleton ──────────────────────────────────────────────────
registry = OpaqueRegistry()
```

## Dependencies

`threading` (Lock). No imports from other PSOAS components.

## File location

```
PSOAS/src/harness/opaque_registry.py
```

## Resolved questions

- **Handle format.** `$var_N` with dumb counter. Simple, sufficient
  for 3-6 handles per session. Descriptive naming rejected — adds
  coupling for negligible LLM benefit.
- **Disk persistence.** Not the registry's job. `execute_tool`
  handles per-tool disk dumps. Registry is in-memory only.
- **Disk filenames.** Descriptive, derived by `execute_tool` from
  tool + input params + handle tag. e.g. `Best_Buy_stencil_var_1.json`.
  Handle tag prevents collision on duplicate calls. Not LLM-generated.
- **inspect_var.** Specced as registry-coupled tool, not separate
  tool contract. Three modes: list, preview (500 chars), full.
  System prompt instructs escalation order.
- **Error on bad handle.** `resolve()` raises KeyError with list
  of available handles. `execute_tool` catches and returns error
  string to LLM.
- **Lifetime.** Per `run_harness()` call. `reset()` at session start.
