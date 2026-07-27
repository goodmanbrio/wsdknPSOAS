"""PMS2 pipeline entry point.

T0 scope: _expand_periods only.
M3a: run_pms2_pipeline wired — Phase 0 (Sekei) only, no Dispatcher.
M3b: Phase 1 (Dispatchers) wired — stubbed extraction, proves control flow.
M5: Phase 2 (merge + compute) wired after dispatchers complete.
M6: End-to-end. Staleness check added.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from src.harness.opaque_registry import registry
from src.harness.terminal_router import ToolChannel, register
from src.harness.trace import TraceBuffer, with_trace, set_current_trace, clear_current_trace
from src.scripts.config import Config
from src.scripts.PMS2.sekei_loop import run_sekei
from src.scripts.PMS2.dispatcher import run_dispatcher
from src.scripts.PMS2.merge_compute import run_phase2

_Q_PREFIXES = ("Q1", "Q2", "Q3", "Q4")
_H_PREFIXES = ("H1", "H2")


def _newest_mtime(directory: Path, exclude_json: bool = False) -> float | None:
    """Return the newest mtime across all files in directory (recursive).

    Returns None if directory doesn't exist or has no files.
    """
    if not directory.exists():
        return None
    newest = None
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        if exclude_json and f.suffix == ".json":
            continue
        mt = f.stat().st_mtime
        if newest is None or mt > newest:
            newest = mt
    return newest


def _check_index_staleness(config: Config, channel) -> None:
    """Warn via channel if the index is stale relative to source files.

    Compares newest mtime across three stages:
      1. data/files_raw/ (source files)
      2. data/files_ingested/ (converted .md, excluding .json metadata)
      3. data/index/docstore.json

    Non-blocking: emits a warning and proceeds. The interactive
    re-ingest prompt (y/n) belongs in psoas.py startup, not here.
    """
    raw_mtime = _newest_mtime(config.pms2_raw_dir)
    ingested_mtime = _newest_mtime(config.pms2_data_dir, exclude_json=True)
    docstore_path = config.pms2_index_dir / "docstore.json"
    docstore_mtime = docstore_path.stat().st_mtime if docstore_path.exists() else None

    if raw_mtime is None:
        return  # no raw files — nothing to compare

    def _fmt(ts: float | None) -> str:
        if ts is None:
            return "missing"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

    if ingested_mtime is not None and raw_mtime > ingested_mtime:
        channel.print(
            f"⚠ files_raw/ modified ({_fmt(raw_mtime)}) after "
            f"files_ingested/ ({_fmt(ingested_mtime)}). "
            f"Index may be stale — consider re-running 00_Ingest + 01_Chunk."
        )
    elif docstore_mtime is not None and ingested_mtime is not None and ingested_mtime > docstore_mtime:
        channel.print(
            f"⚠ files_ingested/ modified ({_fmt(ingested_mtime)}) after "
            f"docstore ({_fmt(docstore_mtime)}). "
            f"Index may be stale — consider re-running 01_Chunk."
        )


def _expand_periods(periods: list[str], granularity: str) -> list[str]:
    """Expand FY periods by granularity. Already-expanded pass through.

    Guard: annual granularity + Q/H-prefixed period = contradiction.
    Orchestrator should not produce this. Raise so it surfaces
    loudly rather than silently extracting wrong timeframes.
    """
    if granularity == "annual":
        # Guard: Q/H prefix with annual = contradiction
        for p in periods:
            if p.startswith(_Q_PREFIXES + _H_PREFIXES):
                raise ValueError(
                    f"Period '{p}' has quarterly/half prefix but "
                    f"granularity is 'annual'. Fix orchestrator params."
                )
        return periods

    expanded = []
    for p in periods:
        if p.startswith(_Q_PREFIXES + _H_PREFIXES):
            # Already expanded (e.g. "Q3FY2025"), pass through
            expanded.append(p)
        elif granularity == "quarterly":
            for q in _Q_PREFIXES:
                expanded.append(f"{q}{p}")
        elif granularity == "half":
            for h in _H_PREFIXES:
                expanded.append(f"{h}{p}")
    return list(dict.fromkeys(expanded))  # dedup, preserve order


def run_pms2_pipeline(
    firms: list[str],
    query: str,
    periods: list[str],
    granularity: str,
    session_dir: Path,
    channel: ToolChannel | None = None,
    config: Config | None = None,
    debug_dir: Path | None = None,
) -> list[dict]:
    """Run full PMS2 pipeline.

    M3a scope: Phase 0 (Sekei) only. Returns display stencils
    (one per firm, all null values — no extraction yet).

    Pre-flight guards fire before Sekei — fail-fast even though
    docstore/file_path_index are only needed for Phase 1 (M4+).
    """
    config = config or Config.from_env()
    channel = channel or register("PMS2")

    # ── Pre-flight guards ────────────────────────────────────────
    docstore_path = config.pms2_index_dir / "docstore.json"
    if not docstore_path.exists():
        raise FileNotFoundError(
            f"docstore.json not found at {docstore_path}. "
            f"Run 00_Ingest.py + 01_Chunk.py first."
        )

    try:
        next(config.pms2_data_dir.rglob("*.md"))
    except StopIteration:
        raise FileNotFoundError(
            f"No .md files in {config.pms2_data_dir}. "
            f"Run 00_Ingest.py first."
        )

    fpi_path = config.pms2_index_dir / "file_path_index.json"
    if not fpi_path.exists():
        raise FileNotFoundError(
            f"file_path_index.json not found at {fpi_path}. "
            f"Run 01_Chunk.py first."
        )

    # ── Staleness check (warning only) ──────────────────────────
    _check_index_staleness(config, channel)

    # ── Period expansion ─────────────────────────────────────────
    expanded_periods = _expand_periods(periods, granularity)

    # ── Top-level dir injection for Sekei ────────────────────────
    top_level_dirs = sorted([
        name for name in os.listdir(config.pms2_data_dir)
        if not name.startswith(".")
        and not name.endswith(".json")
        and (config.pms2_data_dir / name).is_dir()
    ])

    channel.print(
        f"PMS2 pipeline: {len(firms)} firms, "
        f"{len(expanded_periods)} periods, {granularity}."
    )

    # ── Phase 0: Sekei ───────────────────────────────────────────
    work_stencil, ans_stencil, job_stencils, file_inventories = run_sekei(
        firms=firms,
        expanded_periods=expanded_periods,
        query=query,
        granularity=granularity,
        config=config,
        channel=channel,
        session_dir=session_dir,
        debug_dir=debug_dir,
        top_level_dirs=top_level_dirs,
    )

    # ── Resolve file inventories from opaque handles ────────────
    raw_inventories: dict[str, list[dict]] = {}
    for firm_name, handle_or_data in file_inventories.items():
        if isinstance(handle_or_data, str) and handle_or_data.startswith("$var_"):
            raw_inventories[firm_name] = registry.resolve(handle_or_data)
        else:
            raw_inventories[firm_name] = handle_or_data

    # ── Load docstore + file_path_index (once, shared read-only) ─
    from llama_index.core.storage.docstore import SimpleDocumentStore
    docstore = SimpleDocumentStore.from_persist_path(
        str(config.pms2_index_dir / "docstore.json")
    )
    file_path_index = json.loads(
        (config.pms2_index_dir / "file_path_index.json").read_text()
    )

    # ── Phase 1: Dispatchers (parallel per firm) ────────────────
    channel.print(
        f"Phase 1: dispatching {len(firms)} firm(s) in parallel."
    )

    # Create a parent trace for dispatcher threads
    disp_parent_trace = TraceBuffer("PMS2-disp")
    set_current_trace(disp_parent_trace)

    def _run_dispatcher(firm: str, job_stencil: dict) -> dict:
        firm_channel = register(f"PMS2-disp-{firm}")
        return run_dispatcher(
            firm=firm,
            job_stencil=job_stencil,
            file_inventory=raw_inventories.get(firm, []),
            granularity=granularity,
            periods=expanded_periods,
            channel=firm_channel,
            config=config,
            docstore=docstore,
            file_path_index=file_path_index,
            debug_dir=debug_dir,
        )

    completed_jobs = []
    with ThreadPoolExecutor(max_workers=len(firms)) as pool:
        futures = {}
        for i, job in enumerate(job_stencils):
            firm = job.get("firm", firms[i] if i < len(firms) else f"firm_{i}")
            futures[pool.submit(
                with_trace(disp_parent_trace, _run_dispatcher),
                firm, job,
            )] = firm
        for fut in as_completed(futures):
            firm = futures[fut]
            try:
                result = fut.result()
                completed_jobs.append(result)
                channel.print(
                    f"Dispatcher [{firm}]: {result.get('status', '?')}"
                )
            except Exception as e:
                channel.print(f"Dispatcher [{firm}] crashed: {e}")

    clear_current_trace()

    # ── Per-firm fill reporting ───────────────────────────────────
    for job in completed_jobs:
        firm_name = job.get("firm", "?")
        filled = sum(1 for v in job["values"].values() if v is not None)
        total = len(job["values"])
        status = job.get("status", "?")
        channel.print(
            f"[PMS2] {firm_name}: {filled}/{total} cells filled, "
            f"status={status}"
        )

    # ── Phase 2: Merge + Compute ───────────────────────────────
    channel.print("Phase 2: merge + compute.")
    display_stencils = run_phase2(
        work_stencil=work_stencil,
        ans_stencil=ans_stencil,
        completed_jobs=completed_jobs,
        session_dir=session_dir,
        channel=channel,
    )

    return display_stencils
