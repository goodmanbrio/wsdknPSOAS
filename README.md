# PSOAS

**Poony Sophomore Orchestrated Analyst Strapon**

LLM-orchestrated financial data extraction and charting pipeline.
Single tool call `run_pms2(firms, query, periods, granularity)`
extracts multi-firm, multi-period financial data from ingested
PDFs/docx into structured stencils.

---

## Architecture

```
psoas.py → agent_loop.py (orchestrator REPL)
  │
  ├── run_pms2 → pms2.py
  │     Phase 0: Sekei (stencil design) + Mapper (dir discovery)
  │     Phase 1: Dispatcher per firm (parallel)
  │       └── Batch Planner → LengCaller → Leng × N → Validator × N
  │     Phase 2: merge jobs → compute formulas → display stencils
  │
  ├── run_research → research/__init__.py
  │     Stage 1: Decomposer (LLM: question → sub-questions + keywords)
  │     Stage 2: Retriever (BM25 keyword search over docstore chunks)
  │     Stage 3: Synthesizer (LLM: chunks → cited markdown answer)
  │
  ├── run_pteca → tool_pteca.py (chart layout planner)
  ├── run_stencil2chart → stencil2chart.py (SVG renderer)
  └── ask_user, inspect_var, write/read_session_md
```

## Components

| Component | Location | Description |
|---|---|---|
| Harness | `src/harness/` | agent_loop, execute_tool, opaque_registry, terminal_router, system_prompt |
| PMS2 pipeline | `src/scripts/PMS2/` | Sekei → Mapper → Dispatcher → Leng → Validator → merge/compute. Multi-firm extraction |
| Research pipeline | `src/scripts/research/` | Decomposer → Retriever (BM25) → Synthesizer. Open-ended QA over ingested docs |
| PTECA | `src/tools/tool_pteca.py` | Multi-stencil chart planning agent (internal agent loop, always-ask) |
| stencil2chart | `src/scripts/stencil2chart.py` | Matplotlib rendering with direct-line labels |
| LLM backend | `src/scripts/llm.py` | Model-agnostic factory: Anthropic, OpenAI, Gemini via `call_with_tools()` + `structured_complete()` |
| PMS1 (legacy) | `src/scripts/Poony_Multiretrieval_S1/src/` | Single-firm pipeline. Dispatch commented out, code in tree |

## Quick start

```bash
# 1. Drop PDFs/DOCX into data/files_raw/ (mirrors subdirectory structure)

# 2. Convert to markdown
python src/scripts/PMS2/00_Ingest.py

# 3. Build searchable chunks + docstore
python src/scripts/PMS2/01_Chunk.py

# 4. Fire up the REPL
python src/psoas.py
```

The BM25 index for open-ended research is built lazily on the first
`run_research` call (or pre-build it with `python tests/stress_research.py`).

## Usage

```bash
# Interactive REPL
python src/psoas.py

# Tabular extraction (→ run_pms2)
python src/psoas.py "Revenue, Gross Margin for LITE and Innolight FY2025-FY2026"

# Open-ended research (→ run_research)
python src/psoas.py "What is the bull case for LITE?"
python src/psoas.py "Summarize the OFC conference takeaways."
```

## Dependencies

```bash
pip install -r requirements.txt
```

Requires `ANTHROPIC_API_KEY` in `.env` or environment.

`run_research` additionally requires a BM25 index over chunks. This is built
lazily on the first call (or run `tests/stress_research.py` to pre-build it).

## Key design decisions

- **Three-stencil model**: ans (user-facing) / work (full grid) / job (per-firm slice). Topo-sorted rows, Rn formula notation, compare-and-swap writes.
- **Opaque handles** (`$var_N`): Tool results stored in registry, only handles passed to orchestrator LLM.
- **TerminalRouter**: All I/O through `register("LABEL")` → ToolChannel. No raw print/input.
- **Parallel extraction**: Per-firm Dispatchers run in parallel. Within each, Leng × N chunks + Validator × N hits fire concurrently.
- **Model-agnostic**: LLMBackend normalizes tool calling across Anthropic/OpenAI/Gemini APIs.
- **Open-ended QA**: Separate `run_research` pipeline. Query decomposed into sub-questions,
  BM25 keyword retrieval (zero API cost, no embeddings), synthesized into cited
  markdown answer. Runs alongside tabular extraction — orchestrator routes by intent.

## Specs

Design specs in `Specs/`. See `Specs/00_SPEC_INDEX.md` for index.
Active: 17 (PMS2), 17a (build guide), 17b/17c (decision logs).
Archived: 01-16 in `Specs/archive/`.

---

## File layout

```
src/
  psoas.py                     entry point
  harness/
    agent_loop.py              orchestrator REPL
    execute_tool.py            dispatch table
    system_prompt.py           TOOL_DEFINITIONS
    opaque_registry.py         $var_N storage
    turn_memory.py             durable per-turn follow-up memory
    terminal_router.py         I/O routing
    sysprompts.py              template loader
    trace.py                   debug traces
  scripts/
    config.py                  all config
    llm.py                     LLMBackend factory
    PMS2/
      pms2.py                  pipeline entry
      sekei_loop.py            stencil design agent
      mapper.py                dir discovery
      stencil_topo.py          topo sort + cell assignment
      dispatcher.py            per-firm extraction loop
      batch_planner.py         file routing agent
      leng_caller.py           chunk → Leng × N → Validator × N
      validator_loop.py        per-chunk verdict
      merge_compute.py         Phase 2: merge + formula eval
      00_Ingest.py             pdf/docx → md
      01_Chunk.py              md → docstore
    research/
      __init__.py              pipeline entry: decompose → retrieve → synthesize
      decomposer.py            LLM query decomposition (V4 Flash)
      retriever.py             BM25 keyword retrieval + dedup
      synthesizer.py           LLM answer synthesis with citations
      bm25_index.py            BM25Okapi index — lazy-build, cached, staleness-aware

sysprompts/
  orchestrator/                orchestrator prompt
  research_decomposer/         query decomposition prompt
  research_synthesizer/        answer synthesis prompt
  pms2_sekei/                  Sekei prompt
  pms2_mapper/                 Mapper prompt
  pms2_batch_planner/          BP prompt
  pms2_leng/                   Leng prompt
  pms2_validator/              Validator prompt

data/                          (gitignored)
  files_raw/                   source PDFs/docx
  files_ingested/              converted .md + manifest
  index/                       docstore + file_path_index

temp/sessions/<timestamp>/
  transcript.md                 full audit log
  memory/                       turn_NNN.md/.json + active_context.md
```

Follow-up turns receive a bounded cumulative turn history automatically; the
orchestrator must read the latest full turn summary before launching research.
It must then declare the missing information and launch only a targeted
research pass for that gap; direct answers do not require a research call.
