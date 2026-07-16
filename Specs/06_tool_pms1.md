# Tool Contract: PMS1

Tool wrapper that exposes the PMS1 pipeline (Sekei → PTO → Stencil)
as a callable tool for the orchestrator harness.

## JSON schema

Source of truth. 04_system_prompt.md copies this.

```json
{
    "name": "run_pms1",
    "description": "Run the PMS1 pipeline to extract financial data for a single firm. Returns a stencil as an opaque handle.",
    "input_schema": {
        "type": "object",
        "properties": {
            "firm": {
                "type": "string",
                "description": "Company name. e.g. 'Best Buy', 'Amcor'"
            },
            "query": {
                "type": "string",
                "description": "The user's original query or the relevant portion for this firm."
            }
        },
        "required": ["firm", "query"]
    }
}
```

## Architecture

```
execute_tool("run_pms1", {"firm": "Best Buy", "query": "margins FY2022-2023"})
    │
    ▼
_exec_pms1(params)                          ← in execute_tool.py
    │
    ├── channel = register("PMS1-Best Buy")
    │
    ▼
run_pms1_pipeline("Best Buy", "margins FY2022-2023", channel)
    │                                        ← in tool_pms1.py (NEW)
    │
    ├── channel.print("Loading index...")
    ├── config = Config.from_env()
    ├── index = load_index(config)           ← expensive on first call
    │
    ├── channel.print("Sekei planning...")
    ├── plan = sekei(query, config)          ← existing sekei.py
    ├── channel.print(f"done. {n} cells, {m} batches.")
    │
    ├── channel.print(f"PTO batch {batch.id}...")
    ├── results = run(plan, index, config)   ← existing orchestrator.py
    │       │
    │       ├── (internally prints generic [PMS1] warnings — fine)
    │       ├── loops batches: pto_retrieve → pto_judge → merge
    │       └── compute_stencil → fills formula cells
    │
    ├── channel.print(f"Stencil computed. {n} cells filled.")
    │
    ├── stencil_dict = serialize_stencil(plan, results)  ← in stencil.py (NEW)
    │
    └── return stencil_dict
```

### What's new vs what exists

```
EXISTS (no changes to internal logic):
    src/sekei.py          sekei(query, config) → SekeiPlan
    src/orchestrator.py   run(plan, index, config) → dict[str, CellResult]
    src/pto.py            pto_retrieve, pto_judge, pto_judge_to_stencil
    src/stencil.py        compute_stencil, CellResult

EXISTS (migrate print → pto_out.print, no signature changes):
    src/orchestrator.py:54   print("⚠ BATCH VALIDATION...")  → pto_out.print(...)
    src/pto.py:832           print(f"[pto_judge] tokens...")  → pto_out.print(...)
    src/stencil.py:76        print("⚠ VALUE RECONCILIATION") → pto_out.print(...)

NEW:
    src/tool_pms1.py         run_pms1_pipeline() wrapper
    src/stencil.py           serialize_stencil() function (added to existing file)
```

## Entry function

### run_pms1_pipeline(firm: str, query: str, channel: ToolChannel | None = None) → dict

| Param | Type | Description |
|---|---|---|
| `firm` | `str` | Company name |
| `query` | `str` | User's query for this firm |
| `channel` | `ToolChannel \| None` | Per-invocation channel for labeled output. Falls back to module-level `register("PMS1")` if None. |

Returns a stencil dict (see Output contract below).

## Output contract

### Stencil dict

What `serialize_stencil()` produces. Self-contained — no SekeiPlan
or CellResult objects, just plain dicts and lists. JSON-serializable.

```python
{
    "firm": "Best Buy",
    "periods": ["FY2022", "FY2023"],
    "rows": [
        {
            "metric": "Revenue",
            "values": [51761000000, 46298000000],
            "denomination": "bn",
            "unit": "USD",
        },
        {
            "metric": "Gross Profit",
            "values": [15005000000, 13610000000],
            "denomination": "bn",
            "unit": "USD",
        },
        {
            "metric": "Gross Margin",
            "values": [29, 29],
            "denomination": "",
            "unit": "%",
        },
    ]
}
```

Rows are ordered by original SekeiPlan row number (metric order
as planned by Sekei). Each row has values aligned positionally
with `periods`.

Percentage values are stored in human-readable form: 29 not 0.29.
Downstream (PTECA, stencil2chart) receives ready-to-plot values.

