"""Topological sort, formula rewrite, and stencil structure assignment.

Pure Python. No LLM. Handles:
- _parse_formula_refs: extract metric names from {MetricName} syntax
- _topo_sort_firm / _topo_sort_rows: Kahn's per-firm
- _assign_structure: row numbers, Rn formulas, cell IDs, feeds_rows,
  ans/work/job stencils
"""

import json
import re
from collections import defaultdict, deque
from pathlib import Path


def _parse_formula_refs(formula: str) -> list[str]:
    """Extract metric names from {MetricName} or {MetricName}[-k] syntax.

    Cross-column offset is stripped -- dependency is row-level.
    "{Revenue}[-1]" -> "Revenue" (same row dep as "{Revenue}").
    """
    return re.findall(r"\{([^}]+)\}", formula)


def _topo_sort_firm(firm_rows: list[dict], firm: str) -> list[dict]:
    """Topological sort for one firm's rows.

    Returns rows in sorted order. Raises ValueError if cycle,
    duplicate metric names within firm, or compute row missing
    formula.
    """
    # Guard: duplicate metric names within firm
    names = [r["metric"] for r in firm_rows]
    dupes = [n for n in names if names.count(n) > 1]
    if dupes:
        raise ValueError(
            f"Duplicate metrics for {firm}: {set(dupes)}. "
            f"Each metric must be unique within a firm."
        )

    # Guard: compute row missing formula
    for row in firm_rows:
        if row["type"] == "compute" and not row.get("formula"):
            raise ValueError(
                f"Compute row '{row['metric']}' ({firm}) has no formula."
            )

    name_to_row = {r["metric"]: r for r in firm_rows}

    # Build adjacency: metric -> [metrics it depends on]
    deps: dict[str, list[str]] = {}
    for row in firm_rows:
        formula = row.get("formula", "")
        refs = _parse_formula_refs(formula) if formula else []
        # Validate all refs exist within this firm
        for ref in refs:
            if ref not in name_to_row:
                raise ValueError(
                    f"Formula for '{row['metric']}' ({firm}) "
                    f"references unknown metric '{ref}'"
                )
        deps[row["metric"]] = refs

    # Kahn's algorithm
    in_degree = {m: 0 for m in name_to_row}
    reverse_adj: dict[str, list[str]] = defaultdict(list)
    for metric, dep_list in deps.items():
        in_degree[metric] = len(dep_list)
        for d in dep_list:
            reverse_adj[d].append(metric)

    queue = deque(m for m, deg in in_degree.items() if deg == 0)
    sorted_names: list[str] = []

    while queue:
        m = queue.popleft()
        sorted_names.append(m)
        for consumer in reverse_adj[m]:
            in_degree[consumer] -= 1
            if in_degree[consumer] == 0:
                queue.append(consumer)

    if len(sorted_names) != len(firm_rows):
        raise ValueError(f"Circular dependency in {firm} formulas")

    return [name_to_row[m] for m in sorted_names]


def _topo_sort_rows(raw_rows: list[dict], firms: list[str]) -> list[dict]:
    """Topo sort all rows, per-firm independently, concatenate.

    Firm order in output follows `firms` list order.
    Within each firm, topo order guaranteed.
    """
    sorted_all = []
    for firm in firms:
        firm_rows = [r for r in raw_rows if r["firm"] == firm]
        sorted_all.extend(_topo_sort_firm(firm_rows, firm))
    return sorted_all


