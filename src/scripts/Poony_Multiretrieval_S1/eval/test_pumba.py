#!/usr/bin/env python3
"""
test_pumba.py — PUMBA integration test harness.

Constructs PTOBatchRequests from pto_test_cases.yaml, bypasses PTO
entirely, calls run_pumba directly, then feeds PUMBA's chunks to
pto_judge to validate end-to-end.

Three test modes:
  1. pumba-only:  run_pumba → check node_ids exist and chunk text
                  contains expected values (no judge LLM call)
  2. pumba+judge: run_pumba → pto_judge → compare against ground truth
  3. single-tier: test individual tiers (Leng, GuleiPai) in isolation
                  with mock data (cheapest sanity check)

Usage:
    cd Poony_Multiretrieval_S1
    export ANTHROPIC_API_KEY=sk-...

    # Run PUMBA retrieval only (cheapest — no judge call)
    python eval/test_pumba.py --ids e_bby_income

    # Run PUMBA + judge (full pipeline minus PTO)
    python eval/test_pumba.py --ids e_bby_income --judge

    # Run all cases
    python eval/test_pumba.py

    # Run all cases with judge
    python eval/test_pumba.py --judge

    # Test Leng in isolation on a known chunk (cheapest possible)
    python eval/test_pumba.py --tier leng

    # Test GuleiPai in isolation
    python eval/test_pumba.py --tier gulei_pai

    # List available test case IDs
    python eval/test_pumba.py --list
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

# ── Path setup ───────────────────────────────────────────────────────────
# _project_root = PMS1 root (for .env, eval/ data files)
# _psoas_root   = PSOAS root (for src/__init__.py with __path__ extension
#                 that makes src.harness, src.config etc. importable)

_project_root = Path(__file__).resolve().parent.parent
_psoas_root = _project_root.parent.parent.parent  # PMS1 → scripts → src → PSOAS

# PSOAS root must come FIRST so `src` resolves to PSOAS's src/ (which has
# __path__ extension to chain in scripts/ and PMS1/src/).
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
from src.pto import PTOBatchRequest, PTOBatchResult, PTOMethodLog
from src.pumba import (
    run_pumba,
    _run_leng,
    _run_gulei_pai,
    _parse_json_with_fences,
    _build_leng_prompt,
    _build_gulei_pai_prompt,
    LENG_SYSTEM,
    GULEI_PAI_SYSTEM,
)
from src.harness.terminal_router import register

_CASES_PATH = _project_root / "eval" / "pto_test_cases.yaml"


def load_cases(path: Path) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)


# ═════════════════════════════════════════════════════════════════════════
# MODE 1: pumba-only (retrieval check, no judge)
# ═════════════════════════════════════════════════════════════════════════

def run_pumba_retrieval_test(
    case: dict,
    index,
    config: Config,
) -> dict:
    """Run PUMBA on a test case, check if returned chunks contain
    expected values. No judge call — just validates retrieval.
    """
    request = PTOBatchRequest(
        batch_id=case["id"],
        firm=case["firm"],
        period=case["period"],
        statement=case["statement"],
        metrics=case["metrics"],
        retrieve_target=case["retrieve_target"],
    )

    ch = register(f"TEST-PUMBA-{case['firm']}")
    t0 = time.time()

    try:
        node_ids = run_pumba(request, index, config, channel=ch)
    except ValueError as e:
        return {
            "id": case["id"],
            "status": "ERROR",
            "error": str(e),
            "elapsed": time.time() - t0,
        }

    elapsed = time.time() - t0

    if not node_ids:
        return {
            "id": case["id"],
            "status": "EXHAUSTED",
            "node_ids": [],
            "elapsed": elapsed,
        }

    # Validate: check expected values appear in chunk text
    expected = case.get("expected_values", {})
    chunk_texts = []
    for nid in node_ids:
        node = index.docstore.docs.get(nid)
        if node is None:
            chunk_texts.append(f"(stale node_id: {nid})")
            continue
        chunk_texts.append(node.text)

    combined = "\n".join(chunk_texts)
    verdicts = {}
    for metric, variants in expected.items():
        found = any(v in combined for v in variants)
        verdicts[metric] = "PASS" if found else "FAIL"

    return {
        "id": case["id"],
        "status": "OK",
        "node_ids": node_ids,
        "chunk_count": len(node_ids),
        "verdicts": verdicts,
        "elapsed": elapsed,
        "chunk_previews": [
            {
                "node_id": nid,
                "file": index.docstore.docs[nid].metadata.get("file_name", "?")
                    if nid in index.docstore.docs else "?",
                "section": index.docstore.docs[nid].metadata.get("section", "?")
                    if nid in index.docstore.docs else "?",
                "first_200": (index.docstore.docs[nid].text[:200]
                    if nid in index.docstore.docs else "(stale)"),
            }
            for nid in node_ids
        ],
    }


# ═════════════════════════════════════════════════════════════════════════
# MODE 2: pumba+judge (full validation against ground truth)
# ═════════════════════════════════════════════════════════════════════════

def run_pumba_judge_test(
    case: dict,
    index,
    config: Config,
) -> dict:
    """Run PUMBA → judge → compare against expected values."""
    from llama_index.core.schema import NodeWithScore
    from src.pto import pto_judge, pto_judge_to_stencil

    request = PTOBatchRequest(
        batch_id=case["id"],
        firm=case["firm"],
        period=case["period"],
        statement=case["statement"],
        metrics=case["metrics"],
        retrieve_target=case["retrieve_target"],
    )

    cell_map = {f"cell_{i}": m for i, m in enumerate(case["metrics"])}

    ch = register(f"TEST-PUMBA-{case['firm']}")
    t0 = time.time()

    try:
        node_ids = run_pumba(request, index, config, channel=ch)
    except ValueError as e:
        return {
            "id": case["id"],
            "status": "ERROR",
            "error": f"PUMBA: {e}",
            "elapsed": time.time() - t0,
        }

    if not node_ids:
        return {
            "id": case["id"],
            "status": "EXHAUSTED",
            "elapsed": time.time() - t0,
        }

    # Build PTOBatchResult from PUMBA chunks
    top_chunks = []
    for i, nid in enumerate(node_ids):
        node = index.docstore.docs.get(nid)
        if node is None:
            continue
        top_chunks.append(
            NodeWithScore(node=node, score=float(len(node_ids) - i))
        )

    if not top_chunks:
        return {
            "id": case["id"],
            "status": "ERROR",
            "error": "All PUMBA node_ids stale",
            "elapsed": time.time() - t0,
        }

    pumba_result = PTOBatchResult(
        batch_id=request.batch_id,
        top_chunks=top_chunks,
        runner_ups=[],
        method_log=PTOMethodLog(
            hyde_good="PUMBA",
            hyde_bad="",
            metadata_filters={"method": "pumba_fallback"},
        ),
    )

    # Run judge
    try:
        judge_output = pto_judge(request, pumba_result, config)
        batch_values = pto_judge_to_stencil(
            judge_output, cell_map, pumba_result
        )
    except ValueError as e:
        return {
            "id": case["id"],
            "status": "JUDGE_FAIL",
            "error": str(e),
            "node_ids": node_ids,
            "elapsed": time.time() - t0,
        }

    elapsed = time.time() - t0

    # Compare extracted values against expected
    expected = case.get("expected_values", {})
    verdicts = {}
    extracted = {}
    for cell_id, pcr in batch_values.items():
        metric = cell_map[cell_id]
        extracted[metric] = pcr.value
        if metric in expected:
            # Check if extracted value matches any variant (as number)
            variants = expected[metric]
            # Try numeric comparison
            match = False
            for v in variants:
                try:
                    expected_num = float(v.replace(",", ""))
                    if abs(pcr.value - expected_num) < 0.01:
                        match = True
                        break
                except ValueError:
                    pass
            verdicts[metric] = "PASS" if match else f"FAIL (got {pcr.value})"

    return {
        "id": case["id"],
        "status": "OK",
        "node_ids": node_ids,
        "extracted": extracted,
        "verdicts": verdicts,
        "elapsed": elapsed,
    }


# ═════════════════════════════════════════════════════════════════════════
# MODE 3: single-tier isolation tests (cheapest)
# ═════════════════════════════════════════════════════════════════════════

def test_leng_tier(index, config: Config) -> None:
    """Test Leng on a known chunk. Cheapest possible API call (1 haiku)."""
    from src.llm import get_pumba_leng_llm

    leng_llm = get_pumba_leng_llm(config)

    # Find a known Best Buy income statement chunk
    target_file = "BESTBUY_2023_10K.md"
    target_section_substr = "Earnings"
    test_nid = None
    test_text = None
    test_section = None

    for nid, node in index.docstore.docs.items():
        meta = node.metadata
        if (
            meta.get("file_name") == target_file
            and meta.get("chunk_type") == "table"
            and target_section_substr in meta.get("section", "")
        ):
            # Check if "Revenue" appears in text
            if "Revenue" in node.text or "revenue" in node.text:
                test_nid = nid
                test_text = node.text
                test_section = meta.get("section", "")
                break

    if test_nid is None:
        print("SKIP: Could not find a BBY income statement table chunk")
        return

    print(f"Testing Leng on chunk {test_nid[:12]}...")
    print(f"  file: {target_file}")
    print(f"  section: {test_section}")
    print(f"  text preview: {test_text[:120]}...")
    print()

    request = PTOBatchRequest(
        batch_id="leng_test",
        firm="Best Buy",
        period="FY2023",
        statement="income_statement",
        metrics=["Revenue", "Cost of Sales"],
        retrieve_target="Consolidated Statements of Earnings",
    )

    t0 = time.time()
    result = _run_leng(
        test_nid, test_text, target_file, test_section,
        request, leng_llm,
    )
    elapsed = time.time() - t0

    print(f"Leng result ({elapsed:.1f}s):")
    print(f"  found: {result.get('found')}")
    print(f"  node_id: {result.get('node_id', '?')[:12]}...")
    if result.get("found"):
        metrics = result.get("metrics", {})
        print(f"  metrics: {metrics}")
        # Validate
        rev = metrics.get("Revenue")
        cos = metrics.get("Cost of Sales")
        if rev is not None and abs(rev - 46298) < 10:
            print(f"  Revenue: PASS ({rev} ~ 46298)")
        else:
            print(f"  Revenue: FAIL (got {rev}, expected ~46298)")
        if cos is not None and abs(cos - 36386) < 10:
            print(f"  Cost of Sales: PASS ({cos} ~ 36386)")
        else:
            print(f"  Cost of Sales: FAIL (got {cos}, expected ~36386)")
    else:
        print("  FAIL: Leng returned found=false on a known good chunk")


def test_gulei_pai_tier(index, config: Config) -> None:
    """Test GuleiPai on known file's table chunks. 1 sonnet call."""
    from src.llm import get_pumba_gulei_llm

    gulei_llm = get_pumba_gulei_llm(config)

    # Build table_chunks for BESTBUY_2023_10K.md
    target_file = "BESTBUY_2023_10K.md"
    table_chunks = []
    for nid, node in index.docstore.docs.items():
        meta = node.metadata
        if (
            meta.get("file_name") == target_file
            and meta.get("chunk_type") == "table"
        ):
            preview = node.text[:80].replace("\n", " ")
            section = meta.get("section", "")
            table_chunks.append((nid, section, preview))

    if not table_chunks:
        print("SKIP: No table chunks found for BESTBUY_2023_10K.md")
        return

    print(f"Testing GuleiPai on {target_file}: {len(table_chunks)} table chunks")

    request = PTOBatchRequest(
        batch_id="gulei_pai_test",
        firm="Best Buy",
        period="FY2023",
        statement="income_statement",
        metrics=["Revenue", "Cost of Sales"],
        retrieve_target="Consolidated Statements of Earnings",
    )

    t0 = time.time()
    picks = _run_gulei_pai(table_chunks, request, gulei_llm)
    elapsed = time.time() - t0

    print(f"GuleiPai result ({elapsed:.1f}s):")
    print(f"  picks: {picks}")
    print(f"  count: {len(picks)}")

    if not picks:
        print("  FAIL: GuleiPai returned no picks")
        return

    # Check if any picked chunk contains Revenue
    found_income = False
    for i in picks:
        nid, section, preview = table_chunks[i]
        node = index.docstore.docs.get(nid)
        if node and ("Revenue" in node.text or "revenue" in node.text):
            found_income = True
            print(f"  PASS: pick [{i}] section='{section}' contains Revenue")
            break

    if not found_income:
        print("  FAIL: No picked chunk contains 'Revenue'")
        print("  Picked chunks:")
        for i in picks:
            nid, section, preview = table_chunks[i]
            print(f"    [{i}] section='{section}' preview='{preview}'")


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def print_retrieval_result(r: dict) -> None:
    """Pretty-print a pumba-only result."""
    print(f"\n{'='*60}")
    print(f"Case: {r['id']}  |  Status: {r['status']}")
    print(f"Elapsed: {r['elapsed']:.1f}s")

    if r["status"] == "ERROR":
        print(f"Error: {r['error']}")
        return

    if r["status"] == "EXHAUSTED":
        print("PUMBA exhausted all files (or user skipped).")
        return

    print(f"Chunks returned: {r['chunk_count']}")
    for cp in r.get("chunk_previews", []):
        print(f"  {cp['node_id'][:12]}... "
              f"file={cp['file']} section={cp['section']}")
        print(f"    {cp['first_200'][:100]}...")

    verdicts = r.get("verdicts", {})
    if verdicts:
        print(f"\nRetrieval validation:")
        for metric, v in verdicts.items():
            print(f"  {v}  {metric}")


