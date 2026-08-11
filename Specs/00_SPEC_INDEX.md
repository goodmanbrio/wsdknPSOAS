# PSOAS Spec Index

**Poony Sophomore Orchestrated Analyst Strapon**

Harness that wraps PMS2, PTECA, stencil2chart as LLM-orchestrated
tools with CLI user interaction at any depth. Single orchestrator
tool call `run_pms2(firms, query, periods, granularity)` drives
the full extraction pipeline.

---

## Active specs

| # | Spec | What it covers | Status |
|---|---|---|---|
| 17 | `17_PMS2.md` | PMS2 pipeline: three-stencil model (ans/work/job), Sekei (stencil design), Mapper (parallel dir discovery), Batch Planner (file routing), LengCaller (chunk-level extraction), Validator (compare-and-swap), merge + compute. Single `run_pms2` tool call replaces `run_pms1`. | DONE (M6) |
| 17a | `17a_PMS2_BuildGuide.md` | Sequenced milestone build guide for 17. T0 through M6. | Reference |
| 17b | `17b_PMS2noDiscretion.md` | Discretionary design decisions made during PMS2 build. | Log |
| 17c | `17c_PMS2_LengnoDiscretion.md` | Leng/Validator prompt decisions. | Log |

## Archived specs (Specs/archive/)

Specs 01–16 built the harness and PMS1. All implemented, all
reflective (documentation of what IS, not instructions). Moved
to `Specs/archive/` — the code they describe is still live.

| # | Spec | What it covers |
|---|---|---|
| 01 | `01_terminal_router.md` | TerminalRouter, ToolChannel, register(), buffering, stdin brokering |
| 02 | `02_agent_loop.md` | Orchestrator while loop, stop_reason dispatch, message accumulation |
| 03 | `03_opaque_registry.md` | Variable handles ($var_N), _store/_resolve, inspect_var tool |
| 04 | `04_system_prompt.md` | Orchestrator system prompt + TOOL_DEFINITIONS |
| 05 | `05_execute_tool.md` | execute_tool dispatch table, error handling |
| 06 | `06_tool_pms1.md` | PMS1 single-firm pipeline (SUPERSEDED by PMS2, code in tree, dispatch commented out) |
| 07 | `07_tool_pteca.md` | PTECA chart planner agent loop |
| 08 | `08_tool_stencil2chart.md` | SVG chart renderer |
| 09 | `09_tool_askuser.md` | Orchestrator-level ask_user |
| 10 | `10_cli_cosmetics.md` | Rich integration: label colors, spinners, panels |
| 11 | `11_repl_loop.md` | Outer REPL, session state, exit handling |
| 12 | `12_PUMBA.md` | PMS1-internal PUMBA retrieval fallback (Dailo/Gulei/Leng) |
| 13 | `13_ModelAgnosticToolportEtCentralize.md` | LLMBackend abstraction, call_with_tools(), structured_complete() |
| 14 | `14_ParallellizePMSBatches.md` | PMS1 batch parallelization |
| 15 | `15_DebugDaPipelineEtQOL.md` | TraceBuffer debug traces |
| 16 | `16_SyspromptKaisen.md` | Externalized system prompts via load_sysprompt() |

NoDiscretion logs also in archive: 12a, 13a, 13b, 14a.

---

## Current architecture

```
psoas.py → agent_loop.py (orchestrator REPL)
  │
  ├── run_pms2 → pms2.py
  │     Phase 0: Sekei (stencil design) + Mapper (dir discovery)
  │     Phase 1: Dispatcher per firm (parallel)
  │       └── Batch Planner → LengCaller → Leng × N → Validator × N
  │     Phase 2: merge jobs → compute formulas → display stencils
  │
  ├── run_pteca → tool_pteca.py (chart layout planner)
  ├── run_stencil2chart → stencil2chart.py (SVG renderer)
  ├── ask_user, inspect_var, write/read_session_md
  └── (run_pms1 — commented out, code in tree)
```

## File layout

