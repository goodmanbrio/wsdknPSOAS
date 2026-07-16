"""
sekei.py — Multi-retrieval query planning (設計 = design).

Takes a quantitative multi-line-item query and produces a structured
retrieval plan: which cells to retrieve, which to compute, and how
to batch retrieval calls by co-located financial statement items.

Single Opus 4.8 + thinking call. Output is a SekeiPlan (parsed JSON).

Usage:
    from src.sekei import sekei
    plan = sekei("What were Best Buy's revenue and EPS for FY2021-2023?", config)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.config import Config
from src.llm import get_sekei_llm


# ═════════════════════════════════════════════════════════════════════════
# Reference data (loaded at import from hardcode_dependencies/)
# ═════════════════════════════════════════════════════════════════════════

_HARDCODE_DIR = Path(__file__).resolve().parent.parent / "hardcode_dependencies"

with open(_HARDCODE_DIR / "retrievable_line_items.yaml") as _f:
    _LINE_ITEMS_REF: dict = yaml.safe_load(_f)

_STATEMENT_KEYS: list[str] = list(_LINE_ITEMS_REF.get("statements", {}).keys())


def _format_reference() -> str:
    """Format loaded line items YAML as prompt context string."""
    lines: list[str] = []

    for stmt_key, stmt in _LINE_ITEMS_REF.get("statements", {}).items():
        stmt_name = stmt_key.replace("_", " ").title()
        items = [item["name"] for item in stmt.get("line_items", [])]
        lines.append(f"{stmt_name}: {', '.join(items)}")

    lines.append("")

    lines.append("COMPUTED (require formula, never retrieve directly):")
    for item in _LINE_ITEMS_REF.get("computed_metrics", []):
        components = " + ".join(item.get("components", []))
        lines.append(
            f"  {item['name']}: {item['formula']} "
            f"(needs: {components})"
        )

    lines.append("")

    lines.append("BATCHING (items on same statement can share one retrieval):")
    for group in _LINE_ITEMS_REF.get("batching_guidance", []):
        items = ", ".join(group["items"])
        lines.append(f"  {group['group']}: {items}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
# System prompt
# ═════════════════════════════════════════════════════════════════════════

_SEKEI_SYSTEM_TEMPLATE = """\
You are a financial data retrieval planner. Given a quantitative query \
about a company's financial data, you produce a structured retrieval \
plan specifying exactly what data cells to retrieve from 10-K filings \
and what to compute from those cells.

## Your task

1. Identify the **firm** (single company).
2. Identify the **metrics** requested and the **time periods**.
3. For each metric: decide if it is DIRECTLY RETRIEVABLE from a \
standard financial statement, or if it is COMPUTED from retrievable \
components. Use the reference list below.
4. Build a **stencil** — a grid of rows × period columns. ALL cells \
live in one flat `"cells"` dict with standard [A-Z][0-9]+ IDs. \
Two cell types:
   - `"retrieve"`: a raw line item from a financial statement
   - `"compute"`: derived from other cells via a formula
5. **Row ordering**: computed rows sit IMMEDIATELY BELOW the component \
rows they depend on, like a financial modeller adding a subtotal. \
Example for gross margin query:
   - Row 1: Revenue (retrieve)
   - Row 2: Gross Profit (retrieve)
   - Row 3: Gross Margin (compute, = row2/row1)
   - Row 4: Net Income (retrieve)
   - Row 5: Net Margin (compute, = row4/row1)
Row numbers must be CONSISTENT across all period columns — if row 3 \
is "Gross Margin" in column A, it must be "Gross Margin" in column B.
6. Group ONLY retrieve cells into **batches**. Compute cells must NOT \
appear in any batch.
7. Computed cell formulas must only reference cells with LOWER row \
numbers in the SAME column (no forward refs, no cross-column refs).

## Cell naming convention (STRICT — like Excel)

- Letter = period column: A = first period, B = second, C = third, etc.
- Number = metric row: 1 = first, 2 = second, etc.
- ALL cells use this format — both retrieve AND compute.
- Examples: A1 (retrieve), A3 (compute with formula "A2 / A1")

## Reference: retrievable line items

{reference}

## Output schema (JSON only, no explanation)

```json
{{
  "firm": "<company name>",
  "metrics": ["<retrievable_metric1>", "<retrievable_metric2>"],
  "periods": ["FY20XX", "FY20YY"],
  "cells": {{
    "A1": {{"metric": "<metric1>", "period": "FY20XX", "type": "retrieve", "statement": "<statement_key>"}},
    "A2": {{"metric": "<metric2>", "period": "FY20XX", "type": "retrieve", "statement": "<statement_key>"}},
    "A3": {{"metric": "<derived_label>", "period": "FY20XX", "type": "compute", "formula": "A2 / A1", "result_format": "percentage"}},
    "B1": {{"metric": "<metric1>", "period": "FY20YY", "type": "retrieve", "statement": "<statement_key>"}},
    "B2": {{"metric": "<metric2>", "period": "FY20YY", "type": "retrieve", "statement": "<statement_key>"}},
    "B3": {{"metric": "<derived_label>", "period": "FY20YY", "type": "compute", "formula": "B2 / B1", "result_format": "percentage"}}
  }},
  "batches": [
    {{
      "id": "A_12",
      "cells": ["A1", "A2"],
      "period": "FY20XX",
      "statement": "<statement_key>",
      "retrieve_target": "<filing section name>"
    }}
  ]
}}
```

