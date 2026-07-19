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
from src.harness.sysprompts import load_sysprompt
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



def _build_system_prompt(config: Config) -> str:
    """Build the Sekei system prompt with embedded reference data."""
    ref = _format_reference()
    keys_str = ", ".join(_STATEMENT_KEYS)
    return load_sysprompt("sekei", config.sekei_profile,
                          retrievable_line_items=ref,
                          statement_keys=keys_str)


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
    system_prompt = _build_system_prompt(config)
    raw, usage = llm.complete_with_usage(query, system_prompt=system_prompt, label="sekei")
    plan = _parse_sekei_output(raw)
    plan.usage = usage
    return plan
