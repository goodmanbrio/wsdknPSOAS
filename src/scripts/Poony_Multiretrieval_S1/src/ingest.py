"""
ingest.py — Financial-document-aware loading and chunking.

Handles PDF (via PyMuPDF), TXT, and DOCX.  Extracts tables as whole chunks,
preserves section headers, and annotates every chunk with source metadata.

Usage (typically called via index_store.py, not directly):
    from src.ingest import load_and_chunk_documents
    nodes = load_and_chunk_documents(config)
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Iterator

import fitz
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter

from src.config import Config, FIRM_SYNONYMS

# ── Supported file extensions and their label ────────────────────────────
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".docx", ".md"}


# ═════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════

def load_and_chunk_documents(config: Config) -> list:
    """Walk data/, load supported files, and chunk them for indexing.

    Returns a list of llama_index TextNode objects, each with metadata:
        file_name, file_path, page_number, section, chunk_type,
        chunk_index, num_chunks
    """
    raw_docs = _load_documents(config.data_dir)
    if not raw_docs:
        return []

    raw_docs = _merge_stub_headers(raw_docs)
    raw_docs = _split_tables(raw_docs)
    raw_docs = _split_sub_tables(raw_docs)
    nodes = _chunk_documents(raw_docs, config)
    # Tag ordering within each source document
    _tag_chunk_indices(nodes)
    return nodes


def scan_files(data_dir: Path) -> dict[str, str]:
    """Return {relative_path: absolute_path} for all supported files."""
    files: dict[str, str] = {}
    for path in data_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            rel = str(path.relative_to(data_dir))
            files[rel] = str(path)
    return files


# ═════════════════════════════════════════════════════════════════════════
# Document loaders (one per file type)
# ═════════════════════════════════════════════════════════════════════════

def _load_documents(data_dir: Path) -> list[Document]:
    """Dispatch each supported file to the appropriate reader."""
    documents: list[Document] = []
    files = scan_files(data_dir)
    for rel_path, abs_path in files.items():
        suffix = Path(rel_path).suffix.lower()
        try:
            if suffix == ".pdf":
                documents.extend(_read_pdf(abs_path, rel_path))
            elif suffix == ".txt":
                documents.extend(_read_txt(abs_path, rel_path))
            elif suffix == ".docx":
                documents.extend(_read_docx(abs_path, rel_path))
            elif suffix == ".md":
                documents.extend(_read_md(abs_path, rel_path))
        except Exception as exc:
            warnings.warn(f"Skipping {rel_path}: {exc}", RuntimeWarning)
    return documents


def _read_pdf(abs_path: str, rel_path: str) -> list[Document]:
    """Read a PDF with PyMuPDF (fitz), extracting text + tables per page.

    Strategy:
        1. For each page, detect tables with page.find_tables().
        2. Extract text blocks, filtering out regions already covered by tables.
        3. Detect section headers (bold, large font, or pattern-based).
        4. Return one Document per meaningful unit (page text or table).
    """
    import fitz  # PyMuPDF — lazy import so missing dep gives a clear error

    # Suppress MuPDF C-layer "No common ancestor in structure tree" noise
    fitz.TOOLS.mupdf_display_errors(False)

    docs: list[Document] = []
    file_name = Path(rel_path).name

    try:
        pdf = fitz.open(abs_path)
    except Exception as exc:
        warnings.warn(f"Could not open PDF {rel_path}: {exc}", RuntimeWarning)
        return docs

    for page_num, page in enumerate(pdf, start=1):
        # ── 1. Extract tables ───────────────────────────────────────────
        tables = _extract_tables(page)

        # ── 2. Extract text (skip table-covered regions) ────────────────
        text = _extract_text_excluding_tables(page, tables)

        section = ""

        # ── 4. Create documents ─────────────────────────────────────────
        # Text document (one per page — chunker will split later)
        if text.strip():
            docs.append(Document(
                doc_id=f"file:{rel_path}",
                text=text.strip(),
                metadata={
                    "file_name": file_name,
                    "file_path": rel_path,
                    "page_number": page_num,
                    "section": section,
                    "chunk_type": "text",
                }
            ))

        # Table documents (each table is its own unit)
        for t_idx, tbl_text in enumerate(tables):
            clean = _clean_table_text(tbl_text)
            if clean:
                docs.append(Document(
                    doc_id=f"file:{rel_path}",
                    text=clean,
                    metadata={
                        "file_name": file_name,
                        "file_path": rel_path,
                        "page_number": page_num,
                        "section": section,
                        "chunk_type": "table",
                    }
                ))

    pdf.close()
    return docs


def _read_txt(abs_path: str, rel_path: str) -> list[Document]:
    """Read a plain-text file.  Splits on double-newlines as rough sections."""
    file_name = Path(rel_path).name
    raw = Path(abs_path).read_text(encoding="utf-8", errors="replace")

    # Try to find section boundaries: double-newline or lines that look like headers
    # For earnings transcripts, speaker labels like "TIM COOK:" are section markers.
    parts = re.split(r"\n\s*\n", raw)

    docs: list[Document] = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        docs.append(Document(
            doc_id=f"file:{rel_path}",
            text=part,
            metadata={
                "file_name": file_name,
                "file_path": rel_path,
                "page_number": 1,
                "section": "",
                "chunk_type": "text",
            }
        ))
    return docs


def _read_md(abs_path: str, rel_path: str) -> list[Document]:
    """Read a markdown file, splitting on headers to keep tables with context.

    Markdown files (often Docling/PDF-converter output) have deterministic
    structure: ## headers, pipe tables, consistent syntax.  Splitting on
    headers instead of blank lines keeps section headings attached to their
    content — critical for financial tables that lose all searchable context
    when orphaned from their heading.
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

        # Extract the header line for section metadata
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


