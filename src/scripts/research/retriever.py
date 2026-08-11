"""retriever.py — BM25 chunk retrieval for decomposed sub-questions.

Searches the BM25 index with keywords from each sub-question, deduplicates
results across sub-questions, and returns a capped, scored list of chunks
with metadata.

Usage:
    from src.scripts.research.retriever import retrieve_for_sub_questions

    chunks = retrieve_for_sub_questions(sub_questions, config)
    # → [RetrievedChunk(node_id=..., text=..., score=..., ...), ...]
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.config import Config
from src.scripts.research.bm25_index import BM25Retriever


@dataclass
class RetrievedChunk:
    """A document chunk retrieved by BM25, with metadata."""

    node_id: str
    text: str
    score: float  # BM25 score (higher = more relevant)
    file_path: str
    file_name: str
    section: str = ""
    chunk_type: str = "text"
    chunk_index: int = 0
    fiscal_year: str = ""


# Module-level retriever singleton — built once, reused across calls.
_retriever: BM25Retriever | None = None
_retriever_lock: "threading.Lock | None" = None


def _get_retriever(config: Config) -> BM25Retriever:
    """Get or create the module-level BM25Retriever singleton."""
    global _retriever, _retriever_lock

    if _retriever_lock is None:
        import threading
        _retriever_lock = threading.Lock()

    if _retriever is not None:
        return _retriever

    with _retriever_lock:
        if _retriever is not None:
            return _retriever

        docstore_path = config.pms2_index_dir / "docstore.json"
        if not docstore_path.exists():
            raise FileNotFoundError(
                f"docstore.json not found at {docstore_path}. "
                f"Run 01_Chunk.py first."
            )

        _retriever = BM25Retriever(config.pms2_index_dir, docstore_path)
        return _retriever


def retrieve_for_sub_questions(
    sub_questions: list,  # list[SubQuestion] — imported lazily to avoid circular
    config: Config,
) -> list[RetrievedChunk]:
    """Retrieve relevant chunks for each sub-question via BM25.

    Searches the BM25 index with keywords from each sub-question,
    deduplicates by node_id (keeping the highest score), and caps
    at config.research_max_chunks.

    Parameters
    ----------
    sub_questions : list[SubQuestion]
        Decomposed sub-questions with keyword lists.
    config : Config
        PSOAS configuration.

    Returns
    -------
    list[RetrievedChunk]
        Deduplicated, scored, capped chunks sorted by score descending.
    """
    retriever = _get_retriever(config)

    # node_id → (best_score, best_metadata)
    seen: dict[str, tuple[float, dict]] = {}

    for sq in sub_questions:
        query_str = " ".join(sq.keywords)
        terms = query_str.lower().split()
        if not terms:
            continue

        hits = retriever.search(terms, top_k=config.research_bm25_top_k)
        for node_id, score in hits:
            if node_id not in seen or score > seen[node_id][0]:
                meta = retriever.get_metadata(node_id)
                seen[node_id] = (score, meta)

    # Sort by score descending
    sorted_items = sorted(seen.items(), key=lambda x: x[1][0], reverse=True)

    # Cap
    capped = sorted_items[: config.research_max_chunks]

    chunks: list[RetrievedChunk] = []
    for node_id, (score, meta) in capped:
        text = retriever.get_text(node_id)
        file_path = meta.get("file_path", "")
        chunks.append(
            RetrievedChunk(
                node_id=node_id,
                text=text,
                score=score,
                file_path=file_path,
                file_name=meta.get("file_name", Path(file_path).name if file_path else ""),  # type: ignore[arg-type]
                section=meta.get("section", ""),
                chunk_type=meta.get("chunk_type", "text"),
                chunk_index=meta.get("chunk_index", 0),
                fiscal_year=meta.get("fiscal_year", ""),
            )
        )

    return chunks


# Lazy import for Path in the dataclass default
from pathlib import Path
