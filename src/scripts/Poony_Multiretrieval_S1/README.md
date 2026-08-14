# PMS — Poony Multiretrieval System (S1)

Multi-retrieval financial data extraction pipeline. Replaces S0's monolithic hybrid retrieval with structured per-batch subquery lanes.

**Status: scaffold / dev.** Gradio UI wired to S1 pipeline (Sekei → parallel PTO → stencil). Pipeline also exercised via eval test harness.

## Architecture

```
User query
    |
    v
  SEKEI (LLM planner — Opus 4.8 + thinking)
    |   Produces SekeiPlan: cells (retrieve/compute), batches, formulas
    |
    v
  ORCHESTRATOR (orchestrator.py)
    |   Loops batches, adapts SekeiBatch -> PTOBatchRequest,
    |   merges cell results, evaluates stencil
    |
    |   for each batch:
    v
  PTO (Poony Tabular Oneshot — pto.py)
    |   1. Hard filter: entity + FY + table chunks
    |   2. HyDE: deterministic good/bad signals from YAML
    |      (LLM fallback for non-standard tables)
    |   3. BM25 rank with good query
    |   4. BM25 rank with bad query
    |   5. Destructive fusion: demote bad-ranking chunks
    |   6. Judge LLM: extract values + denomination + unit
    |   7. Parse to cell results
    |
    v
  COMPUTE_STENCIL (stencil.py — deterministic)
    |   Fills compute cells (margins, ratios) from formulas
    |
    v
  Filled stencil (dict[str, CellResult])
```

## Key dependencies on reference data

The pipeline relies on curated JSON/YAML lists for precise retrieval:

- **FIRM_SYNONYMS** (config.py) — maps firm names to all known variants (tickers, legal names, filename prefixes). Used for entity hard-filtering. Corpus-only: Best Buy, Amcor, Boeing, 3M.
- **retrievable_line_items.yaml** (hardcode_dependencies/) — statement title aliases, line item aliases, computed metrics, batching guidance. Drives deterministic HyDE (no LLM cost for known statements) and bad-signal generation.
- **llm_profiles.yaml** (src/) — named LLM configurations (model, provider, thinking budget, temperature). Config.py maps pipeline roles to profile names.

## Project structure

```
Poony_Multiretrieval_S1/
|-- src/
|   |-- sekei.py          # Query planner (SekeiPlan, validate_plan, format_plan)
|   |-- stencil.py        # Stencil compute + display (CellResult, compute_stencil, format)
|   |-- pto.py            # Tabular retrieval + judge + pre-gen parser
|   |-- orchestrator.py   # Pipeline wiring: sekei -> PTO -> stencil
|   |-- config.py         # Config + FIRM_SYNONYMS
|   |-- llm.py            # LLM backends (Anthropic, DeepSeek, OpenAI, Gemini)
|   |-- llm_profiles.yaml # Model configurations
|   |-- ingest.py         # Document chunking + metadata (dir_implied_firm, fiscal_year, chunk_type)
|   |-- index_store.py    # Index lifecycle + SHA-256 change detection
|   |-- gradio_ui.py      # S1 pipeline visualizer (live — Sekei → parallel PTO → stencil)
|-- eval/
|   |-- test_sekei_orchestrate_pto_stencil.py  # E2E test harness (main dev entry point)
|   |-- run_sekei_eval.py                      # Sekei-only eval
|   |-- sekei_test_cases.yaml                  # Sekei test cases + ground truth
|   |-- pto_test_cases.yaml                    # PTO retrieval test cases
|   |-- sekei_saved_outputs_for_PTOetStencilTest.json  # Cached Sekei plans for e2e tests
|-- test_pto.py           # PTO retrieval-only test harness
|-- hardcode_dependencies/
|   |-- firm_synonyms.json           # Company name variants for entity filtering
|   |-- denomination_units.json      # Valid denomination + unit enums for judge
|   |-- retrievable_line_items.yaml  # Statement/line-item reference data + HyDE signals
|-- app.py                # Gradio entrypoint (python app.py)
|-- data/                 # Financial filings (10-K, 10-Q, earnings)
|-- index/                # Persistent vector index (auto-generated)
|-- idea/                 # Design briefs for future work
```

## Running

