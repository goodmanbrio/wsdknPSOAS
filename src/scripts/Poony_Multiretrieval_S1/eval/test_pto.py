#!/usr/bin/env python3
"""
test_pto.py — PTO tabular retrieval test harness.

Loads test cases from eval/pto_test_cases.yaml, runs PTO on each,
validates expected values appear in top-k chunks, full dump output.

Usage:
    cd Poony_Multiretrieval_S1
    python test_pto.py                         # run all cases
    python test_pto.py --ids e_bby_income      # run specific case
    python test_pto.py --group e_bby           # run case group
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.config import Config
from src.index_store import IndexManager
from src.pto import PTOBatchRequest, PTOBatchResult, pto_retrieve


_CASES_PATH = _project_root / "eval" / "pto_test_cases.yaml"


def load_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_result(
    result: PTOBatchResult,
    expected_values: dict[str, list[str]],
) -> dict[str, str]:
    """Check if expected values appear in top chunk texts.

    Returns {metric: "PASS top#N" | "FAIL runner#N" | "FAIL absent"}.
    """
    # Combine all top chunk text
    top_texts = [n.node.text for n in result.top_chunks]
    top_combined = "\n".join(top_texts)

    verdicts: dict[str, str] = {}
    for metric, value_variants in expected_values.items():
        # Check top chunks first
        found_top = False
        for i, text in enumerate(top_texts, 1):
            if any(v in text for v in value_variants):
                verdicts[metric] = f"PASS top#{i}"
                found_top = True
                break

        if found_top:
            continue

        # Check runner-ups — we only have metadata, not text.
        # Can't validate runner-ups without text. Mark absent.
        verdicts[metric] = "FAIL absent from top-k"

    return verdicts


def print_result(
    case: dict,
    result: PTOBatchResult,
    verdicts: dict[str, str],
    hard_filter_count: int,
) -> None:
    """Full dump: method_log, hard filter stats, chunks, runner-ups, verdict."""
    print(f"\n{'='*70}")
    print(f"ID: {case['id']}  |  Group: {case['case_group']}")
    print(f"Firm: {case['firm']}  |  Period: {case['period']}")
    print(f"Statement: {case['statement']}")
    print(f"Metrics: {case['metrics']}")
    print(f"Retrieve target: {case['retrieve_target']}")
    print(f"Expected source: {case.get('source', '?')}")
    print(f"{'='*70}")

    # Method log
    ml = result.method_log
    print(f"\n--- METHOD LOG ---")
    print(f"  HyDE good: {ml.hyde_good[:200]}{'...' if len(ml.hyde_good) > 200 else ''}")
    print(f"  HyDE bad:  {ml.hyde_bad[:200]}{'...' if len(ml.hyde_bad) > 200 else ''}")
    print(f"  Metadata filters: {ml.metadata_filters}")
    print(f"--- END METHOD LOG ---")

    # Hard filter stats
    print(f"\n--- HARD FILTER ---")
    print(f"  Chunks surviving filter: {hard_filter_count}")
    print(f"--- END HARD FILTER ---")

    # Top chunks (full text) — show good, bad, final scores
    print(f"\n--- TOP CHUNKS ({len(result.top_chunks)}) ---")
    for i, node in enumerate(result.top_chunks, 1):
        meta = node.node.metadata
        nid = node.node.node_id
        good = result.good_scores.get(nid, 0.0)
        bad = result.bad_scores.get(nid, 0.0)
        final = node.score
        print(f"\n  CHUNK #{i}  good={good:.4f}  bad={bad:.4f}  final={final:.4f}")
        print(f"  file={meta.get('file_name', '?')}  "
              f"section={meta.get('section', '?')}  "
              f"fy={meta.get('fiscal_year', '?')}  "
              f"type={meta.get('chunk_type', '?')}")
        for line in node.node.text.split("\n"):
            print(f"    {line}")
    print(f"--- END TOP CHUNKS ---")

    # Runner-ups (metadata only) — show good, bad, final scores
    print(f"\n--- RUNNER-UPS ({len(result.runner_ups)}) ---")
    for i, ru in enumerate(result.runner_ups, 1):
        good = result.good_scores.get(ru.node_id, 0.0)
        bad = result.bad_scores.get(ru.node_id, 0.0)
        print(f"  #{i}  good={good:.4f}  bad={bad:.4f}  final={ru.score:.4f}  "
              f"file={ru.file_name}  section={ru.section}  "
              f"fy={ru.fiscal_year}")
    print(f"--- END RUNNER-UPS ---")

    # Verdicts
    print(f"\n--- VALIDATION ---")
    for metric, verdict in verdicts.items():
        status = "PASS" if verdict.startswith("PASS") else "FAIL"
        print(f"  {status}  {metric}: {verdict}")
    print(f"--- END VALIDATION ---")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="+", help="Run specific test case IDs")
    parser.add_argument("--group", type=str, help="Run all cases in a group")
    args = parser.parse_args()

    print("Loading config and building index...")
    config = Config.from_env()
    config.validate()

    mgr = IndexManager(config)
    index = mgr.load_or_build()

    all_cases = load_cases(_CASES_PATH)

    if args.ids:
        cases = [c for c in all_cases if c["id"] in args.ids]
    elif args.group:
        cases = [c for c in all_cases if c["case_group"] == args.group]
    else:
        cases = all_cases

    if not cases:
        print("No matching test cases found.")
        sys.exit(1)

    print(f"Running {len(cases)} test case(s)...\n")

    # Track results for summary
    summary: list[tuple[str, dict[str, str]]] = []

    for case in cases:
        request = PTOBatchRequest(
            batch_id=case["id"],
            firm=case["firm"],
            period=case["period"],
            statement=case["statement"],
            metrics=case["metrics"],
            retrieve_target=case["retrieve_target"],
        )

        # Run PTO — also capture hard filter count for diagnostics
        from src.pto import resolve_synonyms, _hard_filter_chunks
        group_key, synonyms = resolve_synonyms(request.firm)
        filtered, fy_relaxed = _hard_filter_chunks(index, group_key, synonyms, request.period)
        hard_filter_count = len(filtered)

        result = pto_retrieve(request, index, config)

        verdicts = validate_result(result, case.get("expected_values", {}))

        print_result(case, result, verdicts, hard_filter_count)

        summary.append((case["id"], verdicts))

    # ── Summary table ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    total_metrics = 0
    total_pass = 0
    for case_id, verdicts in summary:
        for metric, verdict in verdicts.items():
            status = "PASS" if verdict.startswith("PASS") else "FAIL"
            total_metrics += 1
            if status == "PASS":
                total_pass += 1
            print(f"  {status}  {case_id} / {metric}: {verdict}")
    print(f"\n  {total_pass}/{total_metrics} metrics found in top-k")


if __name__ == "__main__":
    main()