**RESOLVED:** `CellResult.value` stores raw decimals for compute
cells (e.g. 0.29 for 29%). Compute cells also have `unit=""` and
`denomination=""` by default. `serialize_stencil` must: (1) detect
compute cells via `cell.type == "compute"`, (2) multiply value by
100, (3) set `unit="%"`. This matches `format_filled_stencil` which
already treats all compute cells as percentages (`:.2%` format).

### serialize_stencil()

Lives in `stencil.py` (alongside `format_filled_stencil`).
Commented as downstream PTECA consumption.

```python
def serialize_stencil(
    plan: SekeiPlan,
    results: dict[str, CellResult],
) -> dict:
    """Serialize stencil to a self-contained dict for downstream PTECA use.

    Combines SekeiPlan metadata (firm, periods, metric names) with
    filled CellResult values into one JSON-serializable dict.
    """
    # Build row-keyed structure from grid coordinates
    # Cell IDs are like A1, B1 (col=period, row=metric)
    row_data: dict[int, dict] = {}

    for cell_id, cell in plan.cells.items():
        match = re.match(r"([A-Z])(\d+)", cell_id)
        col = ord(match.group(1)) - ord("A")
        row = int(match.group(2))

        if row not in row_data:
            row_data[row] = {
                "metric": cell.metric,
                "values": [None] * len(plan.periods),
                "denomination": "",
                "unit": "",
            }

        if cell_id in results:
            value = results[cell_id].value

            # Compute cells (margins/ratios) store raw decimals (0.29).
            # Convert to display form (29) and mark as percentage.
            # WARNING: if compute_stencil is ever changed to set unit
            # or return already-converted values, this will double-convert.
            # Guard: only convert if CellResult.unit is still blank.
            if cell.type == "compute" and not results[cell_id].unit:
                value = round(value * 100, 4)
                row_data[row]["unit"] = "%"

            row_data[row]["values"][col] = value
            if results[cell_id].denomination:
                row_data[row]["denomination"] = results[cell_id].denomination
            if results[cell_id].unit:
                row_data[row]["unit"] = results[cell_id].unit

    return {
        "firm": plan.firm,
        "periods": plan.periods,
        "rows": [row_data[r] for r in sorted(row_data)],
    }
```

## execute_tool branch

From 05_execute_tool.md. Shown here for completeness:

```python
def _exec_pms1(params: dict) -> str:
    firm = params["firm"]
    query = params["query"]
    channel = register(f"PMS1-{firm}")

    stencil = run_pms1_pipeline(firm, query, channel=channel)

    handle = registry.store(stencil, f"{firm} stencil")
    _dump_asset(f"{_sanitize(firm)}_stencil_{handle.lstrip('$')}.json", stencil)

    n_rows = len(stencil.get("rows", []))
    n_periods = len(stencil.get("periods", []))
    return (
        f"PMS1 complete. {firm} stencil stored as {handle}. "
        f"{n_rows} rows, {n_periods} periods."
    )
```

## ToolChannel label

| Context | Label |
|---|---|
| Parallel (via execute_tool) | `"PMS1-{firm}"` e.g. `"PMS1-Best Buy"` |
| Solo / fallback | `"PMS1"` |

Channel is created by execute_tool and passed to the wrapper.
Wrapper uses it for operational prints. Internal PMS1 code
(orchestrator.py, pto.py, stencil.py) uses module-level
`pto_out = register("PMS1")` for its existing diagnostic prints
— generic label, no signature changes to internal functions.

## Internal user interaction

Currently none. PMS1 has no `input()` calls.

Future: "Relax FY filter?" when entity filter returns 0 chunks
(shown in ideal demos across specs). When built, this would use
`channel.input("Relax FY filter? [y/n]")` inside the wrapper or
at the pto_retrieve level. Not specced here — added when the
feature is built.

## Print migration

Three existing `print()` calls migrated to module-level ToolChannel:

```python
# At top of each file:
from src.harness.terminal_router import register
pto_out = register("PMS1")

# orchestrator.py:54
pto_out.print("⚠ SEKEI BATCH↔PLAN CELL VALIDATION NOT BUILT")

# pto.py:832
pto_out.print(f"[pto_judge] batch={request.batch_id} tokens in={inp} out={out}")

# stencil.py:76
pto_out.print("⚠ VALUE RECONCILIATION & GOOD COMPUTE LOGIC NOT BUILT")
```

Generic `[PMS1]` label for these. Fine — they're diagnostics.

## Implementation sketch

