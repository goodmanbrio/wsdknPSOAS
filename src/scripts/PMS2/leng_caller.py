"""LengCaller — chunk fetch -> Leng x N -> Validator per hit.

Pure Python orchestration, no LLM reasoning at this level.
Called by Batch Planner's run_leng_caller terminal tool.
"""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading

from src.harness.sysprompts import load_sysprompt
from src.harness.terminal_router import ToolChannel, register, _router
from src.harness.trace import (
    TraceBuffer,
    set_current_trace,
    clear_current_trace,
    with_trace,
)
from src.scripts.config import Config
from src.scripts.llm import get_pms2_leng_llm
from src.scripts.PMS2.denom_reconcile import DENOM_FACTORS
from src.scripts.PMS2.validator_loop import run_validator
from src.scripts.PMS2.sekei_loop import _period_sort_key

LENG_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {
            "type": "boolean",
            "description": "true if any cell values found in this chunk",
        },
        "cells": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "value": {"type": "number", "description": "Value as-written in source, NOT scaled"},
                    "denom": {"type": "string", "description": "Denomination: units, k, mn, bn, tn, %, bps, or unknown"},
                    "unit": {"type": "string", "description": "Currency or type: USD, JPY, EUR, GBP, CNY, float"},
                },
                "required": ["value", "denom", "unit"],
            },
            "description": "Map of cell_id to extracted value. Only cells found in this chunk.",
        },
    },
    "required": ["found", "cells"],
}

_VALID_DENOMS = list(DENOM_FACTORS.keys())
_LENG_MALFORMED_RETRIES = 2
_COUNTER_INTERVAL = 25  # print progress every N completions


def _build_cell_descriptions(
    cells: list[str],
    job_stencil: dict,
    firm: str,
) -> str:
    """Build cell description text for Leng prompt."""
    lines = [f"Firm: {firm}"]
    for cell_id in cells:
        row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        col = cell_id[: len(cell_id) - len(row_num)]
        row = job_stencil["rows"].get(row_num, {})
        col_idx = (
            job_stencil["col_letters"].index(col)
            if col in job_stencil["col_letters"]
            else -1
        )
        period = (
            job_stencil["periods"][col_idx]
            if 0 <= col_idx < len(job_stencil["periods"])
            else "?"
        )
        timeframe = row.get("timeframe", "?")
        unit = row.get("unit", "?")
        metric = row.get("metric", "?")
        rtype = row.get("type", "?")
        lines.append(
            f"  {cell_id} = {metric}, {period}, {timeframe}, "
            f"type={rtype}, unit={unit}"
        )
    return "\n".join(lines)


def _build_fiscal_calendar_text(fiscal_calendar: dict | None) -> str:
    if not fiscal_calendar:
        return ""
    lines = ["Fiscal calendar:"]
    for period, date_range in fiscal_calendar.items():
        lines.append(f"  {period} = {date_range}")
    return "\n".join(lines)


def _print_plan_table(
    plan: list[dict],
    job_stencil: dict,
    channel: ToolChannel,
) -> None:
    """Print metric-centric plan table via channel.print."""
    col_letters = job_stencil["col_letters"]
    periods = job_stencil["periods"]
    rows = job_stencil["rows"]

    metric_info: dict[str, dict] = {}  # {metric: {periods: set, files: list}}

    for entry in plan:
        fp = entry["file"]
        for cell_id in entry["cells"]:
            row_num = cell_id.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            col = cell_id[: len(cell_id) - len(row_num)]
            row = rows.get(row_num, {})
            metric = row.get("metric", "?")
            col_idx = col_letters.index(col) if col in col_letters else -1
            period = periods[col_idx] if 0 <= col_idx < len(periods) else "?"

            if metric not in metric_info:
                metric_info[metric] = {"periods": set(), "files": []}
            metric_info[metric]["periods"].add(period)
            if fp not in metric_info[metric]["files"]:
                metric_info[metric]["files"].append(fp)

    lines = [
        "Batch plan:",
        "| Metric | Periods | Files |",
        "|--------|---------|-------|",
    ]
    for metric, info in metric_info.items():
        ps = " ".join(sorted(info["periods"], key=_period_sort_key))
        fs = ", ".join(info["files"])
        lines.append(f"| {metric} | {ps} | {fs} |")

    channel.print("\n".join(lines), markdown=True)


