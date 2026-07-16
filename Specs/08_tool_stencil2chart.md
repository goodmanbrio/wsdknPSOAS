# Tool Contract: stencil2chart

Tool wrapper for the existing `stencil2chart.py`. Renders a
chart_input dict as an SVG line chart. Already built — just needs
a thin wrapper for execute_tool dispatch + ToolChannel migration.

## JSON schema

Source of truth. 04_system_prompt.md copies this.

```json
{
    "name": "run_stencil2chart",
    "description": "Render chart-ready data as an SVG line chart. Takes a chart_input handle and returns the saved file path.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_input": {
                "type": "string",
                "description": "Opaque handle to chart_input data, e.g. '$var_2'"
            }
        },
        "required": ["chart_input"]
    }
}
```

## Architecture

```
execute_tool("run_stencil2chart", {"chart_input": <resolved dict>})
    │
    ▼
_exec_stencil2chart(params)                 ← in execute_tool.py
    │
    ├── channel = register("S2C-N")         ← per-invocation label
    ├── chart_input = params["chart_input"]  ← already resolved from $var_N
    ├── output_path = stencil2chart(chart_input, output_dir, channel=channel)
    │       │
    │       ├── _check_gaps()
    │       ├── (if gaps) channel.input("Choice [1/2/3]: ")
    │       ├── plt.style.context → plot → savefig
    │       └── returns Path or None
    │
    ├── if None: return "stencil2chart cancelled by user (gap prompt)."
    └── return f"Saved: {output_path}"
```

### What exists vs what changes

```
EXISTS (logic unchanged except counter lock):
    src/stencil2chart.py       stencil2chart(), _check_gaps(), _STYLES,
                               _call_counter, filename generation

MODIFY (thread safety):
    src/stencil2chart.py       _call_counter increment must be under a
                               threading.Lock to prevent race when
                               parallel stencil2chart calls generate
                               filenames simultaneously. Add:
                               _counter_lock = threading.Lock()
                               and wrap the counter increment + filename
                               generation in `with _counter_lock:`

MODIFY (print/input → ToolChannel):
    src/stencil2chart.py:55    print(f"⚠ {len(gaps)} missing...")  → channel.print(...)
    src/stencil2chart.py:56-58 print(f"  - {g}")                  → channel.print(...)
    src/stencil2chart.py:59-60 print("1. Allow gap...")            → channel.print(...)
    src/stencil2chart.py:62    input("Choice [1/2/3]: ")           → channel.input(...)
    src/stencil2chart.py:64    print("Enter 1, 2, or 3.")         → channel.print(...)

NEW:
    stencil2chart() gains optional channel param
    Module-level _default_channel = register("S2C")
```

## Entry function changes

Current signature:
```python
def stencil2chart(chart_input: dict, output_dir: Path) -> Path | None:
```

New signature:
```python
def stencil2chart(chart_input: dict, output_dir: Path, channel: ToolChannel | None = None) -> Path | None:
```

Only change is the optional `channel` param. All existing logic
stays identical. The `_prompt_gaps` function receives `channel`
to use instead of raw `print()`/`input()`.

```python
from src.harness.terminal_router import register, ToolChannel

_default_channel = register("S2C")


def _prompt_gaps(gaps: list[str], channel: ToolChannel) -> int:
    channel.print(f"⚠ {len(gaps)} missing value(s):")
    for g in gaps:
        channel.print(f"  - {g}")
    channel.print("1. Allow gap (line will break at missing points)")
    channel.print("2. Terminate (caller retries with better input)")
    channel.print("3. Cancel")

    while True:
        choice = channel.input("Choice [1/2/3]: ").strip()
        if choice in ("1", "2", "3"):
            return int(choice)
        channel.print("Enter 1, 2, or 3.")


def stencil2chart(
    chart_input: dict,
    output_dir: Path,
    channel: ToolChannel | None = None,
) -> Path | None:
    ch = channel or _default_channel

    gaps = _check_gaps(chart_input)
    if gaps:
        choice = _prompt_gaps(gaps, ch)
        if choice in (2, 3):
            return None

    # ... rest unchanged ...
```