```bash
# Gradio UI (live Sekei → parallel PTO → stencil)
python app.py
python app.py --port 8080 --share

# E2E pipeline test (loads saved Sekei plans, runs PTO + judge + stencil)
python eval/test_sekei_orchestrate_pto_stencil.py
python eval/test_sekei_orchestrate_pto_stencil.py --ids d_bby

# PTO retrieval-only test
python test_pto.py
python test_pto.py --ids e_bby_income

# Sekei planner eval
python eval/run_sekei_eval.py
python eval/run_sekei_eval.py --ids d_bby d_amcor
```

## LLM profiles in use

| Role | Profile | Model | Notes |
|---|---|---|---|
| Sekei planner | anthropic_opushighthink | claude-opus-4-8 | 10k thinking budget |
| PTO HyDE (fallback) | deepseek_chattemp0 | deepseek-chat | temp=0, only for non-standard tables |
| PTO judge | anthropic_opusmedthink | claude-opus-4-8 | 5k thinking budget |

## Not built yet

### compute_stencil: denomination/unit aware computation

compute_stencil (stencil.py) currently does naive float math on raw values
without checking denomination or unit compatibility. This means:

**Silent wrong results:**
- If Revenue is in "mn" from one batch and Gross Profit is in "k" from
  another, Gross Margin = GP / Revenue silently computes garbage
- No validation that formula components share the same denomination
- No validation that formula components share the same currency

**Display formatting broken:**
- EPS (6.84) displays as "7" — format_filled_stencil uses ,.0f for
  any value >= 1, truncating decimals on per-share / per-unit data
- No denomination suffix shown — "51,761" not "51,761 ($M)"
- All compute cells hardcoded to .2% format — works for margins, but
  non-percentage computes (absolute change, sums) display as garbage
  e.g. Revenue change of -5,463 displays as "-546300.00%"

**Denomination standardization missing:**
- Stencil display needs a standard denomination per unit-group: all USD
  rows display in one denomination (e.g. "mn"), while %, count, and
  per-unit rows are independent groups with their own denomination
- No logic to group rows by unit, pick a standard denomination per
  group, convert values to match, or label it on the stencil

**Division by zero crashes entire stencil:**
- If any denominator cell is 0 (judge misread, pre-revenue company),
  the formula eval raises ZeroDivisionError → ValueError
- Entire stencil fails — no partial results returned, even for cells
  that don't depend on the zero-value cell
- Compute cells evaluated in order: one failure kills all subsequent
  compute cells regardless of dependency

**Sekei plan validation is cosmetic:**
- validate_plan exists (checks batch/cell consistency, cross-column refs,
  forward refs, row consistency) but orchestrator.run() never calls it
- gradio_ui.py calls validate_plan but discards the returned errors —
  invalid plans proceed to execution regardless
- Invalid Sekei plans flow unchecked to eval_stencil — cross-column
  formulas, duplicate cells, forward refs all go undetected until
  eval_stencil crashes or produces wrong results
- A print warning exists but no actual validation guard

**eval() is a code injection vector (two copies):**
- compute_stencil (stencil.py) uses Python eval() on formula strings
  sourced from Sekei LLM output
- _try_eval_compute (gradio_ui.py) duplicates the same eval() logic for
  progressive stencil display — eagerly evaluates compute cells as soon
  as their deps resolve, so margins appear before all batches finish.
  Same eval(), same lack of denomination checks, second copy of the bug.
- If Sekei produces a malicious formula (via prompt injection through
  user query), it executes as arbitrary Python code
- Production blocker. Must be replaced with a safe expression parser
  before any user-facing deployment

**Current state:** a runtime print fires on every call:
"VALUE RECONCILIATION & GOOD COMPUTE LOGIC NOT BUILT".
The denomination and unit fields exist on CellResult but are ignored
during computation and display.

**What needs to be built:**
- Before computing: validate component cells share denomination + unit
- Same denomination → compute normally
- Same unit, different denomination → normalize (multiply to reconcile)
- Different units → flag, refuse to compute
- Compute cell unit/denomination inference from formula operators
  (division → "unit"/"none", addition → inherit from components)
- Display formatting driven by denomination + unit, not magnitude
- Denomination standardization per unit-group for stencil display
- Graceful handling of division by zero (skip cell, return partial)
- Replace eval() with safe expression parser (e.g. ast.literal_eval
  or custom tokenizer supporting only arithmetic operators + cell refs)
- Call validate_plan in orchestrator before running batches

### Pipeline gaps

- **Single-company assumption** — SekeiPlan.firm is a single string.
  Multi-company queries ("Compare Best Buy and Amcor revenue") silently
  degrade: Sekei picks one firm, the other is ignored, user gets a
  half-answer with no warning. No guardrail or error message.
