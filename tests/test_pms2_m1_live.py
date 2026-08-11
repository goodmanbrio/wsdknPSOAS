"""M1 live test — Sekei agent loop with real Anthropic API.

Runs run_sekei N times against the canonical demo query,
validates stencil structure against ground truth expectations.

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python tests/test_pms2_m1_live.py

Ground truth (from corpus):
  LITE (USD):  Q3 FY2026 → Rev $808.4M, GP $357.0M, GM 44.2%, OpInc $174.5M
  Innolight (CNY): FY2020E → Rev 6,432M, GM ~27.6%. FY2021E → Rev 10,129M, GM ~28.2%
  Periods union: [FY2020, FY2021, FY2026]. Some cells will be null (no data).
"""

import json
import sys
import time
from pathlib import Path
from tempfile import mkdtemp

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass

from src.scripts.config import Config
from src.scripts.PMS2.sekei_loop import run_sekei

# ── Auto-answering channel (bypasses TerminalRouter) ─────────────

class AutoChannel:
    """Duck-typed ToolChannel that auto-answers ask_user questions."""

    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self._idx = 0
        self.messages: list[str] = []

    def print(self, msg, **kw):
        self.messages.append(msg)
        print(f"  [PMS2-sekei] {msg}")

    def input(self, question, **kw):
        print(f"  [PMS2-sekei] {question}")
        if self._idx < len(self._answers):
            answer = self._answers[self._idx]
            self._idx += 1
        else:
            answer = "y"
        print(f"  > {answer}")
        return answer


# ── Test parameters ──────────────────────────────────────────────

FIRMS = ["LITE", "Innolight"]
PERIODS = ["FY2020", "FY2021", "FY2026"]
QUERY = "Revenue, Gross Margin"
GRANULARITY = "annual"

# Auto-answers for Sekei's ask_user calls:
#   Turn 1: sector folders + currencies + metric disambiguation
#   Turn 3: stencil preview confirmation
ANSWERS = [
    (
        "yes include 0 Optical for sector context. "
        "Currencies correct: LITE USD, Innolight CNY. "
        "Gross Margin = Gross Profit / Revenue, standard definition."
    ),
    "y",
]

# Expected stencil structure:
#   Per firm: Revenue (retrieve, ans), Gross Profit (retrieve, helper),
#             Gross Margin (compute = GP/Rev, ans)
#   3 rows/firm × 2 firms = 6 rows
#   6 rows × 3 periods = 18 cells
EXPECTED_ROW_COUNT = 6
EXPECTED_CELL_COUNT = 18
EXPECTED_ANS_METRICS = {"Revenue", "Gross Margin"}
EXPECTED_COL_LETTERS = ["A", "B", "C"]


def validate_stencil(work, ans, jobs, file_inv, run_idx):
    """Validate stencil against ground truth expectations. Returns (pass, errors)."""
    errors = []

    # ── Row count ────────────────────────────────────────────────
    n_rows = len(work["rows"])
    if n_rows != EXPECTED_ROW_COUNT:
        errors.append(f"Row count: {n_rows} != {EXPECTED_ROW_COUNT}")

    # ── Cell count ───────────────────────────────────────────────
    n_cells = len(work["values"])
    if n_cells != EXPECTED_CELL_COUNT:
        errors.append(f"Cell count: {n_cells} != {EXPECTED_CELL_COUNT}")

    # ── All values null (Phase 0 snapshot) ───────────────────────
    non_null = {k: v for k, v in work["values"].items() if v is not None}
    if non_null:
        errors.append(f"Non-null values in Phase 0 snapshot: {non_null}")

    # ── Col letters ──────────────────────────────────────────────
    if work["col_letters"] != EXPECTED_COL_LETTERS:
        errors.append(f"col_letters: {work['col_letters']} != {EXPECTED_COL_LETTERS}")

    # ── Periods match ────────────────────────────────────────────
    if work["periods"] != PERIODS:
        errors.append(f"periods: {work['periods']} != {PERIODS}")

    # ── Ans metrics ──────────────────────────────────────────────
    ans_metrics = set(ans["metrics"])
    if ans_metrics != EXPECTED_ANS_METRICS:
        errors.append(f"ans metrics: {ans_metrics} != {EXPECTED_ANS_METRICS}")

    # ── Both firms present in row_mapping ────────────────────────
    for firm in FIRMS:
        if firm not in ans["row_mapping"]:
            errors.append(f"Missing firm in row_mapping: {firm}")

    # ── Topo order: retrieve before compute within each firm ─────
    for firm in FIRMS:
        firm_rows = {
            v["metric"]: int(k) for k, v in work["rows"].items()
            if v["firm"] == firm
        }
        gm_name = None
        for name in firm_rows:
            if "margin" in name.lower() or name == "Gross Margin":
                gm_name = name
                break
        if gm_name and gm_name in firm_rows:
            gm_num = firm_rows[gm_name]
            # GM must come after its dependencies
            for dep_name, dep_num in firm_rows.items():
                dep_row = work["rows"][str(dep_num)]
                if dep_row["type"] == "retrieve" and dep_num >= gm_num:
                    errors.append(
                        f"{firm}: retrieve '{dep_name}' (row {dep_num}) "
                        f"not before compute '{gm_name}' (row {gm_num})"
                    )

    # ── Formula rewrite (no raw {MetricName} refs) ───────────────
    for rn, row in work["rows"].items():
        if row.get("formula") and "{" in row["formula"]:
            errors.append(f"Row {rn} ({row['metric']}): unrewritten formula '{row['formula']}'")

    # ── Job stencils: disjoint cell IDs ──────────────────────────
    if len(jobs) == 2:
        cells_0 = set(jobs[0]["values"].keys())
        cells_1 = set(jobs[1]["values"].keys())
        overlap = cells_0 & cells_1
        if overlap:
            errors.append(f"Job stencil cell overlap: {overlap}")
    else:
        errors.append(f"Expected 2 job stencils, got {len(jobs)}")

    # ── Unit assignments ─────────────────────────────────────────
    for rn, row in work["rows"].items():
        if row["firm"] == "LITE" and row["type"] == "retrieve":
            if row["unit"] != "USD":
                errors.append(f"Row {rn} ({row['metric']} LITE): unit {row['unit']} != USD")
        elif row["firm"] == "Innolight" and row["type"] == "retrieve":
            if row["unit"] != "CNY":
                errors.append(f"Row {rn} ({row['metric']} Innolight): unit {row['unit']} != CNY")
        if "margin" in row.get("metric", "").lower() or row.get("metric") == "Gross Margin":
            if row["unit"] != "float":
                errors.append(f"Row {rn} ({row['metric']}): margin unit {row['unit']} != float")

    # ── Revenue dual-purpose check ───────────────────────────────
    for rn, row in work["rows"].items():
        if row["metric"] == "Revenue":
            if not row.get("ans"):
                errors.append(f"Row {rn} Revenue ({row['firm']}): missing ans=true (dual-purpose)")

    # ── file_inventories: empty or opaque handles ─────────────────
    for firm, val in file_inv.items():
        if not isinstance(val, str) or not val.startswith("$var_"):
            errors.append(f"file_inventories[{firm}]: expected handle string, got {val}")

    # ── Disk artifact ────────────────────────────────────────────
    # (checked by caller)

    return len(errors) == 0, errors


