#!/usr/bin/env python3
"""
test_pumba_cli.py — Run PUMBA through the PSOAS CLI harness.

Uses the harness terminal_router (Rich styled output, interactive
ch.input() prompts) so you see the full [PUMBA-Best Buy] experience.
Bypasses PSOAS agent loop and Sekei — goes straight to run_pumba
with a hand-built PTOBatchRequest.

Usage (from PSOAS root):
    python test_pumba_cli.py                          # default: BBY Revenue FY2023
    python test_pumba_cli.py --case e_bby_income      # from pto_test_cases.yaml
    python test_pumba_cli.py --case a_bby_income      # multi-metric
    python test_pumba_cli.py --firm "Best Buy" --period FY2023 \\
        --statement income_statement --metrics Revenue "Cost of Sales" \\
        --target "Consolidated Statements of Earnings"
    python test_pumba_cli.py --list                    # show available cases
    python test_pumba_cli.py --case e_bby_income --judge  # also run judge

When PUMBA exhausts or hits max turns, you'll get an interactive
prompt (skip/exit) — same as production. Type your answer.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

# ── Path setup (PSOAS root) ─────────────────────────────────────────────

_psoas_root = Path(__file__).resolve().parent
if str(_psoas_root) not in sys.path:
    sys.path.insert(0, str(_psoas_root))

_pms1_root = _psoas_root / "src" / "scripts" / "Poony_Multiretrieval_S1"

try:
    from dotenv import load_dotenv
    load_dotenv(_pms1_root / ".env")
except ImportError:
    pass

# These imports start the terminal_router UI thread (Rich output)
from src.harness.terminal_router import register
from src.config import Config
from src.index_store import IndexManager
from src.pto import PTOBatchRequest, PTOBatchResult, PTOMethodLog
from src.pumba import run_pumba

_CASES_PATH = _pms1_root / "eval" / "pto_test_cases.yaml"


def load_cases() -> list[dict]:
    with open(_CASES_PATH) as f:
        return yaml.safe_load(f)


def build_request_from_case(case: dict) -> PTOBatchRequest:
    return PTOBatchRequest(
        batch_id=case["id"],
        firm=case["firm"],
        period=case["period"],
        statement=case["statement"],
        metrics=case["metrics"],
        retrieve_target=case["retrieve_target"],
    )


def build_request_from_args(args) -> PTOBatchRequest:
    return PTOBatchRequest(
        batch_id="cli_test",
        firm=args.firm,
        period=args.period,
        statement=args.statement,
        metrics=args.metrics,
        retrieve_target=args.target,
    )


def run_judge_on_pumba_chunks(
    request: PTOBatchRequest,
    node_ids: list[str],
    index,
    config: Config,
    expected_values: dict[str, list[str]] | None,
) -> None:
    """Re-run pto_judge on PUMBA chunks, compare with ground truth."""
    from llama_index.core.schema import NodeWithScore
    from src.pto import pto_judge, pto_judge_to_stencil

    top_chunks = []
    for i, nid in enumerate(node_ids):
        node = index.docstore.docs.get(nid)
        if node is None:
            continue
        top_chunks.append(
            NodeWithScore(node=node, score=float(len(node_ids) - i))
        )

    if not top_chunks:
        print("\n  Judge skipped: no valid chunks")
        return

    pumba_result = PTOBatchResult(
        batch_id=request.batch_id,
        top_chunks=top_chunks,
        runner_ups=[],
        method_log=PTOMethodLog(
            hyde_good="PUMBA", hyde_bad="",
            metadata_filters={"method": "pumba_fallback"},
        ),
    )

    cell_map = {f"cell_{i}": m for i, m in enumerate(request.metrics)}

    print("\n  Running pto_judge on PUMBA chunks...")
    try:
        judge_output = pto_judge(request, pumba_result, config)
        batch_values = pto_judge_to_stencil(
            judge_output, cell_map, pumba_result
        )
    except ValueError as e:
        print(f"  Judge FAILED: {e}")
        return

    print("  Judge extracted:")
    for cell_id, pcr in batch_values.items():
        metric = cell_map[cell_id]
        print(f"    {metric}: {pcr.value}  "
              f"(denom={pcr.denomination}, unit={pcr.unit})")

    if expected_values:
        print("\n  Validation:")
        for cell_id, pcr in batch_values.items():
            metric = cell_map[cell_id]
            if metric not in expected_values:
                continue
            variants = expected_values[metric]
            match = False
            for v in variants:
                try:
                    if abs(pcr.value - float(v.replace(",", ""))) < 0.01:
                        match = True
                        break
                except ValueError:
                    pass
            tag = "PASS" if match else "FAIL"
            print(f"    {tag}  {metric}: got {pcr.value}, "
                  f"expected {variants}")


def main():
    parser = argparse.ArgumentParser(
        description="Run PUMBA through the PSOAS CLI harness"
    )
    parser.add_argument(
        "--case", type=str,
        help="Test case ID from pto_test_cases.yaml"
    )
    parser.add_argument("--firm", type=str, default="Best Buy")
    parser.add_argument("--period", type=str, default="FY2023")
    parser.add_argument(
        "--statement", type=str, default="income_statement"
    )
    parser.add_argument(
        "--metrics", nargs="+", default=["Revenue"]
    )
    parser.add_argument(
        "--target", type=str,
        default="Consolidated Statements of Earnings",
        help="retrieve_target for the batch"
    )
    parser.add_argument(
        "--judge", action="store_true",
        help="Also run pto_judge on PUMBA chunks"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available test case IDs"
    )
    args = parser.parse_args()

    if args.list:
        for c in load_cases():
            print(f"  {c['id']:25s} {c['firm']:10s} {c['period']} "
                  f"{c['statement']:25s} {c['metrics']}")
        return

    # Build request
    case = None
    expected_values = None
    if args.case:
        cases = load_cases()
        matches = [c for c in cases if c["id"] == args.case]
        if not matches:
            print(f"Case '{args.case}' not found. Use --list.")
            sys.exit(1)
        case = matches[0]
        request = build_request_from_case(case)
        expected_values = case.get("expected_values")
    else:
        request = build_request_from_args(args)

    # Load index
    out = register("TEST")
    out.print("Loading config and index...")
    config = Config.from_env()
    config.validate()
    mgr = IndexManager(config)
    index = mgr.load_or_build()
    out.print(f"Index: {len(index.docstore.docs)} nodes")

    # Run PUMBA
    ch = register(f"PUMBA-{request.firm}")
    out.print(
        f"Calling run_pumba: {request.firm} {request.period} "
        f"{request.statement} metrics={request.metrics}"
    )

    t0 = time.time()
    try:
        node_ids = run_pumba(request, index, config, channel=ch)
    except ValueError as e:
        print(f"\nPUMBA raised ValueError: {e}")
        sys.exit(1)

    elapsed = time.time() - t0

    # Results
    print(f"\n{'='*60}")
    print(f"PUMBA returned {len(node_ids)} node_id(s) in {elapsed:.1f}s")

    if not node_ids:
        print("(empty — exhausted or user skipped)")
        return

    for i, nid in enumerate(node_ids):
        node = index.docstore.docs.get(nid)
        if node is None:
            print(f"\n  [{i+1}] {nid} — STALE (not in docstore)")
            continue
        meta = node.metadata
        print(f"\n  [{i+1}] {nid[:16]}...")
        print(f"      file:    {meta.get('file_name', '?')}")
        print(f"      section: {meta.get('section', '?')}")
        print(f"      text:    {node.text[:200]}...")

    # Check expected values in chunk text
    if expected_values:
        combined = ""
        for nid in node_ids:
            node = index.docstore.docs.get(nid)
            if node:
                combined += node.text + "\n"
        print(f"\nRetrieval validation:")
        for metric, variants in expected_values.items():
            found = any(v in combined for v in variants)
            tag = "PASS" if found else "FAIL"
            print(f"  {tag}  {metric} ({variants[0]})")

    # Optional judge
    if args.judge:
        run_judge_on_pumba_chunks(
            request, node_ids, index, config, expected_values
        )


if __name__ == "__main__":
    main()
