"""ingest_config.py — Centralized ingest/chunk parameters for the PMS2 pipeline.

Single place to tweak ingest/chunk behavior.  00_Ingest.py and 01_Chunk.py
import from here instead of having tunable constants scattered across files.

Usage:
    from src.scripts.PMS2.ingest_config import (
        SUPPORTED_EXTS, CHUNK_SIZE, CHUNK_STRATEGY, ...
    )
"""

from __future__ import annotations

from pathlib import Path

# ── Paths (relative to project root) ──────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RAW_DIR = _PROJECT_ROOT / "data" / "files_raw"
INGESTED_DIR = _PROJECT_ROOT / "data" / "files_ingested"
INDEX_DIR = _PROJECT_ROOT / "data" / "index"

# ── Supported file types for ingestion ────────────────────────────────────
# Docling handles all these natively (with pandas pre-processing for Excel).
# .msg files delegate to the dedicated EmailConverter.
# .xml / .json fall back to MarkItDown (not supported by docling).

SUPPORTED_EXTS: set[str] = {
    ".pdf", ".docx", ".pptx",
    ".xlsx", ".xlsm", ".xls",
    ".html", ".htm", ".csv",
    ".md", ".txt",
    ".msg",
    ".xml", ".json",
}

# Extensions that need pandas pre-processing before Docling
_EXCEL_EXTENSIONS: set[str] = {".xlsx", ".xlsm", ".xls"}

# Extensions that docling handles natively (all except .msg, .xml, .json)
_DOCLING_FORMATS: set[str] = SUPPORTED_EXTS - {".msg", ".xml", ".json"}

# ── Chunking ──────────────────────────────────────────────────────────────
CHUNK_STRATEGY: str = "sentence"
#   "sentence"  → SentenceSplitter at sentence boundaries (default)
#   "recursive" → SentenceSplitter with financial separators (###, ##, #, paragraphs)
#   "semantic"  → chonkie.SemanticChunker (embedding-based boundary detection);
#                  falls back to "sentence" if chonkie is not installed

CHUNK_SIZE: int = 768           # tokens per chunk
CHUNK_OVERLAP: int = 0          # _inject_overlap is the sole overlap mechanism
MIN_CHUNK_SIZE: int = 100       # discard chunks shorter than this (chars)

# ── Stub header merging ───────────────────────────────────────────────────
STUB_MAX_BODY_CHARS: int = 110
# Max body length (chars) to classify a section as a stub header — a
# heading with no real body that should be merged into the next section.

# ── Table detection ───────────────────────────────────────────────────────
ORPHAN_MERGE_MAX_CHARS: int = 650
# Max chars for orphan prose between two pipe tables to be merged UP into
# the preceding table chunk.  Raised from 400 to capture longer footnotes
# and table introductions common in sell-side models.

SUB_TABLE_MIN_ROWS: int = 10
# Minimum data rows to trigger splitting a large table at sub-header
# boundaries (e.g. "Per Share Data" vs "Operating Statistics").

# ── Table summarization ───────────────────────────────────────────────────
TABLE_SUMMARY_ENABLED: bool = True
# When True, prepend a structured digest to table chunks before adding to
# docstore (column names, row count, numeric ranges).  Improves BM25 and
# vector recall for queries targeting specific metrics.

# ── Hierarchical parent-child chunking ────────────────────────────────────
HIERARCHICAL_CHUNKING_ENABLED: bool = True
# When True, creates parent "section" nodes that concatenate all child
# chunks from the same (file, section) group.  Parents are indexed
# alongside children, enabling small-to-big retrieval: search finds
# precise child chunks, but the LLM receives complete parent sections.

# ── Chunk enrichment ──────────────────────────────────────────────────────
CHUNK_ENRICHMENT_ENABLED: bool = True
# When True, adds lightweight rule-based enrichment fields to every chunk:
# context_sentence (where this chunk fits), hypothetical_questions
# (what queries it could answer), and enriched_summary (for tables).

# ── Overlap injection ─────────────────────────────────────────────────────
OVERLAP_CHARS: int = 1000
# Number of characters to inject from neighboring chunks via _inject_overlap.
