"""bm25_index.py — BM25 keyword-search index over docstore chunks.

Builds a BM25Okapi index from the existing docstore.json (produced by
01_Chunk.py). Built lazily on first use, cached to
data/index/bm25_index.pkl, and auto-rebuilt when the docstore hash
changes. No embedding costs — pure keyword matching, well-suited to
financial text where tickers, metric names, and product terms are
exact-match keywords.

Usage:
    from src.scripts.research.bm25_index import BM25Retriever

    retriever = BM25Retriever(index_dir, docstore_path)
    hits = retriever.search(["LITE", "optical", "revenue"], top_k=5)
    # → [(node_id, bm25_score), ...]
"""

from __future__ import annotations

import hashlib
import pickle
import threading
from pathlib import Path


class BM25Retriever:
    """Lazy-build BM25Okapi index over docstore text nodes.

    Builds on first search() call if no cached index exists or if the
    docstore hash has changed. Thread-safe for concurrent reads after
    the initial build.

    Parameters
    ----------
    index_dir : Path
        Directory containing docstore.json and where bm25_index.pkl is cached.
    docstore_path : Path
        Path to docstore.json (typically index_dir / "docstore.json").
    """

    def __init__(self, index_dir: Path, docstore_path: Path) -> None:
        self._index_path = index_dir / "bm25_index.pkl"
        self._docstore_path = docstore_path

        # In-memory state — populated by _build() or _load()
        self._bm25: "BM25Okapi | None" = None  # type: ignore[name-defined]
        self._node_ids: list[str] = []
        self._texts: dict[str, str] = {}  # node_id → chunk text
        self._metadata: dict[str, dict] = {}  # node_id → metadata dict

        # Scoped BM25 cache — allowed-file-set → (BM25Okapi, node_ids)
        self._scoped_cache: dict[
            frozenset[str], "tuple[BM25Okapi | None, list[str]]"  # type: ignore[name-defined]
        ] = {}

        self._lock = threading.Lock()
        self._built = False

    # ── Public API ───────────────────────────────────────────────────────

    def ensure_ready(self) -> None:
        """Guarantee the index is built and ready. Idempotent."""
        if self._built:
            return
        with self._lock:
            if self._built:  # double-check
                return
            if self._load():
                self._built = True
                return
            self._build()
            self._built = True

    def search(
        self,
        terms: list[str],
        top_k: int = 5,
        allowed_file_paths: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return top-k (node_id, bm25_score) for query terms.

        Parameters
        ----------
        terms : list[str]
            Already-tokenized query terms (lowercased, split).
        top_k : int
            Number of results to return (default 5).
        allowed_file_paths : set[str] | None
            If provided, hard-scope the search to chunks whose file_path
            is in this set. A fresh BM25 is built over only those chunks,
            so IDF/ranking statistics are scoped too. None = search the
            whole corpus (default).

        Returns
        -------
        list[tuple[str, float]]
            Sorted by BM25 score descending. May return fewer than top_k
            if the corpus has fewer documents.
        """
        self.ensure_ready()

        if self._bm25 is None or not self._node_ids:
            return []

        if allowed_file_paths is not None:
            bm25, node_ids = self._get_scoped(allowed_file_paths)
            if not node_ids:
                return []
            scores = bm25.get_scores(terms)
            paired = list(zip(node_ids, scores))
        else:
            scores = self._bm25.get_scores(terms)
            # Pair (node_id, score) and sort descending
            paired = list(zip(self._node_ids, scores))

        paired.sort(key=lambda x: x[1], reverse=True)

        # Return top_k (filter zero scores for efficiency)
        result = [(nid, s) for nid, s in paired[:top_k] if s > 0.0]
        return result

    def get_text(self, node_id: str) -> str:
        """Return the chunk text for a node_id."""
        self.ensure_ready()
        return self._texts.get(node_id, "")

    def get_metadata(self, node_id: str) -> dict:
        """Return the metadata dict for a node_id."""
        self.ensure_ready()
        return self._metadata.get(node_id, {})

    def known_file_paths(self) -> set[str]:
        """Set of all distinct relative file_path values in the searchable index."""
        self.ensure_ready()
        return {
            m.get("file_path", "")
            for m in self._metadata.values()
            if m.get("file_path", "")
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _get_scoped(
        self, allowed_file_paths: set[str]
    ) -> "tuple[BM25Okapi | None, list[str]]":  # type: ignore[name-defined]
        """Build (and cache) a BM25 over only the chunks of the allowed files.

        Hard scope: the returned index contains exactly the nodes whose
        metadata file_path is in allowed_file_paths, so ranking happens
        against the scoped file set alone. Cached per frozenset of paths.
        """
        key = frozenset(allowed_file_paths)
        cached = self._scoped_cache.get(key)
        if cached is not None:
            return cached

        node_ids = [
            nid
            for nid in self._node_ids
            if self._metadata.get(nid, {}).get("file_path", "") in allowed_file_paths
        ]
        if not node_ids:
            self._scoped_cache[key] = (None, [])
            return None, []

        from rank_bm25 import BM25Okapi

        corpus_tokens = [self._texts[nid].lower().split() for nid in node_ids]
        bm25 = BM25Okapi(corpus_tokens)
        self._scoped_cache[key] = (bm25, node_ids)
        return bm25, node_ids

    def _get_docstore_hash(self) -> str:
        """SHA-256 of docstore.json, used for staleness detection."""
        if not self._docstore_path.exists():
            return ""
        h = hashlib.sha256()
        # Read in chunks in case docstore is large
        with open(self._docstore_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _build(self) -> None:
        """Build BM25 index from docstore.json and persist to disk."""
        from rank_bm25 import BM25Okapi

        # Lazy import llama_index — heavy but already a project dep
        from llama_index.core.storage.docstore import SimpleDocumentStore

        if not self._docstore_path.exists():
            raise FileNotFoundError(
                f"docstore.json not found at {self._docstore_path}. "
                f"Run 01_Chunk.py first."
            )

        docstore = SimpleDocumentStore.from_persist_path(
            str(self._docstore_path)
        )

        corpus_tokens: list[list[str]] = []
        node_ids: list[str] = []
        texts: dict[str, str] = {}
        metadata: dict[str, dict] = {}

        for node_id, doc in docstore.docs.items():
            text = doc.text if hasattr(doc, "text") else str(doc)
            if not text or not text.strip():
                continue
            tokens = text.lower().split()
            if len(tokens) < 10:  # skip very short chunks
                continue

            corpus_tokens.append(tokens)
            node_ids.append(node_id)
            texts[node_id] = text
            metadata[node_id] = {
                "file_path": doc.metadata.get("file_path", ""),
                "file_name": doc.metadata.get("file_name", ""),
                "section": doc.metadata.get("section", ""),
                "chunk_type": doc.metadata.get("chunk_type", "text"),
                "chunk_index": doc.metadata.get("chunk_index", 0),
                "fiscal_year": doc.metadata.get("fiscal_year", ""),
            }

        if not corpus_tokens:
            raise ValueError(
                "docstore contains no usable chunks. "
                "Run 01_Chunk.py to populate the index."
            )

        bm25 = BM25Okapi(corpus_tokens)

        # Persist to disk
        docstore_hash = self._get_docstore_hash()
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._index_path, "wb") as f:
            pickle.dump(
                {
                    "docstore_hash": docstore_hash,
                    "node_ids": node_ids,
                    "texts": texts,
                    "metadata": metadata,
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

        # Populate in-memory state
        self._bm25 = bm25
        self._node_ids = node_ids
        self._texts = texts
        self._metadata = metadata

    def _load(self) -> bool:
        """Try to load cached BM25 index from disk.

        Returns True if the cached index is valid, False if it needs
        rebuilding (missing file, hash mismatch, or corrupt pickle).
        """
        if not self._index_path.exists():
            return False

        try:
            with open(self._index_path, "rb") as f:
                data = pickle.load(f)

            current_hash = self._get_docstore_hash()
            if data.get("docstore_hash") != current_hash:
                return False  # docstore changed — rebuild needed

            from rank_bm25 import BM25Okapi

            node_ids: list[str] = data["node_ids"]
            texts: dict[str, str] = data["texts"]
            metadata: dict[str, dict] = data["metadata"]

            # Rebuild BM25Okapi from tokenized texts
            corpus_tokens = [texts[nid].lower().split() for nid in node_ids]
            bm25 = BM25Okapi(corpus_tokens)

            self._bm25 = bm25
            self._node_ids = node_ids
            self._texts = texts
            self._metadata = metadata
            return True

        except (KeyError, EOFError, pickle.UnpicklingError, ImportError):
            return False