def _read_docx(abs_path: str, rel_path: str) -> list[Document]:
    """Read a .docx file paragraph by paragraph."""
    from docx import Document as DocxReader  # python-docx

    file_name = Path(rel_path).name
    docx = DocxReader(abs_path)

    # We accumulate paragraphs until we hit a heading or reach a
    # reasonable size, then emit a Document.
    paragraphs: list[str] = []
    current_section = "Beginning"
    docs: list[Document] = []
    section_idx = 0

    for para in docx.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # Use Word heading styles to detect sections
        if para.style and para.style.name and para.style.name.startswith("Heading"):
            # Flush accumulated text
            if paragraphs:
                joined = "\n".join(paragraphs)
                docs.append(Document(
                    doc_id=f"file:{rel_path}",
                    text=joined,
                    metadata={
                        "file_name": file_name,
                        "file_path": rel_path,
                        "page_number": 1,
                        "section": current_section,
                        "chunk_type": "text",
                    }
                ))
                paragraphs = []
                section_idx += 1
            current_section = text  # heading becomes the section

        paragraphs.append(text)

    # Flush remaining
    if paragraphs:
        joined = "\n".join(paragraphs)
        docs.append(Document(
            doc_id=f"file:{rel_path}",
            text=joined,
            metadata={
                "file_name": file_name,
                "file_path": rel_path,
                "page_number": 1,
                "section": current_section,
                "chunk_type": "text",
            }
        ))

    return docs


# ═════════════════════════════════════════════════════════════════════════
# PDF table extraction helpers
# ═════════════════════════════════════════════════════════════════════════

def _extract_tables(page: "fitz.Page") -> list[str]:
    """Detect and extract tables from a PyMuPDF page.

    Returns a list of markdown-ish table text strings.
    """
    tables: list[str] = []
    try:
        tbl_finder = page.find_tables()
        for tbl in tbl_finder.tables:
            rows = tbl.extract()  # list of lists
            if rows:
                # Convert to a readable text block
                lines = [" | ".join(str(cell or "") for cell in row) for row in rows]
                tables.append("\n".join(lines))
    except Exception:
        # Some PDFs have table extraction issues; silently skip.
        pass
    return tables


def _extract_text_excluding_tables(page: "fitz.Page", tables: list[str]) -> str:
    """Extract page text blocks, excluding regions captured as tables.

    If tables are present and non-trivial, we use a simple heuristic: prefer
    block-level text that doesn't overlap table bounding boxes. When table
    bounding boxes are available, we filter; otherwise we return all text.
    """
    blocks = page.get_text("blocks")  # list of (x0,y0,x1,y1, text, block_no, block_type)

    # If no tables were found, return all text
    if not tables:
        return "\n".join(b[4] for b in blocks if b[4].strip())

    # Get table bounding boxes
    try:
        tbl_finder = page.find_tables()
        table_rects = []
        for tbl in tbl_finder.tables:
            table_rects.append(tbl.bbox)  # (x0, y0, x1, y1)
    except Exception:
        table_rects = []

    if not table_rects:
        return "\n".join(b[4] for b in blocks if b[4].strip())

    # Filter blocks that overlap with any table region
    text_parts: list[str] = []
    for block in blocks:
        bx0, by0, bx1, by1 = block[:4]
        block_text = block[4].strip()
        if not block_text:
            continue
        overlaps = any(
            _rects_overlap((bx0, by0, bx1, by1), tr) for tr in table_rects
        )
        if not overlaps:
            text_parts.append(block_text)

    return "\n".join(text_parts)