```python
"""
tool_pms1.py — PSOAS wrapper for the PMS1 pipeline.
"""
from __future__ import annotations

import threading

from llama_index.core import VectorStoreIndex

from src.harness.terminal_router import register, ToolChannel
from src.config import Config
from src.sekei import sekei
from src.orchestrator import run
from src.stencil import serialize_stencil

_default_channel = register("PMS1")


def run_pms1_pipeline(
    firm: str,
    query: str,
    channel: ToolChannel | None = None,
) -> dict:
    """Run full PMS1 pipeline for one firm. Returns stencil dict."""
    ch = channel or _default_channel

    # ── Load deps ──────────────────────────────────────────
    ch.print("Loading index...")
    config = Config.from_env()
    index = _load_index(config)

    # ── Sekei ──────────────────────────────────────────────
    ch.print("Sekei planning...")
    full_query = f"{firm}: {query}"
    plan = sekei(full_query, config)
    n_cells = len(plan.cells)
    n_batches = len(plan.batches)
    ch.print(f"done. {n_cells} cells, {n_batches} batches.")

    # ── PTO + Stencil ─────────────────────────────────────
    # Note: per-batch progress prints come from internal PMS1 code
    # via module-level pto_out (generic [PMS1] label). The wrapper
    # only prints start/end messages.
    ch.print(f"Running {n_batches} batches...")
    results = run(plan, index, config)

    n_filled = len(results)
    ch.print(f"Stencil computed. {n_filled} cells filled.")

    # ── Serialize ─────────────────────────────────────────
    return serialize_stencil(plan, results)


_index_lock = threading.Lock()
_cached_index: VectorStoreIndex | None = None


def _load_index(config: Config) -> VectorStoreIndex:
    """Load vector store index. Cached after first call for parallel reuse."""
    global _cached_index
    if _cached_index is not None:
        return _cached_index
    with _index_lock:
        if _cached_index is not None:  # double-check after acquiring lock
            return _cached_index
        from src.index_store import load_index
        _cached_index = load_index(config)
        return _cached_index
```

## Dependencies

| Component | What it provides |
|---|---|
| `src/sekei.py` | `sekei()` — query planning |
| `src/orchestrator.py` | `run()` — PTO lanes + stencil eval |
| `src/stencil.py` | `serialize_stencil()` (NEW), `CellResult` |
| `src/config.py` | `Config.from_env()` |
| `src/index_store.py` | `load_index()` |
| `src/harness/terminal_router.py` | `register()`, `ToolChannel` |

## File locations

```
PSOAS/src/tool_pms1.py              wrapper (NEW)
Poony_Multiretrieval_S1/src/stencil.py   serialize_stencil() added (MODIFIED)
```

## Ideal demo

```
[PMS1-Best Buy] Loading index...
[PMS1-Best Buy] Sekei planning...
[PMS1-Best Buy] done. 5 cells, 2 batches.
[PMS1-Best Buy] PTO batch A_124 (FY2022 income_statement)...
[PMS1] ⚠ SEKEI BATCH↔PLAN CELL VALIDATION NOT BUILT
[PMS1] [pto_judge] batch=A_124 tokens in=3200 out=450
[PMS1-Best Buy] PTO batch B_124 (FY2023 income_statement)...
[PMS1] [pto_judge] batch=B_124 tokens in=3100 out=420
[PMS1] ⚠ VALUE RECONCILIATION & GOOD COMPUTE LOGIC NOT BUILT
[PMS1-Best Buy] Stencil computed. 5 cells filled.
```

Note: `[PMS1-Best Buy]` for wrapper prints, `[PMS1]` for internal
diagnostic prints. Both go through TerminalRouter.

## Resolved questions

- **Entry function.** Single `run_pms1_pipeline()` facade that
  calls sekei → run → serialize_stencil internally. execute_tool
  doesn't know PMS1 internals.
- **Stencil output format.** Self-contained dict with firm, periods,
  rows (metric, values, denomination, unit). No cell IDs, no type
  field. PTECA consumes this.
- **serialize_stencil location.** In stencil.py alongside
  format_filled_stencil. Commented as downstream PTECA use.
- **Channel injection depth.** Zero internal signature changes.
  Wrapper holds channel for operational prints. Internal PMS1 code
  uses module-level `register("PMS1")` for diagnostics.
- **Index/config loading.** On first call, no caching abstraction.
  Optimization deferred.
- **Print migration.** 3 existing print() calls → module-level
  pto_out.print(). Generic [PMS1] label. No signature changes.
