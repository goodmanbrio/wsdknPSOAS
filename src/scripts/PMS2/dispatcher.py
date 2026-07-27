"""Phase 1: Dispatcher — per-firm extraction loop.

M3b scope: full loop + FiscalCalResolver. run_leng_caller stubbed
in batch_planner.py (no real extraction). Proves control flow,
exhaustion check, parallel dispatchers, structured_complete.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path

from src.harness.terminal_router import ToolChannel
from src.harness.trace import (
    TraceBuffer,
    set_current_trace,
    clear_current_trace,
)
from src.scripts.config import Config
from src.scripts.llm import get_pms2_fiscal_cal_llm
from src.scripts.PMS2.batch_planner import run_batch_planner

_MAX_DRY_RUNS = 2  # consecutive iterations with 0 new cells -> force ask user

_FISCAL_CAL_MAX_RETRIES = 3


@dataclass
class DispatcherState:
    """Per-firm state owned by the Dispatcher loop."""
    firm: str
    job_stencil: dict
    file_inventory: list[dict]
    searched_files: dict
    iteration: int
    dry_run_count: int
    status: str  # "running" | "complete" | "user_aborted" | "bp_exhausted"
    stencil_lock: threading.Lock
    fiscal_calendar: dict | None


# ── Helper cell pruning ──────────────────────────────────────────────


def _get_null_ans_cells(job_stencil: dict) -> list[str]:
    """Return cell IDs for ans rows that are still null."""
    null_cells = []
    for row_num, row in job_stencil["rows"].items():
        if not row.get("ans"):
            continue
        for col in job_stencil["col_letters"]:
            cell_id = f"{col}{row_num}"
            if job_stencil["values"].get(cell_id) is None:
                null_cells.append(cell_id)
    return null_cells


def _prune_satisfied_helpers(
    job_stencil: dict, null_ans_cells: list[str],
) -> list[str]:
    """Return cells to search: null ans cells + unsatisfied helpers.

    Cell-level pruning: a helper CELL {col}{row} is excluded if
    ALL consumer cells in the SAME COLUMN are already filled.
    """
    active = list(null_ans_cells)

    for row_num, row in job_stencil["rows"].items():
        if row.get("ans"):
            continue
        feeds_list = row.get("feeds_rows")
        if not feeds_list:
            continue

        for col in job_stencil["col_letters"]:
            cell_id = f"{col}{row_num}"
            if job_stencil["values"].get(cell_id) is not None:
                continue

            any_consumer_null = any(
                job_stencil["values"].get(f"{col}{feeds}") is None
                for feeds in feeds_list
            )
            if any_consumer_null:
                active.append(cell_id)

    return active


def _describe_missing_cells(
    job_stencil: dict, null_ans_cells: list[str],
) -> str:
    """Human-readable description of still-missing ans cells."""
    rows = job_stencil["rows"]
    descriptions = []
    for cell_id in null_ans_cells[:10]:  # cap at 10 for readability
        row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        col = cell_id[: len(cell_id) - len(row_num)]
        row = rows.get(row_num, {})
        metric = row.get("metric", "?")
        descriptions.append(f"{cell_id} ({metric})")
    suffix = ""
    if len(null_ans_cells) > 10:
        suffix = f" ... +{len(null_ans_cells) - 10} more"
    return ", ".join(descriptions) + suffix


# ── Fiscal Calendar Resolver ─────────────────────────────────────────


def _resolve_fiscal_calendar(
    firm: str,
    periods: list[str],
    config: Config,
    channel: ToolChannel,
) -> dict | None:
    """Look up firm's fiscal year-end, return period->date mapping.

    3 attempts via structured_complete(web_search=True).
    On total failure, falls back to ask_user for freeform
    fiscal calendar info, then structured_complete (no
    web_search) to parse user text into calendar dict.
    Returns None only if everything fails.
    """
    backend = get_pms2_fiscal_cal_llm(config)

    schema = {
        "type": "object",
        "properties": {
            "fy_end_month_day": {
                "type": "string",
                "description": "Fiscal year end as 'Month Day', e.g. 'June 30', 'December 31'",
            },
            "calendar": {
                "type": "object",
                "additionalProperties": {
                    "type": "string",
                    "description": "Date range, e.g. 'Jul 1 2024 - Sep 30 2024'",
                },
                "description": "Map of each period label to calendar date range",
            },
        },
        "required": ["fy_end_month_day", "calendar"],
    }

    web_prompt = (
        f"What is {firm}'s fiscal year-end date? "
        f"Search the web to confirm.\n\n"
        f"Then compute the calendar date range for each of "
        f"these periods:\n"
        + "\n".join(f"  {p}" for p in periods)
        + "\n\nFor example, if the company's fiscal year ends "
        f"June 30, then Q1FY2025 = Jul 1 2024 - Sep 30 2024, "
        f"H1FY2025 = Jul 1 2024 - Dec 31 2024, "
        f"FY2025 = Jul 1 2024 - Jun 30 2025."
    )

    # --- Phase 1: web search (3 attempts) ---
    for attempt in range(_FISCAL_CAL_MAX_RETRIES):
        try:
            result = backend.structured_complete(
                prompt=web_prompt,
                schema=schema,
                label=f"PMS2-fiscal-{firm}",
                web_search=True,
            )
            calendar = result.get("calendar", {})
            if calendar:
                channel.print(
                    f"[PMS2-fiscal-{firm}] FY ends "
                    f"{result.get('fy_end_month_day', '?')}. "
                    f"Calendar: {len(calendar)} periods resolved."
                )
                return calendar
        except Exception:
            pass
        channel.print(
            f"[PMS2-fiscal-{firm}] attempt {attempt + 1}/"
            f"{_FISCAL_CAL_MAX_RETRIES} failed"
        )

    # --- Phase 2: ask user fallback ---
    user_text = channel.input(
        f"[PMS2-fiscal-{firm}] fiscal cal resolver broken atm. "
        f"what is the calendar equivalent of {firm}'s fiscal "
        f"years/halves/quarters?"
    )

    parse_prompt = (
        f"The user provided this fiscal calendar info for {firm}:\n"
        f'"{user_text}"\n\n'
        f"Parse it into calendar date ranges for these periods:\n"
        + "\n".join(f"  {p}" for p in periods)
    )

    try:
        result = backend.structured_complete(
            prompt=parse_prompt,
            schema=schema,
            label=f"PMS2-fiscal-{firm}-user",
            web_search=False,
        )
        calendar = result.get("calendar", {})
        if calendar:
            channel.print(
                f"[PMS2-fiscal-{firm}] parsed from user input. "
                f"{len(calendar)} periods resolved."
            )
            return calendar
    except Exception:
        pass

    # --- Total failure ---
    channel.print(
        f"[PMS2-fiscal-{firm}] fiscalcalresolver shat the bed. "
        f"forcing onward with no fiscal cal dict"
    )
    return None


# ── Dispatcher loop ──────────────────────────────────────────────────


def run_dispatcher(
    firm: str,
    job_stencil: dict,
    file_inventory: list[dict],
    granularity: str,
    periods: list[str],
    channel: ToolChannel,
    config: Config,
    docstore,
    file_path_index: dict,
    debug_dir: Path | None = None,
) -> dict:
    """Phase 1 extraction loop for one firm.

    Returns filled job_stencil with status field.
    """
    stencil_lock = threading.Lock()

    # Trace: covers pre-loop (fiscal cal) + iteration 0
    trace = TraceBuffer(f"PMS2-disp-{firm}")
    set_current_trace(trace)

    try:
        # PRE-LOOP: Fiscal Calendar Resolver (non-annual only)
        fiscal_calendar = None
        if granularity != "annual":
            fiscal_calendar = _resolve_fiscal_calendar(
                firm=firm,
                periods=periods,
                config=config,
                channel=channel,
            )

        state = DispatcherState(
            firm=firm,
            job_stencil=job_stencil,
            file_inventory=file_inventory,
            searched_files={},
            iteration=0,
            dry_run_count=0,
            status="running",
            stencil_lock=stencil_lock,
            fiscal_calendar=fiscal_calendar,
        )

        while state.status == "running":
            # Flush + new trace at start of subsequent iterations
            if state.iteration > 0:
                flush_dir = debug_dir or Path("tests/debug")
                try:
                    trace.flush_to_disk(flush_dir)
                except Exception:
                    pass
                trace = TraceBuffer(f"PMS2-disp-{firm}")
                set_current_trace(trace)

            # Step 1: check for remaining null ans cells
            null_ans_cells = _get_null_ans_cells(state.job_stencil)
            if not null_ans_cells:
                state.status = "complete"
                break

            # Step 2: helper cell pruning
            active_cells = _prune_satisfied_helpers(
                state.job_stencil, null_ans_cells
            )

            # Snapshot filled count before BP runs
            filled_before = sum(
                1 for v in state.job_stencil["values"].values()
                if v is not None
            )

            # Step 3: call Batch Planner (agent loop)
            channel.print(
                f"Dispatcher [{firm}] iter {state.iteration}: "
                f"{len(null_ans_cells)} null ans cells, "
                f"{len(active_cells)} active cells"
            )

            bp_result = run_batch_planner(
                firm=state.firm,
                job_stencil=state.job_stencil,
                active_cells=active_cells,
                file_inventory=state.file_inventory,
                searched_files=state.searched_files,
                iteration=state.iteration,
                stencil_lock=stencil_lock,
                fiscal_calendar=state.fiscal_calendar,
                channel=channel,
                config=config,
                docstore=docstore,
                file_path_index=file_path_index,
                debug_dir=debug_dir,
            )

            # Step 4: check BP exit reason
            if bp_result == "exhausted":
                state.status = "bp_exhausted"
                break

            # Step 5: dry run safety net
            null_ans_cells = _get_null_ans_cells(state.job_stencil)
            if not null_ans_cells:
                state.status = "complete"
                break

            filled_after = sum(
                1 for v in state.job_stencil["values"].values()
                if v is not None
            )
            if filled_after > filled_before:
                state.dry_run_count = 0
            else:
                state.dry_run_count += 1

            if state.dry_run_count >= _MAX_DRY_RUNS:
                missing_desc = _describe_missing_cells(
                    state.job_stencil, null_ans_cells
                )
                answer = channel.input(
                    f"{state.iteration + 1} iterations for {firm}, "
                    f"{state.dry_run_count} found nothing. "
                    f"Still missing: {missing_desc}. continue? [y/n]"
                )
                if answer.strip().lower().startswith("y"):
                    state.dry_run_count = 0
                else:
                    state.status = "user_aborted"
                    break

            state.iteration += 1

        job_stencil["status"] = state.status
        channel.print(
            f"Dispatcher [{firm}] done: {state.status} "
            f"after {state.iteration + 1} iteration(s)"
        )
        return job_stencil

    finally:
        flush_dir = debug_dir or Path("tests/debug")
        try:
            trace.flush_to_disk(flush_dir)
        except Exception:
            pass
        clear_current_trace()
