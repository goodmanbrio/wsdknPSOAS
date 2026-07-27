"""01_Chunk.py — Chunk .md files from files_ingested/ into docstore.

Reads markdown files from data/files_ingested/, chunks them into a
SimpleDocumentStore at data/index/docstore.json. No embeddings.

Depends on manifest.json (written by 00_Ingest.py). Hard crash if missing.

Pipeline (cloned from PMS1 ingest.py, decoupled):
  _read_md -> _merge_stub_headers -> _split_tables -> _split_sub_tables
  -> _chunk_documents(overlap=0) -> _tag_chunk_indices -> _inject_overlap
  -> serialize (SimpleDocumentStore + file_path_index.json)

Changes from PMS1:
  1. Output path: data/index/
  2. _inject_overlap: post-chunking +/-1k char overlap
  3. filetype metadata from manifest (not inferred)
  4. file_path_index.json built after chunking
  5. file_path relative to data/files_ingested/
  6. Dropped dir_implied_firm, dir_implied_sector
  7. SentenceSplitter overlap = 0

Run: python src/scripts/PMS2/01_Chunk.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import warnings
from pathlib import Path

from llama_index.core import Document
from llama_index.core.schema import TextNode
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.storage.docstore import SimpleDocumentStore

# Ensure sibling modules importable when run as script
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from chunk_overlap import _inject_overlap

# ── Paths ─────────────────────────────────────────────────────────────
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
_INGESTED_DIR = _PROJECT_ROOT / "data" / "files_ingested"
_INDEX_DIR = _PROJECT_ROOT / "data" / "index"
_MANIFEST_PATH = _INGESTED_DIR / "manifest.json"
_DOCSTORE_PATH = _INDEX_DIR / "docstore.json"
_HASHES_PATH = _INDEX_DIR / "file_hashes.json"
_FPI_PATH = _INDEX_DIR / "file_path_index.json"

# ── Chunk parameters ──────────────────────────────────────────────────
_CHUNK_SIZE = 512       # tokens
_CHUNK_OVERLAP = 0      # _inject_overlap is the sole overlap mechanism
_MIN_CHUNK_SIZE = 100   # discard chunks shorter than this (chars)


# ═══════════════════════════════════════════════════════════════════════
# Cloned from PMS1 ingest.py — fully decoupled, no PMS1 imports
# ═══════════════════════════════════════════════════════════════════════

def _read_md(abs_path: str, rel_path: str) -> list[Document]:
    """Read a markdown file, splitting on headers to keep tables with context.

    Markdown files (often Docling/PDF-converter output) have deterministic
    structure: ## headers, pipe tables, consistent syntax.  Splitting on
    headers instead of blank lines keeps section headings attached to their
    content.
    """
    file_name = Path(rel_path).name
    raw = Path(abs_path).read_text(encoding="utf-8", errors="replace")

    # Split at markdown headers (# through ######) using a lookahead so
    # the header line stays attached to the section body that follows it.
    sections = re.split(r"(?=^#{1,6}\s)", raw, flags=re.MULTILINE)

    docs: list[Document] = []
    for section_text in sections:
        section_text = section_text.strip()
        if not section_text:
            continue

        first_line = section_text.split("\n", 1)[0].strip()
        if first_line.startswith("#"):
            section_label = first_line.lstrip("#").strip()
        else:
            section_label = ""

        docs.append(Document(
            doc_id=f"file:{rel_path}",
            text=section_text,
            metadata={
                "file_name": file_name,
                "file_path": rel_path,
                "page_number": 1,
                "section": section_label,
                "chunk_type": "text",
            }
        ))
    return docs


# ── Stub header merging ───────────────────────────────────────────────

_STUB_MAX_BODY_CHARS = 110


def _merge_stub_headers(documents: list[Document]) -> list[Document]:
    """Merge consecutive stub header docs into the next substantive doc.

    _read_md splits at every ## header. Financial statements often have
    multi-line headers. Without merging, the table chunk loses the actual
    statement title that Leng needs for context.
    Only merges docs from the same source file (same file_name).
    """
    if not documents:
        return documents

    result: list[Document] = []
    pending_labels: list[str] = []
    pending_fname: str = ""

    for doc in documents:
        fname = doc.metadata.get("file_name", "")

        if fname != pending_fname and pending_labels:
            pending_labels = []
        pending_fname = fname

        text = doc.text.strip()
        if text.startswith("#"):
            body = text.split("\n", 1)[1].strip() if "\n" in text else ""
        else:
            body = text

        if len(body) < _STUB_MAX_BODY_CHARS:
            section = doc.metadata.get("section", "")
            if section:
                pending_labels.append(section)
            continue

        if pending_labels:
            own_section = doc.metadata.get("section", "")
            if own_section:
                pending_labels.append(own_section)
            merged_section = " ".join(pending_labels)
            new_meta = dict(doc.metadata)
            new_meta["section"] = merged_section
            result.append(Document(
                doc_id=doc.doc_id, text=doc.text, metadata=new_meta,
            ))
            pending_labels = []
        else:
            result.append(doc)

    if pending_labels:
        merged_section = " ".join(pending_labels)
        result.append(Document(
            doc_id=documents[-1].doc_id,
            text="",
            metadata={**documents[-1].metadata, "section": merged_section},
        ))

    return result


# ── Table detection (format-agnostic) ─────────────────────────────────

_ORPHAN_MERGE_MAX = 400


def _split_tables(documents: list[Document]) -> list[Document]:
    """Detect pipe-delimited tables in text Documents and split them out as
    chunk_type='table' so _chunk_documents keeps them atomic.

    Table text is prepended with the section label from metadata so the chunk
    embeds with context (e.g. "Cash Flows" before the pipe rows).
    """
    result: list[Document] = []
    for doc in documents:
        if doc.metadata.get("chunk_type") != "text":
            result.append(doc)
            continue

        lines = doc.text.split("\n")
        blocks: list[tuple[bool, list[str]]] = []
        current: list[str] = []
        in_table = False

        for line in lines:
            is_pipe = line.strip().startswith("|")
            if is_pipe != in_table:
                if current:
                    blocks.append((in_table, current))
                current = [line]
                in_table = is_pipe
            else:
                current.append(line)

        if current:
            blocks.append((in_table, current))

        if not any(is_tbl for is_tbl, _ in blocks):
            result.append(doc)
            continue

        # Validate pipe-line counts before merging
        for i, (is_tbl, block_lines) in enumerate(blocks):
            if is_tbl and sum(
                1 for ln in block_lines if ln.strip().startswith("|")
            ) < 2:
                blocks[i] = (False, block_lines)

        # Merge short orphan text blocks into preceding table block
        merged_blocks: list[tuple[bool, list[str]]] = []
        for is_tbl, block_lines in blocks:
            block_text_raw = "\n".join(block_lines).strip()
            if not block_text_raw:
                continue

            if is_tbl or block_text_raw.startswith("#"):
                merged_blocks.append((is_tbl, block_lines))
                continue

            has_prev_table = merged_blocks and merged_blocks[-1][0]
            if has_prev_table and len(block_text_raw) <= _ORPHAN_MERGE_MAX:
                merged_blocks[-1] = (
                    True,
                    merged_blocks[-1][1] + block_lines,
                )
            else:
                merged_blocks.append((False, block_lines))

        section = doc.metadata.get("section", "")
        for is_tbl, block_lines in merged_blocks:
            block_text = "\n".join(block_lines).strip()
            if not block_text:
                continue

            if is_tbl and section:
                block_text = f"{section}\n\n{block_text}"

            new_meta = dict(doc.metadata)
            new_meta["chunk_type"] = "table" if is_tbl else "text"
            result.append(Document(
                doc_id=doc.doc_id,
                text=block_text,
                metadata=new_meta,
            ))

    return result


# ── Sub-table splitting ───────────────────────────────────────────────

_SUB_TABLE_MIN_ROWS = 10


def _split_sub_tables(documents: list[Document]) -> list[Document]:
    """Split large table Documents at internal sub-header boundaries.

    Small tables (< _SUB_TABLE_MIN_ROWS rows) kept atomic.
    Tables with no detected sub-headers kept atomic.
    Consecutive sub-headers (hierarchical table) → don't split.
    Each sub-table gets the column header row prepended.
    """
    result: list[Document] = []
    for doc in documents:
        if doc.metadata.get("chunk_type") != "table":
            result.append(doc)
            continue

        lines = doc.text.split("\n")

        # Separate prepended section context (non-pipe lines at top)
        context_lines: list[str] = []
        pipe_lines: list[str] = []
        in_pipe = False
        for line in lines:
            if line.strip().startswith("|"):
                in_pipe = True
            if in_pipe:
                pipe_lines.append(line)
            else:
                context_lines.append(line)

        context_prefix = "\n".join(context_lines).strip()

        # Parse pipe rows into cells
        parsed: list[tuple[str, list[str]]] = []
        for line in pipe_lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            parsed.append((line, cells))

        if len(parsed) < _SUB_TABLE_MIN_ROWS:
            result.append(doc)
            continue

        # Identify header and separator rows
        header_line = parsed[0][0] if parsed else ""
        separator_line = ""
        data_start = 1
        if len(parsed) > 1 and all(
            "---" in c or not c for c in parsed[1][1]
        ):
            separator_line = parsed[1][0]
            data_start = 2

        header_block = header_line
        if separator_line:
            header_block = header_line + "\n" + separator_line

        def _is_sub_header(cells: list[str]) -> bool:
            if not cells or not cells[0].strip():
                return False
            if "---" in cells[0]:
                return False
            rest = cells[1:] if len(cells) > 1 else []
            if not rest:
                return False
            blank_count = sum(1 for c in rest if not c.strip())
            return blank_count == len(rest)

        split_indices: list[int] = []
        for i in range(data_start, len(parsed)):
            if _is_sub_header(parsed[i][1]):
                split_indices.append(i)

        if not split_indices:
            result.append(doc)
            continue

        has_consecutive = any(
            split_indices[i + 1] == split_indices[i] + 1
            for i in range(len(split_indices) - 1)
        )
        if has_consecutive:
            result.append(doc)
            continue

        # Split into sub-tables
        boundaries = split_indices + [len(parsed)]

        for b_idx in range(len(boundaries) - 1):
            start = boundaries[b_idx]
            end = boundaries[b_idx + 1]

            sub_header_label = parsed[start][1][0].strip()
            sub_rows = [parsed[j][0] for j in range(start, end)]
            sub_text = "\n".join(sub_rows)

            parts = []
            if context_prefix:
                parts.append(f"{context_prefix} — {sub_header_label}")
            else:
                parts.append(sub_header_label)
            parts.append(header_block)
            parts.append(sub_text)

            new_meta = dict(doc.metadata)
            new_meta["chunk_type"] = "table"
            new_meta["sub_table"] = sub_header_label
            result.append(Document(
                doc_id=doc.doc_id,
                text="\n".join(parts),
                metadata=new_meta,
            ))

        # Emit rows before first sub-header
        if split_indices[0] > data_start:
            pre_rows = [parsed[j][0] for j in range(data_start, split_indices[0])]
            pre_text = "\n".join(pre_rows)
            parts = []
            if context_prefix:
                parts.append(context_prefix)
            parts.append(header_block)
            parts.append(pre_text)

            new_meta = dict(doc.metadata)
            new_meta["chunk_type"] = "table"
            result.append(Document(
                doc_id=doc.doc_id,
                text="\n".join(parts),
                metadata=new_meta,
            ))

    return result


# ═══════════════════════════════════════════════════════════════════════
# Modified from PMS1 — PMS2-specific chunking and tagging
# ═══════════════════════════════════════════════════════════════════════

def _chunk_documents(documents: list[Document]) -> list[TextNode]:
    """Chunk documents. Tables kept atomic. Overlap=0."""
    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
        paragraph_separator="\n\n",
    )

    all_nodes: list[TextNode] = []

    for doc in documents:
        if doc.metadata.get("chunk_type") == "table":
            node = TextNode(text=doc.text, metadata=doc.metadata)
            node.metadata["num_chunks"] = 1
            all_nodes.append(node)
        else:
            nodes = splitter.get_nodes_from_documents([doc])
            for node in nodes:
                for key, value in doc.metadata.items():
                    if key not in node.metadata:
                        node.metadata[key] = value
                node.metadata["num_chunks"] = len(nodes)
            all_nodes.extend(nodes)

    # Drop tiny chunks
    all_nodes = [n for n in all_nodes if len(n.text.strip()) >= _MIN_CHUNK_SIZE]

    return all_nodes


def _tag_chunk_indices(nodes: list[TextNode], manifest: dict) -> None:
    """Add chunk_index, fiscal_year, filetype. No dir_implied_firm/sector."""
    file_counter: dict[str, int] = {}
    for node in nodes:
        fname = node.metadata.get("file_name", "unknown")
        fpath = node.metadata.get("file_path", "")

        # Sequential chunk index within each source file (keyed by
        # file_path, not file_name, to avoid collisions on duplicate basenames)
        idx = file_counter.get(fpath, 0)
        node.metadata["chunk_index"] = idx
        file_counter[fpath] = idx + 1

        # Fiscal year from filename
        m = re.search(r"_(\d{4})[Q_.]", fname)
        node.metadata["fiscal_year"] = f"FY{m.group(1)}" if m else ""

        # Filetype from manifest
        ft = manifest.get(fpath)
        if ft is None:
            warnings.warn(
                f"[01_Chunk] {fpath} not in manifest.json, filetype='unknown'",
                RuntimeWarning,
            )
            ft = "unknown"
        node.metadata["filetype"] = ft


# ═══════════════════════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════════════════════

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_md_files() -> dict[str, str]:
    """Scan files_ingested/ for .md files. Returns {rel_path: abs_path}."""
    files: dict[str, str] = {}
    for path in sorted(_INGESTED_DIR.rglob("*.md")):
        rel = str(path.relative_to(_INGESTED_DIR))
        files[rel] = str(path)
    return files


def _load_hashes() -> dict[str, str]:
    try:
        return json.loads(_HASHES_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_fpi() -> dict:
    try:
        return json.loads(_FPI_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ═══════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════

def _process_files(
    files: dict[str, str], manifest: dict,
) -> list[TextNode]:
    """Run full chunking pipeline on a set of .md files.

    files: {rel_path: abs_path}
    manifest: {md_rel_path: original_ext}
    Returns list of TextNode with overlap injected.
    """
    # 1. Load documents
    documents: list[Document] = []
    for rel_path, abs_path in files.items():
        try:
            documents.extend(_read_md(abs_path, rel_path))
        except Exception as exc:
            warnings.warn(f"Skipping {rel_path}: {exc}", RuntimeWarning)

    if not documents:
        return []

    # 2-4. PMS1 pipeline (unchanged)
    documents = _merge_stub_headers(documents)
    documents = _split_tables(documents)
    documents = _split_sub_tables(documents)

    # 5. Chunk (overlap=0)
    nodes = _chunk_documents(documents)

    # 6. Tag (modified: filetype, no dir_implied_*)
    _tag_chunk_indices(nodes, manifest)

    # 7. Overlap injection (new)
    nodes = _inject_overlap(nodes)

    return nodes


def _build_fpi(
    new_nodes: list[TextNode],
    old_fpi: dict,
    unchanged_files: set[str],
) -> dict:
    """Build file_path_index from fresh nodes + old FPI for unchanged files.

    Conceptually a full rebuild each run (spec requirement), but we avoid
    iterating the docstore by reusing old FPI entries for unchanged files
    and building new entries from fresh nodes.
    """
    fpi: dict = {}

    # Copy entries for unchanged files from previous run
    for fp in unchanged_files:
        if fp in old_fpi:
            fpi[fp] = old_fpi[fp]

    # Build entries from freshly processed nodes
    for node in new_nodes:
        fp = node.metadata.get("file_path", "")
        if not fp:
            continue
        if fp not in fpi:
            fpi[fp] = {
                "node_ids": [],
                "filetype": node.metadata.get("filetype", "unknown"),
            }
        fpi[fp]["node_ids"].append(node.node_id)

    return fpi


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def run_chunk() -> None:
    """Main entry point for 01_Chunk."""
    # Hard dep on manifest
    if not _MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"manifest.json not found at {_MANIFEST_PATH}. "
            f"Run 00_Ingest.py first."
        )
    manifest: dict = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

    # Scan .md files
    md_files = _scan_md_files()
    if not md_files:
        print("[Chunk_master] Raw files are empty JIT!!")
        return

    print(f"[01_Chunk] Found {len(md_files)} .md files in {_INGESTED_DIR}")

    # Compute hashes for all current .md files
    new_hashes: dict[str, str] = {}
    for rel, abs_p in md_files.items():
        new_hashes[rel] = _sha256_file(Path(abs_p))

    old_hashes = _load_hashes()

    # Diff: which files changed?
    added = {p for p in new_hashes if p not in old_hashes}
    removed = {p for p in old_hashes if p not in new_hashes}
    changed = {
        p for p in new_hashes
        if p in old_hashes and new_hashes[p] != old_hashes[p]
    }
    unchanged = set(new_hashes.keys()) - added - changed

    _INDEX_DIR.mkdir(parents=True, exist_ok=True)

    if not (added or removed or changed):
        print("[01_Chunk] Index up to date — no files changed.")
        return

    for label, fset in [("New", added), ("Removed", removed),
                        ("Changed", changed)]:
        if fset:
            print(f"  {label}: {', '.join(sorted(fset))}")

    stale = removed | changed
    fresh = added | changed

    # ── Load or create docstore ────────────────────────────────────────
    old_fpi = _load_fpi()

    if _DOCSTORE_PATH.exists() and (stale or unchanged):
        # Incremental: load existing docstore, delete stale nodes
        docstore = SimpleDocumentStore.from_persist_path(
            str(_DOCSTORE_PATH)
        )
        for fp in stale:
            if fp in old_fpi:
                for node_id in old_fpi[fp]["node_ids"]:
                    try:
                        docstore.delete_document(node_id, raise_error=False)
                    except Exception:
                        pass
        print(f"  Deleted nodes for {len(stale)} stale files")
    else:
        # Fresh build
        docstore = SimpleDocumentStore()

    # ── Process new/changed files ──────────────────────────────────────
    files_to_process = {
        rel: md_files[rel] for rel in fresh if rel in md_files
    }
    new_nodes = _process_files(files_to_process, manifest)

    if new_nodes:
        docstore.add_documents(new_nodes)
        print(
            f"  Added {len(new_nodes)} nodes from "
            f"{len(files_to_process)} files"
        )

    # ── Persist docstore ───────────────────────────────────────────────
    docstore.persist(persist_path=str(_DOCSTORE_PATH))
    print(f"[01_Chunk] docstore.json written to {_DOCSTORE_PATH}")

    # ── Build file_path_index ──────────────────────────────────────────
    fpi = _build_fpi(new_nodes, old_fpi, unchanged)
    _FPI_PATH.write_text(
        json.dumps(fpi, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    total_nodes = sum(len(v["node_ids"]) for v in fpi.values())
    print(
        f"[01_Chunk] file_path_index.json: {len(fpi)} files, "
        f"{total_nodes} total nodes"
    )

    # ── Save hashes ───────────────────────────────────────────────────
    _HASHES_PATH.write_text(
        json.dumps(new_hashes, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print("[01_Chunk] file_hashes.json written")


if __name__ == "__main__":
    run_chunk()
