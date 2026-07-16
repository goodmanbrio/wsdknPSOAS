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

from llama_index.core import VectorStoreIndex

from src.config import Config
from src.sekei import SekeiPlan
from src.stencil import CellResult, compute_stencil
from src.harness.terminal_router import register
from src.pto import (
    PTOBatchRequest,
    PTOCellResult,
    pto_retrieve,
    pto_judge,
    pto_judge_to_stencil,
)

_pto_out = register("PMS1")


def run(
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
) -> dict[str, CellResult]:
    """Execute PTO lanes for all batches and evaluate the stencil.

    Args:
        plan: A validated SekeiPlan (from sekei()).
        index: Loaded vector store index.
        config: Pipeline config.

    Returns:
        All cells (retrieve + compute) as dict[str, CellResult].
    """
    # SEKEI BATCH.CELLS ⊆ PLAN.CELLS VALIDATION NOT BUILT
    # If Sekei LLM outputs a batch referencing a non-existent cell ID,
    # the adapter below will KeyError on plan.cells[cid]. No guard yet.
    _pto_out.print("⚠ SEKEI BATCH↔PLAN CELL VALIDATION NOT BUILT")

    all_values: dict[str, CellResult] = {}

    for batch in plan.batches:
        # ── Adapter: SekeiBatch → PTOBatchRequest ──
        metrics = [plan.cells[cid].metric for cid in batch.cells]

        request = PTOBatchRequest(
            batch_id=batch.id,
            firm=plan.firm,
            period=batch.period,
            statement=batch.statement,
            metrics=metrics,
            retrieve_target=batch.retrieve_target,
        )

        # ── cell_map: decoupling seam ──
        # Flat dict so PTO never imports SekeiPlan.
        # Orchestrator builds it, PTO consumes it.
        cell_map = {
            cid: plan.cells[cid].metric
            for cid in batch.cells
        }

        # ── Route: tabular → PTO, text → POS (not built) ──
        # All current Sekei batches are tabular (financial statements).
        # POS (Poony Oneshot Semantic) not built yet!
        # When POS exists, route here based on batch datatype.

        # ── PTO retrieval ──
        result = pto_retrieve(request, index, config)

        # ── Judge + parse ──
        judge_output = pto_judge(request, result, config)
        batch_values = pto_judge_to_stencil(judge_output, cell_map, result)

        # ── Convert PTOCellResult → CellResult ──
        # PTOCellResult is pto.py's own type, structurally identical to
        # stencil.CellResult. Duplicated to avoid pto.py importing from
        # stencil.py — keeps the two modules independent with orchestrator
        # as the only bridge. Plain field copy.
        for cell_id, pcr in batch_values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )

    # ── Stencil eval (deterministic) ──
    filled = compute_stencil(plan, all_values)
    return filled
