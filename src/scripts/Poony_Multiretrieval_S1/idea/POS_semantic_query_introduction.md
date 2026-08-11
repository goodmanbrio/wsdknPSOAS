# POS — Poony Oneshot Semantic: qualitative retrieval lane spec

## What POS is

The qualitative retrieval lane. Mirrors PTO (tabular) but retrieves prose
chunks instead of financial statement tables. Handles "management outlook on
margins," "competitive landscape," "strategy discussion" — anything that
isn't a number in a table.

## How it fits in the pipeline

```
User query: "Best Buy revenue FY2022-2023 and management's outlook on margins"
    |
    v
  SEKEI (one LLM call — handles both tabular and qualitative)
    |
    |   Outputs SekeiPlan with:
    |     cells/batches       → tabular (Revenue FY2022, FY2023)
    |     pos_batches         → qualitative (margin outlook)
    |
    v
  ORCHESTRATOR
    |
    |-- for batch in plan.batches:    → PTO lane → dict[str, CellResult]
    |-- for pb in plan.pos_batches:   → POS lane → dict[str, POSCellResult]
    |
    v
  MATOME receives both → narrates filled stencil + qualitative answers
```

One Sekei instance handles both tabular and qualitative. Rationale: one
Sekei sees the full query and can reason about which parts are tabular vs
qualitative. Two Sekei instances = double the LLM cost, double the output
token budget, and a handover string re-describing what the first one already
understood. Better to give one instance more tokens than to duplicate
efforts.

## Data structures

### SekeiPlan (new field)

```python
@dataclass
class SekeiPlan:
    # ... existing fields ...
    pos_batches: list[POSBatch]    # qualitative retrieval batches
```

### POSBatch

```python
@dataclass
class POSBatch:
    id: str              # "POS_1"
    cell_id: str         # "POS_1" (one batch = one cell, always)
    query: str           # "management views on margin trajectory"
    firm: str            # "Best Buy" — per-batch, not inherited from plan
    source_hint: str     # "MD&A", "earnings call transcript", "" (optional)
```

Notes:
- `firm` is on the batch, not inherited from plan.firm. Costs nothing now.
  When multi-firm plans are built, the field is already there.
- `source_hint` is optional guidance for retrieval — "look in MD&A" or
  "look in earnings call transcript." Empty = search everywhere.
- One batch = one cell. No multi-metric batching — that's a tabular concept.

### POSCellResult

```python
@dataclass
class POSCellResult:
    query: str           # original question (so Matome knows what POS_1 was asking)
    text: str            # judge's focused prose answer
    source: str          # node_id — downstream does index.docstore.docs[source].text
```

Notes:
- `query` is carried through so Matome can narrate without looking up the
  POSBatch. "Here's what POS_1 asked, here's the answer."
- `source` is a node_id, same contract as PTOCellResult — downstream can
  pull full chunk text for source-checking UI.

### POSJudgeOutput

```python
# Per POS batch, judge reads all retrieved chunks and produces:
{
    "sufficient": true,
    "answer": "Management expects margins to compress 50-75bps due to...",
    "source_chunks": [2, 4]
}
```

Notes:
- `source_chunks` is a LIST, not singular. Qualitative answers often span
  multiple chunks — margin commentary might be in paragraphs across several
  pages. PTO judge uses singular source_chunk because one table chunk
  contains one number. POS needs multiple because prose context is diffuse.
- `answer` is the judge's focused prose answer synthesized from chunks. Not
  raw chunk text — judge reads and produces a focused response to the query.

## POS pipeline (mirrors PTO, 3 stages)

### Stage 1 — pos_retrieve

Semantic/BM25 retrieval scoped by firm + source_hint.

- Hard filter on entity (same FIRM_SYNONYMS mechanism as PTO)
- NO table-only filter — retrieves text chunks (opposite of PTO which
  filters FOR tables)
- Returns top chunks + runner-ups + method_log
- Same output shape as PTOBatchResult but for prose

### Stage 2 — pos_judge

LLM reads retrieved chunks, produces focused prose answer to the query,
cites which chunks it drew from.

- Receives all top chunks (full text) + batch context (query, firm, source_hint)
- Outputs: sufficient (bool) + answer (str) + source_chunks (list[int])
- If no relevant content found: sufficient=false
- Judge synthesizes — doesn't just copy-paste chunks. Produces a focused
  answer to the specific query.

### Stage 3 — pos_judge_to_result

Deterministic parser. Resolves source_chunks[0] → node_id for the primary
source (same pattern as PTO — downstream can look up chunk text via
docstore). Packs into POSCellResult.

## Key differences from PTO

| Dimension | PTO (tabular) | POS (qualitative) |
|---|---|---|
| Input | metrics list, statement, period | free-form query, source_hint |
| Hard filter | entity + FY + table chunks only | entity + text chunks (no table filter) |
| HyDE | deterministic from YAML + bad signal | TBD — semantic query, no statement aliases |
| Judge output | number + denomination + unit | prose answer |
| Source citation | source_chunk: int (singular) | source_chunks: list[int] (multiple) |
| Result destination | stencil (dict[str, CellResult]) | parallel dict[str, POSCellResult] |
| Cell ID namespace | A1, B2, C3 (letter=period, number=row) | POS_1, POS_2, POS_3 (sequential) |

## Orchestrator changes

```python
def run(plan, index, config) -> tuple[dict[str, CellResult], dict[str, POSCellResult]]:
    # ... existing tabular loop (PTO) ...
    
    pos_values: dict[str, POSCellResult] = {}
    for pb in plan.pos_batches:
        result = pos_retrieve(pb, index, config)
        judge_output = pos_judge(pb, result, config)
        pos_values[pb.cell_id] = pos_judge_to_result(judge_output, pb, result)
    
    filled_stencil = compute_stencil(plan, all_values)
    return (filled_stencil, pos_values)
```

Orchestrator routes by structure: `plan.batches` → PTO, `plan.pos_batches`
→ POS. The separation is structural (two lists in SekeiPlan), not a flag
on one list.

## Sekei prompt changes

Sekei needs to categorize each part of the query:

- Numbers from financial statements → tabular cells + batches
- Commentary/outlook/strategy/risks → pos_batches
- Revenue guidance (future number from prose, not a table) → qualitative,
  NOT tabular. POS retrieves the MD&A chunk containing the figure. The
  number is in the chunk text — no need to extract it into the stencil.

Output schema gains `pos_batches` alongside `batches`:

```json
{
  "firm": "Best Buy",
  "cells": { ... },
  "batches": [ ... ],
  "pos_batches": [
    {
      "id": "POS_1",
      "cell_id": "POS_1",
      "query": "management outlook on margin trajectory",
      "firm": "Best Buy",
      "source_hint": "MD&A"
    }
  ]
}
```

## Not specified yet (deferred)

- POS retrieval implementation (what embedding/BM25 strategy for prose —
  HyDE approach, keyword construction, hard filter relaxation)
- POS judge prompt (system + user prompt templates)
- POS judge retry loop (re-call retrieval with adjusted params on
  insufficient)
- How Matome weaves stencil + POS results together into final narration
- Multi-firm POS queries ("Compare Best Buy and Amcor's outlook")
- Hybrid metrics — numbers that appear in prose not tables (e.g. revenue
  guidance). Currently deferred to POS chunk text — Matome reads the number
  from the prose. No stencil extraction.
