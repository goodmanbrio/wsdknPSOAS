# PMS2 Discretionary Decisions Log

Decisions made during implementation that were not fully
specified. Only design choices, not execution mechanics.

---

## M6: End-to-End

### 1. Staleness check: warning-only, not interactive

Spec describes an interactive y/n prompt ("Reingest? [y/n]")
in the psoas.py startup context. M6 build guide routes
`_check_index_staleness` to pms2.py.

`run_pms2_pipeline` is called as a synchronous orchestrator
tool call — cannot block for user input mid-execution. No
mechanism for the pipeline to pause, prompt the user, and
resume.

Decision: `_check_index_staleness` emits a channel warning
and proceeds. Interactive re-ingest prompt (if ever needed)
belongs in psoas.py startup flow, outside M6 scope.

### 2. run_pms1: comment-out scope

Spec says "comment out `run_pms1` dispatch entry (keep code)"
and "PMS1 code stays dead in tree."

Commented out:
- `_dispatch` table entry in execute_tool.py
- `TOOL_DEFINITIONS` entry in system_prompt.py

NOT commented out:
- `_exec_pms1` function body (stays callable, just unreachable)
- `run_pms1` import in orchestrator sysprompt (it's still listed
  as "(Legacy)" with guidance to use run_pms2 instead — keeps
  the LLM aware PMS1 exists if user asks for it specifically)

### 3. Orchestrator sysprompt: PMS2 confirmation section

Spec gives "intent, not final text" for four key lines. Wrote
actual section under `## PMS2 extraction parameters` heading.
Placed before `## Sub-tool user interaction` (logically: PMS2
param rules are orchestrator-level concerns, not sub-tool
delegation concerns).

Included the confirmation example from the spec's "Orchestrator
sysprompt update" section (the `[ORCHESTRATOR] PMS2 extraction:`
block) as a concrete prompt template.

### 4. pms2_raw_dir added to Config

Spec lists `pms2_raw_dir` as a Config path. Implementation was
missing it (only had `pms2_data_dir` and `pms2_index_dir`).
Added `pms2_raw_dir = _PSOAS_ROOT / "data" / "files_raw"`.
Needed by `_check_index_staleness` for mtime comparison.

### 5. Staleness mtime comparison logic

For `files_raw/` and `files_ingested/`: max mtime across all
files (recursive). `files_ingested/` excludes `.json` metadata
files (manifest.json, convert_hashes.json) — those are
byproducts of ingest, not source content.

Two staleness conditions checked independently:
1. raw > ingested → "re-run 00_Ingest + 01_Chunk"
2. ingested > docstore → "re-run 01_Chunk"

Only one warning emitted (raw>ingested takes priority since
it implies both stages are stale).

### 6. Orchestrator sysprompt: run_pms1 removed entirely

Removed `run_pms1` from orchestrator sysprompt tool list.
No "(Legacy)" stub — avoid bloating the sysprompt. PMS1
code stays in tree (execute_tool.py, tool_pms1.py) for
potential reuse but is invisible to the orchestrator LLM.
