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
from pathlib import Path

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


def normalize_file_paths(
    file_scope: list[str] | None,
    config: Config,
) -> set[str]:
    """Normalize incoming file paths to docstore-relative POSIX form.

    Accepts:
      - absolute paths under config.pms2_data_dir (the ingested-data root),
      - paths relative to that root (e.g. "LITE/file.md"),
      - paths relative to the project root / cwd (e.g.
        "data/files_ingested/.../file.md").
    Paths outside the root are dropped — they can never match a docstore
    file, so they contribute nothing (→ "nothing found"). Returns a set of
    relative paths suitable for BM25Retriever.search(..., allowed_file_paths=...).
    """
    if not file_scope:
        return set()

    root = config.pms2_data_dir.resolve()
    project_root = root.parent.parent  # …/data/files_ingested → project root
    anchors = (root, Path.cwd(), project_root)
    allowed: set[str] = set()
    for raw in file_scope:
        if not isinstance(raw, str):
            continue  # guard against nested-list items (see _flatten_paths)
        raw = raw.strip()
        if not raw:
            continue
        p = Path(raw)
        if p.is_absolute():
            try:
                allowed.add(p.resolve().relative_to(root).as_posix())
            except ValueError:
                continue  # outside ingested root → cannot match → "nothing found"
        else:
            for base in anchors:
                try:
                    allowed.add((base / p).resolve().relative_to(root).as_posix())
                except (ValueError, OSError):
                    continue
    return allowed


def retrieve_for_sub_questions(
    sub_questions: list,  # list[SubQuestion] — imported lazily to avoid circular
    config: Config,
    file_scope: list[str] | None = None,
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
    file_scope : list[str] | None
        Optional list of file paths (absolute, or relative to
        data/files_ingested/). When provided, retrieval is hard-scoped to
        chunks from only those files. None or [] = search the whole corpus.

    Returns
    -------
    list[RetrievedChunk]
        Deduplicated, scored, capped chunks sorted by score descending.
    """
    retriever = _get_retriever(config)

    # None or [] → unscoped. Non-empty → hard-scope to these files' chunks.
    allowed_file_paths = (
        normalize_file_paths(file_scope, config) if file_scope else None
    )

    # node_id → (best_score, best_metadata)
    seen: dict[str, tuple[float, dict]] = {}

    for sq in sub_questions:
        query_str = " ".join(sq.keywords)
        terms = query_str.lower().split()
        if not terms:
            continue

        hits = retriever.search(
            terms,
            top_k=config.research_bm25_top_k,
            allowed_file_paths=allowed_file_paths,
        )
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
