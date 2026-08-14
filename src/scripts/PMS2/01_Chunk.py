"""01_Chunk.py — Chunk .md files from files_ingested/ into docstore.

Reads markdown files from data/files_ingested/, chunks them into a
SimpleDocumentStore at data/index/docstore.json. No embeddings.

Enhanced with advanced chunking strategies:
  - Configurable chunk strategies (sentence, recursive, semantic)
  - Hierarchical breadcrumb section naming
  - Synthetic section generation (transcripts, bullet notes, prose)
  - Table summarization for embedding enrichment
  - Chunk prefix building ([Company], [Sector], [Source], etc.)
  - Full fiscal year resolution cascade
  - Hierarchical parent-child chunking (small-to-big retrieval)
  - Rule-based contextual enrichment

Pipeline:
  _read_md -> _merge_stub_headers -> _split_tables -> _split_sub_tables
  -> _chunk_documents(overlap=0) -> _tag_chunk_indices -> _inject_overlap
  -> _build_parent_nodes -> _link_children_to_parents
  -> serialize (SimpleDocumentStore + file_path_index.json)

Run: python src/scripts/PMS2/01_Chunk.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path

from llama_index.core import Document
from llama_index.core.schema import TextNode
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.storage.docstore import SimpleDocumentStore

# Ensure project root and sibling modules importable when run as script
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent.parent
for _p in (str(_SCRIPT_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from chunk_overlap import _inject_overlap  # noqa: E402

from src.scripts.PMS2.ingest_config import (  # noqa: E402
    CHUNK_STRATEGY, CHUNK_SIZE, CHUNK_OVERLAP, MIN_CHUNK_SIZE,
    STUB_MAX_BODY_CHARS, ORPHAN_MERGE_MAX_CHARS, SUB_TABLE_MIN_ROWS,
    TABLE_SUMMARY_ENABLED, HIERARCHICAL_CHUNKING_ENABLED,
    CHUNK_ENRICHMENT_ENABLED,
)

# ── Paths ─────────────────────────────────────────────────────────────────
_INGESTED_DIR = _PROJECT_ROOT / "data" / "files_ingested"
_INDEX_DIR = _PROJECT_ROOT / "data" / "index"
_MANIFEST_PATH = _INGESTED_DIR / "manifest.json"
_DOCSTORE_PATH = _INDEX_DIR / "docstore.json"
_HASHES_PATH = _INDEX_DIR / "file_hashes.json"
_FPI_PATH = _INDEX_DIR / "file_path_index.json"


# ═════════════════════════════════════════════════════════════════════════
# Section detection
# ═════════════════════════════════════════════════════════════════════════

_SECTION_PATTERNS = [
    re.compile(r"^[\d]+[\s\.\)]+\s*[A-Z][^\n]{2,80}$", re.MULTILINE),
    re.compile(r"^[A-Z][A-Z\s\-/:]{3,60}$", re.MULTILINE),
    re.compile(r"^(Consolidated\s+)?(Balance\s+Sheet|Income\s+Statement|Cash\s+Flow|Statement\s+of)\b",
               re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[A-Z][A-Z\s\.]+(?:[—–-]|:).*$", re.MULTILINE),
]

# ── Prefix marker for embedding enrichment ────────────────────────────────
_CHUNK_PREFIX_SEPARATOR = "\n"

# ── Table summary patterns ─────────────────────────────────────────────────
_PERIOD_COL_RE = re.compile(
    r"(Q[1-4]\s*(?:FY|CY)?\s*\d{2,4})|(FY\s*\d{2,4})|(\d{1}[HQ]\d{2})",
    re.IGNORECASE,
)
_UNIT_HINTS_RE = re.compile(
    r"(?i)\b(\$\s*(?:in\s+)?(?:millions?|billions?|thousands?|m|bn?|k))\b|"
    r"\b(USD|RMB|JPY|EUR|GBP)\b|"
    r"\b(bps|basis\s+points?)\b|"
    r"\b(millions?|billions?|thousands?)\b"
)


def _detect_section(text: str) -> str:
    """Heuristically identify the section header from text."""
    for pattern in _SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0).strip().rstrip(":").strip()
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:80] if first_line else "General"


def _generate_synthetic_sections(text: str) -> list[tuple[str, str]]:
    """Generate meaningful section labels for files without markdown headings.

    Handles: transcripts (speaker labels), bullet notes, unstructured prose.
    Falls back to a single ("General", text) section if nothing matches.
    """
    lines = text.splitlines()
    if not lines:
        return [("General", text)]

    # Pattern 1: Transcript with speaker labels
    speaker_re = re.compile(
        r"^[A-Z][A-Z\s\.'-]{2,40}(?:[—–-]\s*(?:CFO|CEO|COO|Analyst|VP|Director))?\s*:",
        re.MULTILINE,
    )
    speaker_matches = list(speaker_re.finditer(text))
    if len(speaker_matches) >= 3:
        sections: list[tuple[str, str]] = []
        for idx, m in enumerate(speaker_matches):
            speaker = m.group(0).rstrip(":").strip()
            body_start = m.end()
            body_end = speaker_matches[idx + 1].start() if idx + 1 < len(speaker_matches) else len(text)
            body = text[body_start:body_end].strip()
            if body:
                sections.append((speaker, body))
        if speaker_matches[0].start() > 0:
            pre = text[:speaker_matches[0].start()].strip()
            if pre:
                sections.insert(0, ("Preamble", pre))
        if sections:
            return sections

    # Pattern 2: Bold markers / bullet-heavy content
    bold_re = re.compile(r"\*\*(.+?)\*\*")
    bold_match = bold_re.search(text)
    if bold_match:
        sections = []
        splits = bold_re.split(text)
        if splits[0].strip():
            sections.append(("Preamble", splits[0].strip()))
        for j in range(1, len(splits), 2):
            label = splits[j].strip()[:80]
            body = splits[j + 1].strip() if j + 1 < len(splits) else ""
            if body:
                sections.append((label, body))
        if sections:
            return sections

    # Pattern 3: Blank-line paragraph groups
    blocks = re.split(r"\n\s*\n", text)
    if len(blocks) >= 2:
        sections = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            first_line = block.split("\n", 1)[0].strip()
            label = re.sub(r"^[\*\-•]\s*", "", first_line)[:80]
            sections.append((label, block))
        if sections:
            return sections

    return [("General", text)]


# ═════════════════════════════════════════════════════════════════════════
# MD reading — with hierarchical breadcrumbs + synthetic sections
# ═════════════════════════════════════════════════════════════════════════

def _read_md(abs_path: str, rel_path: str) -> list[Document]:
    """Read a markdown file, splitting on headers with hierarchical breadcrumbs.

    Markdown ATX headings (#, ##, ###...) define section boundaries.
    Section titles are hierarchical breadcrumbs:
      "Item 7 MD&A > Results of Operations > Revenue"
    instead of flat "Revenue".

    If the file has YAML frontmatter, extracts and forwards useful metadata
    fields (entities, sector, doctype, language, filetype) to every child doc.
    """
    import frontmatter as fm

    file_name = Path(rel_path).name

    # Try to parse frontmatter
    frontmatter_meta: dict = {}
    body_text = ""
    try:
        post = fm.load(abs_path)
        body_text = post.content
        frontmatter_meta = dict(post.metadata)
    except Exception:
        body_text = Path(abs_path).read_text(encoding="utf-8", errors="replace")

    # Forward useful frontmatter fields to chunk metadata
    _USEFUL_META_KEYS = {
        "entities", "sector", "doctype", "language", "filetype", "last_modified",
    }
    extra_meta: dict = {}
    for k, v in frontmatter_meta.items():
        if k in _USEFUL_META_KEYS:
            if hasattr(v, "isoformat"):
                extra_meta[k] = v.isoformat()
            elif isinstance(v, list):
                extra_meta[k] = ", ".join(str(x) for x in v)
            else:
                extra_meta[k] = v

    # Split on markdown headings with hierarchical breadcrumbs
    sections = _split_by_md_headings(body_text)

    docs: list[Document] = []
    for section_title, section_text in sections:
        docs.append(Document(
            doc_id=f"file:{rel_path}",
            text=section_text.strip(),
            metadata={
                "file_name": file_name,
                "file_path": rel_path,
                "page_number": 1,
                "section": section_title,
                "chunk_type": "text",
                **extra_meta,
            }
        ))
    return docs


def _split_by_md_headings(text: str) -> list[tuple[str, str]]:
    """Split markdown body on ATX heading boundaries with hierarchical breadcrumbs.

    Returns a list of (section_title, section_body) tuples.  Text before
    the first heading is titled "Beginning".  If there are no headings,
    falls back to synthetic section detection.
    """
    heading_re = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(text))

    if not matches:
        return _generate_synthetic_sections(text)

    sections: list[tuple[str, str]] = []

    first = matches[0]
    if first.start() > 0:
        pre = text[:first.start()].strip()
        if pre:
            sections.append(("Beginning", pre))

    stack: list[str] = []

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()

        while len(stack) >= level:
            stack.pop()

        stack.append(title)
        breadcrumb = " > ".join(stack)

        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        sections.append((breadcrumb, body))

    return sections


# ═════════════════════════════════════════════════════════════════════════
# Stub header merging (hierarchical)
# ═════════════════════════════════════════════════════════════════════════

def _merge_stub_headers(documents: list[Document]) -> list[Document]:
    """Merge consecutive stub header docs into the next substantive doc.

    Only merges docs from the same source file (same file_name).
    Uses hierarchical " — " separator for merged headers.
    """
    if not documents:
        return documents

    result: list[Document] = []
    pending_labels: list[str] = []
    pending_fname: str = ""

    for doc in documents:
        fname = doc.metadata.get("file_name", "")

        if fname != pending_fname and pending_labels:
            for label in pending_labels:
                result.append(Document(
                    doc_id=doc.doc_id, text="",
                    metadata={**doc.metadata, "section": label},
                ))
            pending_labels = []

        pending_fname = fname
        text = doc.text.strip()

        if text.startswith("#"):
            body = text.split("\n", 1)[1].strip() if "\n" in text else ""
        else:
            body = text

        if len(body) < STUB_MAX_BODY_CHARS:
            section = doc.metadata.get("section", "")
            if section:
                pending_labels.append(section)
            continue

        if pending_labels:
            own_section = doc.metadata.get("section", "")
            if own_section:
                pending_labels.append(own_section)
            merged_section = " — ".join(pending_labels)
            new_meta = dict(doc.metadata)
            new_meta["section"] = merged_section
            result.append(Document(
                doc_id=doc.doc_id, text=doc.text, metadata=new_meta,
            ))
            pending_labels = []
        else:
            result.append(doc)

    if pending_labels:
        merged_section = " — ".join(pending_labels)
        result.append(Document(
            doc_id=documents[-1].doc_id, text="",
            metadata={**documents[-1].metadata, "section": merged_section},
        ))

    return result


# ═════════════════════════════════════════════════════════════════════════
# Table detection + splitting (ported from old ingest.py)
# ═════════════════════════════════════════════════════════════════════════

def _split_tables(documents: list[Document]) -> list[Document]:
    """Detect pipe-delimited tables and split out as chunk_type='table'."""
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

        # Validate pipe-line counts
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
            if has_prev_table and len(block_text_raw) <= ORPHAN_MERGE_MAX_CHARS:
                merged_blocks[-1] = (True, merged_blocks[-1][1] + block_lines)
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


# ═════════════════════════════════════════════════════════════════════════
# Sub-table splitting
# ═════════════════════════════════════════════════════════════════════════

def _split_sub_tables(documents: list[Document]) -> list[Document]:
    """Split large table Documents at internal sub-header boundaries."""
    result: list[Document] = []
    for doc in documents:
        if doc.metadata.get("chunk_type") != "table":
            result.append(doc)
            continue

        lines = doc.text.split("\n")

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

        parsed: list[tuple[str, list[str]]] = []
        for line in pipe_lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            parsed.append((line, cells))

        if len(parsed) < SUB_TABLE_MIN_ROWS:
            result.append(doc)
            continue

        header_line = parsed[0][0] if parsed else ""
        separator_line = ""
        data_start = 1
        if len(parsed) > 1 and all("---" in c or not c for c in parsed[1][1]):
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
            return sum(1 for c in rest if not c.strip()) == len(rest)

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


# ═════════════════════════════════════════════════════════════════════════
# Table summarization for embedding
# ═════════════════════════════════════════════════════════════════════════

def _summarize_table(table_text: str) -> str:
    """Generate a concise structured digest for table embedding.

    Example output:
        [TABLE: Revenue by Product — 3 cols (Q3 FY26, Q2 FY26, YoY%),
        2 rows. Units: $ millions. Ranges: Components $443.7–$533.3M,
        Systems $221.8–$275.1M. Periods: Q3 FY26, Q2 FY26]
    """
    lines = table_text.strip().split("\n")
    pipe_lines = [l for l in lines if l.strip().startswith("|")]
    if len(pipe_lines) < 2:
        return ""

    parsed: list[list[str]] = []
    for line in pipe_lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        parsed.append(cells)

    if len(parsed) < 2:
        return ""

    sep_idx = None
    for idx, row in enumerate(parsed):
        if all("---" in c or not c.strip() for c in row):
            sep_idx = idx
            break

    header_row = parsed[0] if sep_idx is None or sep_idx == 0 else (
        parsed[sep_idx - 1] if sep_idx > 0 else parsed[0])
    data_start = (sep_idx + 1) if sep_idx is not None else 1
    data_rows = parsed[data_start:]

    if not data_rows:
        return ""

    col_names = [h for h in header_row if h and h.lower() not in ("", "nan")]
    n_cols = len(col_names)
    n_rows = len(data_rows)

    row_labels: list[str] = []
    for row in data_rows:
        if row and row[0].strip() and row[0].strip().lower() != "nan":
            lbl = row[0].strip()
            if lbl not in row_labels:
                row_labels.append(lbl)

    numeric_cols: dict[str, list[float]] = {}
    for col_idx in range(1, max(len(r) for r in data_rows)):
        numbers: list[float] = []
        for row in data_rows:
            if col_idx < len(row):
                cell = row[col_idx]
                try:
                    val = float(
                        cell.replace(",", "").replace("$", "").replace("%", "")
                        .replace("(", "-").replace(")", "").strip()
                    )
                    numbers.append(val)
                except (ValueError, AttributeError):
                    pass
        if numbers:
            name = col_names[col_idx] if col_idx < len(col_names) else f"Col{col_idx}"
            numeric_cols[name] = numbers

    period_cols: list[str] = []
    for name in col_names:
        name_clean = name.replace("<br>", " ").replace("  ", " ")
        if _PERIOD_COL_RE.search(name_clean):
            period_cols.append(name_clean[:40])

    context_text = " ".join(
        l for l in lines if not l.strip().startswith("|")
    ) + " " + " ".join(header_row)
    units_found = list(dict.fromkeys(
        m.group(0) for m in _UNIT_HINTS_RE.finditer(context_text)
    ))

    parts: list[str] = ["[TABLE:"]

    context_title = ""
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") and stripped:
            context_title = stripped[:80]
            break

    if context_title:
        parts.append(f" {context_title} —")

    parts.append(f" {n_cols} cols")
    if col_names:
        shown = [c[:35] for c in col_names[:4]]
        parts.append(f" ({', '.join(shown)}")
        if len(col_names) > 4:
            parts.append(f", +{len(col_names) - 4} more")
        parts.append(")")

    parts.append(f", {n_rows} rows")

    if units_found:
        parts.append(f". Units: {', '.join(units_found[:3])}")

    if period_cols:
        parts.append(f". Periods: {', '.join(period_cols[:5])}")

    if row_labels and numeric_cols:
        parts.append(". Key rows: ")
        items: list[str] = []
        max_items = 3
        for row_label in row_labels[:max_items]:
            item_parts = [row_label[:35]]
            try:
                row_idx = row_labels.index(row_label)
            except ValueError:
                continue
            for col_name, values in numeric_cols.items():
                if row_idx < len(values):
                    item_parts.append(f"{values[row_idx]:.1f}")
            items.append(": ".join(item_parts))
        parts.append("; ".join(items))
        if len(row_labels) > max_items:
            parts.append(f"; +{len(row_labels) - max_items} more")
    elif numeric_cols:
        parts.append(". Ranges: ")
        range_items = []
        for col_name, values in list(numeric_cols.items())[:4]:
            mn, mx = min(values), max(values)
            if mn == mx:
                range_items.append(f"{col_name} {mn:.1f}")
            else:
                range_items.append(f"{col_name} {mn:.1f}–{mx:.1f}")
        parts.append("; ".join(range_items))

    parts.append("]")
    return "".join(parts)


# ═════════════════════════════════════════════════════════════════════════
# Chunk prefix building
# ═════════════════════════════════════════════════════════════════════════

def _build_chunk_prefix(meta: dict) -> str:
    """Build a structured prefix for embedding enrichment.

    Example:
        [Company: Lumentum (LITE)] [Sector: optical] [Source: Q3-FY26.pdf]
        [Type: filing] [Period: FY2026] [Section: Revenue]
    """
    parts: list[str] = []

    entities = meta.get("entities", "")
    if entities:
        parts.append(f"[Company: {entities}]")

    sector = meta.get("sector", "")
    if sector:
        parts.append(f"[Sector: {sector}]")

    fname = meta.get("file_name", "")
    if fname:
        parts.append(f"[Source: {fname}]")

    doctype = meta.get("doctype", "")
    if doctype:
        parts.append(f"[Type: {doctype}]")

    fiscal = meta.get("fiscal_year", "")
    if fiscal:
        parts.append(f"[Period: {fiscal}]")

    section = meta.get("section", "")
    if section and section not in ("General", "Beginning", ""):
        parts.append(f"[Section: {section}]")

    return " ".join(parts) + "\n" if parts else ""


# ═════════════════════════════════════════════════════════════════════════
# Chunking
# ═════════════════════════════════════════════════════════════════════════

def _build_recursive_splitter() -> SentenceSplitter:
    """Build a SentenceSplitter approximating recursive splitting with
    financial markdown separators."""
    return SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        paragraph_separator="\n### ",
        secondary_chunking_regex=r"\n## |\n# |\n\n|[.!?]\s+",
        include_metadata=False,
    )


def _build_semantic_splitter():
    """Build a semantic chunker; falls back to sentence splitter if unavailable."""
    try:
        from chonkie import SemanticChunker
        return SemanticChunker(
            embedding_model="BAAI/bge-base-en-v1.5",
            max_chunk_size=CHUNK_SIZE,
            similarity_threshold=0.7,
        )
    except ImportError:
        return SentenceSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            paragraph_separator="\n\n",
            include_metadata=False,
        )


def _chunk_documents(documents: list[Document]) -> list[TextNode]:
    """Chunk documents using configured strategy. Tables kept atomic. Overlap=0."""
    from src.scripts.PMS2.frontmatter_helpers import enrich_chunk_metadata

    strategy = CHUNK_STRATEGY

    if strategy == "sentence":
        splitter = SentenceSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            paragraph_separator="\n\n",
            include_metadata=False,
        )
    elif strategy == "recursive":
        splitter = _build_recursive_splitter()
    elif strategy == "semantic":
        splitter = _build_semantic_splitter()
    else:
        raise ValueError(
            f"Unknown chunk_strategy: {strategy!r}. "
            f"Expected 'sentence', 'recursive', or 'semantic'."
        )

    all_nodes: list[TextNode] = []

    for doc in documents:
        prefix = _build_chunk_prefix(doc.metadata)

        # Stash heavy metadata fields before splitting
        _heavy_keys = {"section", "file_path", "md_rel_path", "entities",
                       "sector", "doctype", "language", "filetype"}
        _stashed = {}
        for k in _heavy_keys:
            if k in doc.metadata:
                _stashed[k] = doc.metadata.pop(k)

        if doc.metadata.get("chunk_type") == "table":
            enriched_text = prefix + doc.text if prefix else doc.text

            if TABLE_SUMMARY_ENABLED:
                table_summary = _summarize_table(doc.text)
                if table_summary:
                    enriched_text = table_summary + "\n" + enriched_text

            node = TextNode(
                text=enriched_text,
                metadata={
                    **doc.metadata,
                    **_stashed,
                    "_display_text": doc.text,
                },
            )
            node.metadata["num_chunks"] = 1
            node.metadata["_has_prefix"] = bool(prefix)
            node.metadata.update(enrich_chunk_metadata(doc.text))
            all_nodes.append(node)
        else:
            nodes = splitter.get_nodes_from_documents([doc])
            for node in nodes:
                for key, value in doc.metadata.items():
                    if key not in node.metadata:
                        node.metadata[key] = value
                for k, v in _stashed.items():
                    node.metadata[k] = v
                node.metadata["num_chunks"] = len(nodes)
                original_text = node.text
                if prefix:
                    node.text = prefix + original_text
                    node.metadata["_display_text"] = original_text
                    node.metadata["_has_prefix"] = True
                node.metadata.update(enrich_chunk_metadata(original_text))
            all_nodes.extend(nodes)

    # Drop tiny chunks
    all_nodes = [n for n in all_nodes if len(n.text.strip()) >= MIN_CHUNK_SIZE]

    # ── Hierarchical parent-child chunking ──────────────────────────────
    if HIERARCHICAL_CHUNKING_ENABLED:
        parent_nodes = _build_parent_nodes(all_nodes)
        _link_children_to_parents(all_nodes, parent_nodes)
        all_nodes.extend(parent_nodes)

    # ── Contextual enrichment ──────────────────────────────────────────
    if CHUNK_ENRICHMENT_ENABLED:
        for node in all_nodes:
            enrichment = _enrich_node_metadata(node)
            node.metadata.update(enrichment)

    return all_nodes


# ═════════════════════════════════════════════════════════════════════════
# Hierarchical parent-child chunking
# ═════════════════════════════════════════════════════════════════════════

def _build_parent_nodes(nodes: list[TextNode]) -> list[TextNode]:
    """Build one parent node per (file_name, section) group.

    Parent nodes contain the concatenated text of all their child chunks,
    enabling small-to-big retrieval.
    """
    groups: dict[tuple[str, str], list[TextNode]] = defaultdict(list)
    for node in nodes:
        fname = node.metadata.get("file_name", "")
        section = node.metadata.get("section", "")
        if fname and section:
            groups[(fname, section)].append(node)

    parents: list[TextNode] = []
    for (fname, section), children in groups.items():
        if len(children) < 2:
            continue

        children.sort(key=lambda n: n.metadata.get("chunk_index", 0))

        combined = "\n\n".join(
            n.metadata.get("_display_text", n.text) for n in children
        )
        children_ids = [n.node_id for n in children]

        base_meta = dict(children[0].metadata)
        base_meta.update({
            "node_type": "parent",
            "chunk_type": "section",
            "children_ids": children_ids,
            "child_count": len(children),
            "section_path": section,
            "_display_text": combined,
        })

        parent = TextNode(
            text=combined,
            metadata=base_meta,
            id_=f"parent::{fname}::{section}",
        )
        parents.append(parent)

    return parents


def _link_children_to_parents(children: list[TextNode], parents: list[TextNode]) -> None:
    """Add parent_id metadata to each child node."""
    parent_lookup: dict[tuple[str, str], str] = {}
    for p in parents:
        fname = p.metadata.get("file_name", "")
        section = p.metadata.get("section_path", "")
        if fname and section:
            parent_lookup[(fname, section)] = p.node_id

    for child in children:
        fname = child.metadata.get("file_name", "")
        section = child.metadata.get("section", "")
        key = (fname, section)
        if key in parent_lookup:
            child.metadata["parent_id"] = parent_lookup[key]


# ═════════════════════════════════════════════════════════════════════════
# Contextual enrichment (rule-based, zero LLM calls)
# ═════════════════════════════════════════════════════════════════════════

def _enrich_node_metadata(node: TextNode) -> dict[str, str]:
    """Add lightweight contextual enrichment fields to a node."""
    meta = node.metadata
    text = meta.get("_display_text", node.text)
    text_clean = text.strip()
    ctype = meta.get("chunk_type", "text")
    section = meta.get("section", "")
    entities = meta.get("entities", "")
    fiscal = meta.get("fiscal_year", "")
    doctype = meta.get("doctype", "")
    statement = meta.get("statement_type", "")

    result: dict[str, str] = {}

    # context_sentence
    ctx_parts: list[str] = []
    if entities:
        ctx_parts.append(f"about {entities}")
    if fiscal:
        ctx_parts.append(f"for {fiscal}")
    if doctype:
        ctx_parts.append(f"in a {doctype}")
    if section and section not in ("General", "Beginning", ""):
        ctx_parts.append(f"in section '{section[:80]}'")
    if ctype == "table":
        ctx_parts.append("as tabular data")

    if ctx_parts:
        result["context_sentence"] = (
            f"This chunk contains information {' '.join(ctx_parts)}."
        )

    # hypothetical_questions
    questions: list[str] = []
    if entities and fiscal:
        questions.append(f"What were {entities} financial results for {fiscal}?")
    if entities and statement:
        stmt_label = statement.replace("_", " ")
        questions.append(f"What does the {stmt_label} show for {entities}?")
    if entities and ctype == "table":
        questions.append(f"What tabular data is available for {entities}?")
    if ctype == "text" and not questions:
        first_words = " ".join(text_clean.split()[:8])[:80]
        if first_words:
            questions.append(f"What does the document say about {first_words}?")

    if questions:
        result["hypothetical_questions"] = questions[:2]

    # enriched_summary (tables and long sections only)
    if ctype == "table" or len(text_clean) > 2000:
        lines = text_clean.split("\n")
        word_count = len(text_clean.split())
        line_count = len([l for l in lines if l.strip()])
        summary = f"{word_count} words, {line_count} lines"
        if ctype == "table":
            pipe_count = sum(1 for l in lines if l.strip().startswith("|"))
            summary += f", {pipe_count} table rows"
        if statement:
            summary += f", statement type: {statement.replace('_', ' ')}"
        result["enriched_summary"] = summary

    return result


# ═════════════════════════════════════════════════════════════════════════
# Fiscal year resolution
# ═════════════════════════════════════════════════════════════════════════

def _resolve_fiscal_year(fname: str, node_meta: dict) -> str:
    """Resolve fiscal year for a chunk using a fallback cascade.

    Cascade:
      1. Formal filing pattern: _YYYY[Q_.] (e.g. LITE_2024_10K.pdf)
      2. Bare 4-digit year in filename
      3. last_modified from YAML frontmatter
      4. period_referenced from chunk text
      5. Empty string (no temporal signal)
    """
    # 1. Formal filing pattern
    m = re.search(r"_(\d{4})[Q_.]", fname)
    if m:
        return f"FY{m.group(1)}"

    # 2. Bare 4-digit year in filename
    years = re.findall(r"\b(20\d{2})\b", fname)
    if years:
        return f"FY{max(years)}"

    # 3. last_modified from YAML frontmatter
    last_mod = node_meta.get("last_modified", "")
    if last_mod:
        m = re.match(r"20(\d{2})", str(last_mod))
        if m:
            return f"FY20{m.group(1)}"

    # 4. period_referenced from chunk text
    period_ref = node_meta.get("period_referenced", "")
    if period_ref:
        fy_years = re.findall(r"FY\s*20(\d{2})", period_ref, re.IGNORECASE)
        bare_years = re.findall(r"\b20(\d{2})\b", period_ref)
        all_years = fy_years + bare_years
        if all_years:
            most_recent = max(int(y) for y in all_years)
            return f"FY20{most_recent:02d}"

    return ""


# ═════════════════════════════════════════════════════════════════════════
# Tagging
# ═════════════════════════════════════════════════════════════════════════

def _tag_chunk_indices(nodes: list[TextNode], manifest: dict) -> None:
    """Add chunk_index, fiscal_year, filetype. Enrich from frontmatter."""
    file_counter: dict[str, int] = {}
    for node in nodes:
        fpath = node.metadata.get("file_path", "")
        fname = node.metadata.get("file_name", "unknown")

        # Sequential chunk index within each source file
        idx = file_counter.get(fpath, 0)
        node.metadata["chunk_index"] = idx
        file_counter[fpath] = idx + 1

        # Fiscal year — full cascade
        node.metadata["fiscal_year"] = _resolve_fiscal_year(fname, node.metadata)

        # Filetype from manifest
        ft = manifest.get(fpath)
        if ft is None:
            ft = "unknown"
        node.metadata["filetype"] = ft


# ═════════════════════════════════════════════════════════════════════════
# Utility
# ═════════════════════════════════════════════════════════════════════════

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
        if path.name in ("manifest.json", "convert_hashes.json"):
            continue
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


# ═════════════════════════════════════════════════════════════════════════
# Pipeline
# ═════════════════════════════════════════════════════════════════════════

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

    # 2-4. Pipeline stages
    documents = _merge_stub_headers(documents)
    documents = _split_tables(documents)
    documents = _split_sub_tables(documents)

    # 5. Chunk
    nodes = _chunk_documents(documents)

    # 6. Tag
    _tag_chunk_indices(nodes, manifest)

    # 7. Overlap injection
    nodes = _inject_overlap(nodes)

    return nodes


def _build_fpi(
    new_nodes: list[TextNode],
    old_fpi: dict,
    unchanged_files: set[str],
) -> dict:
    """Build file_path_index from fresh nodes + old FPI for unchanged files."""
    fpi: dict = {}

    for fp in unchanged_files:
        if fp in old_fpi:
            fpi[fp] = old_fpi[fp]

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


# ═════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════

def run_chunk() -> None:
    """Main entry point for 01_Chunk."""
    if not _MANIFEST_PATH.exists():
        raise FileNotFoundError(
            f"manifest.json not found at {_MANIFEST_PATH}. "
            f"Run 00_Ingest.py first."
        )
    manifest: dict = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))

    md_files = _scan_md_files()
    if not md_files:
        print("[01_Chunk] No .md files found in files_ingested/")
        return

    print(f"[01_Chunk] Found {len(md_files)} .md files in {_INGESTED_DIR}")
    print(f"[01_Chunk] Strategy: {CHUNK_STRATEGY}, chunk_size={CHUNK_SIZE}, "
          f"hierarchical={HIERARCHICAL_CHUNKING_ENABLED}, "
          f"enrichment={CHUNK_ENRICHMENT_ENABLED}")

    # Compute hashes for all current .md files
    new_hashes: dict[str, str] = {}
    for rel, abs_p in md_files.items():
        new_hashes[rel] = _sha256_file(Path(abs_p))

    old_hashes = _load_hashes()

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

    old_fpi = _load_fpi()

    if _DOCSTORE_PATH.exists() and (stale or unchanged):
        docstore = SimpleDocumentStore.from_persist_path(str(_DOCSTORE_PATH))
        for fp in stale:
            if fp in old_fpi:
                for node_id in old_fpi[fp]["node_ids"]:
                    try:
                        docstore.delete_document(node_id, raise_error=False)
                    except Exception:
                        pass
        print(f"  Deleted nodes for {len(stale)} stale files")
    else:
        docstore = SimpleDocumentStore()

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

    docstore.persist(persist_path=str(_DOCSTORE_PATH))
    print(f"[01_Chunk] docstore.json written to {_DOCSTORE_PATH}")

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

    _HASHES_PATH.write_text(
        json.dumps(new_hashes, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print("[01_Chunk] file_hashes.json written")


if __name__ == "__main__":
    run_chunk()
