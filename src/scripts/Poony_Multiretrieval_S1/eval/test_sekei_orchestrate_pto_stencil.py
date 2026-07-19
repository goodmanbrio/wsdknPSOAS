#!/usr/bin/env python3
"""
test_sekei_orchestrate_pto_stencil.py — End-to-end pipeline test.

Loads pre-saved Sekei plans, runs orchestrator (PTO retrieval + judge +
stencil eval), compares filled values against ground truth.

Usage:
    cd Poony_Multiretrieval_S1
    python eval/test_sekei_orchestrate_pto_stencil.py              # run all
    python eval/test_sekei_orchestrate_pto_stencil.py --ids d_bby  # run one
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
_psoas_root = _project_root.parent.parent.parent  # PMS1 → scripts → src → PSOAS

if str(_psoas_root) not in sys.path:
    sys.path.insert(0, str(_psoas_root))
if str(_project_root) not in sys.path:
    sys.path.insert(1, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.config import Config
from src.index_store import IndexManager
from src.sekei import (
    SekeiPlan, SekeiCell, SekeiBatch,
    format_plan, validate_plan,
)
from src.stencil import (
    CellResult, compute_stencil, format_filled_stencil, format_sources,
)
from src.orchestrator import run as orchestrator_run
from src.pto import (
    PTOBatchRequest, pto_retrieve, pto_judge, pto_judge_to_stencil,
)

_SAVED_PATH = _project_root / "eval" / "sekei_saved_outputs_for_PTOetStencilTest.json"


def load_saved_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def dict_to_plan(d: dict) -> SekeiPlan:
    """Convert saved sekei_output dict → SekeiPlan."""
    cells: dict[str, SekeiCell] = {}
    for cell_id, cd in d.get("cells", {}).items():
        cells[cell_id] = SekeiCell(
            metric=cd.get("metric", ""),
            period=cd.get("period", ""),
            type=cd.get("type", "retrieve"),
            statement=cd.get("statement", ""),
            formula=cd.get("formula", ""),
            result_format=cd.get("result_format", ""),
        )

    batches: list[SekeiBatch] = []
    for bd in d.get("batches", []):
        batches.append(SekeiBatch(
            id=bd.get("id", ""),
            cells=bd.get("cells", []),
            period=bd.get("period", ""),
            statement=bd.get("statement", ""),
            retrieve_target=bd.get("retrieve_target", ""),
        ))

    return SekeiPlan(
        firm=d.get("firm", ""),
        metrics=d.get("metrics", []),
        periods=d.get("periods", []),
        cells=cells,
        batches=batches,
        raw_json=d,
    )


def run_one(case: dict, index, config: Config) -> dict:
    """Run full pipeline on one saved case. Verbose dump."""
    test_id = case["test_id"]
    query = case["query"]
    plan = dict_to_plan(case["sekei_output"])
    gt = case.get("ground_truth", {})

    print(f"\n{'='*70}")
    print(f"ID: {test_id}")
    print(f"Query: {query}")
    print(f"{'='*70}")

    # ── Show parsed plan ──
    errors = validate_plan(plan)
    print(f"\n--- SEKEI PLAN ---")
    print(format_plan(plan))
    if errors:
        print(f"VALIDATION ERRORS: {errors}")
    print(f"--- END PLAN ---")

    # ── Run each batch manually for verbose dump ──
    all_values: dict[str, CellResult] = {}

    for batch in plan.batches:
        metrics = [plan.cells[cid].metric for cid in batch.cells]
        cell_map = {cid: plan.cells[cid].metric for cid in batch.cells}

        request = PTOBatchRequest(
            batch_id=batch.id,
            firm=plan.firm,
            period=batch.period,
            statement=batch.statement,
            metrics=metrics,
            retrieve_target=batch.retrieve_target,
        )

        print(f"\n{'─'*70}")
        print(f"BATCH: {batch.id}  |  {batch.period}  |  {batch.statement}")
        print(f"Metrics: {metrics}")
        print(f"Cell map: {cell_map}")
        print(f"{'─'*70}")

        # ── PTO retrieval ──
        result = pto_retrieve(request, index, config)

        print(f"\n--- PTO RETRIEVAL ---")
        print(f"  Hard filter survived: top={len(result.top_chunks)} runner_ups={len(result.runner_ups)}")

        ml = result.method_log
        print(f"  HyDE good: {ml.hyde_good[:150]}{'...' if len(ml.hyde_good) > 150 else ''}")
        print(f"  HyDE bad:  {ml.hyde_bad[:150]}{'...' if len(ml.hyde_bad) > 150 else ''}")
        print(f"  Metadata filters: {ml.metadata_filters}")

        # Top chunks
        print(f"\n  TOP CHUNKS ({len(result.top_chunks)}):")
        for i, node in enumerate(result.top_chunks):
            meta = node.node.metadata
            nid = node.node.node_id
            good = result.good_scores.get(nid, 0.0)
            bad = result.bad_scores.get(nid, 0.0)
            print(f"\n    CHUNK #{i}  good={good:.4f}  bad={bad:.4f}  final={node.score:.4f}")
            print(f"    file={meta.get('file_name', '?')}  "
                  f"section={meta.get('section', '?')}  "
                  f"fy={meta.get('fiscal_year', '?')}")
            for line in node.node.text.split("\n"):
                print(f"      {line}")

        # Runner-ups
        print(f"\n  RUNNER-UPS ({len(result.runner_ups)}):")
        for i, ru in enumerate(result.runner_ups):
            good = result.good_scores.get(ru.node_id, 0.0)
            bad = result.bad_scores.get(ru.node_id, 0.0)
            print(f"    #{i}  good={good:.4f}  bad={bad:.4f}  final={ru.score:.4f}  "
                  f"file={ru.file_name}  section={ru.section}  fy={ru.fiscal_year}")
        print(f"--- END PTO RETRIEVAL ---")

        # ── Judge ──
        print(f"\n--- JUDGE ---")
        judge_output = pto_judge(request, result, config)
        print(f"  Raw judge output:")
        print(f"  {json.dumps(judge_output, indent=2)}")

        # ── Parse to stencil ──
        stencil_result = pto_judge_to_stencil(judge_output, cell_map, result)
        print(f"\n  Parsed cells:")
        for cell_id, pcr in stencil_result.values.items():
            print(f"    {cell_id}: value={pcr.value}  denom={pcr.denomination}  "
                  f"unit={pcr.unit}  source={pcr.source[:20]}...")
        # Convert PTOCellResult → CellResult
        for cell_id, pcr in stencil_result.values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )
        if stencil_result.failures:
            failed = ", ".join(f"{m} ({cid})" for cid, m in stencil_result.failures)
            print(f"  PARSE FAILURES: {failed}")
        print(f"--- END JUDGE ---")

    # ── Stencil eval ──
    print(f"\n--- STENCIL EVAL ---")
    try:
        filled = compute_stencil(plan, all_values)
        print(format_filled_stencil(plan, filled))
        print()
        print(format_sources(plan, filled))
    except ValueError as exc:
        print(f"  EVAL ERROR: {exc}")
        filled = all_values
    print(f"--- END STENCIL ---")

    # ── Ground truth comparison ──
    print(f"\n--- GROUND TRUTH CHECK ---")
    n_checked = 0
    n_pass = 0
    for cell_id, gt_entry in gt.items():
        gt_val = gt_entry["value"]
        if cell_id not in filled:
            print(f"  MISS  {cell_id}: expected={gt_val}, not in filled stencil")
            n_checked += 1
            continue

        actual = filled[cell_id].value
        # For compute cells (ratios), tolerance 0.001. For retrieve, exact match.
        if isinstance(gt_val, float) and gt_val < 1:
            diff = abs(actual - gt_val)
            ok = diff < 0.001
        else:
            ok = abs(actual - gt_val) < 0.5  # allow rounding

        status = "PASS" if ok else "FAIL"
        display = gt_entry.get("display", "")
        n_checked += 1
        if ok:
            n_pass += 1
        print(f"  {status}  {cell_id}: actual={actual}  expected={gt_val}  {display}")
    print(f"\n  {n_pass}/{n_checked} values correct")
    print(f"--- END GROUND TRUTH ---")

    return {"id": test_id, "n_checked": n_checked, "n_pass": n_pass}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", help="Run specific test case IDs")
    args = parser.parse_args()

    print("Loading config and building index...")
    config = Config.from_env()
    config.validate()

    mgr = IndexManager(config)
    index = mgr.load_or_build()

    all_cases = load_saved_cases(_SAVED_PATH)

    if args.ids:
        cases = [c for c in all_cases if c["test_id"] in args.ids]
    else:
        cases = all_cases

    if not cases:
        print("No matching test cases found.")
        sys.exit(1)

    print(f"Running {len(cases)} e2e test case(s)...\n")

    results = []
    for case in cases:
        r = run_one(case, index, config)
        results.append(r)

    # ── Summary ──
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    total_checked = 0
    total_pass = 0
    for r in results:
        total_checked += r["n_checked"]
        total_pass += r["n_pass"]
        print(f"  {r['id']}: {r['n_pass']}/{r['n_checked']}")
    print(f"\n  TOTAL: {total_pass}/{total_checked} values correct")


if __name__ == "__main__":
    main()
