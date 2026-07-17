"""
tool_pms1.py — PSOAS wrapper for the PMS1 pipeline.

Facade that calls Sekei → PTO → Stencil and returns a serialized
stencil dict. execute_tool calls this; never called directly by
the orchestrator LLM.

Usage:
    from src.tool_pms1 import run_pms1_pipeline
    stencil = run_pms1_pipeline("Best Buy", "margins FY2022-2023", channel=ch)
"""

from __future__ import annotations

import threading

from llama_index.core import VectorStoreIndex

from src.harness.terminal_router import register, ToolChannel, override_channel, clear_override
from src.config import Config
from src.sekei import sekei
from src.orchestrator import run
from src.stencil import serialize_stencil

_default_channel = register("PMS1")


def run_pms1_pipeline(
    firm: str,
    query: str,
    channel: ToolChannel | None = None,
) -> dict:
    """Run full PMS1 pipeline for one firm. Returns stencil dict."""
    ch = channel or _default_channel

    # Redirect bare "PMS1" prints from internal modules (pto.py,
    # orchestrator.py, stencil.py) to the firm-specific channel.
    override_channel("PMS1", ch)
    try:
        # ── Load deps ─────────────────────────────────────────
        ch.print("Loading index...")
        config = Config.from_env()
        index = _load_index(config)

        # ── Sekei ─────────────────────────────────────────────
        ch.print("Sekei planning...")
        full_query = f"{firm}: {query}"
        plan = sekei(full_query, config)
        n_cells = len(plan.cells)
        n_batches = len(plan.batches)
        ch.print(f"done. {n_cells} cells, {n_batches} batches.")

        # ── PTO + Stencil ─────────────────────────────────────
        ch.print(f"Running {n_batches} batches...")
        results = run(plan, index, config)

        n_filled = len(results)
        ch.print(f"Stencil computed. {n_filled} cells filled.")

        # ── Serialize ─────────────────────────────────────────
        return serialize_stencil(plan, results)
    finally:
        clear_override("PMS1")


# ── Index cache (double-check locking) ────────────────────────────────

_index_lock = threading.Lock()
_cached_index: VectorStoreIndex | None = None


def _load_index(config: Config) -> VectorStoreIndex:
    """Load vector store index. Cached after first call."""
    global _cached_index
    if _cached_index is not None:
        return _cached_index
    with _index_lock:
        if _cached_index is not None:
            return _cached_index
        from src.index_store import IndexManager
        mgr = IndexManager(config)
        _cached_index = mgr.load_or_build()
        return _cached_index
