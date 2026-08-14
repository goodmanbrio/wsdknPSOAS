#!/usr/bin/env python3
"""
eval_pto_judge.py — Diagnostic eval for PTO retrieve → judge → parse.

Runs each test case through the full PTO pipeline, captures raw LLM
output before JSON parsing, and writes everything to markdown.

Usage:
    cd PSOAS_Jul17_Deepseek
    ANTHROPIC_API_KEY=sk-... python tests/eval_pto_judge.py

Output:
    tests/eval_runs/pto_judge_YYYYMMDD_HHMMSS.md
"""

import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

# ── sys.path setup (same as psoas.py) ──
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.config import Config
from src.index_store import IndexManager
import src.pto as pto_mod
from src.pto import (
    PTOBatchRequest,
    PTOBatchResult,
    PTOCellResult,
    pto_retrieve,
    pto_judge,
    pto_judge_to_stencil,
)


# ═══════════════════════════════════════════════════════════════════
# Test cases
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EvalCase:
    id: str
    firm: str
    period: str
    statement: str
    metrics: list[str]
    retrieve_target: str


CASES = [
    # ── Amcor: income statement margins ──
    EvalCase(
        id="amcor_is_fy2021",
        firm="Amcor",
        period="FY2021",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Income",
    ),
    EvalCase(
        id="amcor_is_fy2022",
        firm="Amcor",
        period="FY2022",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Income",
    ),
    EvalCase(
        id="amcor_is_fy2023",
        firm="Amcor",
        period="FY2023",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Income",
    ),
    # ── Best Buy: income statement margins ──
    EvalCase(
        id="bestbuy_is_fy2021",
        firm="Best Buy",
        period="FY2021",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Earnings",
    ),
    EvalCase(
        id="bestbuy_is_fy2022",
        firm="Best Buy",
        period="FY2022",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Earnings",
    ),
    EvalCase(
        id="bestbuy_is_fy2023",
        firm="Best Buy",
        period="FY2023",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Earnings",
    ),
    # ── Best Buy: balance sheet PPE ──
    EvalCase(
        id="bestbuy_bs_ppe_fy2022",
        firm="Best Buy",
        period="FY2022",
        statement="balance_sheet",
        metrics=["Total Assets", "Property and Equipment, Net"],
        retrieve_target="Consolidated Balance Sheets",
    ),
    EvalCase(
        id="bestbuy_bs_ppe_fy2023",
        firm="Best Buy",
        period="FY2023",
        statement="balance_sheet",
        metrics=["Total Assets", "Property and Equipment, Net"],
        retrieve_target="Consolidated Balance Sheets",
    ),
    # ── Boeing: income statement margins (known problematic) ──
    EvalCase(
        id="boeing_is_fy2020",
        firm="Boeing",
        period="FY2020",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Operations",
    ),
    EvalCase(
        id="boeing_is_fy2021",
        firm="Boeing",
        period="FY2021",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Operations",
    ),
    EvalCase(
        id="boeing_is_fy2022",
        firm="Boeing",
        period="FY2022",
        statement="income_statement",
        metrics=["Revenue", "Gross Profit", "Net Income"],
        retrieve_target="Consolidated Statements of Operations",
    ),
]


# ═══════════════════════════════════════════════════════════════════
# Monkey-patched pto_judge that captures raw LLM output
# ═══════════════════════════════════════════════════════════════════

def pto_judge_diagnostic(
    request: PTOBatchRequest,
    result: PTOBatchResult,
    config: Config,
) -> tuple[str, dict | None, Exception | None]:
    """Like pto_judge but returns (raw_text, parsed_dict_or_None, error_or_None).

    Never raises — captures everything for diagnostics.
    """
    from src.llm import get_pto_judge_llm

    user_prompt = pto_mod._build_judge_user_prompt(request, result)
    llm = get_pto_judge_llm(config)
    raw, usage = llm.complete_with_usage(user_prompt, system_prompt=pto_mod._build_judge_system(config))

    # ── Attempt parse (same logic as pto_judge) ──
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        return raw, parsed, None
    except json.JSONDecodeError as e:
        # ── Aggressive extraction (spec 14 proposed fix) ──
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
                return raw, parsed, None  # aggressive extraction saved it
            except json.JSONDecodeError:
                pass
        return raw, None, e


# ═══════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════

