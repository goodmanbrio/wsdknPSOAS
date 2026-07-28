# PMS2 — Poony Multiretrieval System 2

Multi-firm financial data extraction pipeline. Single orchestrator
call `run_pms2(query)` drives stencil
design, file discovery, parallel chunk-level extraction, validation,
and formula computation.

Design philosophy: **performance before efficiency.** Every routing
and disambiguation decision made by an LLM. No embeddings, no vector
search, no hardcoded file-to-firm mappings. LLMs reason, Python
structures.

## Architecture

```
 Orchestrator
    │
    ▼
 run_pms2(query)
    │
 ═══════════════════════════════════════════════════════════════
 PHASE 0: DESIGN
    │
    ▼
 Sekei (Opus, high think) ─── agent loop
    ├── ask_user (sector dirs, metric disambig, currency confirm)
    ├── run_mapper
    │      └── Mapper (Haiku) x N firms ── PARALLEL
    │            list_dir → report_dirs → _walk_dirs()
    │            → file inventories per firm
    └── finalize_stencil
           └── Python post-process:
               topo sort → Rn rewrite → work/ans/job stencils
 ═══════════════════════════════════════════════════════════════
 PHASE 1: EXTRACT (per firm, PARALLEL)
    │
    ├── Dispatcher [firm A]          Dispatcher [firm B]
    │   while null ans cells:        (same structure)
    │     ├─ prune satisfied helpers
    │     ├─ Batch Planner (Sonnet, med think) ── agent loop
    │     │     produces [{file, cells}, ...] plan
    │     │     calls run_leng_caller(plan)
    │     │        │
    │     │        ▼
    │     │   LengCaller (pure Python orchestrator)
    │     │     1. file_path_index → node_ids
    │     │     2. docstore.get(node_id) x N
    │     │     3. Leng (Haiku) structured_complete ── ALL PARALLEL
    │     │        per chunk: "found any cells?" → {cell_id: value}
    │     │     4. per hit → Validator (Haiku) agent loop ── PARALLEL
    │     │        cross-ref chunk text → submit_verdicts
    │     │        unit check → denom normalize → lock → CAS write
    │     │     5. update searched_files
    │     │
    │     └─ dry_run check (2x zero-fill → ask user → abort/retry)
    │
 ═══════════════════════════════════════════════════════════════
 PHASE 2: MERGE + COMPUTE (pure Python, no LLM)
    │
    merge job stencils → work stencil
    → compute formula cells (topo-ordered AST eval)
    → fill ans stencil → serialize display stencils
    → persist work_stencil.json + ans_stencil.json
```

## Stencil model

Three stencil types. Work is global, jobs are per-firm subsets,
ans is the user-facing output.

```
 WORK STENCIL (all firms, all rows)
 ┌─────────────────────────────────────────────────┐
 │ col_letters: [A, B, C]                          │
 │ periods:     [FY2025, FY2026, FY2027]           │
 │                                                 │
 │ rows (topo-sorted, 1-indexed):                  │
 │   R1: MktCap     LITE  retrieve  USD            │  depth 0
 │   R2: NetDebt    LITE  retrieve  USD            │  depth 0
 │   R3: OpIncome   LITE  retrieve  USD            │  depth 0
 │   R4: D&A        LITE  retrieve  USD            │  depth 0
 │   R5: EV         LITE  compute   R1+R2          │  depth 1
 │   R6: EBITDA     LITE  compute   R3+R4          │  depth 1
 │   R7: EV/EBITDA  LITE  compute   R5/R6  ans=T   │  depth 2
 │   ...                                           │
 │   R12: MktCap    Inno  retrieve  CNY            │
 │   ...                                           │
 │                                                 │
 │ values:  {A1: 8.2e9, B1: None, A7: None, ...}  │
 │ sources: {A1: "node_abc123", ...}               │
 └─────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
 JOB [LITE]                     JOB [Inno]
 rows R1-R11                    rows R12-R22
 cells A1-C11                   cells A12-C22
 disjoint cell IDs              disjoint cell IDs
 ↕ CAS writes via lock          ↕ CAS writes via lock

 ANS STENCIL
 metrics: [EV/EBITDA, P/E, Laser Rev]
 row_mapping: {LITE: {EV/EBITDA: 7}, Inno: {EV/EBITDA: 18}}
 filled: {LITE: {A7: 9.7, ...}, Inno: {...}}
```

Formulas use `{MetricName}` in Sekei, get rewritten to `Rn` refs
by Python. Topo sort guarantees retrieve rows < depth-1 compute <
depth-2 compute. Cross-firm isolation: LITE `R5/R6` and Innolight
`R16/R17` reference different row numbers.

## LLM roles