## chart_input contract

The dict that stencil2chart expects. Produced by PTECA's
`_build_chart_inputs` (spec 07).

```json
{
    "title": "Best Buy — Gross Margin (FY2022-2023)",
    "periods": ["FY2022", "FY2023"],
    "denomination_label": "(%)",
    "series": [
        {"metric": "Gross Margin", "values": [29, 29]},
        {"metric": "Net Profit Margin", "values": [4.7, 3.1]}
    ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `title` | `str` | Yes | Chart title. Set by PTECA LLM via finalize. |
| `periods` | `list[str]` | Yes | X-axis labels, aligned with values. |
| `denomination_label` | `str` | No | Y-axis label. `""` = no label. |
| `series` | `list[dict]` | Yes | One entry per line on chart. |

Series entry:

| Field | Type | Description |
|---|---|---|
| `metric` | `str` | Legend label. |
| `values` | `list[float\|null]` | One per period. `null` = gap (triggers prompt). |

Constraints:
- `len(values)` == `len(periods)` for every series.
- All series share the same unit (splitting by PTECA upstream).
- % values in human-readable form (29 not 0.29).

## execute_tool branch

```python
def _exec_stencil2chart(params: dict) -> str:
    chart_input = params["chart_input"]
    channel = register(_next_s2c_label())    # "S2C-1", "S2C-2", etc.

    output_dir = _session_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = stencil2chart(chart_input, output_dir, channel=channel)

    if output_path is None:
        return "stencil2chart cancelled by user (gap prompt)."
    return f"Saved: {output_path}"
```

No registry store — result is a file path string (small, returned
inline). No disk dump — stencil2chart already writes the SVG.

## ToolChannel label

| Context | Label |
|---|---|
| Via execute_tool (harness) | `"S2C-{N}"` e.g. `"S2C-1"`, `"S2C-2"` (per-invocation) |
| Standalone / fallback | `"S2C"` |

Per-invocation labels distinguish parallel stencil2chart calls in
terminal output (e.g. two gap prompts from different charts).
`_next_s2c_label()` in execute_tool.py generates the label.
`_default_channel = register("S2C")` in stencil2chart.py is the
fallback when `channel` is None (standalone use, no harness).

## Internal user interaction

Gap prompt only. If any series has `None` values, user is asked
to choose: allow gap (1), terminate (2), or cancel (3). Goes
through `channel.input()` → TerminalRouter.

```
[S2C-1] ⚠ 1 missing value(s):
[S2C-1]   - Gross Profit @ FY2023
[S2C-1] 1. Allow gap (line will break at missing points)
[S2C-1] 2. Terminate (caller retries with better input)
[S2C-1] 3. Cancel
[S2C-1] Choice [1/2/3]:
> 1
```

## Dependencies

| Component | What it provides |
|---|---|
| `matplotlib` | Plotting |
| `lovelyplots` | Style sheets (`ipynb`, `colors10-markers`, `svg_no_fonttype`) |
| `src/harness/terminal_router.py` | `register()`, `ToolChannel` |

## File location

```
Poony_Multiretrieval_S1/src/stencil2chart.py   (MODIFIED, already exists)
```

No new file. The existing `stencil2chart.py` gains an optional
`channel` param and migrates print/input calls.

## Resolved questions

- **Thin wrapper.** No new file needed. Existing function gains
  optional `channel` param. execute_tool calls it directly.
- **No registry store.** Returns file path inline (small string).
- **No disk dump.** stencil2chart already writes the SVG file.
- **Label.** Per-invocation `"S2C-{N}"` via `_next_s2c_label()` in
  execute_tool. Fallback `"S2C"` in stencil2chart.py for standalone use.
- **Gap prompt.** Migrated from raw input() to channel.input().
  Same UX, routed through TerminalRouter.
