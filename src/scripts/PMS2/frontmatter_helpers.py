"""frontmatter_helpers.py — Rule-based metadata detection (zero LLM calls).

All functions are deterministic: regex, filesystem stat, langdetect,
and keyword-lookup only.  Used by 00_Ingest.py for frontmatter building
and 01_Chunk.py for per-chunk metadata enrichment.

Ported from buy-side-research - chunk strat/src/frontmatter_helpers.py
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


# ═════════════════════════════════════════════════════════════════════════
# Config loaders
# ═════════════════════════════════════════════════════════════════════════

def load_entities(path: str = "entities.yaml") -> dict[str, list[str]]:
    """Load the company→ticker mapping from a YAML file.

    Returns {company_lower: [ticker, ...]}.
    Falls back to empty dict if the file is missing or unreadable.
    """
    p = Path(path)
    if not p.exists():
        # Try relative to project root
        alt = Path(__file__).resolve().parent.parent.parent.parent / path
        if alt.exists():
            p = alt
        else:
            return {}
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {str(k).lower(): v for k, v in data.items()}
    except Exception:
        return {}


def load_keywords_config(path: str = "keywords.yaml") -> dict:
    """Load keyword/label/doctype configuration from YAML.

    Returns the full config dict.  Falls back to empty dict if the file
    is missing or unreadable.
    """
    p = Path(path)
    if not p.exists():
        alt = Path(__file__).resolve().parent.parent.parent.parent / path
        if alt.exists():
            p = alt
        else:
            return {}
    try:
        import yaml
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ═════════════════════════════════════════════════════════════════════════
# Language detection — langdetect
# ═════════════════════════════════════════════════════════════════════════

_LANG_PROB_THRESHOLD = 0.10


def detect_language(text: str, default_lang: str = "en") -> list[str]:
    """Detect languages in *text* using ``langdetect`` (Google port).

    Returns languages with >10% probability as ISO 639-1 codes, e.g.
    ``["en", "zh-cn"]`` or ``["fr"]``.

    Returns ``[default_lang]`` if the text is too short, too sparse
    (spreadsheets), or looks like a markdown table.  Returns an empty
    list if the text is empty or detection fails completely.
    """
    if not text or len(text.strip()) < 10:
        return []

    # Spreadsheets / table-heavy content confuse langdetect — skip it.
    alpha_chars = sum(1 for ch in text if ch.isalpha())
    if alpha_chars < 20:
        return [default_lang]

    nlines = [l for l in text.splitlines() if l.strip()]
    if nlines:
        table_rows = sum(1 for l in nlines if l.strip().startswith("|"))
        if (table_rows / len(nlines)) > 0.5:
            return [default_lang]

    try:
        from langdetect import detect_langs
        profiles = detect_langs(text)
    except Exception:
        return []

    langs: list[str] = []
    for p in profiles:
        if p.prob >= _LANG_PROB_THRESHOLD:
            if p.lang not in langs:
                langs.append(p.lang)

    return langs


# ═════════════════════════════════════════════════════════════════════════
# Entity resolution — company name → ticker
# ═════════════════════════════════════════════════════════════════════════

def detect_entities(
    company: str | None,
    entity_map: dict[str, list[str]],
) -> list[str]:
    """Resolve ticker(s) from the company name.

    Looks up the lowercased company name in *entity_map* (populated from
    ``entities.yaml``).  Returns an empty list if *company* is ``None``
    or no mapping is found.
    """
    if not company:
        return []
    return entity_map.get(company.lower(), [])


# ═════════════════════════════════════════════════════════════════════════
# Chunk-level metadata — fiscal periods, datatype, statement type
# ═════════════════════════════════════════════════════════════════════════

_FISCAL_PATTERNS = [
    r"\bfiscal\s+year\s+20\d{2}\b",
    r"\bFY\s?\d{2}\b",
    r"\b\d{1}[HQ]\d{2}\b",
    r"\bQ[1-4]\s?CY\s?20\d{2}\b",
    r"\bQ[1-4]\s?20\d{2}\b",
    r"\b\d{1}Q\s?20\d{2}\b",
    r"\b\d{1}H\s?20\d{2}\b",
    r"\bFY\s?20\d{2}\b",
]
_FISCAL_RE = re.compile("|".join(f"({p})" for p in _FISCAL_PATTERNS), re.IGNORECASE)

_MD_TABLE_RE = re.compile(r"^\|.+\|$", re.MULTILINE)

_STATEMENT_PATTERNS: list[tuple[str, str]] = [
    # Balance sheet
    (r"(?i)\b(balance\s+sheet|statement\s+of\s+financial\s+position|total\s+assets.*total\s+liabilities)\b", "balance_sheet"),
    # P&L / Income statement
    (r"(?i)\b(income\s+statement|P\s?&?\s?L|profit\s+and\s+loss|statement\s+of\s+operations|statement\s+of\s+earnings|statement\s+of\s+comprehensive\s+income|financial\s+overview|operating\s+results|consolidated\s+statements?\s+of\s+operations)\b", "pnl"),
    (r"(?i)\b(net\s+revenue|gross\s+margin|operating\s+(income|margin|profit)|net\s+(income|loss|earnings)|diluted\s+EPS|GAAP\s+(net\s+income|results|operating))\b", "pnl"),
    # Cash flow
    (r"(?i)\b(cash\s+flow|statement\s+of\s+cash\s+flows|free\s+cash\s+flow|operating\s+cash|investing\s+cash|financing\s+cash)\b", "cash_flow"),
    # Equity
    (r"(?i)\b(statement\s+of\s+(shareholders.?|stockholders.?|changes\s+in)\s+equity|comprehensive\s+income)\b", "equity"),
    # Segment reporting
    (r"(?i)\b(segment\s+(reporting|information|revenue|results|data)|revenue\s+by\s+(segment|product|geography|region))\b", "segment"),
]


def detect_fiscal_periods(text: str) -> list[str]:
    """Extract fiscal period references from chunk text via regex.

    Returns unique, normalised matches, e.g. ``["FY26", "1H26"]``.
    Whitespace is collapsed so ``"FY 26"`` → ``"FY26"``.
    """
    if not text:
        return []
    seen: set[str] = set()
    for m in _FISCAL_RE.finditer(text):
        raw = m.group(0).strip()
        norm = re.sub(r"\s+", "", raw).upper()
        if norm.startswith("FISCALYEAR"):
            norm = "FY" + norm[len("FISCALYEAR"):]
        seen.add(norm)
    return sorted(seen)


def detect_datatype(text: str) -> str:
    """Classify chunk content as ``text``, ``tabular``, or ``img``.

    - ``tabular`` if >30% of non-empty lines are markdown table rows.
    - ``img`` if the chunk references an image placeholder.
    - ``text`` otherwise.
    """
    if not text.strip():
        return "text"
    nlines = [l for l in text.splitlines() if l.strip()]
    if nlines:
        table_rows = sum(1 for l in nlines if _MD_TABLE_RE.match(l.strip()))
        if (table_rows / len(nlines)) > 0.3:
            return "tabular"
    if "==> picture" in text or "![" in text or "](" in text:
        return "img"
    return "text"


def detect_statement_type(text: str) -> str:
    """Identify financial-statement type from chunk content.

    Returns one of: ``balance_sheet``, ``pnl``, ``cash_flow``, ``equity``,
    ``segment``, or ``misc``.
    """
    if not text.strip():
        return "misc"
    for pattern, stype in _STATEMENT_PATTERNS:
        if re.search(pattern, text):
            return stype
    return "misc"


def enrich_chunk_metadata(chunk_text: str) -> dict[str, str]:
    """Return per-chunk metadata fields as a dict.

    Called after chunking to annotate each chunk with fiscal periods,
    datatype, and statement type.
    """
    return {
        "period_referenced": ", ".join(detect_fiscal_periods(chunk_text)),
        "datatype": detect_datatype(chunk_text),
        "statement_type": detect_statement_type(chunk_text),
    }


# ═════════════════════════════════════════════════════════════════════════
# Doctype inference — folder structure + filename patterns + ext fallback
# ═════════════════════════════════════════════════════════════════════════

def detect_doctype(
    rel_path: str,
    file_ext: str,
    keywords_config: dict | None = None,
) -> list[str]:
    """Infer document type from folder structure and filename patterns.

    Priority (first match wins):
        1. Folder-to-doctype mapping from config
        2. Filename pattern matching from config
        3. Extension-based fallback (``.xlsx`` → model, ``.msg`` → email)

    Returns a single-element list, e.g. ``["filing"]``, or ``["unknown"]``.
    """
    path_parts = Path(rel_path).parts

    if keywords_config:
        # Step 1: Folder-based mapping
        folder_map = keywords_config.get("doctype_folder_mapping", {})
        for part in path_parts:
            if part in folder_map:
                return [folder_map[part]]

        # Step 2: Filename pattern matching
        fname = Path(rel_path).name
        filename_patterns = keywords_config.get("doctype_filename_patterns", [])
        for entry in filename_patterns:
            pattern = entry.get("pattern", "")
            if pattern and re.search(pattern, fname):
                return [entry.get("doctype", "unknown")]

    # Step 3: Extension fallback
    ext = file_ext.lower().lstrip(".")
    if ext in ("xlsx", "xlsm", "xls", "csv"):
        return ["model"]
    if ext == "msg":
        return ["email"]

    return ["unknown"]


# ═════════════════════════════════════════════════════════════════════════
# File metadata
# ═════════════════════════════════════════════════════════════════════════

def get_file_mtime(input_dir: Path, rel_path: str) -> str:
    """Get the original file's last-modified timestamp as ``YYYY-MM-DD HH:MM:SS``.

    Returns ``""`` if the file does not exist or is inaccessible.
    """
    abs_path = input_dir / rel_path
    try:
        mtime = abs_path.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


# ═════════════════════════════════════════════════════════════════════════
# YAML formatting helpers
# ═════════════════════════════════════════════════════════════════════════

_YAML_SPECIAL_RE = re.compile(r'[:#\{\}\[\],&*!|>%@`]|\s:\s|^"')


def escape_yaml_value(value: str) -> str:
    """Escape a string for safe inclusion in a YAML value.

    Returns the value quoted (with internal escapes) when it contains
    YAML-special characters; returns bare otherwise.
    """
    if not isinstance(value, str):
        value = str(value)
    if not value.strip():
        return '""'
    if _YAML_SPECIAL_RE.search(value):
        value = value.replace("\\", "\\\\")
        value = value.replace('"', '\\"')
        return f'"{value}"'
    return value


def yaml_list(values: list[str]) -> str:
    """Format a list of strings as comma-separated values: ``en, zh-cn``.

    Returns ``""`` for an empty list.
    """
    if not values:
        return ""
    return ", ".join(values)
