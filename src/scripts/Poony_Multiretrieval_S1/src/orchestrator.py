"""
orchestrator.py — Top-level pipeline: Sekei plan → PTO lanes → filled stencil.

Loops batches from a SekeiPlan, calls PTO (retrieval + judge + parse)
per batch, merges cell results, evaluates the stencil.

orchestrator.py imports from both sekei.py and pto.py.
pto.py and sekei.py do NOT import from each other — orchestrator
is the only module that bridges them. cell_map (dict[str, str]) is
the decoupling seam: orchestrator builds it from SekeiPlan, passes
it to PTO as a plain dict so PTO never sees Sekei types.

Usage:
    from src.orchestrator import run
    from src.sekei import sekei
    plan = sekei(query, config)
    filled = run(plan, index, config)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore

from src.config import Config
from src.sekei import SekeiPlan, SekeiBatch
from src.stencil import CellResult, compute_stencil
from src.harness.terminal_router import register
from src.harness.trace import (
    TraceBuffer, set_current_trace, get_current_trace, clear_current_trace,
)
from src.pto import (
    PTOBatchRequest,
    PTOBatchResult,
    PTOCellResult,
    PTOMethodLog,
    JudgeStencilResult,
    pto_retrieve,
    pto_judge,
    pto_judge_to_stencil,
)
from src.pumba import run_pumba

_pto_out = register("PMS1")


# ── Phase 1/2 outcome type ────────────────────────────────────────────

@dataclass
class BatchOutcome:
    """Result of one PTO or PUMBA batch attempt."""
    batch: SekeiBatch
    request: PTOBatchRequest
    cell_map: dict[str, str]
    stencil_result: JudgeStencilResult
    pto_result: PTOBatchResult | None
    error: Exception | None
    user_exit: bool = False


def _run_pto_batch(
    batch: SekeiBatch,
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
    debug_dir: Path | None = None,
) -> BatchOutcome:
    """One PTO lane: retrieve → judge → parse. Thread-safe."""
    metrics = [plan.cells[cid].metric for cid in batch.cells]
    request = PTOBatchRequest(
        batch_id=batch.id,
        firm=plan.firm,
        period=batch.period,
        statement=batch.statement,
        metrics=metrics,
        retrieve_target=batch.retrieve_target,
    )
    cell_map = {cid: plan.cells[cid].metric for cid in batch.cells}

    batch_idx = int(batch.id.split("_")[-1]) if "_" in batch.id else 0
    trace = TraceBuffer("PTO", firm=plan.firm, batch_idx=batch_idx)
    set_current_trace(trace)
    try:
        pto_result = pto_retrieve(request, index, config)

        # Record retrieval in trace
        trace.record_retrieval(pto_result)

        judge_output = pto_judge(request, pto_result, config)
        stencil_result = pto_judge_to_stencil(
            judge_output, cell_map, pto_result
        )
        return BatchOutcome(
            batch=batch, request=request, cell_map=cell_map,
            stencil_result=stencil_result, pto_result=pto_result,
            error=None,
        )
    except Exception as e:
        # Judge JSON garbled, API error, etc.
        return BatchOutcome(
            batch=batch, request=request, cell_map=cell_map,
            stencil_result=JudgeStencilResult({}, [], {}),
            pto_result=None, error=e,
        )
    finally:
        if debug_dir:
            try:
                trace.flush_to_disk(debug_dir)
            except OSError:
                pass
        clear_current_trace()


def _run_pumba_batch(
    outcome: BatchOutcome,
    index: VectorStoreIndex,
    config: Config,
    debug_dir: Path | None = None,
) -> BatchOutcome:
    """PUMBA fallback for one failed batch. Thread-safe."""
    pumba_ch = register(
        f"PUMBA-{outcome.request.firm} "
        f"{outcome.request.period} "
        f"{outcome.request.statement}"
    )

    batch_idx = (
        int(outcome.batch.id.split("_")[-1])
        if "_" in outcome.batch.id else 0
    )
    trace = TraceBuffer(
        "PUMBA", firm=outcome.request.firm, batch_idx=batch_idx,
    )
    set_current_trace(trace)
    try:
        try:
            pumba_node_ids = run_pumba(
                outcome.request, index, config, channel=pumba_ch
            )
        except ValueError:
            # User chose "exit" — mark and let other threads finish
            return BatchOutcome(
                batch=outcome.batch, request=outcome.request,
                cell_map=outcome.cell_map,
                stencil_result=JudgeStencilResult({}, [], {}),
                pto_result=None, error=None, user_exit=True,
            )

        if not pumba_node_ids:
            return BatchOutcome(
                batch=outcome.batch, request=outcome.request,
                cell_map=outcome.cell_map,
                stencil_result=JudgeStencilResult({}, [], {}),
                pto_result=None, error=None,
            )

        pumba_top_chunks = []
        for i, nid in enumerate(pumba_node_ids):
            node = index.docstore.docs.get(nid)
            if node is None:
                continue
            pumba_top_chunks.append(
                NodeWithScore(
                    node=node,
                    score=float(len(pumba_node_ids) - i),
                )
            )

        if not pumba_top_chunks:
            return BatchOutcome(
                batch=outcome.batch, request=outcome.request,
                cell_map=outcome.cell_map,
                stencil_result=JudgeStencilResult({}, [], {}),
                pto_result=None, error=None,
            )

        pumba_result = PTOBatchResult(
            batch_id=outcome.request.batch_id,
            top_chunks=pumba_top_chunks,
            runner_ups=[],
            method_log=PTOMethodLog(
                hyde_good="PUMBA",
                hyde_bad="",
                metadata_filters={"method": "pumba_fallback"},
            ),
        )

        try:
            judge_output = pto_judge(
                outcome.request, pumba_result, config
            )
            stencil_result = pto_judge_to_stencil(
                judge_output, outcome.cell_map, pumba_result
            )
        except Exception as e:
            return BatchOutcome(
                batch=outcome.batch, request=outcome.request,
                cell_map=outcome.cell_map,
                stencil_result=JudgeStencilResult({}, [], {}),
                pto_result=pumba_result, error=e,
            )

        return BatchOutcome(
            batch=outcome.batch, request=outcome.request,
            cell_map=outcome.cell_map,
            stencil_result=stencil_result,
            pto_result=pumba_result, error=None,
        )
    finally:
        if debug_dir:
            try:
                trace.flush_to_disk(debug_dir)
            except OSError:
                pass
        clear_current_trace()


def run(
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
    debug_dir: Path | None = None,
) -> dict[str, CellResult]:
    """Execute PTO lanes for all batches and evaluate the stencil.

    Two-phase parallel: Phase 1 runs all PTO batches concurrently,
    Phase 2 runs PUMBA for all failed batches concurrently, Phase 2.5
    NaN-fills any remaining missing cells, Phase 3 computes stencil.
    """
    _pto_out.print("⚠ SEKEI BATCH↔PLAN CELL VALIDATION NOT BUILT")

    all_values: dict[str, CellResult] = {}

    # ── Phase 1: PTO all batches in parallel ─────────────────────
    outcomes: list[BatchOutcome] = []

    with ThreadPoolExecutor(max_workers=max(len(plan.batches), 1)) as pool:
        futures = {
            pool.submit(_run_pto_batch, b, plan, index, config, debug_dir): b
            for b in plan.batches
        }
        for future in as_completed(futures):
            outcomes.append(future.result())

    # Partition results
    ok_outcomes = []
    pumba_needed = []
    crashed = []

    for outcome in outcomes:
        if outcome.error is not None:
            crashed.append(outcome)
        elif outcome.stencil_result.failures:
            pumba_needed.append(outcome)
        else:
            ok_outcomes.append(outcome)

    # Merge successful PTO values
    for outcome in ok_outcomes:
        for cell_id, pcr in outcome.stencil_result.values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )

    # Merge partial successes from failed batches
    for outcome in pumba_needed:
        for cell_id, pcr in outcome.stencil_result.values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )

    # Log failures
    for outcome in pumba_needed:
        failed_cells = ", ".join(
            f"{m} ({cid})" for cid, m in outcome.stencil_result.failures
        )
        _pto_out.print(
            f"PTO batch {outcome.request.batch_id} failed cells: "
            f"{failed_cells}. Falling back to PUMBA..."
        )
    for outcome in crashed:
        _pto_out.print(
            f"PTO batch {outcome.request.batch_id} crashed: "
            f"{outcome.error}"
        )

    # ── Phase 2: PUMBA for all failed batches in parallel ────────
    if pumba_needed:
        pumba_outcomes: list[BatchOutcome] = []

        with ThreadPoolExecutor(
            max_workers=len(pumba_needed)
        ) as pool:
            futures = {
                pool.submit(_run_pumba_batch, o, index, config, debug_dir): o
                for o in pumba_needed
            }
            for future in as_completed(futures):
                outcome = future.result()
                pumba_outcomes.append(outcome)
                sr = outcome.stencil_result

                # Merge PUMBA successes (overwrites PTO partials)
                for cell_id, pcr in sr.values.items():
                    all_values[cell_id] = CellResult(
                        value=pcr.value, source=pcr.source,
                        denomination=pcr.denomination, unit=pcr.unit,
                    )

                # Report Phase 2 errors and remaining failures
                if outcome.error is not None:
                    _pto_out.print(
                        f"PUMBA re-judge crashed for batch "
                        f"{outcome.request.batch_id}: {outcome.error}"
                    )
                elif sr.failures:
                    failed = ", ".join(
                        f"{m} ({cid})" for cid, m in sr.failures
                    )
                    _pto_out.print(
                        f"PUMBA also failed for batch "
                        f"{outcome.request.batch_id}: {failed}"
                    )

        # User chose "exit" in any PUMBA prompt → abort pipeline
        if any(o.user_exit for o in pumba_outcomes):
            raise ValueError("User aborted (exit)")

    # ── Phase 2.5: NaN-fill missing retrieve cells ───────────────
    for cell_id, cell in plan.cells.items():
        if cell.type == "retrieve" and cell_id not in all_values:
            _pto_out.print(
                f"⚠ {cell_id} ({cell.metric}) missing — NaN placeholder"
            )
            all_values[cell_id] = CellResult(
                value=float("nan"), source="FAILED",
                denomination="", unit="",
            )

    # ── Phase 3: stencil (deterministic) ─────────────────────────
    filled = compute_stencil(plan, all_values)
    return filled