def _run_single_leng(
    chunk_text: str,
    node_id: str,
    cell_descriptions: str,
    fiscal_calendar_text: str,
    firm: str,
    backend,
    sys_prompt: str,
) -> tuple[str, dict]:
    """Run one Leng structured_complete call for one chunk.

    Returns (node_id, leng_output_dict).
    Retries up to _LENG_MALFORMED_RETRIES times if Haiku returns
    cells as a string instead of a dict (known failure mode).
    """
    prompt = (
        f"{cell_descriptions}\n\n"
        f"{fiscal_calendar_text}\n\n"
        f"--- CHUNK (node_id: {node_id}) ---\n"
        f"{chunk_text}\n"
        f"--- END CHUNK ---"
    )

    for attempt in range(1 + _LENG_MALFORMED_RETRIES):
        result = backend.structured_complete(
            prompt=prompt,
            schema=LENG_OUTPUT_SCHEMA,
            system_prompt=sys_prompt,
            label=f"PMS2-leng-{firm}",
        )
        cells = result.get("cells", {})
        if isinstance(cells, dict):
            valid = all(isinstance(v, dict) and "value" in v for v in cells.values())
            if not valid:
                continue  # retry (malformed cell values)
            return node_id, result
        # Malformed — retry

    # All retries exhausted: mark as error, return empty
    result["_malformed_cells"] = True
    result["cells"] = {}
    return node_id, result


def _run_validator_wrapper(
    file_path: str,
    node_id: str,
    chunk_text: str,
    leng_cells: dict,
    cell_descriptions_for_validator: str,
    fiscal_calendar_text: str,
    firm: str,
    job_stencil: dict,
    stencil_lock: threading.Lock,
    config: Config,
    channel: ToolChannel,
) -> tuple[str, list[tuple]]:
    """Run one Validator for one Leng hit. Returns (file_path, cell_outcomes)."""
    cell_outcomes = run_validator(
        firm=firm,
        node_id=node_id,
        chunk_text=chunk_text,
        leng_cells=leng_cells,
        cell_descriptions=cell_descriptions_for_validator,
        fiscal_calendar_text=fiscal_calendar_text,
        job_stencil=job_stencil,
        stencil_lock=stencil_lock,
        config=config,
        channel=channel,
    )
    return file_path, cell_outcomes