## Rules

- `"metrics"` lists ONLY the raw retrievable line items (not computed \
labels like "margin").
- There is NO separate `"computed"` section. Compute cells are inline \
in `"cells"` with `"type": "compute"`.
- Every retrieve cell must appear in exactly one batch.
- Compute cells must NOT appear in any batch.
- Batches must NOT mix items from different financial statements.
- Batches CAN mix items from the same statement for the same period.
- Periods: normalize to FY20XX format.
- statement_key MUST be exactly one of: {statement_keys}. \
Do not invent variants like "income_statement_core".
- Output JSON only. No explanation, no markdown fences.
"""


def _build_system_prompt() -> str:
    """Build the Sekei system prompt with embedded reference data."""
    ref = _format_reference()
    keys_str = ", ".join(_STATEMENT_KEYS)
    return _SEKEI_SYSTEM_TEMPLATE.format(reference=ref, statement_keys=keys_str)


# ═════════════════════════════════════════════════════════════════════════
# Output types
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class SekeiCell:
    metric: str
    period: str
    type: str  # "retrieve" or "compute"
    statement: str = ""
    formula: str = ""
    result_format: str = ""


@dataclass
class SekeiBatch:
    id: str
    cells: list[str]
    period: str
    statement: str
    retrieve_target: str = ""


@dataclass
class SekeiPlan:
    firm: str
    metrics: list[str]
    periods: list[str]
    cells: dict[str, SekeiCell]
    batches: list[SekeiBatch]
    raw_json: dict = field(default_factory=dict)
    raw_output: str = ""
    usage: dict = field(default_factory=dict)


# ═════════════════════════════════════════════════════════════════════════
# Parsing
# ═════════════════════════════════════════════════════════════════════════

def _parse_sekei_output(raw: str) -> SekeiPlan:
    """Parse Sekei LLM JSON output into a SekeiPlan."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    parsed = json.loads(text)

    cells: dict[str, SekeiCell] = {}
    for cell_id, cell_data in parsed.get("cells", {}).items():
        cells[cell_id] = SekeiCell(
            metric=cell_data.get("metric", ""),
            period=cell_data.get("period", ""),
            type=cell_data.get("type", "retrieve"),
            statement=cell_data.get("statement", ""),
            formula=cell_data.get("formula", ""),
            result_format=cell_data.get("result_format", ""),
        )

    batches: list[SekeiBatch] = []
    for batch_data in parsed.get("batches", []):
        batches.append(SekeiBatch(
            id=batch_data.get("id", ""),
            cells=batch_data.get("cells", []),
            period=batch_data.get("period", ""),
            statement=batch_data.get("statement", ""),
            retrieve_target=batch_data.get("retrieve_target", ""),
        ))

    return SekeiPlan(
        firm=parsed.get("firm", ""),
        metrics=parsed.get("metrics", []),
        periods=parsed.get("periods", []),
        cells=cells,
        batches=batches,
        raw_json=parsed,
        raw_output=raw,
    )


# ═════════════════════════════════════════════════════════════════════════
# Validation
# ═════════════════════════════════════════════════════════════════════════

