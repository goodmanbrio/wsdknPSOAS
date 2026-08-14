---
Created: 2026-08-11
Updated: 2026-08-13
Last checked: 2026-08-13
---

# handoff1 — OSHA Gate 1 refresh, Bunragman design session

## 1. Global goal

OSHA (research pipeline) revision for PSOAS — the retrieval/synthesis pipeline that
answers open-ended research questions against the document corpus. Currently a flat
BM25-over-everything design: one global index, one global chunk cap, no file scoping,
no per-chunk size ceiling.

## 2. Session goal

Refresh the stale Gate 1 spec for OSHA, then design `wsnBunragMan` — a source-scoped
router sitting above OSHA — in response to what `failSystematicLog.md` shows is
actually broken.

## 3. Narrative

- `Specs/23_OSHAg.md` was a Gate 1 doc scoped to 3 files (`decomposer.py`,
  `retriever.py`, `synthesizer.py`), dated 20260805, graphs not matching current
  convention. Widened scope to the full `run_research` round trip — orchestrator
  dispatch (`execute_tool.py`), the pipeline orchestrator (`research/__init__.py`),
  handle/registry/console mechanics — because the orchestrator does more than an
  opaque call-and-return.
- `graphify explain`/`grep` confirmed it: `_exec_research()` stores an opaque handle
  (`execute_tool.py:300`) but also inlines the full answer text into the same return
  string (`execute_tool.py:307-309`), contradicting `opaque_registry.py`'s own stated
  purpose. Also found: a dead empty-chunks guard in `synthesizer.py:43-48`
  (unreachable, caller already filters), confirmed line-number drift proving the
  prior doc was stale, and that `src.config` resolves through a namespace-package
  path splice in `src/__init__.py`, not a literal file.
- Rewrote `23_OSHAg.md`'s function graph and process view at PMS2 resolution (red
  decision diamonds, blue resource parallelograms, gap-styled findings).
- Measured real chunk sizes against the live `data/index/docstore.json` (12,299
  chunks): text chunks average 784 tokens (soft-capped by `CHUNK_SIZE=768`), table
  chunks average 1,408 tokens and are kept fully atomic with no size ceiling
  (`01_Chunk.py:788`), worst case 2,632,209 chars (~953k tokens) in one chunk.
  `research_max_chunks=20` is arbitrary — budget math supports ~220 chunks before a
  300k-token ceiling — but count was never the real constraint; unbounded per-chunk
  size is. 16 table chunks exceed 50k chars; 15 of 16 are xlsx/xlsm-sourced
  (confirmed via `metadata.filetype`, 91.5% of all table chunks are xlsx/xlsm). The
  16th outlier is PDF-sourced (`20250221_Jefferies_LITE_COHR-LITE_...md`,
  chunk_index 23, 56,507 chars) and survives even if xlsx gets pulled from
  ingestion — a dense financial-model table with no blank-first-cell row never
  trips `_is_sub_header()` (`01_Chunk.py:463-471`), a second, independent gap from
  the xlsx one.
- Folded these findings into `23_OSHAg.md`: max-chunks process node annotated with
  the tail-risk finding, a Problem-space section opened with two bullets (91.5% of
  table chunks are xlsx-sourced; no systematic mechanism bounds chunk size), and the
  PDF edge case as a sub-bullet evidencing the second bullet.
- Read `question_sets/failSystematicLog.md` (7 systematic failures) and
  `Specs/wsnBunragMan.md` (idea sketch, existed in two near-duplicate copies —
  Excepelimte copy since deleted, Specs copy is canonical). #4 (xlsx table-to-markdown
  chunking corrupts data: labels shift off values, headers go blank, wrong company
  tags — `LITE US BOE - Nov 2025.md` mislabels all 362 chunks `[Company: LITE]`
  regardless of actual row owner) and #5 (global 20-chunk cap starves individual
  sub-questions, confirmed 3x, escalates to whole-source exclusion) are what
  Bunragman answers. #1 (inference tagged as sourced fact), #2/#7 (cross-document/
  cross-vintage blending), #3 (source-internal arithmetic inconsistency), #6
  (orchestrator skips tool calls, free-composes uncited answers) are sysprompt-level,
  untouched by a routing redesign.
- Design converged through direct back-and-forth, not a single pass: source =
  arbitrarily named file selection (firm/broker/dir/sector, whatever the query
  needs), one Bunragman agent per source. `mapper.py`'s `list_dir`/`ask_user`/
  `report_dirs` loop (`mapper.py:131-283`) is reusable for source discovery, but its
  terminal tool returns a flat `dirs: list[str]` — doesn't support named source
  groups (`{label: [files]}`), needs a different data structure, not a straight
  lift. PMS1's `pto.py:131` `_entity_matches()` cascade is prior art for file
  scoping, but Bunragman's actual need (exact path match, since discovery already
  yields exact paths) is simpler than PMS1's fuzzy synonym cascade.
- Drew the Gate-1-style process diagram into `wsnBunragMan.md` per
  `/wsnSpecsutekisa` convention. Corrected mid-session: dropped all code citations
  except OSHA's (the one thing genuinely reused unmodified) — citing `mapper.py`/
  `pto.py`/BunNavHarness internals implied false "reuse" claims where the actual
  shape differs.
