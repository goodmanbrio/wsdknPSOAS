"""
pto.py — Poony Tabular Oneshot: per-batch tabular retrieval lane.

Takes a PTOBatchRequest (flattened from Sekei batch) and retrieves
the top table chunks most likely to contain the requested line items.
BM25-only over hard-filtered (entity + fiscal year + table) chunk set,
with HyDE good/bad signals for soft ranking/demotion.

Usage:
    from src.pto import pto_retrieve, PTOBatchRequest
    request = PTOBatchRequest(
        batch_id="A_12",
        firm="Best Buy",
        period="FY2022",
        statement="income_statement",
        metrics=["Revenue", "Operating Income"],
        retrieve_target="Consolidated Statements of Earnings",
    )
    result = pto_retrieve(request, index, config)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore

from src.config import Config, FIRM_SYNONYMS
from src.harness.terminal_router import register

_pto_out = register("PMS1")

# ── Load reference data at import time ─────────────────────────────────
_HARDCODE_DIR = Path(__file__).resolve().parent.parent / "hardcode_dependencies"

with open(_HARDCODE_DIR / "retrievable_line_items.yaml") as _f:
    _LINE_ITEMS_REF: dict = yaml.safe_load(_f)

with open(_HARDCODE_DIR / "denomination_units.json") as _f:
    _denom_units = json.load(_f)
    _DENOMINATIONS: list[str] = _denom_units["denominations"]
    _UNITS: list[str] = _denom_units["units"]


# ═════════════════════════════════════════════════════════════════
# Input / Output contracts
# ═════════════════════════════════════════════════════════════════

@dataclass
class PTOBatchRequest:
    """Input to PTO — one per Sekei batch."""
    batch_id: str                          # "A_12"
    firm: str                              # "Best Buy"
    period: str                            # "FY2022"
    statement: str                         # BM25 search hint, not metadata filter
    metrics: list[str]                     # ["Revenue", "Operating Income"]
    retrieve_target: str                   # "Consolidated Statements of Earnings"
    hyde_good_override: str | None = None  # judge retry: replacement good signal
    hyde_bad_override: str | None = None   # judge retry: replacement bad signal


@dataclass
class PTORunnerUpMeta:
    """Lightweight chunk metadata for judge review — no full text."""
    node_id: str
    file_name: str
    section: str
    fiscal_year: str
    chunk_type: str
    score: float


@dataclass
class PTOMethodLog:
    """What PTO tried — structured so judge can propose modifications."""
    hyde_good: str                   # final BM25 good query (after strip + dedup)
    hyde_bad: str                    # final BM25 bad query (after strip + dedup)
    metadata_filters: dict[str, str]


@dataclass
class PTOBatchResult:
    """Output from PTO — top chunks + runner-ups + methodology."""
    batch_id: str
    top_chunks: list[NodeWithScore]        # top 5, full text
    runner_ups: list[PTORunnerUpMeta]       # next 15, metadata only
    method_log: PTOMethodLog
    good_scores: dict[str, float] = field(default_factory=dict)  # node_id → BM25 good score
    bad_scores: dict[str, float] = field(default_factory=dict)   # node_id → BM25 bad score


# ═════════════════════════════════════════════════════════════════
# Synonym resolution
# ═════════════════════════════════════════════════════════════════

def resolve_synonyms(
    firm: str,
    firm_synonyms: dict[str, list[str]] | None = None,
) -> tuple[str, list[str]]:
    """Reverse lookup: given any firm string, find its synonym group.

    Searches FIRM_SYNONYMS for any group whose value list contains `firm`.
    Case-insensitive, stripped.

    Returns (group_key, synonym_list).
        - group_key: readable firm name (e.g. "Best Buy"), matches
          dir_implied_firm on chunks. Used for cheap exact-match filtering.
        - synonym_list: all equivalent strings for regex matching on
          text/section/filename.

    If no match found, returns (firm, [firm]).
    """
    if firm_synonyms is None:
        firm_synonyms = FIRM_SYNONYMS

    firm_norm = firm.strip().lower()

    for group_key, synonyms in firm_synonyms.items():
        if any(s.strip().lower() == firm_norm for s in synonyms):
            return group_key, [s.strip() for s in synonyms]

    # No match — return input as its own group
    return firm.strip(), [firm.strip()]


# ═════════════════════════════════════════════════════════════════
# Hard filtering
# ═════════════════════════════════════════════════════════════════

def _entity_matches(
    node,
    group_key: str,
    patterns: list,
) -> bool:
    """Check if a chunk belongs to the target firm.

    Cascade (first hit wins, cheapest first):
        1. dir_implied_firm metadata == group_key  (exact, cheapest)
        2. synonym regex on file_name metadata
        3. synonym regex on section metadata
        4. synonym regex on chunk text body         (most expensive)

    dir_implied_firm is set at ingest from the parent directory name,
    reverse-looked-up against FIRM_SYNONYMS. Standard financial statement
    tables (income statement, balance sheet) often have NO company name
    in their text — dir_implied_firm is the only reliable signal for those.
    """
    meta = node.metadata

    # 1. dir_implied_firm — exact match on group key
    if meta.get("dir_implied_firm", "") == group_key:
        return True

    # 2. file_name — e.g. "BESTBUY" in "BESTBUY_2023_10K.md"
    fname = meta.get("file_name", "")
    if fname and any(p.search(fname) for p in patterns):
        return True

    # 3. section — e.g. "Amcor plc" in section label
    section = meta.get("section", "")
    if section and any(p.search(section) for p in patterns):
        return True

    # 4. chunk text body — most expensive, last resort
    if any(p.search(node.text) for p in patterns):
        return True

    return False


def _hard_filter_chunks(
    index: VectorStoreIndex,
    group_key: str,
    synonyms: list[str],
    period: str,
) -> tuple[list[NodeWithScore], bool]:
    """Filter index to table chunks matching entity + fiscal year.

    Entity match: dir_implied_firm OR synonym in filename/section/text.
    Fiscal year match: chunk metadata fiscal_year == period.
    Only chunk_type="table" passes.

    Cascade on empty:
        1. table + fiscal_year + entity  (tight)
        2. table + entity                (relaxed — data may live in adjacent year's filing)
        3. empty                         (entity not in corpus)

    Returns (filtered_chunks, fiscal_year_relaxed).
    """
    import re

    patterns = [
        re.compile(r"\b" + re.escape(syn) + r"\b", re.IGNORECASE)
        for syn in synonyms
    ]

    _BLACKLIST_SECTIONS = {"table of contents", "index"}

    # Pass 1: all entity table chunks (superset, filter FY after)
    entity_tables: list[NodeWithScore] = []
    for _node_id, node in index.docstore.docs.items():
        meta = node.metadata
        if meta.get("chunk_type") != "table":
            continue
        if meta.get("section", "").strip().lower() in _BLACKLIST_SECTIONS:
            continue
        if not _entity_matches(node, group_key, patterns):
            continue
        entity_tables.append(NodeWithScore(node=node, score=0.0))

    # Pass 2: narrow to fiscal year
    tight = [
        n for n in entity_tables
        if n.node.metadata.get("fiscal_year", "") == period
    ]

    if tight:
        return tight, False

    # Cascade: FY filter too strict — return all entity tables
    if entity_tables:
        return entity_tables, True

    return [], False


# ═════════════════════════════════════════════════════════════════
# HyDE generation
# ═════════════════════════════════════════════════════════════════



def _deterministic_hyde(
    statement: str,
    metrics: list[str],
    ref: dict,
) -> tuple[str, str] | None:
    """Try to build good/bad signals from yaml reference data.

    Returns (hyde_good, hyde_bad) if statement is a known yaml key.
    Returns None if statement not in yaml → caller falls back to LLM.

    Good signal: title aliases of the target statement + aliases of
                 each requested metric found in that statement's line items.
                 Unmatched metrics are included raw as fallback — they're
                 already in the BM25 query from pto_retrieve step 4, but
                 repeating here strengthens the signal.
    Bad signal:  title aliases of all OTHER statements (table titles only,
                 never line items — line item terms like "total assets" appear
                 as columns/footnotes in unrelated tables, causing false positives).

    Design rationale: bad signal is pure function of statement, not metrics.
    "Consolidated Balance Sheets" is always wrong for income_statement
    regardless of what metrics are requested. So even if some metrics
    don't match yaml (e.g. "Adjusted EBITDA"), we still return deterministic
    good/bad rather than deferring to LLM — losing the perfectly known bad
    signal just because one metric is novel is a worse trade than including
    the unmatched metric raw.
    Full LLM defer only when the statement ITSELF is unknown.
    """
    statements = ref.get("statements", {})
    if statement not in statements:
        return None

    target_stmt = statements[statement]

    # Good: target statement title aliases
    good_parts: list[str] = list(target_stmt.get("aliases", []))

    # Good: aliases for each requested metric within this statement.
    # Track which metrics matched so unmatched ones can be included raw.
    matched_metrics: set[str] = set()

    for item in target_stmt.get("line_items", []):
        item_name = item["name"].strip().lower()
        item_aliases = [a.strip().lower() for a in item.get("aliases", [])]
        all_names = [item_name] + item_aliases

        for metric in metrics:
            if metric.strip().lower() in all_names:
                good_parts.append(item["name"])
                good_parts.extend(item.get("aliases", []))
                matched_metrics.add(metric)
                break

    # Include unmatched metrics raw — no aliases available but the
    # exact term still helps BM25 (e.g. "Adjusted EBITDA")
    for metric in metrics:
        if metric not in matched_metrics:
            good_parts.append(metric)

    # Bad: title aliases of every OTHER statement (titles only, not line items)
    bad_parts: list[str] = []
    for other_key, other_stmt in statements.items():
        if other_key == statement:
            continue
        bad_parts.extend(other_stmt.get("aliases", []))

    hyde_good = " ".join(good_parts)
    hyde_bad = " ".join(bad_parts)
    return hyde_good, hyde_bad


def _all_statement_titles(ref: dict, exclude: str | None = None) -> str:
    """Collect title aliases from all (or all-except-one) yaml statements.

    Used for bad signal:
        - Known statement:   exclude that statement → other titles are bad
        - Unknown statement: exclude nothing → ALL standard statement titles
          are bad (segment/custom data is never inside a standard statement,
          and standard P&L/BS/CF "Revenue" would drown niche metrics in BM25)
    """
    parts: list[str] = []
    for key, stmt in ref.get("statements", {}).items():
        if key == exclude:
            continue
        parts.extend(stmt.get("aliases", []))
    return " ".join(parts)


# ── LLM fallback prompt for non-standard tables ─────────────────

_HYDE_GOOD_SYSTEM = """\
You generate BM25 search keywords for financial table retrieval.

## Why this matters
These keywords are fed to BM25 (keyword matching). BM25 scores documents \
by exact word overlap — it has no semantic understanding. "company's \
top-line performance" scores ZERO against a table containing "Revenue". \
Only output words/phrases that literally appear in the target table or \
its surrounding context.

## What a table chunk looks like in our corpus
Tables are pipe-delimited markdown extracted from filings:

```
Revenue by Operating Segment

| | Fiscal 2023 | Fiscal 2022 | Change |
|---|---|---|---|
| United States | $8,241 | $7,890 | 4.4% |
| International | $3,102 | $2,944 | 5.4% |
| Total | $11,343 | $10,834 | 4.7% |
```

Good keywords for this table: "Revenue by Operating Segment" \
"United States" "International" "Fiscal 2023" "Fiscal 2022"

## Your task
Given a company, table type, and metrics — generate keywords that would \
appear in or near the correct table. Think about:
1. Section header above the table (e.g. "Revenue by Operating Segment")
2. Column headers (e.g. "Fiscal 2022", "Year Ended December 31")
3. Row labels (e.g. "United States", "Domestic", "International")
4. Nearby prose anchors (e.g. "The following table summarizes")
5. Alternative names for the same data (e.g. "geographic" vs "by region")

## Rules
- Every keyword must be a plausible exact string in a real filing
- Do NOT output generic terms ("financial data", "company performance", \
"key metrics", "results", "summary")
- Do NOT output standard financial statement headers ("Consolidated \
Statements of Earnings", "Balance Sheet") — those are handled separately
- Prefer multi-word phrases over single words when the phrase is specific \
(e.g. "Revenue by Segment" not just "Revenue")

## Output format
JSON only, no explanation:
{"hyde_good": "phrase one phrase two keyword1 keyword2 ..."}
"""


def _generate_hyde(
    request: PTOBatchRequest,
    synonyms: list[str],
    config: Config,
) -> tuple[str, str]:
    """Generate good/bad BM25 keyword signals for this batch.

    Hybrid approach:
        1. If request.statement is a known yaml key (income_statement,
           balance_sheet, cash_flow_statement) → deterministic lookup.
           Zero LLM cost, zero hallucination risk, perfectly reproducible.
        2. Otherwise (segment data, Nielsen, custom tables) → LLM generates
           good signal only. Bad signal is ALWAYS deterministic: all standard
           statement title aliases, because niche metrics (e.g. "U.S. Revenue")
           get drowned by standard P&L "Revenue" in BM25 if we don't penalize.

    Bad signal is always deterministic from yaml:
        - Known statement:  other statement titles
        - Unknown statement: ALL statement titles
    Only good signal needs LLM for unknown statements.

    Returns (hyde_good, hyde_bad). Uses overrides if present (judge retry).
    """
    # Judge retry path: overrides skip all generation
    if request.hyde_good_override is not None and request.hyde_bad_override is not None:
        return request.hyde_good_override, request.hyde_bad_override

    ref = _LINE_ITEMS_REF

    # Try fully deterministic path (known statement)
    deterministic = _deterministic_hyde(request.statement, request.metrics, ref)
    if deterministic is not None:
        return deterministic

    # ── BAD SIGNAL IS ALWAYS DETERMINISTIC — NEVER LLM-GENERATED ──
    # Unknown statement → bad = ALL standard statement titles.
    # Why ALL, not just "other"? Because there is no "same" — the
    # statement isn't in yaml at all. And standard P&L/BS/CF tables
    # are the primary noise source: BM25 query "Revenue" matches
    # "Consolidated Statements of Earnings | Revenue | $46,298"
    # and drowns the actual segment table we want.
    # If debugging PTO and a good chunk got penalized, check here —
    # this is the line that treats every standard statement as bad.
    hyde_bad = _all_statement_titles(ref)

    # Good signal: LLM generates — only case where LLM is called
    hyde_good = _llm_hyde_good(request, synonyms, config)
    return hyde_good, hyde_bad


def _llm_hyde_good(
    request: PTOBatchRequest,
    synonyms: list[str],
    config: Config,
) -> str:
    """LLM call to generate good BM25 keywords for non-standard tables.

    Uses deepseek-chat temp=0 for reproducibility.
    """
    import json
    from src.llm import get_pto_hyde_llm

    user_prompt = (
        f"Company: {request.firm} (also known as: {', '.join(synonyms)})\n"
        f"Table type: {request.statement}\n"
        f"Metrics to find: {', '.join(request.metrics)}\n"
        f"Filing section hint: {request.retrieve_target}\n"
    )

    llm = get_pto_hyde_llm(config)
    raw = llm.complete(user_prompt, system_prompt=_HYDE_GOOD_SYSTEM)

    # Parse JSON response
    text = raw.strip()
    if text.startswith("```"):
        import re
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed.get("hyde_good", "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        # Parse failure — return raw text as fallback, BM25 is forgiving
        return text


# ═════════════════════════════════════════════════════════════════
# BM25 ranking
# ═════════════════════════════════════════════════════════════════

def _bm25_rank(
    chunks: list[NodeWithScore],
    query_str: str,
) -> dict[str, float]:
    """BM25-score pre-filtered chunks against a query string.

    Returns scores as a dict (node_id → score), NOT mutated on nodes.
    Two BM25 passes are needed (good query + bad query) and they must
    not overwrite each other's scores — if both wrote to node.score,
    the second pass would destroy the first. Returning dicts keeps
    good_scores and bad_scores independent for destructive fusion.

    Uses rank_bm25 directly (transitive dep of llama-index-retrievers-bm25).
    Tokenization: simple lowercase split. Financial table text is already
    pipe-delimited with clear keywords — no stemming needed.
    """
    # dep: rank_bm25 (via llama-index-retrievers-bm25 in requirements.txt)
    from rank_bm25 import BM25Okapi

    if not chunks or not query_str.strip():
        return {}

    corpus = [n.node.text.lower().split() for n in chunks]
    query_tokens = query_str.lower().split()

    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)

    return {
        node.node.node_id: float(score)
        for node, score in zip(chunks, scores)
    }


# ═════════════════════════════════════════════════════════════════
# Destructive fusion
# ═════════════════════════════════════════════════════════════════

def _destructive_fusion(
    chunks: list[NodeWithScore],
    good_scores: dict[str, float],
    bad_scores: dict[str, float],
    alpha: float = 7.0,
    k: int = 10,
) -> list[NodeWithScore]:
    """Fuse good/bad BM25 scores: demote chunks that rank high on bad query.

    Takes two independent score dicts (from separate _bm25_rank calls)
    and the original chunk list. Computes final score as:
        final = good_score * penalty
        penalty = 1 - (alpha / (bad_rank + k))

    Chunks not in bad_scores are untouched (penalty = 1.0).
    Final scores are written to node.score once, at the end.

    alpha=7.0 is very aggressive — top bad chunk at 0.3x (70% demotion).
    k=10 controls how fast penalty fades with bad rank.
    """
    # Sort by bad score descending to get bad rank (0 = worst offender)
    bad_ranked = sorted(bad_scores.items(), key=lambda x: x[1], reverse=True)
    bad_rank_map: dict[str, int] = {
        node_id: rank for rank, (node_id, _score) in enumerate(bad_ranked)
    }

    for node in chunks:
        nid = node.node.node_id
        good = good_scores.get(nid, 0.0)

        bad_rank = bad_rank_map.get(nid)
        if bad_rank is not None and bad_scores:
            penalty = 1.0 - (alpha / (bad_rank + k))
            node.score = good * max(penalty, 0.0)
        else:
            node.score = good

    chunks.sort(key=lambda n: n.score, reverse=True)
    return chunks


# ═════════════════════════════════════════════════════════════════
# Main entry
# ═════════════════════════════════════════════════════════════════

_TOP_K = 5
_RUNNER_UP_K = 15


def pto_retrieve(
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    config: Config,
) -> PTOBatchResult:
    """Run PTO tabular retrieval for one Sekei batch.

    Steps:
        1. Resolve firm synonyms
        2. Hard filter chunks (entity + fiscal_year + table)
        3. Generate HyDE good/bad (or use overrides)
        4. BM25 rank with good query → base_list
        5. BM25 rank with bad query → bad_list
        6. Destructive fusion
        7. Split top_k + runner_ups
        8. Build method_log
        9. Return PTOBatchResult
    """
    # 1. Resolve firm synonyms
    group_key, synonyms = resolve_synonyms(request.firm)

    # 2. Hard filter
    filtered, fy_relaxed = _hard_filter_chunks(index, group_key, synonyms, request.period)

    if not filtered:
        return PTOBatchResult(
            batch_id=request.batch_id,
            top_chunks=[],
            runner_ups=[],
            method_log=PTOMethodLog(
                hyde_good="",
                hyde_bad="",
                metadata_filters={
                    "entity": request.firm,
                    "fiscal_year": request.period,
                    "chunk_type": "table",
                    "fy_relaxed": str(fy_relaxed),
                },
            ),
        )

    # 3. HyDE good/bad
    hyde_good, hyde_bad = _generate_hyde(request, synonyms, config)

    # 4. Good query → BM25 rank
    query_str_good = " ".join([
        request.retrieve_target,
        " ".join(request.metrics),
        hyde_good,
    ]).strip()

    # ── Strip shared tokens from BOTH good and bad queries ──
    # Financial statement titles + line items share generic tokens
    # ("consolidated", "statements", "of", "net", "total", "income")
    # that inflate BM25 scores for wrong-statement chunks. E.g. cash
    # flow indirect method text contains "net earnings" and "total
    # cash provided by operating activities" — matching income
    # statement good query tokens "net", "earnings", "total".
    # Stripping shared tokens from both queries ensures BM25 only
    # scores on discriminative terms.
    # Tradeoff: stripping "net" kills "net sales" match — but "sales"
    # alone is still discriminative and survives. Acceptable loss vs
    # the alternative of cash flow chunks dominating income statement
    # retrieval.
    good_tokens_raw = query_str_good.lower().split()
    bad_tokens_raw = hyde_bad.lower().split() if hyde_bad.strip() else []
    shared = set(good_tokens_raw) & set(bad_tokens_raw)

    # Strip shared tokens, then dedup — prevents alias duplication
    # from inflating BM25 term frequency (e.g. "cash" 8x from 6 CFS
    # aliases all containing "cash"). Dedup makes BM25 reward breadth
    # of keyword coverage, not repetition of one term.
    good_stripped = [t for t in good_tokens_raw if t not in shared]
    good_deduped = list(dict.fromkeys(good_stripped))  # preserves order
    hyde_good = " ".join(good_deduped)
    if not hyde_good.strip():
        hyde_good = query_str_good  # fallback if stripping empties it

    good_scores = _bm25_rank(filtered, hyde_good)

    # 5. Bad query → BM25 rank (only if we have bad signal)
    bad_scores: dict[str, float] = {}
    if bad_tokens_raw:
        bad_stripped = [t for t in bad_tokens_raw if t not in shared]
        bad_deduped = list(dict.fromkeys(bad_stripped))
        hyde_bad = " ".join(bad_deduped)
        if hyde_bad.strip():
            bad_scores = _bm25_rank(filtered, hyde_bad)

    # 6. Destructive fusion — applies penalty from bad scores onto good scores,
    # writes final scores to node.score on filtered list
    fused = _destructive_fusion(filtered, good_scores, bad_scores)

    # 7. Split top_k + runner_ups
    top_chunks = fused[:_TOP_K]
    runner_up_nodes = fused[_TOP_K:_TOP_K + _RUNNER_UP_K]

    runner_ups = [
        PTORunnerUpMeta(
            node_id=n.node.node_id,
            file_name=n.node.metadata.get("file_name", ""),
            section=n.node.metadata.get("section", ""),
            fiscal_year=n.node.metadata.get("fiscal_year", ""),
            chunk_type=n.node.metadata.get("chunk_type", ""),
            score=n.score,
        )
        for n in runner_up_nodes
    ]

    # 8. Build method_log
    method_log = PTOMethodLog(
        hyde_good=hyde_good,
        hyde_bad=hyde_bad,
        metadata_filters={
            "entity": request.firm,
            "fiscal_year": request.period,
            "chunk_type": "table",
            "fy_relaxed": str(fy_relaxed),
        },
    )

    # 9. Return
    return PTOBatchResult(
        batch_id=request.batch_id,
        top_chunks=top_chunks,
        runner_ups=runner_ups,
        method_log=method_log,
        good_scores=good_scores,
        bad_scores=bad_scores,
    )


# ═════════════════════════════════════════════════════════════════
# PTO cell result (decoupled from stencil.CellResult)
# ═════════════════════════════════════════════════════════════════

@dataclass
class PTOCellResult:
    """A filled cell from PTO lane — value + provenance."""
    value: float
    source: str           # node_id — downstream does index.docstore.docs[source].text to get chunk
    denomination: str     # "k","10k","100k","mn","10mn","100mn","bn","10bn","100bn","unit"
    unit: str             # "USD","RMB","JPY","GBP","EUR","%","count","none"


# ═════════════════════════════════════════════════════════════════
# Judge
# ═════════════════════════════════════════════════════════════════

def _build_judge_system() -> str:
    """Build judge system prompt with denomination/unit enums from JSON."""
    denom_str = " | ".join(_DENOMINATIONS)
    unit_str = " | ".join(_UNITS)

    return f"""\
You are a financial data extraction judge. Given retrieved table chunks \
from a filing, extract exact numeric values for each requested metric.

## Input
You receive:
- Firm, period, statement type, and a list of metrics to find
- Retrieved chunks (numbered 0-N) with full text and metadata
- Retrieval methodology used (for context, not action)
- Runner-up chunks (metadata only, no text)

## Task
For each metric in the metrics list:
1. Search all chunks for the exact numeric value
2. If found: report the value, which chunk, denomination, and unit
3. If not found in any chunk: mark insufficient

## Denomination and unit extraction
Financial tables specify denomination in headers or footnotes \
(e.g. "In millions, except per share data", "amounts in thousands").

denomination — the scale multiplier on the raw number. MUST be exactly one of:
  {denom_str}

  "unit" = number as-is, no multiplier (e.g. EPS, ratios, percentages)

unit — what the number measures. MUST be exactly one of:
  {unit_str}

  "%" = percentage value
  "count" = countable things (shares, stores, employees)
  "none" = truly dimensionless (ratios, multiples)

## Output format
JSON only, no explanation:
{{
  "Revenue": {{"sufficient": true, "answer": 51761, "denomination": "mn", "unit": "USD", "source_chunk": 0}},
  "Diluted EPS": {{"sufficient": true, "answer": 6.84, "denomination": "unit", "unit": "USD", "source_chunk": 0}},
  "Gross Margin": {{"sufficient": true, "answer": 22.5, "denomination": "unit", "unit": "%", "source_chunk": 2}},
  "Operating Income": {{"sufficient": false}}
}}

## Rules
- answer must be a raw number — no commas, no currency symbols, no parentheses
- For negative values (e.g. net loss): (2,454) → -2454
- If a metric appears in multiple chunks, prefer the chunk where it sits \
in the target statement (not notes/supplemental)
- source_chunk is the 0-indexed chunk number
- Do not fabricate values — if the number is not literally in a chunk, \
mark insufficient
- Read table headers carefully for denomination — a table header \
"In millions" means the number 51,761 represents 51,761 million
- Use the EXACT metric names from the input as your JSON keys. Do not \
rename, abbreviate, or paraphrase them. If input says "Revenue", \
output key must be "Revenue", not "Net Revenue" or "Total Revenue".
"""


def _build_judge_user_prompt(
    request: PTOBatchRequest,
    result: PTOBatchResult,
) -> str:
    """Assemble the user prompt for judge LLM call."""
    parts: list[str] = []

    # Batch context
    parts.append(f"Firm: {request.firm}")
    parts.append(f"Period: {request.period}")
    parts.append(f"Statement: {request.statement}")
    parts.append(f"Metrics to extract: {', '.join(request.metrics)}")
    parts.append(f"Retrieve target: {request.retrieve_target}")
    parts.append("")

    # Method log
    ml = result.method_log
    parts.append("--- RETRIEVAL METHODOLOGY ---")
    parts.append(f"HyDE good query: {ml.hyde_good}")
    parts.append(f"HyDE bad query: {ml.hyde_bad}")
    parts.append(f"Metadata filters: {ml.metadata_filters}")
    parts.append("--- END METHODOLOGY ---")
    parts.append("")

    # Top chunks (full text + metadata)
    parts.append(f"--- RETRIEVED CHUNKS ({len(result.top_chunks)}) ---")
    for i, node in enumerate(result.top_chunks):
        meta = node.node.metadata
        parts.append(f"CHUNK {i}  score={node.score:.4f}")
        parts.append(f"  file={meta.get('file_name', '?')}  "
                      f"section={meta.get('section', '?')}  "
                      f"fy={meta.get('fiscal_year', '?')}  "
                      f"type={meta.get('chunk_type', '?')}")
        parts.append(node.node.text)
        parts.append("")
    parts.append("--- END CHUNKS ---")
    parts.append("")

    # Runner-ups (metadata only)
    parts.append(f"--- RUNNER-UPS ({len(result.runner_ups)}) ---")
    for i, ru in enumerate(result.runner_ups):
        parts.append(f"  #{i}  score={ru.score:.4f}  "
                      f"file={ru.file_name}  section={ru.section}  "
                      f"fy={ru.fiscal_year}")
    parts.append("--- END RUNNER-UPS ---")

    return "\n".join(parts)


def pto_judge(
    request: PTOBatchRequest,
    result: PTOBatchResult,
    config: Config,
) -> dict:
    """Judge LLM call: extract values from retrieved chunks.

    One call per batch. Returns parsed JSON dict keyed by metric name.
    """
    import json
    import re
    from src.llm import get_pto_judge_llm

    user_prompt = _build_judge_user_prompt(request, result)
    llm = get_pto_judge_llm(config)
    raw, usage = llm.complete_with_usage(user_prompt, system_prompt=_build_judge_system())

    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        _pto_out.print(f"[pto_judge] batch={request.batch_id} tokens in={inp} out={out}")

    # Parse JSON — strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


# ═════════════════════════════════════════════════════════════════
# Pre-generation parser
# ═════════════════════════════════════════════════════════════════

def pto_judge_to_stencil(
    judge_output: dict,
    cell_map: dict[str, str],
    result: PTOBatchResult,
) -> dict[str, PTOCellResult]:
    """Map judge output to cell-keyed results for stencil.

    Args:
        judge_output: Judge JSON keyed by metric name, e.g.
            {"Revenue": {"sufficient": true, "answer": 51761, "denomination": "mn", "unit": "USD", "source_chunk": 0}}
        cell_map: Flat mapping from orchestrator, e.g.
            {"A1": "Revenue", "A2": "Gross Profit", "A4": "Net Income"}
        result: PTOBatchResult — top_chunks used for provenance lookup.

    Returns:
        dict mapping cell IDs to PTOCellResult.

    Raises:
        ValueError: if any metric is missing or insufficient.
            Retry loop not implemented — happy path only.
    """
    values: dict[str, PTOCellResult] = {}

    # Case-insensitive lookup: judge LLM may capitalize differently
    # than Sekei (e.g. "Gross Profit" vs "Gross profit"). Build a
    # lowercased index so one capital letter doesn't crash the pipeline.
    judge_lower = {k.lower(): v for k, v in judge_output.items()}

    for cell_id, metric in cell_map.items():
        entry = judge_lower.get(metric.lower())

        if entry is None:
            raise ValueError(
                f"Judge output missing metric '{metric}' (cell {cell_id})"
            )

        if not entry.get("sufficient"):
            raise ValueError(
                f"Insufficient: metric '{metric}' (cell {cell_id}). "
                f"Retry not implemented."
            )

        chunk_idx = entry["source_chunk"]
        if chunk_idx < 0 or chunk_idx >= len(result.top_chunks):
            raise ValueError(
                f"Invalid source_chunk={chunk_idx} for metric '{metric}' "
                f"(cell {cell_id}), top_chunks has {len(result.top_chunks)} entries"
            )

        chunk = result.top_chunks[chunk_idx]
        node_id = chunk.node.node_id

        values[cell_id] = PTOCellResult(
            value=float(entry["answer"]),
            source=node_id,
            denomination=entry.get("denomination", ""),
            unit=entry.get("unit", ""),
        )

    return values
