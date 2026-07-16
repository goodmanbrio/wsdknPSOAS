#!/usr/bin/env python3
"""
run_sekei_eval.py — Run Sekei on test cases and print results for eyeballing.

All output goes to both stdout and a log file (eval/sekei_eval_results.md).
Run here or in a separate shell — the log file is the source of truth.

Usage:
    python eval/run_sekei_eval.py                    # run all 10
    python eval/run_sekei_eval.py --ids a_bby d_bby  # run specific cases
    python eval/run_sekei_eval.py --cat a             # run category a only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.config import Config
from src.sekei import sekei, format_plan, validate_plan
from src.stencil import (
    CellResult, compute_stencil, format_filled_stencil, format_sources,
)


_LOG_PATH = _project_root / "eval" / "sekei_eval_results.md"


class Tee:
    """Write to both stdout and a file."""
    def __init__(self, path: Path):
        self._file = open(path, "w", encoding="utf-8")
        self._stdout = sys.stdout

    def write(self, text):
        self._stdout.write(text)
        self._file.write(text)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()


def load_test_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)


def run_one(case: dict, config: Config) -> dict:
    """Run Sekei on a single test case, return results dict."""
    query = case["query"].strip()
    print(f"\n{'='*70}")
    print(f"ID: {case['id']}  |  Category: {case['category']}  |  {case['company']}")
    print(f"Query: {query}")
    print(f"{'='*70}")

    try:
        plan = sekei(query, config)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return {"id": case["id"], "error": str(exc)}

    # ── RAW LLM OUTPUT (what Sekei actually generated) ──
    print("\n--- RAW LLM OUTPUT ---")
    print(plan.raw_output)
    print("--- END RAW ---")

    # ── TOKEN USAGE + COST ──
    usage = plan.usage
    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        # Opus 4.8 pricing: $15/MTok input, $75/MTok output
        cost_input = inp * 15 / 1_000_000
        cost_output = out * 75 / 1_000_000
        cost_total = cost_input + cost_output
        print(f"\n--- TOKENS ---")
        print(f"  Input:  {inp:,}")
        print(f"  Output: {out:,} (includes thinking)")
        print(f"  Cost:   ${cost_total:.4f} (in=${cost_input:.4f} out=${cost_output:.4f})")
        print(f"--- END TOKENS ---")
    else:
        print("\n--- TOKENS: unavailable ---")

    # ── PARSED STENCIL (what the parser made of it) ──
    print("\n--- PARSED STENCIL ---")
    print(format_plan(plan))
    print("--- END PARSED ---")

    # Validation
    errors = validate_plan(plan)

    # Compare structure to expected
    expected_metrics = set(case.get("expected_stencil", {}).get("metrics", []))
    actual_metrics = set(plan.metrics)
    expected_periods = set(case.get("expected_stencil", {}).get("periods", []))
    actual_periods = set(plan.periods)
    expected_n_batches = len(case.get("expected_batches", []))
    actual_n_batches = len(plan.batches)

    print(f"\n--- CHECKS ---")
    print(f"  Firm:       {plan.firm}")
    print(f"  Metrics:    expected={sorted(expected_metrics)}")
    print(f"              got=    {sorted(actual_metrics)}")
    print(f"  Periods:    expected={sorted(expected_periods)}")
    print(f"              got=    {sorted(actual_periods)}")
    n_retrieve = sum(1 for c in plan.cells.values() if c.type == "retrieve")
    n_compute = sum(1 for c in plan.cells.values() if c.type == "compute")

    print(f"  Batches:    expected={expected_n_batches}, got={actual_n_batches}")
    print(f"  Cells:      {n_retrieve} retrieve, {n_compute} compute (inline)")
    print(f"  Validation: {errors if errors else 'CLEAN'}")

    if case["category"] == "d" and n_compute == 0:
        print("  WARNING: category d expects compute cells but got none!")

    # ── COMPUTE CHECK — if plan has compute cells and we have ground truth ──
    compute_ok = None
    if n_compute > 0:
        gt = case.get("ground_truth", {})

        # Build lookup: (metric_lower, period) → ground truth value + full entry
        gt_by_metric: dict[tuple[str, str], float] = {}
        gt_by_metric_full: dict[tuple[str, str], dict] = {}
        expected_cells = case.get("expected_stencil", {}).get("cells", {})
        for gt_key, gt_val in gt.items():
            if not isinstance(gt_val, dict) or "value" not in gt_val:
                continue
            if gt_key in expected_cells:
                spec = expected_cells[gt_key]
                key = (spec["metric"].lower(), spec["period"])
                gt_by_metric[key] = gt_val["value"]
                gt_by_metric_full[key] = gt_val

        # Match Sekei's retrieve cells to ground truth by metric+period
        retrieve_values: dict[str, CellResult] = {}
        for cell_id, cell in plan.cells.items():
            if cell.type != "retrieve":
                continue
            metric_lower = cell.metric.lower()
            synonyms = [
                metric_lower,
                metric_lower.replace("revenue", "net sales"),
                metric_lower.replace("net sales", "revenue"),
                metric_lower.replace("net income", "net earnings"),
                metric_lower.replace("net earnings", "net income"),
            ]
            for syn in synonyms:
                key = (syn, cell.period)
                if key in gt_by_metric:
                    # Build dummy source from YAML ground truth
                    gt_entry = gt_by_metric_full.get(key, {})
                    source = gt_entry.get("source", "unknown")
                    retrieve_values[cell_id] = CellResult(
                        value=gt_by_metric[key],
                        source=source,
                    )
                    break

        if retrieve_values:
            try:
                result = compute_stencil(plan, retrieve_values)
                print(f"\n--- FILLED STENCIL ---")
                print(format_filled_stencil(plan, result))
                print(f"--- END FILLED ---")

                print(f"\n--- SOURCES ---")
                print(format_sources(plan, result))
                print(f"--- END SOURCES ---")

                # Verify computed values against ground truth
                gt_computed = {
                    k: v for k, v in gt.items()
                    if isinstance(v, dict) and "value" in v and "display" in v
                }
                if gt_computed:
                    print(f"\n--- COMPUTE CHECKS ---")
                    for cell_id, cell in plan.cells.items():
                        if cell.type != "compute" or cell_id not in result:
                            continue
                        actual = result[cell_id].value
                        matched = False
                        for gt_key, gt_val in gt_computed.items():
                            gt_key_lower = gt_key.lower().replace("_", " ")
                            cell_metric_lower = cell.metric.lower()
                            if (cell.period.lower().replace("fy", "fy") in gt_key_lower
                                    and any(w in gt_key_lower for w in cell_metric_lower.split()[:2])):
                                expected = gt_val["value"]
                                diff = abs(actual - expected)
                                status = "OK" if diff < 0.001 else f"MISMATCH (expected={expected:.4f})"
                                display = gt_val.get("display", "")
                                print(f"  {cell_id} {cell.metric}: {actual:.4f} vs {display} -> {status}")
                                matched = True
                                break
                        if not matched:
                            print(f"  {cell_id} {cell.metric}: {actual:.4f} (no GT to compare)")
                    print(f"--- END COMPUTE CHECKS ---")

                compute_ok = True
            except ValueError as exc:
                print(f"\n--- COMPUTE ERROR: {exc} ---")
                compute_ok = False

    return {
        "id": case["id"],
        "firm": plan.firm,
        "metrics_match": expected_metrics == actual_metrics,
        "periods_match": expected_periods == actual_periods,
        "n_batches": actual_n_batches,
        "n_cells": n_retrieve,
        "n_computed": n_compute,
        "validation_errors": errors,
        "compute_ok": compute_ok,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", help="Run specific test case IDs")
    parser.add_argument("--cat", type=str, help="Run all cases in a category (a-e)")
    parser.add_argument("--model", type=str, default=None, help="Override sekei LLM profile")
    args = parser.parse_args()

    # Tee output to log file
    tee = Tee(_LOG_PATH)
    sys.stdout = tee

    config = Config.from_env()
    if args.model:
        config.sekei_profile = args.model
    config.validate()

    cases_path = _project_root / "eval" / "sekei_test_cases.yaml"
    all_cases = load_test_cases(cases_path)

    if args.ids:
        cases = [c for c in all_cases if c["id"] in args.ids]
    elif args.cat:
        cases = [c for c in all_cases if c["category"] == args.cat]
    else:
        cases = all_cases

    if not cases:
        print("No matching test cases found.")
        sys.exit(1)

    print(f"# Sekei Eval — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Model: {config.sekei_profile}")
    print(f"Cases: {len(cases)}")

    results = []
    for case in cases:
        result = run_one(case, config)
        results.append(result)

    # Summary table
    print(f"\n{'='*70}")
    print("## SUMMARY")
    print(f"{'='*70}")
    print(f"{'ID':<12} {'STATUS':<6} {'CELLS':>5} {'COMP':>4} {'BATCH':>5}  METRICS  PERIODS")
    print(f"{'-'*12} {'-'*6} {'-'*5} {'-'*4} {'-'*5}  {'-'*7}  {'-'*7}")
    for r in results:
        if r.get("error"):
            status = "ERROR"
        elif r.get("validation_errors"):
            status = "FAIL"
        else:
            status = "PASS"
        print(
            f"{r['id']:<12} {status:<6} "
            f"{r.get('n_cells', '?'):>5} "
            f"{r.get('n_computed', '?'):>4} "
            f"{r.get('n_batches', '?'):>5}  "
            f"{'Y' if r.get('metrics_match') else 'N':<7}  "
            f"{'Y' if r.get('periods_match') else 'N'}"
        )
        if r.get("validation_errors"):
            for err in r["validation_errors"]:
                print(f"  !! {err}")

    print(f"\nLog written to: {_LOG_PATH}")

    sys.stdout = tee._stdout
    tee.close()


if __name__ == "__main__":
    main()