- **format_sources shows opaque node_ids** — CellResult.source stores
  node_id for chunk traceability, but format_sources renders it as a
  raw UUID. Unreadable in dev output. Needs index (docstore) passed in
  to resolve node_id → file_name + section for display.

### hardcode_dependencies/ — centralized reference data

Deterministic reference data ("names to recognize") lives in
`hardcode_dependencies/`, not scattered across Python files. These
are strings/lists that must grow when the corpus or supported filings
change. They are NOT tuning parameters (those stay in config.py).

- **hardcode_dependencies/** = dumb lookup tables. "What company names
  exist?" "What denominations are valid?" "What statement types do we
  know about?" Append-only as corpus grows. If stale → silent failures.
- **config.py** = numerical knobs. chunk_size, TOP_K, fusion alpha.
  Tweak for quality. If wrong → degraded results, not silent breakage.

**What lives in hardcode_dependencies/:**

| File | What | Risk if stale | How to update |
|---|---|---|---|
| firm_synonyms.json | Company name variants | HIGH — entity filter kills all chunks silently | Add new company entry |
| denomination_units.json | Valid denomination + unit enums | MEDIUM — judge outputs unrecognized value | Append new denomination or unit |
| retrievable_line_items.yaml | Statement titles, line items, aliases, batching | HIGH — HyDE falls back to LLM, Sekei rejects statements | Add new statement or line item |

**How it works:**

- Python code loads from JSON/YAML at startup, not inline dicts/strings
- Judge prompt assembles denomination/unit enum strings dynamically
  from the JSON (`" | ".join(denoms)`)
- Sekei prompt derives statement key whitelist from the YAML keys
- Adding a new company = edit firm_synonyms.json, not hunt through Python

**TODO — manifest file:**

- Create `hardcode_dependencies/MANIFEST.md` — in-directory index explaining
  which file to edit for what change ("to add a company, edit
  firm_synonyms.json"). The README table above serves this purpose but a
  dev browsing the directory on disk sees raw JSON/YAML with no guidance.
  Manifest should include: file name, what it controls, risk if stale,
  which Python modules consume it, example of how to add an entry.

**Not externalized** (too trivial or too pervasive):

- `_BLACKLIST_SECTIONS` (pto.py) — 2-element set, LOW risk, one callsite
- chunk_type "table"/"text" — two-value enum, 15+ callsites, never changes
- PTO tuning params (TOP_K, RUNNER_UP_K, fusion alpha/k) — config knobs,
  not reference data

### Filing convention registry

Design brief: `idea/metadata_standardization_filing_conventions_brief.md`

An agent reads ~30 filing conventions (10-K, 10-Q, earnings, IFRS, etc.)
and produces detection rules, boundary patterns, and statement type
mappings per convention. Output MUST be a JSON file at
`hardcode_dependencies/filing_conventions.json`. Ingest.py loads it at
import time and tags chunks with:
- `filing_type` — "10-K", "10-Q", "earnings_release", etc.
- `is_primary_statement` — boolean, distinguishes consolidated financial
  statements from notes/supplemental/segment tables
- `statement_type` — canonical label ("income_statement", "balance_sheet",
  etc.) mapped from filing-specific section headers

**Why this matters**: PTO currently retrieves 238 chunks for an Amcor
balance sheet query. The actual consolidated balance sheet ranks #12
because BM25 can't distinguish it from note tables containing similar
keywords. `is_primary_statement` as a hard filter or boost would cut
238 → ~20 candidates.

### Judge retry loop

When judge returns `sufficient=false` for any metric in a batch:
1. Persist the sufficient cells (don't re-retrieve what already worked)
2. Examine method_log + runner-up metadata for clues
3. Re-call PTO with adjusted params:
   - Modified HyDE good/bad signals (via `hyde_good_override`/`hyde_bad_override`
     fields already on PTOBatchRequest)
   - Relaxed hard filters (entity or FY cascade)
   - Promote a runner-up to top chunks if judge suspects answer is there
4. Re-judge only the insufficient metrics
5. Cap at N retry rounds (prevent infinite loops)

Currently: `pto_judge_to_stencil` raises `ValueError` on any
insufficient metric. No partial results, no retry.

### POS (Poony Oneshot Semantic) — qualitative retrieval lane

Handles non-tabular queries: "What is management's outlook on margins?",
"Describe key risk factors", "What drove revenue decline?"

**Open design questions:**
- Sekei handles BOTH tabular and qualitative in one plan (one LLM call,
  not two). Better to give one instance more tokens than to duplicate
  efforts sending a handover string to a specialized SekeiQualitative.
- Qualitative results live in a parallel structure outside the stencil
  (stencil is numeric cells, prose doesn't fit). Matome receives both.
- `SekeiBatch.datatype` field ("tabular" | "qualitative") for
  orchestrator routing. Or: qualitative queries as a separate list in
  SekeiPlan, not shoehorned into the batch/cell model.
- Hybrid metrics (guidance numbers from MD&A prose) are a bridge problem
  between stencil and qualitative — deferred.

### Other

- **POS** (Poony Oneshot Semantic) — text/qualitative retrieval lane.
  Full spec at `idea/POS_semantic_query_introduction.md`. POSBatch,
  POSCellResult, pos_retrieve → pos_judge → pos_judge_to_result pipeline.
  Results live in parallel dict, not stencil. Sekei outputs both
  tabular batches and pos_batches in one call.
- **Matome** — LLM narration of filled stencil + POS results. Receives
  both `dict[str, CellResult]` and `dict[str, POSCellResult]`.
- **Judge retry loop** — when judge returns sufficient=false, re-call
  PTO/POS retrieval with adjusted params (different HyDE, relaxed filters).
  Currently raises ValueError on insufficient. Needs: persist sufficient
  cells, re-retrieve only insufficient cells, merge results.
- **Filing convention registry** — is_primary_statement metadata for chunk
  disambiguation. Design brief at
  `idea/metadata_standardization_filing_conventions_brief.md`.
  Output must land as `hardcode_dependencies/filing_conventions.json`.
  Ingest.py loads at import time, tags chunks with filing_type,
  is_primary_statement, statement_type.

## Gradio UI (gradio_ui.py)

S1 pipeline visualizer. Runs the full Sekei → PTO → stencil pipeline
with real-time streaming updates.

**Layout:**
- Top bar: Query | Sector (cosmetic — accepted but unused by pipeline) |
  Firm(s) | Run button
- Upper: Stencil HTML table — starts empty (shows cell IDs), fills
  in-place as batches complete
- Middle (during execution): Horizontal batch cards — one per SekeiBatch,
  showing per-batch phase progression
- Lower (after execution): Source browser — cell selector radio list +
  chunk text display

**Streaming pipeline** (`_stream_s1`):
1. Calls `sekei()` live (not cached). Appends firm input to query string.
2. Renders empty stencil, submits ALL batches to ThreadPoolExecutor in
   parallel (`max_workers=max(len(batches), 2)`).
3. Polls every 150ms. Each batch transitions independently through
   phases: pending → retrieving → retrieved → judging → judged/error.
4. As each batch's judge returns, `_try_eval_compute` eagerly fills
   compute cells whose deps are all resolved — margins appear as soon
   as both component batches finish, not after all batches finish.
5. After all batches done: serializes state to dicts for `gr.State`,
   populates cell selector, renders source browser.

**Batch card phases:**
- **pending**: spinner + "Queued"
- **retrieving**: spinner + "PTO Retrieve"
- **retrieved**: HyDE good/bad (truncated), top 3 chunks with
  good/bad/fused BM25 scores + file/section/text preview
- **judged**: per-metric extraction results (value, denomination, unit)
  or INSUFFICIENT
- **error**: error message

**Source browser:** resolves opaque node_ids through
`index.docstore.docs[node_id].text` to show the actual chunk text.
For compute cells, shows formula + component values.

## Pushing changes to GitHub

The local working directory (`Poony_Multiretrieval_S1/`) is NOT a git
repo — it has no `.git/`. Do NOT `git init` here or set this directory
as a remote. The `data/`, `index/`, and `__pycache__/` directories are
local-only and must never be committed.

To push changes:
```bash
# 1. Clone the repo to a temp directory
gh repo clone omayadealwis/wagasyanohimitunaLAG /tmp/pms-push -- -b pms-s1

# 2. Copy changed files into the clone
cp README.md /tmp/pms-push/
cp src/gradio_ui.py /tmp/pms-push/src/
cp app.py /tmp/pms-push/

# 3. Commit and push from the clone
cd /tmp/pms-push
git add -A && git commit -m "your message"
git push

# 4. Clean up
rm -rf /tmp/pms-push
```

## API keys

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # Sekei + PTO judge
export DEEPSEEK_API_KEY="sk-..."        # PTO HyDE fallback
```
