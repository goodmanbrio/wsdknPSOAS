---
Created: 2026-08-14
Updated: 2026-08-14
Last checked: 2026-08-14
---

# handoff3 — changes since 2026-08-14 08:00

Everything below is currently uncommitted on top of `f4e4e88` (2026-08-14
11:43, the branch's only prior commit) — this doc covers that whole diff,
not just one session's worth of edits.

## Bunragman per-source agent: simplified to CALLOSHA → WRITEDISK

The per-source agent no longer makes a SELECT (xlsx target selection) or
SUMMARIZE (Bunragman-level summarize) LLM call, and no longer calls the
BunNavHarness stub. `call_bunragman()` now makes one real call —
`_call_osha()` (`run_research_pipeline()`, scoped to the source's files via
`file_scope`) — and writes OSHA's own already-cited answer straight to disk
as the source's summary, unwound back to raw `[Source: ...]` tags via
`expand_answer_sheet_for_summary()`.

Removed with it:
- `_partition_files()`, `_select_xlsx_targets()`, `_write_summary()`,
  `_call_bunnavharness_stub()` — `src/scripts/bunragman/sekei.py`
- `bunragman_agent_profile` — `src/scripts/config.py`
- `get_bunragman_agent_llm()` — `src/scripts/llm.py`
- its DeepSeek profile entry — `src/scripts/llm_profiles.yaml`
- `sysprompts/bunragman_agent_select/`, `sysprompts/bunragman_agent_summarize/`
  — deleted

`Specs/wsnBunragMan.md` has NOT been updated to match this — it still
describes SELECT/SUMMARIZE as real, live control flow. Flagged, not fixed,
this pass.

## Terminal output: per-lane tagging, no more duplicate tags

Prints were double-tagged — e.g. `[RESEARCH] [BUNRAGMAN:BofA] ...` — because
the router auto-prefixes `[label]` from the registered channel, and the code
was also hardcoding that same tag into the message text. Fixed by
registering a dedicated channel per lane instead of every source sharing one:

- `bunragman/sekei.py`: `run_bunragman_sekei()` registers its own `"LANE"`
  channel for dispatcher-level prints; `call_bunragman()` and `_call_osha()`
  register `"LANE-{label}"` / `"RESEARCH-{label}"` per source. Manual
  `[BUNRAGMAN...]` / `[RESEARCH]` text prefixes dropped from every print.
- `research/__init__.py`: same fix for OSHA's own progress prints. Also
  dropped the "Decomposing:", "Decomposed into N sub-questions", and
  "Retrieved N unique chunks" lines entirely — kept "Synthesizing..." and
  "Complete...".

## BUNRAGMAN renamed to LANE in all PM-visible output

Every user-visible string in `bunragman/sekei.py` — spinner labels
(`BUNRAGMAN-GROUP`→`LANE-GROUP`, `BUNRAGMAN-{label}-osha`→`LANE-{label}-osha`,
`BUNRAGMAN-RECONCILE`→`LANE-RECONCILE`) and the on-disk per-source summary
folder (`session_dir/bunragman`→`session_dir/lane`). Internal naming (module,
function, config, and sysprompt names, tests) is untouched — not PM-visible,
left out of scope on purpose. Two test files (`test_bunragman_e2e.py`,
`test_bunragman_agent_live.py`) independently build the old
`session_dir / "bunragman"` path for their own debug output; not updated —
harmless, one is a guarded glob, the other a plain print.

## zako_bunragman sysprompt: directory-selection directives

Three rules added to `sysprompts/zako_bunragman/`: always select
`Email research reports` regardless of query relevance (an explicit
exception carved into the existing "don't over-select" rule); a
sector-level query also selects that sector's constituent firm
directories; a firm-level query also selects that firm's sector directory.
Both expansions rely on the model's own knowledge of which ticker belongs
to which sector — no sector→firm mapping exists anywhere in the repo, so
this is a soft instruction, not deterministic or enforced.

## research_synthesizer sysprompt: de-duplicated

37 near-duplicate citation/labeling rules collapsed to 10 — no concept
dropped, each stated once. Answer template trimmed: removed `Run record`,
the `Expected-answer chunk coverage` table, the `Retrieved-chunk ledger`
table (previously mandatory, one row per retrieved chunk — up to
`research_max_chunks` = 20, every answer), and the `Independent QA` table.
Kept `Derived math`, `Interpretation and limits`, `Citation audit`. Grepped
the codebase first — nothing parses the removed section headers, synthesis
output only has to carry `[Source: ...]` tags.

## Citation/footnote bug: brackets in email-ingested filenames broke the Bibliography

Root cause: email-ingested source files (`Email research reports/`,
filenames and section titles carrying a literal `[External]` mail-gateway
tag) contain a `]` character. `answer_sheet_contract.py`'s citation regex,
`\[Source: [^\]\n]+\]`, stopped matching at the first `]` — the one
belonging to the embedded `[External]` tag, not the citation's real closing
bracket — so the reference failed to parse and was silently dropped. Any
citation to one of these files (63 of 557 ingested carry a bracket in
`file_name` or `section`) collapsed the whole answer's Bibliography to a
`[Source: unavailable]` placeholder, with the raw malformed citation text
left inline.

Two-part fix:
- `research/synthesizer.py`: new `_strip_brackets()` swaps `[`/`]` for
  `(`/`)` in `file_name`/`section` before they reach the model — brackets
  never enter a citation in the first place.
- `harness/answer_sheet_contract.py`: `_UPSTREAM_SOURCE_REFERENCE_RE` now
  tolerates one level of nested `[...]` inside a `[Source: ...]` tag —
  defense in depth, in case a bracket reaches the citation some other way.

Verified against the actual failing case
(`temp/sessions/20260814_172918/research_What_are_the_recent_themes_in_Global_Memory_Tech.md`)
— the malformed citation now parses into a real Bibliography entry, and a
synthetic two-citation test confirmed adjacent citations still split
correctly (no cross-consumption).

## Orchestrator sysprompt

Dropped the "PSOAS reporting for duty sir!" forced greeting and the
"grizzled english soldier" character voice — replaced with a neutral,
professional tone instruction. Added `run_pms2` and `render_markdown_table`
tool descriptions, follow-up/session-context handling (reuse stored stencil
handles across turns, resolve short references like "same company"), and a
rule against exposing opaque handles or `OSHA_*` metadata in the final
answer. Fixed a stray "PMS1" reference that should have read "PMS2".

## Other

`question_sets/Demo_qs.md` — new, untracked, a short demo question list.
Included in this push as-is, not otherwise touched.