def validate_plan(plan: SekeiPlan) -> list[str]:
    """Check structural correctness of a SekeiPlan. Returns list of errors."""
    errors: list[str] = []

    retrieve_cells = {cid for cid, c in plan.cells.items() if c.type == "retrieve"}
    compute_cells = {cid for cid, c in plan.cells.items() if c.type == "compute"}

    # Every retrieve cell must appear in exactly one batch
    batched_cells: set[str] = set()
    for batch in plan.batches:
        for cell_id in batch.cells:
            if cell_id in batched_cells:
                errors.append(f"Cell {cell_id} appears in multiple batches")
            batched_cells.add(cell_id)

    for cell_id in retrieve_cells:
        if cell_id not in batched_cells:
            errors.append(f"Retrieve cell {cell_id} not in any batch")

    # Compute cells must NOT be in any batch
    for cell_id in compute_cells:
        if cell_id in batched_cells:
            errors.append(f"Compute cell {cell_id} should not be in a batch")

    # Batches must not mix statements
    for batch in plan.batches:
        stmts = set()
        for cell_id in batch.cells:
            if cell_id in plan.cells:
                stmts.add(plan.cells[cell_id].statement)
        if len(stmts) > 1:
            errors.append(f"Batch {batch.id} mixes statements: {stmts}")

    # Batches must not mix periods
    for batch in plan.batches:
        periods = set()
        for cell_id in batch.cells:
            if cell_id in plan.cells:
                periods.add(plan.cells[cell_id].period)
        if len(periods) > 1:
            errors.append(f"Batch {batch.id} mixes periods: {periods}")

    # Cell naming format
    for cell_id in plan.cells:
        if not re.match(r"^[A-Z]\d+$", cell_id):
            errors.append(f"Cell ID {cell_id} doesn't match [A-Z][0-9]+ pattern")

    # Computed cell formula refs must exist and have lower row numbers
    for cell_id, cell in plan.cells.items():
        if cell.type != "compute":
            continue
        match = re.match(r"([A-Z])(\d+)", cell_id)
        if not match:
            continue
        col_letter = match.group(1)
        row_num = int(match.group(2))
        refs = re.findall(r"[A-Z]\d+", cell.formula)
        for ref in refs:
            if ref not in plan.cells:
                errors.append(f"Compute cell {cell_id} references unknown cell {ref}")
            else:
                ref_match = re.match(r"([A-Z])(\d+)", ref)
                if ref_match:
                    ref_col = ref_match.group(1)
                    ref_row = int(ref_match.group(2))
                    if ref_col != col_letter:
                        errors.append(
                            f"Compute cell {cell_id} cross-column ref {ref}"
                        )
                    if ref_row >= row_num:
                        errors.append(
                            f"Compute cell {cell_id} forward-refs {ref} (row {ref_row} >= {row_num})"
                        )

    # Row consistency: same row number = same metric across columns
    row_metrics: dict[int, set[str]] = {}
    for cell_id, cell in plan.cells.items():
        match = re.match(r"[A-Z](\d+)", cell_id)
        if match:
            row = int(match.group(1))
            row_metrics.setdefault(row, set()).add(cell.metric)
    for row, metrics in row_metrics.items():
        if len(metrics) > 1:
            errors.append(f"Row {row} has inconsistent metrics: {metrics}")

    return errors


# ═════════════════════════════════════════════════════════════════════════
# Display
# ═════════════════════════════════════════════════════════════════════════

def format_plan(plan: SekeiPlan) -> str:
    """Human-readable stencil display with compute rows inline."""
    lines: list[str] = []
    lines.append(f"Firm: {plan.firm}")
    lines.append(f"Metrics: {plan.metrics}")
    lines.append(f"Periods: {plan.periods}")

    # Stencil grid
    header = [""] + plan.periods
    lines.append("")
    lines.append("  ".join(f"{h:<20}" for h in header))
    lines.append("-" * (21 * (len(plan.periods) + 1)))

    # Group cells by row number
    row_metrics: dict[int, str] = {}
    row_types: dict[int, str] = {}
    grid: dict[tuple[int, int], str] = {}  # (row, col) -> cell_id
    formulas: dict[str, str] = {}  # cell_id -> formula

    for cell_id, cell in plan.cells.items():
        match = re.match(r"([A-Z])(\d+)", cell_id)
        if match:
            col = ord(match.group(1)) - ord("A")
            row = int(match.group(2))
            grid[(row, col)] = cell_id
            row_metrics[row] = cell.metric
            row_types[row] = cell.type
            if cell.formula:
                formulas[cell_id] = cell.formula

    for row_num in sorted(row_metrics):
        metric = row_metrics[row_num]
        is_compute = row_types.get(row_num) == "compute"
        row_cells = []
        for col in range(len(plan.periods)):
            cell_id = grid.get((row_num, col), "")
            if cell_id and cell_id in formulas:
                row_cells.append(f"{cell_id}={formulas[cell_id]:<14}")
            else:
                row_cells.append(f"{cell_id:<20}")
        tag = " [C]" if is_compute else ""
        lines.append(f"{metric:<20}  " + "  ".join(row_cells) + tag)

    # Batches
    lines.append("")
    lines.append(f"Batches ({len(plan.batches)}):")
    for batch in plan.batches:
        lines.append(
            f"  {batch.id}: {batch.cells} | {batch.period} | {batch.statement}"
        )

    # Validation
    errors = validate_plan(plan)
    if errors:
        lines.append("")
        lines.append("VALIDATION ERRORS:")
        for err in errors:
            lines.append(f"  !! {err}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════

def sekei(query: str, config: Config) -> SekeiPlan:
    """Run Sekei planning call. Returns a parsed SekeiPlan."""
    llm = get_sekei_llm(config)
    system_prompt = _build_system_prompt()
    raw, usage = llm.complete_with_usage(query, system_prompt=system_prompt)
    plan = _parse_sekei_output(raw)
    plan.usage = usage
    return plan