def _assign_structure(
    sorted_rows: list[dict],
    periods: list[str],
    firms: list[str],
) -> tuple[dict, dict, list[dict]]:
    """Assign row numbers, rewrite formulas, build stencils.

    Rows are firm-specific (each has a 'firm' field). Job stencils
    are filtered subsets of the work stencil, not deep copies.
    ans determination uses Sekei's `ans: true` tags directly.

    Returns (work_stencil, ans_stencil, job_stencils).
    """
    # Guard: every row has a valid firm field
    for row in sorted_rows:
        if row.get("firm") not in firms:
            raise ValueError(
                f"Row '{row['metric']}' has firm '{row.get('firm')}' "
                f"not in firms list {firms}."
            )

    if len(periods) > 26:
        raise ValueError(
            f"Too many periods ({len(periods)}). Max 26 "
            f"(single-letter column IDs A-Z)."
        )
    col_letters = [chr(ord("A") + i) for i in range(len(periods))]

    # (firm, metric) -> row_number
    fm_to_rownum: dict[tuple[str, str], int] = {}

    # Step 1: assign row numbers (1-indexed, global across all firms)
    for i, row in enumerate(sorted_rows, start=1):
        fm_to_rownum[(row["firm"], row["metric"])] = i

    # Step 2: rewrite formulas + assign feeds_rows
    work_rows = {}
    for i, row in enumerate(sorted_rows, start=1):
        firm = row["firm"]
        entry = {
            "metric": row["metric"],
            "firm": firm,
            "type": row["type"],
            "timeframe": row["timeframe"],
            "unit": row["unit"],
        }

        if row.get("ans"):
            entry["ans"] = True
        else:
            # Helper-only: find ALL consumers WITHIN SAME FIRM
            consumers = [
                fm_to_rownum[(r["firm"], r["metric"])]
                for r in sorted_rows
                if r["firm"] == firm
                and row["metric"] in _parse_formula_refs(
                    r.get("formula", "")
                )
            ]
            if consumers:
                entry["feeds_rows"] = sorted(consumers)

        if row.get("formula"):
            # Rewrite {MetricName} -> Rn (scoped to same firm)
            # Sort refs longest-first to avoid substring collision
            formula = row["formula"]
            refs = _parse_formula_refs(row["formula"])
            refs_sorted = sorted(refs, key=len, reverse=True)
            for ref_name in refs_sorted:
                rn = fm_to_rownum[(firm, ref_name)]
                formula = formula.replace(f"{{{ref_name}}}", f"R{rn}")
            entry["formula"] = formula

        work_rows[str(i)] = entry

    # Step 2b: Guard: unit not in canonical set
    units_path = (
        Path(__file__).resolve().parent
        / "hardcode_dependencies" / "units.json"
    )
    valid_units = json.loads(units_path.read_text())
    for row_num, row in work_rows.items():
        if row["unit"] not in valid_units:
            raise ValueError(
                f"Row {row_num} ({row['metric']}): "
                f"unit '{row['unit']}' not in {valid_units}."
            )

    # Step 3: build values map (all null)
    values = {}
    for col in col_letters:
        for row_num in work_rows:
            values[f"{col}{row_num}"] = None

    work_stencil = {
        "firms": firms,
        "periods": periods,
        "col_letters": col_letters,
        "rows": work_rows,
        "values": values,
        "sources": {},
    }

    # Step 4: build ans stencil (row_mapping is per-firm)
    row_mapping = {}
    for row_num, row in work_rows.items():
        if row.get("ans"):
            firm = row["firm"]
            row_mapping.setdefault(firm, {})[row["metric"]] = int(row_num)

    ans_stencil = {
        "metrics": list(dict.fromkeys(
            row["metric"] for row in sorted_rows if row.get("ans")
        )),
        "periods": periods,
        "firms": firms,
        "col_letters": col_letters,
        "row_mapping": row_mapping,
        "filled": {},
    }

    # Step 5: dry-run formula syntax
    from src.scripts.PMS2.stencil_safe_math import _safe_math_eval
    for row_num, row in work_rows.items():
        formula = row.get("formula")
        if not formula:
            continue
        test_expr = re.sub(r"R\d+(\[[+-]?\d+\])?", "1.0", formula)
        try:
            _safe_math_eval(test_expr)
        except (ValueError, ZeroDivisionError) as e:
            raise ValueError(
                f"Bad formula syntax in row {row_num} "
                f"({row['metric']}): '{formula}' -- {e}"
            )

    # Step 6: split into job stencils (filter by firm, not deep copy)
    job_stencils = []
    for firm in firms:
        firm_rows = {
            rn: row for rn, row in work_rows.items()
            if row["firm"] == firm
        }
        firm_values = {
            cid: v for cid, v in values.items()
            if cid.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") in firm_rows
        }
        job_stencils.append({
            "firm": firm,
            "periods": periods,
            "col_letters": col_letters,
            "rows": firm_rows,
            "values": firm_values,
            "sources": {},
        })

    return work_stencil, ans_stencil, job_stencils