```
src/
  harness/
    agent_loop.py          orchestrator REPL
    execute_tool.py        dispatch table (_exec_pms2, _exec_pteca, etc.)
    system_prompt.py       TOOL_DEFINITIONS (JSON schemas for API tools)
    opaque_registry.py     $var_N handle storage
    terminal_router.py     ToolChannel I/O routing
    sysprompts.py          load_sysprompt() template loader
    trace.py               TraceBuffer for debug traces
  scripts/
    config.py              all config (LLM profiles, paths, concurrency)
    llm.py                 LLMBackend + AnthropicLLM/OpenAI/Gemini
    PMS2/
      pms2.py              pipeline entry (run_pms2_pipeline)
      sekei_loop.py        Phase 0 agent loop
      mapper.py            dir discovery per firm
      stencil_topo.py      topo sort + cell ID assignment
      stencil_safe_math.py AST-walked arithmetic eval
      dispatcher.py        Phase 1 loop + FiscalCalResolver
      batch_planner.py     file routing agent loop
      leng_caller.py       chunk fetch → Leng × N → Validator × N
      validator_loop.py    per-chunk verdict agent
      denom_reconcile.py   DENOM_FACTORS, unit/denom aliases
      merge_compute.py     Phase 2: merge + formula eval + display stencils
      chunk_overlap.py     ±1k char overlap injection
      00_Ingest.py         docling convert (pdf/docx → md)
      01_Chunk.py          chunk md → docstore
  psoas.py                 CLI entry point

sysprompts/
  orchestrator/            orchestrator system prompt
  pms2_sekei/              Sekei prompt
  pms2_mapper/             Mapper prompt
  pms2_batch_planner/      Batch Planner prompt
  pms2_leng/               Leng extraction prompt
  pms2_validator/          Validator verdict prompt

data/
  files_raw/               source PDFs/docx
  files_ingested/          docling-converted .md + manifest.json
  index/                   docstore.json + file_path_index.json

tests/
  PMS2_FailureModes.md     extraction quality forensics
  test_pms2_unit.py        T0 unit tests
```

## Ideal demo (PMS2, end-to-end)

```
$ python psoas.py "Revenue, Gross Margin for LITE FY2026. Revenue, Gross Margin for Innolight FY2020-FY2021."

[ORCHESTRATOR] PSOAS reporting for duty sir!
[ORCHESTRATOR] PMS2 extraction:
  Firms: LITE, Innolight
  Periods: FY2020, FY2021, FY2026
  Granularity: annual
  Metrics: Revenue, Gross Margin
  Confirm? [y / edit]
> y

[PMS2-sekei] Sector: include 0 Optical/? Currencies: LITE→USD, Inno→CNY?
             Gross Margin: GP/Revenue confirmed?
> yes optical. currencies correct. gm standard

[PMS2-sekei] Mapper working...
[PMS2-map-LITE] Found 4 files
[PMS2-map-Innolight] Found 5 files

[PMS2-sekei] Preview: 6 rows × 3 periods. Confirm?
> y

[PMS2] Topo sort: 6 rows, 0 cycles. 18 cells assigned.

[PMS2-disp-LITE] Iteration 0: 327 chunks, 83 Leng hits
[PMS2-disp-LITE] LITE Rev FY26 WRITTEN, GP FY26 WRITTEN
[PMS2-disp-Innolight] Iteration 0: 289 chunks, 49 Leng hits
[PMS2-disp-Innolight] Inno Rev FY20-21 WRITTEN, GP FY20-21 WRITTEN

[PMS2] Phase 2: Merge + Compute
[PMS2]   LITE GM FY26 = GP/Rev → 0.329
[PMS2]   Inno GM FY20 = GP/Rev → 0.412, FY21 → 0.398
[PMS2] 2 stencils complete.

[ORCHESTRATOR] LITE=$var_1, Innolight=$var_2.
```

## Handoff notes

- PMS2 replaces PMS1. `run_pms1` dispatch entry commented out,
  TOOL_DEFINITIONS entry commented out. PMS1 code stays in tree.
- PMS2 handles multi-firm in a single call. Orchestrator confirms
  firms/periods/granularity via ask_user before calling run_pms2.
- All PMS2 agents use externalized sysprompts via load_sysprompt().
- Extraction quality bottleneck: Leng prompt tuning for temporal
  disambiguation, price target vs share price, GAAP vs non-GAAP.
  See `tests/PMS2_FailureModes.md`.
- FiscalCalResolver (Haiku + web_search) occasionally gets FY end
  wrong. Known issue for quarterly extractions.