def run_eval(cases: list[EvalCase], config: Config, index) -> str:
    """Run all cases, return markdown report string."""
    md = StringIO()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md.write(f"# PTO Judge Eval — {ts}\n\n")
    md.write(f"**Profile**: `{config.pto_judge_profile}`\n\n")
    md.write("---\n\n")

    summary_rows = []

    for case in cases:
        print(f"\n{'='*60}")
        print(f"CASE: {case.id}")
        print(f"  {case.firm} | {case.period} | {case.statement}")
        print(f"  Metrics: {case.metrics}")
        print(f"{'='*60}")

        md.write(f"## {case.id}\n\n")
        md.write(f"- **Firm**: {case.firm}\n")
        md.write(f"- **Period**: {case.period}\n")
        md.write(f"- **Statement**: {case.statement}\n")
        md.write(f"- **Metrics**: {', '.join(case.metrics)}\n")
        md.write(f"- **Retrieve target**: {case.retrieve_target}\n\n")

        request = PTOBatchRequest(
            batch_id=case.id,
            firm=case.firm,
            period=case.period,
            statement=case.statement,
            metrics=case.metrics,
            retrieve_target=case.retrieve_target,
        )
        cell_map = {f"C{i}": m for i, m in enumerate(case.metrics)}

        # ── Phase 1: Retrieve ──
        t0 = time.time()
        try:
            result = pto_retrieve(request, index, config)
        except Exception as e:
            msg = f"RETRIEVE CRASHED: {e}"
            print(f"  {msg}")
            md.write(f"### Retrieve\n\n**CRASHED**: `{e}`\n\n---\n\n")
            summary_rows.append((case.id, "RETRIEVE_CRASH", str(e)))
            continue
        t_retrieve = time.time() - t0

        print(f"  Retrieve: {len(result.top_chunks)} chunks, {t_retrieve:.1f}s")
        md.write(f"### Retrieve ({t_retrieve:.1f}s)\n\n")
        md.write(f"- **Chunks**: {len(result.top_chunks)} top, "
                 f"{len(result.runner_ups)} runner-ups\n")
        md.write(f"- **HyDE good**: `{result.method_log.hyde_good[:120]}`\n")
        md.write(f"- **HyDE bad**: `{result.method_log.hyde_bad[:120]}`\n\n")

        for i, chunk in enumerate(result.top_chunks):
            meta = chunk.node.metadata
            good = result.good_scores.get(chunk.node.node_id, 0.0)
            bad = result.bad_scores.get(chunk.node.node_id, 0.0)
            md.write(f"**Chunk {i}** — g={good:.3f} b={bad:.3f} f={chunk.score:.3f}\n")
            md.write(f"- file: `{meta.get('file_name', '?')}`\n")
            md.write(f"- section: `{meta.get('section', '?')}`\n")
            md.write(f"- fy: `{meta.get('fiscal_year', '?')}`\n\n")
            # First 500 chars of chunk text
            preview = chunk.node.text[:500].replace("\n", "\n> ")
            md.write(f"> {preview}\n\n")

        if not result.top_chunks:
            md.write("*No chunks retrieved.*\n\n")
            summary_rows.append((case.id, "NO_CHUNKS", ""))
            md.write("---\n\n")
            continue

        # ── Phase 2: Judge ──
        t0 = time.time()
        raw_text, parsed, error = pto_judge_diagnostic(request, result, config)
        t_judge = time.time() - t0

        md.write(f"### Judge ({t_judge:.1f}s)\n\n")

        # Raw LLM output — the diagnostic gold
        md.write("#### Raw LLM output\n\n")
        md.write("```\n")
        md.write(raw_text)
        md.write("\n```\n\n")

        if error:
            print(f"  Judge: JSONDecodeError — {error}")
            md.write(f"#### Parse error\n\n`{error}`\n\n")

            # Show what aggressive extraction would find
            text_stripped = raw_text.strip()
            start = text_stripped.find("{")
            end = text_stripped.rfind("}")
            if start != -1 and end > start:
                snippet = text_stripped[start:end + 1]
                md.write("#### Aggressive extraction attempt\n\n")
                md.write(f"Found `{{` at pos {start}, `}}` at pos {end}\n\n")
                md.write("```json\n")
                md.write(snippet[:2000])
                md.write("\n```\n\n")
            else:
                md.write("*No `{...}` found in raw output.*\n\n")

            summary_rows.append((case.id, "JSON_ERROR", str(error)[:80]))
            md.write("---\n\n")
            continue

        # Parsed OK
        print(f"  Judge: parsed OK, {len(parsed)} metrics")
        md.write("#### Parsed JSON\n\n")
        md.write("```json\n")
        md.write(json.dumps(parsed, indent=2))
        md.write("\n```\n\n")

        # ── Phase 3: Stencil parse ──
        md.write("### Stencil parse\n\n")
        stencil_result = pto_judge_to_stencil(parsed, cell_map, result)
        md.write("| Cell | Metric | Value | Denom | Unit | Source |\n")
        md.write("|------|--------|-------|-------|------|--------|\n")
        for cid, pcr in stencil_result.values.items():
            metric = cell_map[cid]
            src_short = pcr.source[:16] + "..."
            md.write(f"| {cid} | {metric} | {pcr.value} | "
                     f"{pcr.denomination} | {pcr.unit} | `{src_short}` |\n")
            print(f"    {cid} {metric}: {pcr.value} ({pcr.denomination} {pcr.unit})")
        md.write("\n")
        if stencil_result.failures:
            md.write("**Failed cells**:\n\n")
            for cid, metric in stencil_result.failures:
                md.write(f"- {cid}: {metric}\n")
                print(f"    {cid} {metric}: FAILED")
            md.write("\n")
            summary_rows.append((case.id, "PARTIAL",
                                 f"{len(stencil_result.failures)} failed"))
        else:
            summary_rows.append((case.id, "OK", ""))

        md.write("---\n\n")

    # ── Summary table ──
    md.write("## Summary\n\n")
    md.write("| Case | Status | Detail |\n")
    md.write("|------|--------|--------|\n")
    for case_id, status, detail in summary_rows:
        md.write(f"| {case_id} | {status} | {detail} |\n")
    md.write("\n")

    ok = sum(1 for _, s, _ in summary_rows if s == "OK")
    total = len(summary_rows)
    md.write(f"**{ok}/{total} passed**\n")

    return md.getvalue()


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    config = Config.from_env()
    config.validate()

    print(f"Loading index from {config.index_dir}...")
    store = IndexManager(config)
    index = store.load_or_build()
    print(f"Index loaded: {len(index.docstore.docs)} nodes")

    report = run_eval(CASES, config, index)

    # Write to disk
    out_dir = Path(__file__).parent / "eval_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"pto_judge_{ts}.md"
    out_path.write_text(report)
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