def _rects_overlap(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """True if two rectangles overlap."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _clean_table_text(raw: str) -> str:
    """Normalise table text for embedding and display."""
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


# ═════════════════════════════════════════════════════════════════════════
# Stub header merging
# ═════════════════════════════════════════════════════════════════════════

# Threshold for "stub" — a doc whose body (excluding # header line) is
# shorter than this is considered a header-only stub and its section label
# gets absorbed into the next doc. Arbitrary — tune if real content gets
# swallowed or real stubs get missed.
_STUB_MAX_BODY_CHARS = 110


def _merge_stub_headers(documents: list[Document]) -> list[Document]:
    """Merge consecutive stub header docs into the next substantive doc.

    _read_md splits at every ## header. Financial statements often have
    multi-line headers:
        ## Amcor plc and Subsidiaries        (stub — no body)
        ## Consolidated Balance Sheets        (stub — no body)
        ## ($ in millions...)                 (has table body)

    Without merging, the table chunk gets section="($ in millions...)"
    and loses "Consolidated Balance Sheets" — the actual statement title
    that BM25 needs.

    After merging, section="Amcor plc and Subsidiaries Consolidated
    Balance Sheets ($ in millions...)" — all headers concatenated.

    Only merges docs from the same source file (same file_name).
    """
    if not documents:
        return documents

    result: list[Document] = []
    pending_labels: list[str] = []  # stub section labels waiting to merge
    pending_fname: str = ""         # file_name of pending stubs

    for doc in documents:
        fname = doc.metadata.get("file_name", "")

        # File boundary — flush pending stubs as standalone docs
        # (no next sibling to merge into)
        if fname != pending_fname and pending_labels:
            # Stubs at end of previous file — emit as-is, nothing to merge into
            pending_labels = []

        pending_fname = fname

        # Check if this doc is a stub: body (text minus header line) < threshold
        text = doc.text.strip()
        if text.startswith("#"):
            # Remove the header line to measure body size
            body = text.split("\n", 1)[1].strip() if "\n" in text else ""
        else:
            body = text

        if len(body) < _STUB_MAX_BODY_CHARS:
            # Stub — accumulate its section label, don't emit yet
            section = doc.metadata.get("section", "")
            if section:
                pending_labels.append(section)
            continue

        # Substantive doc — absorb any pending stub labels into its section
        if pending_labels:
            own_section = doc.metadata.get("section", "")
            if own_section:
                pending_labels.append(own_section)
            merged_section = " ".join(pending_labels)
            new_meta = dict(doc.metadata)
            new_meta["section"] = merged_section
            result.append(Document(
                doc_id=doc.doc_id,
                text=doc.text,
                metadata=new_meta,
            ))
            pending_labels = []
        else:
            result.append(doc)

    # Trailing stubs with no following substantive doc — emit as
    # empty-section docs JIC (could be orphan headers for content
    # that lives in a different format or was truncated)
    if pending_labels:
        merged_section = " ".join(pending_labels)
        # Use last pending stub's doc_id and metadata as base
        result.append(Document(
            doc_id=documents[-1].doc_id,
            text="",
            metadata={
                **documents[-1].metadata,
                "section": merged_section,
            },
        ))

    return result


# ═════════════════════════════════════════════════════════════════════════
# Table detection (format-agnostic)
# ═════════════════════════════════════════════════════════════════════════

def _split_tables(documents: list[Document]) -> list[Document]:
    """Detect pipe-delimited tables in text Documents and split them out as
    chunk_type='table' so _chunk_documents keeps them atomic.

    Applies to ALL Documents regardless of source reader (.md, .txt, .docx).
    Documents already tagged as tables (e.g. from _read_pdf) are passed through.

    Table text is prepended with the section label from metadata so the chunk
    embeds with context (e.g. "Cash Flows" before the pipe rows).
    """
    result: list[Document] = []
    for doc in documents:
        if doc.metadata.get("chunk_type") != "text":
            result.append(doc)
            continue

        lines = doc.text.split("\n")
        blocks: list[tuple[bool, list[str]]] = []  # (is_table, lines)
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

        # No tables found — keep original Document unchanged
        if not any(is_tbl for is_tbl, _ in blocks):
            result.append(doc)
            continue

        # Validate pipe-line counts before merging
        for i, (is_tbl, block_lines) in enumerate(blocks):
            if is_tbl and sum(1 for l in block_lines if l.strip().startswith("|")) < 2:
                blocks[i] = (False, block_lines)

        # Merge short orphan text blocks into preceding table block.
        # 10-K markdown has frequent short prose between pipe tables:
        # footnotes, table intros, substantive 1-2 liners (e.g. "U.S.
        # government represented 40% of consolidated revenues"). These
        # become tiny standalone chunks that can't compete in retrieval.
        # Merge UP into preceding table if ≤ 400 chars. Longer blocks
        # are standalone content that embeds well on its own.
        _ORPHAN_MERGE_MAX = 400

        merged_blocks: list[tuple[bool, list[str]]] = []
        for is_tbl, block_lines in blocks:
            block_text_raw = "\n".join(block_lines).strip()
            if not block_text_raw:
                continue

            if is_tbl or block_text_raw.startswith("#"):
                merged_blocks.append((is_tbl, block_lines))
                continue

            # Short non-header text with a preceding table → merge UP
            has_prev_table = merged_blocks and merged_blocks[-1][0]
            if has_prev_table and len(block_text_raw) <= _ORPHAN_MERGE_MAX:
                merged_blocks[-1] = (
                    True,
                    merged_blocks[-1][1] + block_lines,
                )
            else:
                merged_blocks.append((False, block_lines))

        # Emit Documents from merged blocks
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


_SUB_TABLE_MIN_ROWS = 10  # only split tables with this many data rows or more


def _split_sub_tables(documents: list[Document]) -> list[Document]:
    """Split large table Documents at internal sub-header boundaries.

    Financial tables often contain logical sections (e.g. "Per Share Data",
    "Operating Statistics", "Year-End Data") marked by rows where only the
    first cell has text and the rest are blank.  Splitting at these boundaries
    produces focused sub-table chunks whose embeddings aren't diluted by
    unrelated metrics.

    Small tables (< _SUB_TABLE_MIN_ROWS rows) are kept atomic.
    Tables with no detected sub-headers are kept atomic.
    Each sub-table gets the column header row prepended so column labels
    aren't lost.
    """
    result: list[Document] = []
    for doc in documents:
        if doc.metadata.get("chunk_type") != "table":
            result.append(doc)
            continue

        lines = doc.text.split("\n")

        # Separate any prepended section context (non-pipe lines at top)
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
        parsed: list[tuple[str, list[str]]] = []  # (raw_line, cells)
        for line in pipe_lines:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            parsed.append((line, cells))

        # Need enough rows to bother splitting
        if len(parsed) < _SUB_TABLE_MIN_ROWS:
            result.append(doc)
            continue

        # Identify header row (row 0) and separator row (row with ---)
        header_line = parsed[0][0] if parsed else ""
        separator_line = ""
        data_start = 1
        if len(parsed) > 1 and all("---" in c or not c for c in parsed[1][1]):
            separator_line = parsed[1][0]
            data_start = 2

        header_block = header_line
        if separator_line:
            header_block = header_line + "\n" + separator_line

        # Detect sub-header rows in the data portion
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

        # Collect split points
        split_indices: list[int] = []
        for i in range(data_start, len(parsed)):
            if _is_sub_header(parsed[i][1]):
                split_indices.append(i)

        # No sub-headers found → keep atomic
        if not split_indices:
            result.append(doc)
            continue

        # Consecutive sub-headers → hierarchical table → don't split.
        # e.g. balance sheet: "Assets" → "Current assets" are adjacent
        # sub-headers (no data between). Splitting here orphans summary
        # rows like "Total assets" into unrelated sub-tables.
        # Flat tables (income stmt, cash flow) never have consecutive
        # sub-headers — each is followed by data rows.
        # Cursory check: holds for US GAAP, IFRS, CAS balance sheets.
        has_consecutive = any(
            split_indices[i + 1] == split_indices[i] + 1
            for i in range(len(split_indices) - 1)
        )
        if has_consecutive:
            result.append(doc)
            continue

        # Split into sub-tables at sub-header boundaries
        # Add end sentinel
        boundaries = split_indices + [len(parsed)]

        for b_idx in range(len(boundaries) - 1):
            start = boundaries[b_idx]
            end = boundaries[b_idx + 1]

            sub_header_label = parsed[start][1][0].strip()
            sub_rows = [parsed[j][0] for j in range(start, end)]
            sub_text = "\n".join(sub_rows)

            # Build chunk: context + sub-header label + column headers + rows
            parts = []
            if context_prefix:
                parts.append(f"{context_prefix} — {sub_header_label}")
            else:
                parts.append(sub_header_label)
            parts.append(header_block)
            parts.append(sub_text)

            chunk_text = "\n".join(parts)

            new_meta = dict(doc.metadata)
            new_meta["chunk_type"] = "table"
            new_meta["sub_table"] = sub_header_label
            result.append(Document(
                doc_id=doc.doc_id,
                text=chunk_text,
                metadata=new_meta,
            ))

        # Also emit rows BEFORE the first sub-header (if any data rows exist there)
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
# Chunking
# ═════════════════════════════════════════════════════════════════════════

def _chunk_documents(documents: list[Document], config: Config) -> list:
    """Split documents into chunks using SentenceSplitter.

    Key behavior:
        - Text chunks: split at sentence boundaries, chunk_size tokens.
        - Table chunks: kept whole (never split), no matter the size.
        - Metadata preserved and forwarded to each child chunk.
    """
    splitter = SentenceSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
        paragraph_separator="\n\n",
    )

    all_nodes: list = []

    for doc in documents:
        if doc.metadata.get("chunk_type") == "table":
            # Tables are atomic — we don't split them, but we still need
            # to wrap them as a node.
            from llama_index.core.schema import TextNode
            node = TextNode(
                text=doc.text,
                metadata=doc.metadata,
            )
            node.metadata["num_chunks"] = 1
            all_nodes.append(node)
        else:
            # Text documents — run through SentenceSplitter.
            nodes = splitter.get_nodes_from_documents([doc])
            # The splitter copies metadata, but let's ensure it's preserved.
            for node in nodes:
                for key, value in doc.metadata.items():
                    if key not in node.metadata:
                        node.metadata[key] = value
                node.metadata["num_chunks"] = len(nodes)
            all_nodes.extend(nodes)

    # Drop tiny chunks (e.g., a single number or "Page intentionally blank")
    all_nodes = [n for n in all_nodes if len(n.text.strip()) >= config.min_chunk_size]

    return all_nodes


def _resolve_dir_implied_firm(dir_name: str) -> str:
    """Reverse-lookup a directory name against FIRM_SYNONYMS.

    e.g. "BESTBUY" → found in "Best Buy" group → returns "Best Buy".
    Returns "" if no match (file not in a recognized company subdir).
    """
    dir_norm = dir_name.strip().lower()
    for group_key, synonyms in FIRM_SYNONYMS.items():
        if any(s.strip().lower() == dir_norm for s in synonyms):
            return group_key
    return ""


def _tag_chunk_indices(nodes: list) -> None:
    """Add chunk_index, fiscal_year, dir_implied_firm, dir_implied_sector."""
    file_counter: dict[str, int] = {}
    for node in nodes:
        fname = node.metadata.get("file_name", "unknown")
        fpath = node.metadata.get("file_path", "")

        # Sequential chunk index within each source file
        idx = file_counter.get(fname, 0)
        node.metadata["chunk_index"] = idx
        file_counter[fname] = idx + 1

        # Fiscal year from filename
        m = re.search(r"_(\d{4})[Q_.]", fname)
        node.metadata["fiscal_year"] = f"FY{m.group(1)}" if m else ""

        # dir_implied_firm: parent directory → reverse lookup FIRM_SYNONYMS
        # e.g. "Consumer Discretionary/BESTBUY/BESTBUY_2023_10K.md"
        #       parent = "BESTBUY" → group key "Best Buy"
        parts = Path(fpath).parts  # ("Consumer Discretionary", "BESTBUY", "BESTBUY_2023_10K.md")
        if len(parts) >= 2:
            parent_dir = parts[-2]  # company dir, one level above file
            node.metadata["dir_implied_firm"] = _resolve_dir_implied_firm(parent_dir)
        else:
            node.metadata["dir_implied_firm"] = ""

        # dir_implied_sector: grandparent directory
        # e.g. "Consumer Discretionary/BESTBUY/file.md" → "Consumer Discretionary"
        if len(parts) >= 3:
            node.metadata["dir_implied_sector"] = parts[-3]
        else:
            node.metadata["dir_implied_sector"] = ""
