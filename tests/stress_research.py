"""Stress-test the research pipeline without going through the REPL.

Exercises: decomposition, BM25 retrieval, synthesis, edge cases.

Run from project root:
    python tests/stress_research.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config
from src.scripts.research.decomposer import decompose_question
from src.scripts.research.retriever import retrieve_for_sub_questions
from src.scripts.research.synthesizer import synthesize_answer


# ── Test cases ──────────────────────────────────────────────────────────

SINGLE_FIRM = [
    "What is the bull case for LITE?",
    "What are the key risks for Lumentum's business?",
    "Summarize LITE's competitive position in optical components.",
    "What do analysts think about LITE's pricing power?",
    "How has LITE's guidance trended over recent quarters?",
    "What are the key takeaways from LITE's recent earnings calls?",
]

CROSS_FIRM = [
    "Compare the outlook for LITE and Coherent in datacom.",
    "How does Furukawa Electric's optical strategy differ from Lumentum's?",
    "Which of LITE, COHR, and Furukawa has the strongest growth narrative?",
]

THEMATIC = [
    "What is the outlook for the optical fiber and datacom market?",
    "How is the optical components industry evolving?",
    "Summarize the OFC conference takeaways.",
    "What are the key themes in optical networking for 2026?",
]

EDGE_CASES = [
    # Very short
    "LITE outlook",
    # Very long
    (
        "Provide a comprehensive analysis of Lumentum's business including "
        "revenue drivers, competitive positioning, margin trends, capacity "
        "expansion plans, management guidance, analyst consensus, key risks, "
        "and industry-wide trends affecting the optical components market "
        "with specific attention to datacom vs telecom exposure."
    ),
    # Should find nothing
    "What is Apple's market share in consumer smartphones?",
    # Gibberish (tests graceful handling)
    "asdf qwerty zxcvbn blargle flarn?",
    # Empty-ish
    "   ",
]

# ── Runner ──────────────────────────────────────────────────────────────


def run_test(question: str, config: Config) -> dict:
    """Run the full pipeline for one question, return timing + stats."""
    result = {
        "question": question[:120],
        "decompose_ok": False,
        "retrieve_ok": False,
        "synthesize_ok": False,
        "n_sub_questions": 0,
        "n_chunks": 0,
        "answer_len": 0,
        "decompose_time": 0.0,
        "retrieve_time": 0.0,
        "synthesize_time": 0.0,
        "error": None,
    }

    try:
        # Stage 1: Decompose
        t0 = time.perf_counter()
        sub_qs = decompose_question(question, config)
        result["decompose_time"] = time.perf_counter() - t0
        result["decompose_ok"] = True
        result["n_sub_questions"] = len(sub_qs)

        if not sub_qs:
            result["error"] = "No sub-questions produced"
            return result

        # Stage 2: Retrieve
        t0 = time.perf_counter()
        chunks = retrieve_for_sub_questions(sub_qs, config)
        result["retrieve_time"] = time.perf_counter() - t0
        result["retrieve_ok"] = True
        result["n_chunks"] = len(chunks)

        if not chunks:
            result["error"] = "No chunks retrieved"
            return result

        # Stage 3: Synthesize
        t0 = time.perf_counter()
        answer = synthesize_answer(question, chunks, config)
        result["synthesize_time"] = time.perf_counter() - t0
        result["synthesize_ok"] = True
        result["answer_len"] = len(answer)

    except Exception as exc:
        result["error"] = str(exc)[:200]

    return result


def print_result(i: int, r: dict, verbose: bool = False) -> None:
    """Print a single test result."""
    status = "✓" if (r["decompose_ok"] and r["retrieve_ok"] and r["synthesize_ok"]) else "✗"
    print(f"\n{'─' * 60}")
    print(f"  {status} Test {i}: {r['question']}")
    print(f"    Sub-questions: {r['n_sub_questions']} | Chunks: {r['n_chunks']} | Answer: {r['answer_len']} chars")
    print(f"    Times: decompose={r['decompose_time']:.1f}s retrieve={r['retrieve_time']:.2f}s synthesize={r['synthesize_time']:.1f}s")
    if r["error"]:
        print(f"    ERROR: {r['error']}")
    if verbose and r["synthesize_ok"]:
        print(f"    --- Answer preview ---")
        from rich.console import Console
        Console().print(r.get("answer_preview", "")[:300])


def main() -> None:
    config = Config()

    test_cases = SINGLE_FIRM + CROSS_FIRM + THEMATIC + EDGE_CASES
    print(f"Running {len(test_cases)} stress tests...\n")

    results = []
    total_start = time.perf_counter()

    for i, question in enumerate(test_cases):
        r = run_test(question, config)
        results.append(r)
        print_result(i + 1, r)

    total_time = time.perf_counter() - total_start

    # ── Summary ──────────────────────────────────────────────────────────
    ok = sum(1 for r in results if r["decompose_ok"] and r["retrieve_ok"] and r["synthesize_ok"])
    fail = len(results) - ok

    print(f"\n{'=' * 60}")
    print(f"  SUMMARY: {ok}/{len(results)} passed, {fail} failed")
    print(f"  Total time: {total_time:.1f}s")

    if results:
        avg_sub_q = sum(r["n_sub_questions"] for r in results) / len(results)
        avg_chunks = sum(r["n_chunks"] for r in results if r["retrieve_ok"]) / max(1, ok)
        avg_decomp = sum(r["decompose_time"] for r in results) / len(results)
        avg_synth = sum(r["synthesize_time"] for r in results if r["synthesize_ok"]) / max(1, ok)
        print(f"  Avg sub-questions: {avg_sub_q:.1f}")
        print(f"  Avg chunks (successes): {avg_chunks:.1f}")
        print(f"  Avg decompose time: {avg_decomp:.1f}s")
        print(f"  Avg synthesize time: {avg_synth:.1f}s")

    # ── Specific checks ──────────────────────────────────────────────────
    print(f"\n  Edge case checks:")
    for r in results:
        if "Apple" in r["question"]:
            print(f"    Apple question: {r['n_chunks']} chunks (expect low/zero)")
        if "asdf" in r["question"]:
            print(f"    Gibberish question: decomposer {'passed' if r['decompose_ok'] else 'failed'}")
        if "   " in r["question"]:
            print(f"    Whitespace question: {'handled gracefully' if r['error'] else 'may need guard'}")

    # ── Report failures ──────────────────────────────────────────────────
    if fail:
        print(f"\n  Failures:")
        for r in results:
            if r["error"]:
                print(f"    [{r['question'][:80]}] → {r['error']}")


if __name__ == "__main__":
    main()
