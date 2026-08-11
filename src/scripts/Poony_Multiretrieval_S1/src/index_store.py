"""
index_store.py — Persistent vector-index lifecycle with change detection.

Uses SHA-256 file hashes to only re-index new, changed, or deleted files.
The index (embeddings + docstore) is persisted to disk under ./index/.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from llama_index.core import (
    Settings,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.schema import TextNode
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from src.config import Config
from src.ingest import load_and_chunk_documents, scan_files, SUPPORTED_SUFFIXES

_HASH_FILE = "file_hashes.json"


class IndexManager:
    """Owns the vector-index lifecycle: build, persist, load, and incremental
    refresh when files change.

    Usage::

        mgr = IndexManager(config)
        index = mgr.load_or_build()      # handles all the diff logic
        # … answer queries with `index` …
        mgr.persist()                     # save index + hash registry
    """

    def __init__(self, config: Config):
        self.config = config
        self.index: VectorStoreIndex | None = None
        self.file_hashes: dict[str, str] = {}    # rel_path → sha256
        self._hash_path = config.index_dir / _HASH_FILE

        # ── Configure global LlamaIndex settings ─────────────────────
        Settings.embed_model = HuggingFaceEmbedding(
            model_name=config.embed_model_name,
            device="cpu",                   # Apple Silicon MPS isn't always stable
        )
        # We do NOT set Settings.llm here — LLM is called separately
        # after retrieval, not during indexing.

    # ── Public API ──────────────────────────────────────────────────────

    def load_or_build(self) -> VectorStoreIndex:
        """Return a ready-to-query VectorStoreIndex.

        - First run:            build from scratch, persist.
        - Index exists, no Δ:   load from disk (instant).
        - Files changed:        incrementally update index.
        """
        if self._index_persisted():
            try:
                self.index = self._load_from_disk()
                self.file_hashes = self._load_hash_registry()
                self._incremental_update()
            except Exception:
                # Corrupt index — rebuild from scratch.
                self.index = self._build_fresh()
        else:
            self.index = self._build_fresh()

        return self.index

    def persist(self) -> None:
        """Save index and file-hash registry to disk."""
        if self.index is None:
            return
        self.index.storage_context.persist(
            persist_dir=str(self.config.index_dir)
        )
        self._save_hash_registry()

    # ── Internal: index persistence ────────────────────────────────────

    def _index_persisted(self) -> bool:
        """Check if an index was previously saved to disk."""
        return (self.config.index_dir / "docstore.json").exists()

    def _load_from_disk(self) -> VectorStoreIndex:
        storage_context = StorageContext.from_defaults(
            persist_dir=str(self.config.index_dir)
        )
        return load_index_from_storage(storage_context)

    def _build_fresh(self) -> VectorStoreIndex:
        """Index every supported file from data/."""
        print("🔨 Building index from scratch …")
        nodes = load_and_chunk_documents(self.config)
        if not nodes:
            print("⚠️  No documents found in data/. Index will be empty.")
            # Create an empty index so the rest of the pipeline doesn't crash.
            return VectorStoreIndex([])

        self.index = VectorStoreIndex(nodes, show_progress=True)
        # Record hashes for all files we just indexed
        self._snapshot_hashes()
        # Persist index + hashes to disk immediately
        self.index.storage_context.persist(persist_dir=str(self.config.index_dir))
        self._save_hash_registry()
        print(f"✅ Indexed {len(nodes)} chunks from {self._unique_file_count(nodes)} files. Persisted to {self.config.index_dir}")
        return self.index

    # ── Internal: incremental update ───────────────────────────────────

    def _incremental_update(self) -> None:
        """Scan data/ and re-index only files that changed, appear, or disappeared."""

        old_hashes = self.file_hashes
        new_hashes = self._compute_file_hashes()

        added   = {p for p in new_hashes if p not in old_hashes}
        removed = {p for p in old_hashes if p not in new_hashes}
        changed = {p for p in new_hashes
                   if p in old_hashes and new_hashes[p] != old_hashes[p]}

        if not (added or removed or changed):
            print("♻️  Index up to date — no files changed.")
            return

        # Report what's happening
        for label, files in [("New", added), ("Removed", removed),
                              ("Changed", changed)]:
            if files:
                print(f"🔄 {label}: {', '.join(sorted(files))}")

        # Delete nodes for removed and changed files
        all_stale = removed | changed
        if all_stale and self.index:
            for rel_path in all_stale:
                try:
                    self.index.delete_ref_doc(
                        f"file:{rel_path}", delete_from_docstore=True
                    )
                except Exception:
                    pass  # doc may not exist in index yet

        # Insert nodes for new and changed files
        all_fresh = added | changed
        if all_fresh and self.index:
            fresh_nodes = self._load_nodes_for_files(all_fresh)
            if fresh_nodes:
                self.index.insert_nodes(fresh_nodes)

        # Update the hash snapshot and persist to disk
        self.file_hashes = new_hashes
        self.index.storage_context.persist(persist_dir=str(self.config.index_dir))
        self._save_hash_registry()

        total = len(new_hashes)
        print(f"✅ Index updated: {total} files tracked, "
              f"{len(added)} added, {len(removed)} removed, {len(changed)} changed.")

    def _load_nodes_for_files(self, file_paths: set[str]) -> list[TextNode]:
        """Load and chunk only the specified file paths, attaching ref_doc_id.

        We temporarily override Config.data_dir to point only at these files.
        A simpler approach: re-run the full loader and filter.
        """
        all_nodes = load_and_chunk_documents(self.config)
        return [n for n in all_nodes
                if n.metadata.get("file_path") in file_paths]

    # ── Internal: file hashing ─────────────────────────────────────────

    def _compute_file_hashes(self) -> dict[str, str]:
        """SHA-256 for every supported file in data/."""
        hashes: dict[str, str] = {}
        files = scan_files(self.config.data_dir)
        for rel_path, abs_path in files.items():
            try:
                hashes[rel_path] = _sha256_file(abs_path)
            except OSError:
                continue
        return hashes

    def _snapshot_hashes(self) -> None:
        """Capture hashes of all currently-indexed files."""
        self.file_hashes = self._compute_file_hashes()

    def _save_hash_registry(self) -> None:
        """Write file_hashes.json to the index directory."""
        self._hash_path.write_text(
            json.dumps(self.file_hashes, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _load_hash_registry(self) -> dict[str, str]:
        """Read file_hashes.json; return empty dict if missing or corrupt."""
        try:
            return json.loads(self._hash_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _unique_file_count(nodes: list) -> int:
        return len({n.metadata.get("file_name", "") for n in nodes})


# ═════════════════════════════════════════════════════════════════════════
# Utility
# ═════════════════════════════════════════════════════════════════════════

def _sha256_file(path: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file (fast for files up to ~50 MB)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        # Read in 64 KB chunks — efficient for large files.
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
