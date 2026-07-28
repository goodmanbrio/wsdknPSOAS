"""Sekei (Phase 0) agent loop — stencil design via LLM + user interaction.

M1 scope: ask_user + finalize_stencil. run_mapper stubbed.
M2: run_mapper wired to real mapper agent loops.
M3a: run_mapper stores inventories as opaque handles via registry.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.harness.opaque_registry import registry
from src.harness.sysprompts import load_sysprompt
from src.harness import terminal_router
from src.harness.terminal_router import ToolChannel, _router, register
from src.harness.trace import (
    TraceBuffer,
    set_current_trace,
    clear_current_trace,
    with_trace,
)
from src.scripts.config import Config
from src.scripts.llm import LLMResponse, get_pms2_sekei_llm
from src.scripts.PMS2.mapper import run_mapper_for_firm
from src.scripts.PMS2.stencil_topo import _topo_sort_rows, _assign_structure

_MAX_TURNS = 8

# ── Period validation helpers (spec 19) ─────────────────────────────

_PERIOD_RE = re.compile(r"^(Q[1-4]|H[12])?FY\d{4}$")

_GRANULARITY_RE = {
    "annual":    re.compile(r"^FY\d{4}$"),
    "quarterly": re.compile(r"^Q[1-4]FY\d{4}$"),
    "half":      re.compile(r"^H[12]FY\d{4}$"),
}

_GRANULARITY_FMT = {
    "annual":    "FY{year} (e.g. FY2025)",
    "quarterly": "Q{n}FY{year} (e.g. Q3FY2025)",
    "half":      "H{n}FY{year} (e.g. H1FY2025)",
}


def _period_sort_key(period: str) -> tuple[int, int]:
    """Return (year, sub) for chronological sorting.

    FY2025      → (2025, 0)
    Q3FY2025    → (2025, 3)
    H2FY2025    → (2025, 2)
    Invalid     → (9999, 99)  sorts last
    """
    m = re.match(r"^(Q(\d+)|H(\d+))?FY(\d{4})$", period)
    if not m:
        return (9999, 99)
    year = int(m.group(4))
    if m.group(2) is not None:    # Q prefix
        sub = int(m.group(2))
    elif m.group(3) is not None:  # H prefix
        sub = int(m.group(3))
    else:                          # annual — no prefix
        sub = 0
    return (year, sub)


def _check_period_granularity_consistency(
    periods: list[str], granularity: str
) -> str | None:
    """Return error string if any period's format doesn't match granularity.

    Returns None if all periods are consistent.
    """
    pat = _GRANULARITY_RE.get(granularity)
    if pat is None:
        return f"Error: unknown granularity '{granularity}'."
    bad = [p for p in periods if not pat.fullmatch(p)]
    if bad:
        return (
            f"Error: periods {bad} inconsistent with "
            f"granularity='{granularity}'. "
            f"Expected format: {_GRANULARITY_FMT[granularity]}."
        )
    return None


# ── Sekei tool schemas (spec 17 § Sekei tools) ──────────────────────

SEKEI_TOOLS = [
    {
        "name": "ask_user",
        "description": (
            "Ask the user a question. Use for: sector folder selection, "
            "metric disambiguation, reporting currency confirmation, "
            "stencil confirmation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "run_mapper",
        "description": (
            "Run Mapper for confirmed firms. Spawns per-firm "
            "mapper agent loops in parallel, each walks "
            "data/files_ingested/ to build file inventory. "
            "Must be called AFTER ask_user confirms sector "
            "folders and currencies — not in the same turn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "firms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Confirmed firm list.",
                },
                "notes": {
                    "type": "string",
                    "description": (
                        "Freeform instructions for Mapper. "
                        "e.g. 'include 0 Optical/ for sector context' "
                        "or 'filings only, skip analyst notes'."
                    ),
                },
            },
            "required": ["firms"],
        },
    },
    {
        "name": "finalize_stencil",
        "description": (
            "Output the final stencil after user confirms. "
            "Formulas use {ExactMetricName} syntax. "
            "Do NOT assign row numbers or cell IDs — Python handles that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "firms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Confirmed firm list.",
                },
                "periods": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['FY2025', 'FY2026', 'FY2027']",
                },
                "granularity": {
                    "type": "string",
                    "enum": ["annual", "quarterly", "half"],
                    "description": "Granularity confirmed with user.",
                },
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "metric": {
                                "type": "string",
                                "description": (
                                    "Plain metric name. "
                                    "e.g. 'Revenue', 'D&A'. "
                                    "NOT globally unique — disambiguated "
                                    "by firm field."
                                ),
                            },
                            "firm": {
                                "type": "string",
                                "description": (
                                    "Which firm this row belongs to. "
                                    "Must match a value in firms array."
                                ),
                            },
                            "type": {
                                "type": "string",
                                "enum": ["retrieve", "compute"],
                            },
                            "formula": {
                                "type": "string",
                                "description": (
                                    "Uses {ExactMetricName} refs "
                                    "(plain names, same for all firms). "
                                    "e.g. '{Gross Profit}/{Revenue}'. "
                                    "Only for compute rows. "
                                    "Refs resolved within same firm block."
                                ),
                            },
                            "timeframe": {"type": "string"},
                            "unit": {
                                "type": "string",
                                "description": (
                                    "What kind of thing: 'USD', 'JPY', "
                                    "'float'. From canonical units.json."
                                ),
                            },
                            "ans": {
                                "type": "boolean",
                                "description": (
                                    "true if user requested this metric."
                                ),
                            },
                        },
                        "required": [
                            "metric", "firm", "type",
                            "timeframe", "unit",
                        ],
                    },
                },
            },
            "required": ["firms", "periods", "granularity", "rows"],
        },
    },
]

TERMINAL_TOOLS = {"finalize_stencil"}
EXPLORATION_TOOLS = {"run_mapper"}


# ── Sekei agent loop ─────────────────────────────────────────────────

def run_sekei(
    query: str,
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
    debug_dir: Path | None = None,
    top_level_dirs: list[str] | None = None,
) -> tuple[dict, dict, list[dict], dict]:
    """Run Sekei agent loop.

    Returns (work_stencil, ans_stencil, job_stencils, file_inventories).
    Raises RuntimeError if MAX_TURNS exhausted without finalize.
    channel param kept for pipeline-level logging (unused internally).
    """
    sekei_ch = register("PMS2-Sekei")

    # Load system prompt with template vars
    valid_units = json.loads(
        (Path(__file__).parent / "hardcode_dependencies" / "units.json")
        .read_text()
    )

    sys_prompt = load_sysprompt(
        "pms2_sekei",
        config.pms2_sekei_profile,
        query=query,
        top_level_dirs=json.dumps(top_level_dirs or []),
        valid_units=json.dumps(valid_units),
    )

    backend = get_pms2_sekei_llm(config)

    # Mutable closure state — run_mapper handler writes, finalize reads
    file_inventories: dict[str, list[dict]] = {}
    # Hard gate: LLM must not finalize until user explicitly confirms
    user_confirmed = False

    # Trace
    trace = TraceBuffer("PMS2-sekei")
    set_current_trace(trace)

    messages: list[dict] = [{"role": "user", "content": "Begin."}]
    turn_counter = 0

    try:
        while turn_counter < _MAX_TURNS:
            _router.start_spinner("PMS2-Sekei")
            try:
                response = backend.call_with_tools(
                    messages=messages,
                    system_prompt=sys_prompt,
                    tools=SEKEI_TOOLS,
                    label="PMS2-sekei",
                )
            finally:
                _router.stop_spinner()

            # Print LLM text (stencil previews, explanations, etc.)
            if response.text and response.text.strip():
                sekei_ch.print(response.text, markdown=True)

            # end_turn without tool call = error
            if response.stop_reason == "end_turn":
                messages.append(response.to_assistant_message())
                messages.append({
                    "role": "user",
                    "content": (
                        "Error: you must call finalize_stencil, "
                        "ask_user, or run_mapper. Do not end your "
                        "turn without calling a tool."
                    ),
                })
                turn_counter += 1
                continue

            # Classify tool calls for same-turn guards
            has_ask_user = any(
                t.name == "ask_user" for t in response.tool_calls
            )
            has_explore = any(
                t.name in EXPLORATION_TOOLS for t in response.tool_calls
            )
            terminal_count = sum(
                1 for t in response.tool_calls
                if t.name in TERMINAL_TOOLS
            )

            def _dispatch_tc(tc):
                nonlocal user_confirmed

                if tc.name == "ask_user":
                    answer = sekei_ch.input(tc.input["question"], markdown=True)
                    trace.record_user_interaction(
                        tc.input["question"], answer
                    )
                    # Set confirmation flag if answer looks affirmative
                    if re.match(r"^\s*(y|yes|ok|confirm|lgtm)\b", answer, re.I):
                        user_confirmed = True
                    else:
                        user_confirmed = False
                    return tc, f"User answered: {answer}", None

                elif tc.name == "finalize_stencil":
                    # Guard: reject if ask_user or exploration also
                    # called this turn (results would be lost)
                    if has_ask_user or has_explore:
                        return tc, (
                            "Error: cannot finalize in same turn as "
                            "ask_user or run_mapper. Call finalize_stencil "
                            "alone in a separate turn."
                        ), None
                    if terminal_count > 1:
                        return tc, (
                            "Error: cannot call multiple terminal tools "
                            "in the same turn."
                        ), None

                    # Hard gate: user must have confirmed via ask_user
                    if not user_confirmed:
                        ans = sekei_ch.input(
                            "Finalize stencil? [y/n]"
                        ).strip().lower()
                        if not ans.startswith("y"):
                            return tc, (
                                "Error: user rejected finalization. "
                                "Call ask_user to show the stencil and "
                                "get confirmation before retrying."
                            ), None

                    data, status = _handle_finalize(
                        tc.input, file_inventories, session_dir,
                    )
                    return tc, status, data

                elif tc.name == "run_mapper":
                    # Guard: must not fire in same turn as ask_user
                    # (needs sector answer first)
                    if has_ask_user:
                        return tc, (
                            "Error: cannot run_mapper in same turn "
                            "as ask_user. Call run_mapper in a separate "
                            "turn after receiving the user's answer."
                        ), None
                    result = _handle_run_mapper(
                        tc.input, config, query, sekei_ch, trace,
                        debug_dir or (session_dir / "pms2"),
                    )
                    # Store inventories as opaque handles
                    summary_parts = []
                    for firm_name, inv in result.items():
                        handle = registry.store(
                            inv, f"{firm_name} file inventory"
                        )
                        file_inventories[firm_name] = handle
                        summary_parts.append(
                            f"{firm_name}: {len(inv)} files ({handle})"
                        )
                    return tc, (
                        f"Mapper complete: {', '.join(summary_parts)}. "
                        f"Proceed to stencil design."
                    ), None

                return tc, f"Error: unknown tool '{tc.name}'.", None

            # Dispatch all tool calls (parallel via ThreadPoolExecutor)
            tool_results = []
            terminal_result = None

            with ThreadPoolExecutor() as pool:
                futures = {
                    pool.submit(_dispatch_tc, tc): tc
                    for tc in response.tool_calls
                }
                for fut in as_completed(futures):
                    tc, content, t_result = fut.result()
                    tool_results.append({
                        "id": tc.id,
                        "name": tc.name,
                        "content": content,
                    })
                    if tc.name == "ask_user":
                        turn_counter = 0  # intentional: no ceiling on user interaction
                    if t_result is not None:
                        terminal_result = t_result

            messages.append(response.to_assistant_message())
            messages.append(
                LLMResponse.make_tool_results_message(tool_results)
            )

            # Terminal tool succeeded → exit loop
            if terminal_result is not None:
                return terminal_result

            turn_counter += 1

        raise RuntimeError(
            f"Sekei agent loop exhausted {_MAX_TURNS} turns "
            f"without finalizing stencil."
        )

    finally:
        flush_dir = debug_dir or (session_dir / "pms2")
        try:
            trace.flush_to_disk(flush_dir)
        except Exception:
            pass
        clear_current_trace()


# ── run_mapper handler ───────────────────────────────────────────────

def _handle_run_mapper(
    params: dict,
    config: Config,
    query: str,
    channel: ToolChannel,
    parent_trace: TraceBuffer,
    debug_dir: Path,
) -> dict[str, list[dict]]:
    """Spawn per-firm mapper agent loops in parallel.

    Returns {firm: [file_inventory_dicts]}.
    """
    firms = params.get("firms", [])
    notes = params.get("notes", "")
    data_dir = config.pms2_data_dir

    # Load manifest once, share across all mapper threads
    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(
            f"manifest.json not found at {manifest_path}. "
            f"Run 00_Ingest.py first."
        )
    manifest = json.loads(manifest_path.read_text())

    results: dict[str, list[dict]] = {}

    def _run_one(firm: str) -> tuple[str, list[dict]]:
        firm_channel = terminal_router.register(f"PMS2-map-{firm}")
        inventory = run_mapper_for_firm(
            firm=firm,
            query=query,
            notes=notes,
            config=config,
            data_dir=data_dir,
            manifest=manifest,
            channel=firm_channel,
            debug_dir=debug_dir,
        )
        return firm, inventory

    # Parallel per-firm mappers, each with trace inheritance
    with ThreadPoolExecutor(max_workers=len(firms)) as pool:
        futures = {
            pool.submit(with_trace(parent_trace, _run_one), firm): firm
            for firm in firms
        }
        for fut in as_completed(futures):
            firm = futures[fut]
            try:
                firm_name, inventory = fut.result()
                results[firm_name] = inventory
                channel.print(
                    f"Mapper {firm_name}: {len(inventory)} files catalogued."
                )
            except Exception as e:
                channel.print(f"Mapper {firm} failed: {e}")
                results[firm] = []

    return results


# ── finalize_stencil handler ─────────────────────────────────────────

def _handle_finalize(
    params: dict,
    file_inventories: dict,
    session_dir: Path,
) -> tuple[tuple | None, str]:
    """Process finalize_stencil tool call.

    Returns (data, status_string).
      data = (work, ans, jobs, file_inventories) on success.
      data = None on validation/topo sort/formula error (LLM retries).
      status_string = always present, sent to LLM as tool result.
    """
    try:
        # ── Step 1: Validate granularity ──────────────────────────────
        granularity = params.get("granularity")
        if granularity not in ("annual", "quarterly", "half"):
            return None, (
                f"Error: granularity '{granularity}' invalid. "
                f"Must be one of: annual, quarterly, half."
            )

        # ── Step 2: Validate firms ────────────────────────────────────
        submitted_firms = params.get("firms", [])
        if not submitted_firms:
            return None, "Error: firms list is empty."

        # ── Step 3: Validate + canonicalize periods ───────────────────
        raw_periods = params.get("periods", [])
        if not raw_periods:
            return None, "Error: periods list is empty."

        # Dedup, preserve order
        submitted_periods = list(dict.fromkeys(raw_periods))

        # Regex validation
        bad_format = [p for p in submitted_periods if not _PERIOD_RE.fullmatch(p)]
        if bad_format:
            return None, (
                f"Error: periods {bad_format} have invalid format. "
                f"Use canonical format: FY{{year}}, Q{{n}}FY{{year}}, "
                f"or H{{n}}FY{{year}} with 4-digit year (e.g. Q3FY2025)."
            )

        # Granularity consistency
        consistency_err = _check_period_granularity_consistency(
            submitted_periods, granularity
        )
        if consistency_err:
            return None, consistency_err

        # Chronological sort
        periods = sorted(submitted_periods, key=_period_sort_key)

        # ── Step 4: Validate rows ─────────────────────────────────────
        rows = params.get("rows", [])
        if not rows:
            return None, "Error: no rows in stencil."

        # Every row's firm must be in submitted_firms
        for row in rows:
            if row.get("firm") not in submitted_firms:
                return None, (
                    f"Error: row '{row.get('metric', '?')}' has "
                    f"firm '{row.get('firm')}' not in firms list "
                    f"{submitted_firms}."
                )

        # ── Step 5: Topo sort ─────────────────────────────────────────
        sorted_rows = _topo_sort_rows(rows, submitted_firms)

        # ── Step 6: Assign structure (row nums, Rn formulas, stencils)
        work, ans, jobs = _assign_structure(
            sorted_rows, periods, submitted_firms, granularity
        )

        # ── Step 7: Save to disk (Phase 0 snapshot — pre-fill) ───────
        pms2_dir = session_dir / "pms2"
        pms2_dir.mkdir(parents=True, exist_ok=True)
        (pms2_dir / "work_stencil.json").write_text(
            json.dumps(work, indent=2, ensure_ascii=False)
        )

        # ── Summary ──────────────────────────────────────────────────
        fin_ch = register("PMS2-StencilFinalizer")
        total_rows = len(work["rows"])
        total_cells = len(work["values"])
        ans_metrics = ans["metrics"]
        summary = (
            f"Stencil finalized: {total_rows} rows × "
            f"{len(periods)} periods = {total_cells} cells. "
            f"{len(ans_metrics)} ans metrics: "
            f"{', '.join(ans_metrics)}. "
            f"Topo sort OK. Formulas rewritten to Rn notation. "
            f"Saved to {pms2_dir / 'work_stencil.json'}."
        )
        fin_ch.print(summary)

        return (work, ans, jobs, file_inventories), summary

    except ValueError as e:
        return None, f"Error: {e}"