def run_one(run_idx, session_dir):
    """Execute one run_sekei call. Returns (work, ans, jobs, file_inv, elapsed, errors)."""
    config = Config.from_env()
    channel = AutoChannel(list(ANSWERS))

    top_level_dirs = ["LITE", "Innolight", "0 Optical"]

    print(f"\n{'='*60}")
    print(f"RUN {run_idx + 1}")
    print(f"{'='*60}")

    t0 = time.time()
    try:
        result = run_sekei(
            firms=FIRMS,
            expanded_periods=PERIODS,
            query=QUERY,
            granularity=GRANULARITY,
            config=config,
            channel=channel,
            session_dir=session_dir,
            top_level_dirs=top_level_dirs,
        )
        elapsed = time.time() - t0
        work, ans, jobs, file_inv = result

        passed, errors = validate_stencil(work, ans, jobs, file_inv, run_idx)

        # Check disk artifact
        stencil_path = session_dir / "pms2" / "work_stencil.json"
        if not stencil_path.exists():
            errors.append("work_stencil.json not written to disk")
            passed = False

        return work, ans, jobs, file_inv, elapsed, errors

    except Exception as e:
        elapsed = time.time() - t0
        return None, None, None, None, elapsed, [f"CRASH: {e}"]


def main():
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    session_dir = Path(mkdtemp(prefix="pms2_m1_"))
    print(f"Session dir: {session_dir}")
    print(f"Query: {QUERY}")
    print(f"Firms: {FIRMS}")
    print(f"Periods: {PERIODS}")
    print(f"Expected: {EXPECTED_ROW_COUNT} rows, {EXPECTED_CELL_COUNT} cells")

    results = []
    for i in range(n_runs):
        work, ans, jobs, file_inv, elapsed, errors = run_one(i, session_dir)
        passed = len(errors) == 0

        status = "PASS" if passed else "FAIL"
        print(f"\n  [{status}] Run {i+1}: {elapsed:.1f}s")
        if errors:
            for e in errors:
                print(f"    ✗ {e}")
        else:
            n_rows = len(work["rows"])
            n_cells = len(work["values"])
            ans_m = ans["metrics"]
            print(f"    ✓ {n_rows} rows, {n_cells} cells, ans={ans_m}")
            # Show row structure
            for rn in sorted(work["rows"].keys(), key=int):
                row = work["rows"][rn]
                flags = []
                if row.get("ans"):
                    flags.append("ans")
                if row.get("feeds_rows"):
                    flags.append(f"feeds={row['feeds_rows']}")
                if row.get("formula"):
                    flags.append(f"formula={row['formula']}")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                print(f"      R{rn}: {row['metric']} [{row['firm']}] {row['type']} {row['unit']}{flag_str}")

        results.append((passed, elapsed, errors))

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    pass_count = sum(1 for p, _, _ in results if p)
    total = len(results)
    print(f"  {pass_count}/{total} passed")
    avg_time = sum(e for _, e, _ in results) / total
    print(f"  Avg time: {avg_time:.1f}s")

    if pass_count == total:
        print(f"\n  M1 STENCILING: ALL {total} RUNS PASS")

        # Cross-run consistency check
        # (stencil structure should be equivalent across runs)
        print(f"\n  Stencil artifacts in: {session_dir / 'pms2'}")
    else:
        print(f"\n  M1 STENCILING: {total - pass_count} FAILURES")
        sys.exit(1)


if __name__ == "__main__":
    main()