def run_leng_caller(
    plan: list[dict],
    job_stencil: dict,
    stencil_lock: threading.Lock,
    searched_files: dict,
    fiscal_calendar: dict | None,
    docstore,
    file_path_index: dict,
    config: Config,
    channel: ToolChannel,
    firm: str,
    debug_dir: Path | None = None,
) -> str:
    """Execute extraction plan: chunk fetch -> Leng x N -> Validator per hit.

    Returns "complete".
    """
    leng_ch = register(f"PMS2-leng-{firm}")
    val_ch = register(f"PMS2-val-{firm}")
    trace = TraceBuffer(f"PMS2-lv-{firm}")
    set_current_trace(trace)

    try:
        fiscal_cal_text = _build_fiscal_calendar_text(fiscal_calendar)

        # Pre-build shared Leng resources (one backend + one sys_prompt)
        valid_units = json.loads(
            (Path(__file__).parent / "hardcode_dependencies" / "units.json")
            .read_text()
        )
        leng_backend = get_pms2_leng_llm(config)
        leng_sys_prompt = load_sysprompt(
            "pms2_leng",
            config.pms2_leng_profile,
            firm=firm,
            valid_denoms=json.dumps(_VALID_DENOMS),
            valid_units=json.dumps(valid_units),
            fiscal_calendar=fiscal_cal_text if fiscal_cal_text else "Not available.",
        )

        # Build per-file cell lists from plan (merge duplicates)
        plan_entry_cells: dict[str, list[str]] = {}
        for entry in plan:
            fp = entry["file"]
            cells = entry["cells"]
            if fp in plan_entry_cells:
                for c in cells:
                    if c not in plan_entry_cells[fp]:
                        plan_entry_cells[fp].append(c)
            else:
                plan_entry_cells[fp] = list(cells)

        # ── STEP 1: Chunk fetch (all files) ─────────────────────────
        # Collect all (file_path, node_id, chunk_text, cells) tuples
        leng_tasks = []  # (file_path, node_id, chunk_text, cells)

        for file_path, cells in plan_entry_cells.items():
            fpi_entry = file_path_index.get(file_path)
            if fpi_entry is None:
                leng_ch.print(
                    f"file_path_index miss: {file_path} — skipping"
                )
                continue

            node_ids = fpi_entry.get("node_ids", [])
            chunks = []
            for nid in node_ids:
                try:
                    doc = docstore.get_document(nid)
                    chunks.append((nid, doc))
                except Exception:
                    leng_ch.print(f"docstore miss: {nid}")

            # Sort by chunk_index
            chunks.sort(
                key=lambda x: x[1].metadata.get("chunk_index", 0)
            )

            cell_desc = _build_cell_descriptions(cells, job_stencil, firm)

            for nid, doc in chunks:
                leng_tasks.append((file_path, nid, doc.text, cells, cell_desc))

        if not leng_tasks:
            leng_ch.print(
                f"no chunks to process "
                f"({len(plan)} plan entries, all missed)."
            )
            # Still update searched_files
            for fp, cells in plan_entry_cells.items():
                if fp not in searched_files:
                    searched_files[fp] = {
                        "cells_searched": cells,
                        "cells_found": [],
                        "rejections": {},
                        "leng_errors": [],
                    }
            return "complete"

        _print_plan_table(plan, job_stencil, channel)

        leng_ch.print(
            f"{len(leng_tasks)} chunks across "
            f"{len(plan_entry_cells)} files. Firing Lengs..."
        )

        # ── STEP 2: Leng x N (all parallel) ────────────────────────
        leng_results = []  # (file_path, node_id, leng_output)
        leng_errors_by_file: dict[str, list] = defaultdict(list)

        _router.start_spinner(f"PMS2-leng-{firm}")
        try:
            with ThreadPoolExecutor(max_workers=config.pms2_leng_max_workers) as pool:
                leng_futures = {}
                for fp, nid, chunk_text, cells, cell_desc in leng_tasks:
                    fut = pool.submit(
                        with_trace(trace, _run_single_leng),
                        chunk_text, nid, cell_desc, fiscal_cal_text,
                        firm, leng_backend, leng_sys_prompt,
                    )
                    leng_futures[fut] = (fp, nid, chunk_text, cells)

                done_count = 0
                hit_count = 0
                for fut in as_completed(leng_futures):
                    fp, nid, chunk_text, cells = leng_futures[fut]
                    try:
                        _, leng_output = fut.result()
                        leng_results.append((fp, nid, chunk_text, cells, leng_output))
                        if leng_output.get("cells"):
                            hit_count += 1
                    except Exception as e:
                        leng_ch.print(f"⚠ Leng chunk {nid} crashed: {e}")
                        leng_errors_by_file[fp].append(f"{nid}: {e}")
                    done_count += 1
                    if done_count % _COUNTER_INTERVAL == 0 or done_count == len(leng_futures):
                        leng_ch.print(
                            f"{done_count}/{len(leng_futures)} "
                            f"Lengs done ({hit_count} hits)"
                        )
        finally:
            _router.stop_spinner()

        # ── STEP 3: Validator per hit (parallel) ────────────────────
        # Collect valid cell IDs from the job stencil for filtering
        valid_cell_ids = set(job_stencil["values"].keys())

        hits = []
        for fp, nid, chunk_text, cells, leng_output in leng_results:
            if leng_output.get("_malformed_cells"):
                leng_errors_by_file[fp].append(
                    f"{nid}: malformed cells after "
                    f"{1 + _LENG_MALFORMED_RETRIES} attempts"
                )
                continue
            leng_cells = leng_output.get("cells", {})
            # Haiku frequently omits the `found` field. Infer from
            # non-empty cells map — if cells exist, it's a hit.
            found = leng_output.get("found", bool(leng_cells))
            if found and leng_cells:
                # Filter/recover hallucinated cell IDs.
                # Haiku sometimes returns "A1_Revenue_Q3FY2026" instead
                # of "A1". Try exact match first, then prefix recovery:
                # if hallucinated ID starts with a valid cell ID followed
                # by a non-alphanumeric char, recover the valid prefix.
                filtered = {}
                for cid, v in leng_cells.items():
                    if cid in valid_cell_ids:
                        filtered[cid] = v
                    else:
                        # Prefix recovery: "A1_Revenue" -> "A1"
                        recovered = None
                        for valid_id in valid_cell_ids:
                            if (cid.startswith(valid_id)
                                    and len(cid) > len(valid_id)
                                    and not cid[len(valid_id)].isalnum()):
                                recovered = valid_id
                                break
                        if recovered and recovered not in filtered:
                            filtered[recovered] = v
                if filtered:
                    hits.append((fp, nid, chunk_text, cells, filtered))

        leng_ch.print(
            f"{len(hits)} hits from "
            f"{len(leng_results)} Leng calls. Spawning Validators..."
        )

        # Build validator-specific cell descriptions per hit
        this_run_found: dict[str, list] = defaultdict(list)
        this_run_rejections: dict[str, dict] = defaultdict(dict)

        if hits:
            _router.start_spinner(f"PMS2-val-{firm}")
            try:
                with ThreadPoolExecutor(max_workers=config.pms2_validator_max_workers) as pool:
                    validator_futures = {}
                    for fp, nid, chunk_text, cells, leng_cells in hits:
                        # Build cell descriptions specifically for found cells
                        val_cell_desc = _build_cell_descriptions(
                            list(leng_cells.keys()), job_stencil, firm,
                        )
                        fut = pool.submit(
                            with_trace(trace, _run_validator_wrapper),
                            fp, nid, chunk_text, leng_cells,
                            val_cell_desc, fiscal_cal_text,
                            firm, job_stencil, stencil_lock,
                            config, channel,
                        )
                        validator_futures[fut] = fp

                    done_count = 0
                    written_count = 0
                    rejected_count = 0
                    for fut in as_completed(validator_futures):
                        fp = validator_futures[fut]
                        try:
                            file_path, cell_outcomes = fut.result()
                            for cid, out, reason in cell_outcomes:
                                if out in ("written", "skipped"):
                                    this_run_found[file_path].append(cid)
                                    written_count += 1
                                elif out == "rejected" and reason:
                                    this_run_rejections[file_path][cid] = reason
                                    rejected_count += 1
                        except Exception as e:
                            val_ch.print(f"⚠ crashed: {e}")
                        done_count += 1
                        if done_count % _COUNTER_INTERVAL == 0 or done_count == len(validator_futures):
                            val_ch.print(
                                f"{done_count}/{len(validator_futures)} "
                                f"Validators done "
                                f"({written_count} written, {rejected_count} rejected)"
                            )
            finally:
                _router.stop_spinner()

        # ── STEP 4: Update searched_files ───────────────────────────
        for fp, cells in plan_entry_cells.items():
            existing = searched_files.get(fp)
            if existing:
                existing["cells_searched"] = list(set(
                    existing["cells_searched"] + cells
                ))
                existing["cells_found"] = list(set(
                    existing["cells_found"] + this_run_found.get(fp, [])
                ))
                existing["rejections"].update(
                    this_run_rejections.get(fp, {})
                )
                existing["leng_errors"].extend(
                    leng_errors_by_file.get(fp, [])
                )
            else:
                searched_files[fp] = {
                    "cells_searched": cells,
                    "cells_found": this_run_found.get(fp, []),
                    "rejections": this_run_rejections.get(fp, {}),
                    "leng_errors": leng_errors_by_file.get(fp, []),
                }

        total_found = sum(len(v) for v in this_run_found.values())
        total_rejected = sum(len(v) for v in this_run_rejections.values())
        leng_ch.print(
            f"done: {total_found} cells found, "
            f"{total_rejected} rejected."
        )

        return "complete"

    finally:
        flush_dir = debug_dir or Path("tests/debug")
        try:
            trace.flush_to_disk(flush_dir)
        except Exception:
            pass
        clear_current_trace()
