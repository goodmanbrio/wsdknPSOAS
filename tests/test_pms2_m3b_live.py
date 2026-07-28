"""M3b live test — Dispatcher + Batch Planner with stubbed extraction.

Exercises the full pipeline: Sekei → Mapper → stenciling → Dispatcher
→ Batch Planner → stub leng → exhaustion → user abort.

Tests:
  1. structured_complete works (FiscalCalResolver with quarterly)
  2. BP produces structurally valid plan
  3. Dispatcher exhaustion: stub → dry run → force ask_user → exit
  4. Parallel dispatchers for 2 firms
  5. Traces written

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python tests/test_pms2_m3b_live.py [n_runs]
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

# ── Auto-answering channel ──────────────────────────────────────────


class AutoChannel:
    """Duck-typed ToolChannel that auto-answers ask_user questions."""

    def __init__(self, label: str, answers: list[str]):
        self.label = label
        self._answers = list(answers)
        self._idx = 0
        self.messages: list[str] = []
        self.questions: list[str] = []

    def print(self, msg, **kw):
        self.messages.append(msg)
        print(f"  [{self.label}] {msg}")

    def input(self, question, **kw):
        self.questions.append(question)
        print(f"  [{self.label}] Q: {question}")
        if self._idx < len(self._answers):
            answer = self._answers[self._idx]
            self._idx += 1
        else:
            answer = "n"  # default to "n" — triggers user_aborted
        print(f"  [{self.label}] A: {answer}")
        return answer


# ── Shared test channel registry (duck-typed) ──────────────────────


class ChannelFactory:
    """Creates auto-answering channels. Tracks all created channels."""

    def __init__(self, default_answers: list[str]):
        self._default_answers = default_answers
        self.channels: dict[str, AutoChannel] = {}

    def create(self, label: str) -> AutoChannel:
        ch = AutoChannel(label, list(self._default_answers))
        self.channels[label] = ch
        return ch


# =====================================================================
# TEST 1: Dispatcher loop with stub (annual, 2 firms)
# =====================================================================


def test_dispatcher_stub_annual():
    """Full pipeline: Sekei → Dispatcher → BP → stub → exhaustion.

    Annual granularity = no FiscalCalResolver.
    Stub leng = 0 cells filled → dry_run_count → force ask_user → abort.
    """
    print("\n" + "=" * 60)
    print("TEST 1: Dispatcher stub (annual, 2 firms)")
    print("=" * 60)

    config = Config.from_env()
    session_dir = Path(mkdtemp(prefix="pms2_m3b_"))
    debug_dir = session_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    firms = ["LITE", "Innolight"]
    periods = ["FY2025", "FY2026", "FY2027"]
    query = "EV/EBITDA, P/E, Laser Revenue for LITE and Innolight, FY2025-FY2027 annual"
    granularity = "annual"

    # Answers: Sekei ask_user (firms/periods/granularity + sector + disambig),
    # preview confirm, then dispatcher force-ask-user → "n" to abort (default)
    answers = [
        (
            "Confirmed. Firms LITE and Innolight, periods FY2025/FY2026/FY2027, annual. "
            "Include 0 Optical sector. Currencies: LITE USD, Innolight CNY. "
            "EV = Market Cap + Net Debt. EBITDA = Operating Income + D&A. "
            "P/E = Share Price / EPS. "
            "Laser Revenue = Laser segment revenue (retrieve directly)."
        ),
        "y",   # stencil preview
        "y",   # buffer
    ]

    # Monkey-patch terminal_router.register to use AutoChannels
    import src.harness.terminal_router as tr
    original_register = tr.register
    channel_map: dict[str, AutoChannel] = {}

    def mock_register(label):
        if label not in channel_map:
            channel_map[label] = AutoChannel(label, list(answers))
        return channel_map[label]

    tr.register = mock_register

    errors = []
    t0 = time.time()

    try:
        from src.scripts.PMS2.pms2 import run_pms2_pipeline

        display_stencils = run_pms2_pipeline(
            query=query,
            session_dir=session_dir,
            channel=mock_register("PMS2"),
            config=config,
            debug_dir=debug_dir,
        )
        elapsed = time.time() - t0

        # ── Validate display stencils ─────────────────────────────
        if not display_stencils:
            errors.append("No display stencils returned")
        else:
            if len(display_stencils) != 2:
                errors.append(f"Expected 2 display stencils, got {len(display_stencils)}")
            for ds in display_stencils:
                if ds["firm"] not in firms:
                    errors.append(f"Unknown firm in display stencil: {ds['firm']}")
                if ds["periods"] != periods:
                    errors.append(f"Display stencil periods mismatch: {ds['periods']}")
                # All values should be null (stub extraction)
                for row in ds["rows"]:
                    for v in row["values"]:
                        if v is not None:
                            errors.append(f"Non-null value in stub display stencil: {row['metric']}={v}")

        # ── Check work stencil on disk ────────────────────────────
        ws_path = session_dir / "pms2" / "work_stencil.json"
        if ws_path.exists():
            work = json.loads(ws_path.read_text())
            n_rows = len(work["rows"])
            n_cells = len(work["values"])
            print(f"\n  Work stencil: {n_rows} rows, {n_cells} cells")

            # All values still null (stub)
            non_null = {k: v for k, v in work["values"].items() if v is not None}
            if non_null:
                errors.append(f"Non-null values after stub extraction: {non_null}")

            # Topo order: retrieve before compute
            for rn, row in sorted(work["rows"].items(), key=lambda x: int(x[0])):
                flags = []
                if row.get("ans"):
                    flags.append("ans")
                if row.get("feeds_rows"):
                    flags.append(f"feeds={row['feeds_rows']}")
                if row.get("formula"):
                    flags.append(f"f={row['formula']}")
                flag_str = f" ({', '.join(flags)})" if flags else ""
                print(f"    R{rn}: {row['metric']} [{row['firm']}] {row['type']} {row['unit']}{flag_str}")
        else:
            errors.append("work_stencil.json not found on disk")

        # ── Check trace files ─────────────────────────────────────
        trace_files = list(debug_dir.rglob("*.md"))
        if trace_files:
            print(f"\n  Trace files ({len(trace_files)}):")
            for tf in sorted(trace_files):
                size = tf.stat().st_size
                print(f"    {tf.name} ({size} bytes)")
        else:
            errors.append("No trace files written")

        # ── Check dispatcher channels saw questions ───────────────
        disp_labels = [l for l in channel_map if "disp" in l.lower()]
        if disp_labels:
            print(f"\n  Dispatcher channels: {disp_labels}")
            for label in disp_labels:
                ch = channel_map[label]
                if ch.questions:
                    print(f"    {label}: {len(ch.questions)} question(s)")
                    for q in ch.questions:
                        print(f"      Q: {q[:100]}...")
        else:
            # Dispatcher channels might be registered under different names
            print(f"\n  All channels: {list(channel_map.keys())}")

    except Exception as e:
        elapsed = time.time() - t0
        errors.append(f"CRASH: {e}")
        import traceback
        traceback.print_exc()

    finally:
        tr.register = original_register

    status = "PASS" if not errors else "FAIL"
    print(f"\n  [{status}] Test 1: {elapsed:.1f}s")
    if errors:
        for e in errors:
            print(f"    x {e}")
    else:
        print(f"    Session: {session_dir}")

    return not errors, elapsed


# =====================================================================
# TEST 2: FiscalCalResolver (quarterly, structured_complete+web_search)
# =====================================================================


def test_fiscal_cal_resolver():
    """FiscalCalResolver fires for quarterly granularity.

    Separate test — doesn't need full pipeline, just the resolver.
    Tests structured_complete + web_search flag on AnthropicLLM.
    """
    print("\n" + "=" * 60)
    print("TEST 2: FiscalCalResolver (quarterly, web_search)")
    print("=" * 60)

    config = Config.from_env()
    channel = AutoChannel("PMS2-fiscal-test", [
        "LITE fiscal year ends June 30"  # fallback answer if web search fails
    ])

    errors = []
    t0 = time.time()

    try:
        from src.scripts.PMS2.dispatcher import _resolve_fiscal_calendar

        periods = ["Q1FY2025", "Q2FY2025", "Q3FY2025", "Q4FY2025"]

        calendar = _resolve_fiscal_calendar(
            firm="LITE",
            periods=periods,
            config=config,
            channel=channel,
        )
        elapsed = time.time() - t0

        if calendar is None:
            # Web search failed AND user fallback failed — acceptable
            # but note it
            print("  FiscalCalResolver returned None (all attempts failed)")
            print("  This is acceptable but suboptimal")
        else:
            print(f"  Calendar resolved: {len(calendar)} periods")
            for period, date_range in calendar.items():
                print(f"    {period}: {date_range}")

            # Validate: should have entries for each period
            for p in periods:
                if p not in calendar:
                    errors.append(f"Missing period in calendar: {p}")

        # Check channel messages for debug
        print(f"\n  Channel messages:")
        for msg in channel.messages:
            print(f"    {msg}")

    except Exception as e:
        elapsed = time.time() - t0
        errors.append(f"CRASH: {e}")
        import traceback
        traceback.print_exc()

    status = "PASS" if not errors else "FAIL"
    print(f"\n  [{status}] Test 2: {elapsed:.1f}s")
    if errors:
        for e in errors:
            print(f"    x {e}")

    return not errors, elapsed


# =====================================================================
# TEST 3: FiscalCalResolver skips for annual
# =====================================================================


def test_fiscal_cal_skipped_annual():
    """FiscalCalResolver should NOT fire for annual granularity.

    Quick sanity check — no API calls needed.
    """
    print("\n" + "=" * 60)
    print("TEST 3: FiscalCalResolver skipped (annual)")
    print("=" * 60)

    import threading
    from src.scripts.PMS2.dispatcher import run_dispatcher, DispatcherState

    # Build a minimal job stencil
    job_stencil = {
        "firm": "TestFirm",
        "periods": ["FY2025"],
        "granularity": "annual",
        "col_letters": ["A"],
        "rows": {
            "1": {
                "metric": "Revenue",
                "firm": "TestFirm",
                "type": "retrieve",
                "unit": "USD",
                "timeframe": "annual",
                "ans": True,
            }
        },
        "values": {"A1": None},
        "sources": {},
    }

    # Track if fiscal cal resolver was called
    import src.scripts.PMS2.dispatcher as disp_mod
    original_resolver = disp_mod._resolve_fiscal_calendar
    resolver_called = [False]

    def mock_resolver(*args, **kwargs):
        resolver_called[0] = True
        return None

    disp_mod._resolve_fiscal_calendar = mock_resolver

    # Mock BP to immediately return exhausted — must patch on
    # the dispatcher module since that's where it's imported
    original_bp = disp_mod.run_batch_planner

    def mock_bp(**kwargs):
        return "exhausted"

    disp_mod.run_batch_planner = mock_bp

    errors = []
    t0 = time.time()

    try:
        channel = AutoChannel("test-annual", ["n"])

        result = run_dispatcher(
            firm="TestFirm",
            job_stencil=job_stencil,
            file_inventory=[],
            channel=channel,
            config=Config.from_env(),
            docstore=None,
            file_path_index={},
            debug_dir=Path(mkdtemp(prefix="pms2_t3_")),
        )
        elapsed = time.time() - t0

        if resolver_called[0]:
            errors.append("FiscalCalResolver should NOT fire for annual")
        else:
            print("  FiscalCalResolver correctly skipped for annual")

        if result.get("status") != "bp_exhausted":
            errors.append(f"Expected bp_exhausted, got {result.get('status')}")
        else:
            print(f"  Dispatcher status: {result['status']}")

    except Exception as e:
        elapsed = time.time() - t0
        errors.append(f"CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        disp_mod._resolve_fiscal_calendar = original_resolver
        disp_mod.run_batch_planner = original_bp

    status = "PASS" if not errors else "FAIL"
    print(f"\n  [{status}] Test 3: {elapsed:.1f}s")
    if errors:
        for e in errors:
            print(f"    x {e}")

    return not errors, elapsed


# =====================================================================
# TEST 4: BP plan structure validation (mocked loop)
# =====================================================================


def test_bp_plan_structure():
    """Run one BP agent loop, validate the plan it produces.

    Real LLM call but with a simple 2-row stencil so it's fast.
    """
    print("\n" + "=" * 60)
    print("TEST 4: BP plan structure (real LLM, simple stencil)")
    print("=" * 60)

    config = Config.from_env()
    errors = []
    t0 = time.time()

    # Monkey-patch to capture the plan before stub handles it
    captured_plans = []
    from src.scripts.PMS2 import batch_planner as bp_mod
    original_stub = bp_mod._handle_run_leng_caller_stub

    def capturing_stub(params, searched_files, channel):
        captured_plans.append(params.get("plan", []))
        return original_stub(params, searched_files, channel)

    bp_mod._handle_run_leng_caller_stub = capturing_stub

    # Monkey-patch register
    import src.harness.terminal_router as tr
    original_register = tr.register

    def mock_register(label):
        return AutoChannel(label, ["n"])

    tr.register = mock_register

    try:
        import threading
        from src.scripts.PMS2.batch_planner import run_batch_planner

        job_stencil = {
            "firm": "LITE",
            "periods": ["FY2025", "FY2026"],
            "col_letters": ["A", "B"],
            "rows": {
                "1": {
                    "metric": "Revenue",
                    "firm": "LITE",
                    "type": "retrieve",
                    "unit": "USD",
                    "timeframe": "annual",
                    "ans": True,
                },
                "2": {
                    "metric": "EPS",
                    "firm": "LITE",
                    "type": "retrieve",
                    "unit": "USD",
                    "timeframe": "annual",
                    "ans": True,
                },
            },
            "values": {"A1": None, "B1": None, "A2": None, "B2": None},
            "sources": {},
        }

        file_inventory = [
            {"path": "LITE/Company/Lumentum-results-2026.md", "filetype": "pdf", "filesize": 125000},
            {"path": "LITE/Company/Lumentum-10K-2025.md", "filetype": "pdf", "filesize": 890000},
            {"path": "LITE/Notes/analyst-note.md", "filetype": "docx", "filesize": 45000},
        ]

        channel = AutoChannel("PMS2-bp-test", ["n"])

        result = run_batch_planner(
            firm="LITE",
            job_stencil=job_stencil,
            active_cells=["A1", "B1", "A2", "B2"],
            file_inventory=file_inventory,
            searched_files={},
            iteration=0,
            stencil_lock=threading.Lock(),
            fiscal_calendar=None,
            channel=channel,
            config=config,
            docstore=None,
            file_path_index={},
            debug_dir=Path(mkdtemp(prefix="pms2_t4_")),
        )
        elapsed = time.time() - t0

        print(f"  BP returned: {result}")

        if result not in ("complete", "exhausted"):
            errors.append(f"Unexpected BP result: {result}")

        if captured_plans:
            plan = captured_plans[0]
            print(f"  Plan has {len(plan)} entries:")
            valid_paths = {f["path"] for f in file_inventory}
            valid_cells = {"A1", "B1", "A2", "B2"}

            for entry in plan:
                file_path = entry.get("file", "")
                cells = entry.get("cells", [])
                print(f"    {file_path}: {cells}")

                if file_path not in valid_paths:
                    errors.append(f"Plan file not in inventory: {file_path}")
                for c in cells:
                    if c not in valid_cells:
                        errors.append(f"Plan cell not in active_cells: {c}")

            if not plan:
                errors.append("Plan is empty")
        elif result == "exhausted":
            print("  BP declared exhausted without producing a plan")
            # This is acceptable for iter 0 if BP decides to
        else:
            errors.append("No plan captured but result was 'complete'")

    except Exception as e:
        elapsed = time.time() - t0
        errors.append(f"CRASH: {e}")
        import traceback
        traceback.print_exc()
    finally:
        bp_mod._handle_run_leng_caller_stub = original_stub
        tr.register = original_register

    status = "PASS" if not errors else "FAIL"
    print(f"\n  [{status}] Test 4: {elapsed:.1f}s")
    if errors:
        for e in errors:
            print(f"    x {e}")

    return not errors, elapsed


# =====================================================================
# MAIN
# =====================================================================


def main():
    n_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    results = {}

    # Test 3 first (no API calls, fast sanity check)
    passed, elapsed = test_fiscal_cal_skipped_annual()
    results["3-fiscal-skip-annual"] = (passed, elapsed)

    # Test 4: BP plan structure (1 API call)
    passed, elapsed = test_bp_plan_structure()
    results["4-bp-plan-structure"] = (passed, elapsed)

    # Test 2: FiscalCalResolver (1-5 API calls)
    passed, elapsed = test_fiscal_cal_resolver()
    results["2-fiscal-cal-resolver"] = (passed, elapsed)

    # Test 1: Full pipeline (many API calls, run n_runs times)
    for i in range(n_runs):
        passed, elapsed = test_dispatcher_stub_annual()
        results[f"1-full-pipeline-run{i+1}"] = (passed, elapsed)

    # ── Summary ──────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    pass_count = sum(1 for p, _ in results.values() if p)
    total = len(results)
    for name, (passed, elapsed) in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {elapsed:.1f}s")

    print(f"\n  {pass_count}/{total} passed")
    if pass_count == total:
        print(f"\n  M3b: ALL TESTS PASS")
    else:
        print(f"\n  M3b: {total - pass_count} FAILURES")
        sys.exit(1)


if __name__ == "__main__":
    main()