- Walked the open gaps high-to-low-level, one question at a time:
  - BunNavHarness integration: opaque black-box tool, `ask_bunnavharness(question,
    xlsx_path) -> str`. Its internal REPL/tool loop is invisible to Bunragman.
  - OSHA file-scope filter: pre-search restriction — rank only within a source's
    file set before BM25 ranking, never let an out-of-scope chunk occupy a slot.
    Avoids reproducing failSystematicLog #5's starvation bug in file-scoped form.
  - Toolcall cap scope: per-agent, not a shared pool. Collapsed three diagram nodes
    (`RES_TOOLCALLCAP`/`D_CALLCAP`/`CAPPED`) into the existing `D_AGENTDONE` decision
    — a per-agent cap can't cause a source to be dropped.
  - Per-agent loop shape: no loop. All of a source's non-xlsx files search in ONE
    OSHA call (that's what the file-scope filter already does — scopes to a set,
    not one file); xlsx files fire in parallel, one BunNavHarness call each.
    Partition once, fan out once, done. This ate the turn-cap question entirely —
    removed `D_FILETYPE` as a repeating decision, `D_AGENTDONE`, and
    `RES_MAXTURN_AGENT` — and surfaced a new named assumption instead
    (`RES_MAXCHUNKS_ASSUME`): the single pass depends on `research_max_chunks`
    raised 20→50 making one OSHA call exhaustive for a source's non-xlsx files —
    plausible for a small file set, unverified for a large one.
- Diagram polish: left-aligned all node text (added `text-align:left` to every
  classDef including a new `default` one; also asserted as a 10-word convention in
  `SKILL.md`). Corrected `SOURCES`/`PERSOURCE` from oval (terminator shape) to
  parallelogram (data shape) — they're mid-pipeline data, not start/end states, a
  real shape-convention violation, not just a rendering complaint. Recolored
  `RES_MAXCHUNKS_ASSUME` blue (new `resourcegap` class, blue fill + dashed red
  border) — it's a genuine hardcode, so it gets resource color, but it's also an
  unverified assumption, so it keeps the gap-flagged border too.

## 4. Concrete findings worth flagging forward

- `execute_tool.py:307-309` inlines the full research answer alongside a stored
  handle — the one tool on the dispatch table that bypasses the opaque-var contract
  `opaque_registry.py:4-5` states.
- `synthesizer.py:43-48`'s empty-chunks guard is dead code — unreachable given
  `research/__init__.py:88-93` already filters before calling it.
- No per-chunk size ceiling exists anywhere in OSHA's retrieval path
  (`bm25_index.py:146-152` floors at 10 tokens, never ceilings). 15 of 16 oversized
  table chunks (>50k chars) are xlsx/xlsm-sourced; the 16th is a PDF table that
  evades the sub-table splitter by a different mechanism (`_is_sub_header()` never
  trips on a row-uniform financial model). Removing xlsx from ingestion fixes most
  of this but not the ceiling problem itself.
- `research_max_chunks=20` has no derivation in code or comments; measured budget
  math supports ~220 chunks before a 300k-token ceiling on DeepSeek V4 Pro.
- Bunragman doesn't address failSystematicLog #1, #2, #3, #6, #7 — all sysprompt or
  orchestrator-level. Whether those get designed separately or stay explicitly out
  of scope for Bunragman is an open call for next session.
- Skeleton built as real code (see `handoff2.md`): sekei's discovery→batch→
  fan-out→reconcile control flow lives in `src/scripts/bunragman/sekei.py`,
  matching `wsnBunragMan.md`'s diagram, which is now kept current rather than
  "imagined."
- `report_dirs`'s replacement (§5 item 3 below, previously undrawn) is resolved
  — not built fresh. Pulled `zako_discover()` from `wsdknPSOAS`'s `ookinoD`
  branch: a real scan + LLM select + `ask_user` confirm/modify/redo loop.
- Zako's file paths (relative to `config.zako_source_root` = `data/files_ingested`)
  already match `docstore.json`'s `file_path` field exactly — checked against the
  live index. No path-translation layer needed between Bunragman and OSHA.
- OSHA's `research_synthesizer` output changed separately: `OSHA_ID`/numbered
  local citations/a `## Bibliography`, via `src/harness/answer_sheet_contract.py`
  (Layer A of a larger feature on `ookinoD`; Layer B, a condensed-summary
  generator, deliberately left out).
- All of the above pushed to `omayadealwis/wagasyanohimitunaLAG`, branch `osha`.

## 5. Confirmed next step

Build the real Bunragman per-source agent behind `call_bunragman(query, source_dict)`
— `source_dict` unchanged, sekei's existing single-entry `{label: [file_path,...]}`
fan-out call. The agent: partitions the source's files once (`PARTITION`: non-xlsx
group, each xlsx file on its own), calls OSHA and BunNavHarness for real (tools
themselves may still be stubbed — separate work streams), resolves
`D_INTERNALCONFLICT`, writes its own summary (`SUMMARYFLAGGED`/`SUMMARYPLAIN` →
`WRITEDISK`), returns the filepath to sekei.

Still open, not this pass's scope:
1. `RES_MAXCHUNKS_ASSUME` — unresolved.
2. `SYSGAP` — deferred by choice.
3. OSHA's filename-filter contract — drafted this session, not yet written to a
   file: extend `run_research_pipeline()` with `file_scope: list[str] | None = None`,
   filtered before ranking, paths matching `docstore.json`'s `file_path` exactly.
   Coworker-owned, on `osha`.

Files to read to pick this up: `handoff2.md`, `Specs/wsnBunragMan.md`,
`src/scripts/bunragman/sekei.py`, `src/scripts/zako/session.py`,
`src/harness/answer_sheet_contract.py`, `Excepelimte/BunNavHarness/README.md`.