| Role | Model | Call pattern | What it does |
|------|-------|-------------|--------------|
| Sekei | Opus (high think) | 1 agent loop | Metric disambiguation, formula design, stencil architecture |
| Mapper | Haiku | 1 per firm, parallel | Walk `files_ingested/`, pick company dirs |
| Batch Planner | Sonnet (med think) | 1 per firm per dispatcher iter | File routing: which files likely contain which cells |
| Leng | Haiku | N chunks x M files, ALL parallel | Bulk extraction: `structured_complete` per chunk |
| Validator | Haiku | 1 per Leng hit, parallel | Cross-ref Leng claim against chunk text, submit verdicts |
| FiscalCalResolver | Haiku + web_search | 1 per firm (non-annual) | Map fiscal periods to calendar dates |

Expensive reasoning (Opus) fires once. High-volume extraction/validation
is all Haiku.

## File map

```
PMS2/
├── pms2.py                 pipeline entry: run_pms2_pipeline
├── sekei_loop.py           Phase 0 agent loop: ask_user + run_mapper + finalize
├── mapper.py               per-firm dir discovery agent loop + _walk_dirs
├── dispatcher.py           Phase 1 loop: prune → BP → dry_run check
├── batch_planner.py        file routing agent loop + run_leng_caller dispatch
├── leng_caller.py          chunk fetch → Leng x N → Validator per hit
├── validator_loop.py       verdict agent loop + _handle_submit_verdicts (CAS)
├── merge_compute.py        Phase 2: merge jobs → compute formulas → display
├── stencil_topo.py         topo sort, Rn formula rewrite, stencil structure
├── stencil_safe_math.py    AST-walked arithmetic eval (no eval())
├── chunk_overlap.py        post-chunking +/-1k char overlap injection
├── denom_reconcile.py      DENOM_FACTORS, unit/denom alias maps
├── 00_Ingest.py            docling convert: files_raw/ → files_ingested/
├── 01_Chunk.py             chunk .md → data/index/ docstore
└── hardcode_dependencies/
    └── units.json          canonical units: [USD, JPY, EUR, GBP, CNY, float]
```

## Data layout

```
data/
├── files_raw/              original source files (pdf, docx)
│   ├── LITE/               company dirs flat at root
│   ├── Innolight/
│   └── 0 Optical/          0-prefix = sector/thematic folder
├── files_ingested/         .md conversions (mirrors files_raw/)
│   ├── manifest.json       {rel_path.md: original_ext}
│   └── convert_hashes.json SHA-256 for incremental convert
└── index/                  shared docstore
    ├── docstore.json       chunks with +/-1k overlap, no embeddings
    ├── file_path_index.json {file_path: {node_ids, filetype}}
    └── file_hashes.json    incremental chunk tracking
```

No embeddings. Chunks looked up by `file_path_index.json`
(file → node_ids), not by semantic similarity. Batch Planner LLM
decides which files to search.

## Key mechanisms

**Compare-and-swap writes.** Multiple Validators race to fill the
same cell. `stencil_lock` + None-check: first write wins, second
gets "skipped". Thread-safe across parallel Leng/Validator threads.

**Denom normalization.** Haiku says `value=8.2, denom="bn"` →
handler computes `8.2 * 1e9 = 8_200_000_000`. Percentages:
`value=32.8, denom="%"` → `0.328`. Alias maps catch Haiku's
non-canonical strings (`RMB→CNY`, `million→mn`, `per share→units`).

**Helper cell pruning.** If MktCap is a helper for EV, and all
of EV's consumer cells are already filled, MktCap gets pruned
from active_cells on the next dispatcher iteration.

**Overlap injection.** SentenceSplitter overlap=0. Post-chunking
pass injects +/-1k chars from neighbor chunks of the same file,
demarcated with `--- context ---` / `--- end context ---` markers.

**Formula computation.** `_safe_math_eval` walks the AST — only
`+`, `-`, `*`, `/` allowed. No `eval()`, no builtins, no
exponentiation. Null propagation: if any dependency is None,
computed cell stays None. Leng-direct values (found literally
in a document) are never overwritten by formula computation;
divergence > 2% emits a warning.

**Dry run safety net.** 2 consecutive dispatcher iterations with
0 new cells → force `ask_user` → user decides continue or abort.

## Sysprompts

Externalized via `load_sysprompt()` (Spec 16). Templates in:

```
sysprompts/
├── pms2_sekei/            stencil design instructions
├── pms2_mapper/           dir navigation strategy
├── pms2_batch_planner/    file routing reasoning
├── pms2_leng/             chunk extraction instructions
└── pms2_validator/        cross-reference verification
```

## Agent loop pattern

All PMS2 agent loops share the same scaffold:

1. Task context goes into **system prompt** via template vars
2. Initial user message is `"Begin."`
3. Tool calls dispatched via `ThreadPoolExecutor`
4. Same-turn guards: terminal tools cannot co-occur with `ask_user`
   or exploration tools
5. `ask_user` resets turn counter (no ceiling on user interaction)
6. `TraceBuffer` set-point per agent for debug traces
