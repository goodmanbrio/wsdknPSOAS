"""Phase 2: merge job values + compute formula cells.

T0 scope: _merge_jobs_into_work, _compute_formula_cells.
M3a: _serialize_to_display_stencils added (pure function, needed
by pipeline return before Phase 2 exists).
M5: _fill_ans_stencil, _persist_phase2, run_phase2.
"""

import json
import re
from pathlib import Path

from src.scripts.PMS2.stencil_safe_math import _safe_math_eval

_DIVERGENCE_THRESHOLD = 0.02  # 2%


def _merge_jobs_into_work(
    work_stencil: dict,
    job_results: list[dict],
) -> None:
    """Copy filled values + sources from job stencils into work stencil.

    Job stencils are per-firm subsets with disjoint cell IDs.
    No collisions. Mutates work_stencil in place.
    """
    for job in job_results:
        for cell_id, value in job["values"].items():
            if value is not None:
                work_stencil["values"][cell_id] = value
        for cell_id, source in job.get("sources", {}).items():
            work_stencil["sources"][cell_id] = source


def _compute_formula_cells(work_stencil: dict, channel=None) -> None:
    """Fill compute cells whose value is still None.

    Iterates rows in ascending row number order. Topo sort
    guarantees ascending = dependency order.

    Leng-direct values not overwritten. Divergence check if
    formula also computable.
    """
    rows = work_stencil["rows"]
    values = work_stencil["values"]
    col_letters = work_stencil["col_letters"]

    for row_num, row in sorted(rows.items(), key=lambda x: int(x[0])):
        if row.get("type") != "compute":
            continue
        formula_template = row.get("formula", "")

        for col in col_letters:
            cell_id = f"{col}{row_num}"
            leng_direct = values.get(cell_id)

            # Try computing from formula regardless
            expr = formula_template
            can_compute = True
            col_idx = col_letters.index(col)

            def _resolve_ref(m):
                nonlocal can_compute
                rn = m.group(1)
                offset_str = m.group(2)
                if offset_str:
                    offset = int(offset_str.strip("[]"))
                    target_idx = col_idx + offset
                    if target_idx < 0 or target_idx >= len(col_letters):
                        can_compute = False
                        return "0"
                    ref_cell = f"{col_letters[target_idx]}{rn}"
                else:
                    ref_cell = f"{col}{rn}"
                ref_val = values.get(ref_cell)
                if ref_val is None:
                    can_compute = False
                    return "0"
                return str(ref_val)

            expr = re.sub(
                r"R(\d+)(\[[+-]?\d+\])?", _resolve_ref, formula_template
            )

            computed = None
            if can_compute:
                try:
                    computed = _safe_math_eval(expr)
                except ZeroDivisionError:
                    metric = row.get("metric", "?")
                    firm = row.get("firm", "?")
                    msg = (
                        f"⚠ {cell_id} ({metric} {firm}): "
                        f"division by zero in {formula_template} "
                        f"-- likely bad extraction upstream"
                    )
                    if channel:
                        channel.print(msg)
                    else:
                        print(msg)
                except ValueError:
                    pass

            if leng_direct is not None:
                # Leng-direct wins. Check divergence if formula
                # is also computable.
                if computed is not None and leng_direct != 0:
                    pct_diff = abs(computed - leng_direct) / abs(leng_direct)
                    if pct_diff > _DIVERGENCE_THRESHOLD:
                        metric = row.get("metric", "?")
                        firm = row.get("firm", "?")
                        msg = (
                            f"⚠ {cell_id} ({metric} {firm}): "
                            f"Leng-direct={leng_direct}, "
                            f"formula {formula_template}={computed}, "
                            f"divergence={pct_diff:.0%}"
                        )
                        if channel:
                            channel.print(msg)
                        else:
                            print(msg)
                continue  # Leng-direct wins either way

            if computed is not None:
                values[cell_id] = computed
                work_stencil["sources"][cell_id] = f"formula:{formula_template}"


def _serialize_to_display_stencils(
    work_stencil: dict,
) -> list[dict]:
    """Extract per-firm display stencils from work stencil.

    Only ans-marked rows included. One display stencil per firm.
    """
    firms = work_stencil["firms"]
    display_stencils = []

    for firm in firms:
        rows_out = []
        for row_num, row in sorted(
            work_stencil["rows"].items(),
            key=lambda x: int(x[0]),
        ):
            if row["firm"] != firm or not row.get("ans"):
                continue
            values_list = []
            for col in work_stencil["col_letters"]:
                cell_id = f"{col}{row_num}"
                values_list.append(work_stencil["values"].get(cell_id))
            rows_out.append({
                "metric": row["metric"],
                "values": values_list,
                "unit": row.get("unit", ""),
            })
        display_stencils.append({
            "firm": firm,
            "periods": work_stencil["periods"],
            "rows": rows_out,
        })

    return display_stencils


def _fill_ans_stencil(
    work_stencil: dict, ans_stencil: dict,
) -> dict:
    """Extract ans-marked values from work stencil into ans stencil.

    Uses work stencil cell IDs directly — no ID translation.
    """
    row_mapping = ans_stencil["row_mapping"]  # {firm: {metric: row_num}}
    col_letters = ans_stencil["col_letters"]

    for firm, firm_rows in row_mapping.items():
        ans_stencil["filled"][firm] = {}
        for metric, row_num in firm_rows.items():
            for col in col_letters:
                cell_id = f"{col}{row_num}"
                ans_stencil["filled"][firm][cell_id] = (
                    work_stencil["values"].get(cell_id)
                )

    return ans_stencil


def _persist_phase2(
    session_dir: Path,
    work_stencil: dict,
    ans_stencil: dict,
) -> None:
    """Write work + ans stencils to disk. Job stencils are ephemeral."""
    pms2_dir = session_dir / "pms2"
    pms2_dir.mkdir(parents=True, exist_ok=True)
    (pms2_dir / "work_stencil.json").write_text(
        json.dumps(work_stencil, indent=2)
    )
    (pms2_dir / "ans_stencil.json").write_text(
        json.dumps(ans_stencil, indent=2)
    )


def run_phase2(
    work_stencil: dict,
    ans_stencil: dict,
    completed_jobs: list[dict],
    session_dir: Path,
    channel=None,
) -> list[dict]:
    """Phase 2: merge → compute → fill ans → persist → serialize.

    Returns display stencils (one per firm, ans rows only).
    """
    _merge_jobs_into_work(work_stencil, completed_jobs)
    _compute_formula_cells(work_stencil, channel)
    _fill_ans_stencil(work_stencil, ans_stencil)
    _persist_phase2(session_dir, work_stencil, ans_stencil)
    return _serialize_to_display_stencils(work_stencil)