def print_judge_result(r: dict) -> None:
    """Pretty-print a pumba+judge result."""
    print(f"\n{'='*60}")
    print(f"Case: {r['id']}  |  Status: {r['status']}")
    print(f"Elapsed: {r['elapsed']:.1f}s")

    if r["status"] in ("ERROR", "EXHAUSTED"):
        print(f"  {r.get('error', 'exhausted')}")
        return

    if r["status"] == "JUDGE_FAIL":
        print(f"  Judge failed: {r['error']}")
        print(f"  Node IDs: {r.get('node_ids', [])}")
        return

    print(f"Node IDs: {r.get('node_ids', [])}")
    print(f"Extracted: {r.get('extracted', {})}")

    verdicts = r.get("verdicts", {})
    if verdicts:
        print(f"\nJudge validation:")
        for metric, v in verdicts.items():
            print(f"  {v}  {metric}")


def main():
    parser = argparse.ArgumentParser(
        description="PUMBA integration test harness"
    )
    parser.add_argument(
        "--ids", nargs="+",
        help="Run specific test case IDs from pto_test_cases.yaml"
    )
    parser.add_argument(
        "--group", type=str,
        help="Run all cases in a group (e.g. e_bby, a_bby)"
    )
    parser.add_argument(
        "--judge", action="store_true",
        help="Also run pto_judge on PUMBA chunks (more expensive)"
    )
    parser.add_argument(
        "--tier", choices=["leng", "gulei_pai"],
        help="Test a single tier in isolation (cheapest)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available test case IDs and exit"
    )
    args = parser.parse_args()

    # List mode
    if args.list:
        cases = load_cases(_CASES_PATH)
        print("Available test cases:")
        for c in cases:
            print(f"  {c['id']:25s} group={c['case_group']:10s} "
                  f"{c['firm']} {c['period']} {c['statement']}")
        return

    # Load index (needed for all modes)
    print("Loading config and index...")
    config = Config.from_env()
    config.validate()

    mgr = IndexManager(config)
    index = mgr.load_or_build()
    print(f"Index loaded: {len(index.docstore.docs)} nodes\n")

    # Single-tier mode
    if args.tier:
        if args.tier == "leng":
            test_leng_tier(index, config)
        elif args.tier == "gulei_pai":
            test_gulei_pai_tier(index, config)
        return

    # Full PUMBA mode
    all_cases = load_cases(_CASES_PATH)
    if args.ids:
        cases = [c for c in all_cases if c["id"] in args.ids]
    elif args.group:
        cases = [c for c in all_cases if c["case_group"] == args.group]
    else:
        cases = all_cases

    if not cases:
        print("No matching test cases. Use --list to see available IDs.")
        sys.exit(1)

    print(f"Running {len(cases)} case(s) "
          f"{'with judge' if args.judge else 'retrieval-only'}...\n")

    results = []
    for case in cases:
        if args.judge:
            r = run_pumba_judge_test(case, index, config)
            print_judge_result(r)
        else:
            r = run_pumba_retrieval_test(case, index, config)
            print_retrieval_result(r)
        results.append(r)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")

    for r in results:
        verdicts = r.get("verdicts", {})
        if not verdicts:
            status = r["status"]
            print(f"  {status:12s}  {r['id']}  ({r['elapsed']:.1f}s)")
        else:
            all_pass = all(
                v == "PASS" or v.startswith("PASS")
                for v in verdicts.values()
            )
            tag = "ALL PASS" if all_pass else "HAS FAIL"
            detail = ", ".join(
                f"{m}: {v}" for m, v in verdicts.items()
            )
            print(f"  {tag:12s}  {r['id']}  ({r['elapsed']:.1f}s)  "
                  f"{detail}")

    total = len(results)
    ok = sum(1 for r in results if r["status"] == "OK")
    print(f"\n  {ok}/{total} cases returned chunks")


if __name__ == "__main__":
    main()
