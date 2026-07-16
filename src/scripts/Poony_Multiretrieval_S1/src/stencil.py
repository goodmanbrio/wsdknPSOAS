"""
stencil.py — Stencil computation, display, and value reconciliation.

Takes a SekeiPlan + retrieved CellResults, computes formula cells,
formats for display. Future home for denomination/unit reconciliation.

Usage:
    from src.stencil import compute_stencil, CellResult
    filled = compute_stencil(plan, values)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.sekei import SekeiPlan
from src.harness.terminal_router import register

_pto_out = register("PMS1")


# ═════════════════════════════════════════════════════════════════════════
# Cell result type
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class CellResult:
    """A filled cell: numeric value + provenance."""
    value: float
    source: str               # retrieve: node_id (index.docstore.docs[source].text for chunk), compute: formula string
    denomination: str = ""    # "k","10k","100k","mn","10mn","100mn","bn","10bn","100bn","unit". Blank for compute cells.
    unit: str = ""            # "USD","RMB","JPY","GBP","EUR","%","count","none". Blank for compute cells.


# ═════════════════════════════════════════════════════════════════════════
# Stencil computation (deterministic — no LLM)
# ═════════════════════════════════════════════════════════════════════════

def compute_stencil(
    plan: SekeiPlan,
    values: dict[str, CellResult],
) -> dict[str, CellResult]:
    """Fill a stencil with retrieved values and compute formula cells.

    Args:
        plan: A SekeiPlan with cells (retrieve + compute).
        values: Map of retrieve cell IDs → CellResult (value + source).

    Returns:
        All cell IDs → CellResult, including computed results.
        Computed cells get source = their formula string.

    Raises:
        ValueError: if a retrieve cell is missing from values.
    """
    resolved: dict[str, CellResult] = {}

    # Check all retrieve cells are provided
    for cell_id, cell in plan.cells.items():
        if cell.type == "retrieve":
            if cell_id not in values:
                raise ValueError(f"Missing value for retrieve cell {cell_id}")
            resolved[cell_id] = values[cell_id]

    # Compute cells in row order
    compute_cells = sorted(
        [(cid, c) for cid, c in plan.cells.items() if c.type == "compute"],
        key=lambda x: int(re.search(r"\d+", x[0]).group()),
    )

    # WARNING: value reconciliation not built. Compute cells naively eval
    # raw floats without checking denomination/unit compatibility of components.
    # e.g. Revenue(mn) / Revenue(k) would silently give wrong result.
    # TODO: before eval, check component denominations match. If same currency
    # but different denomination, multiply to reconcile. If different currencies,
    # flag and refuse to compute. Infer result denomination/unit from formula
    # operator (division → "unit"/"%"/"none", addition → inherit from components).
    _pto_out.print("⚠ VALUE RECONCILIATION & GOOD COMPUTE LOGIC NOT BUILT")

    for cell_id, cell in compute_cells:
        expr = cell.formula
        # Substitute cell refs with resolved values (longest IDs first to
        # avoid partial matches like A1 matching inside A10)
        for ref in sorted(resolved, key=len, reverse=True):
            expr = expr.replace(ref, str(resolved[ref].value))
        try:
            computed_val = eval(expr)  # noqa: S307
        except Exception as exc:
            raise ValueError(
                f"Failed to eval {cell_id} = {cell.formula} → {expr}: {exc}"
            )
        resolved[cell_id] = CellResult(
            value=computed_val,
            source=cell.formula,
        )

    return resolved


# ═════════════════════════════════════════════════════════════════════════
# Display
# ═════════════════════════════════════════════════════════════════════════

def format_filled_stencil(
    plan: SekeiPlan,
    results: dict[str, CellResult],
) -> str:
    """Render stencil grid with numeric values filled in."""
    lines: list[str] = []

    header = [""] + plan.periods
    lines.append("  ".join(f"{h:>16}" for h in header))
    lines.append("-" * (17 * (len(plan.periods) + 1)))

    row_metrics: dict[int, str] = {}
    row_types: dict[int, str] = {}
    grid: dict[tuple[int, int], str] = {}

    for cell_id, cell in plan.cells.items():
        match = re.match(r"([A-Z])(\d+)", cell_id)
        if match:
            col = ord(match.group(1)) - ord("A")
            row = int(match.group(2))
            grid[(row, col)] = cell_id
            row_metrics[row] = cell.metric
            row_types[row] = cell.type

    for row_num in sorted(row_metrics):
        metric = row_metrics[row_num]
        is_compute = row_types.get(row_num) == "compute"
        row_vals = []
        for col in range(len(plan.periods)):
            cell_id = grid.get((row_num, col), "")
            if cell_id and cell_id in results:
                val = results[cell_id].value
                if is_compute:
                    row_vals.append(f"{val:>15.2%}")
                elif abs(val) >= 1:
                    row_vals.append(f"{val:>16,.0f}")
                else:
                    row_vals.append(f"{val:>16.4f}")
            else:
                row_vals.append(f"{'—':>16}")
        tag = " *" if is_compute else ""
        lines.append(f"{metric:<16}  " + "  ".join(row_vals) + tag)

    return "\n".join(lines)


def format_sources(
    plan: SekeiPlan,
    results: dict[str, CellResult],
) -> str:
    """Render per-cell source trace."""
    lines: list[str] = []

    for cell_id in sorted(results, key=lambda c: (c[0], int(c[1:]))):
        cell = plan.cells[cell_id]
        r = results[cell_id]
        is_compute = cell.type == "compute"

        if is_compute:
            val_str = f"{r.value:.2%}"
        elif abs(r.value) >= 1:
            val_str = f"{r.value:,.0f}"
        else:
            val_str = f"{r.value:.4f}"

        label = f"{cell_id} {cell.metric} {cell.period}:"
        lines.append(f"  {label:<40} {val_str:>12}  <- {r.source}")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════
# Serialization (for downstream PTECA consumption)
# ═════════════════════════════════════════════════════════════════════════

def serialize_stencil(
    plan: SekeiPlan,
    results: dict[str, CellResult],
) -> dict:
    """Serialize stencil to a self-contained dict for downstream PTECA use.

    Combines SekeiPlan metadata (firm, periods, metric names) with
    filled CellResult values into one JSON-serializable dict.
    """
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
