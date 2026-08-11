# ClaudenoDiscretion — Design/Execution Ambiguity Log

## D1: Package import structure — where does PMS1 code live?

**Forensics:**
- Specs reference `from src.config import Config`, `from src.sekei import sekei`
- Actual PMS1 code lives at `src/scripts/config.py`, `src/scripts/sekei.py` (copies from `Poony_Multiretrieval_S1/src/`)
- Specs say new harness code goes to `PSOAS/src/harness/`
- Both old code (`src.config`) and new code (`src.harness.terminal_router`) must be importable under `src.*`
- No `src/__init__.py` existed — `src/` was not a Python package

**Hypothesis 1:** Move files from `src/scripts/` up to `src/` directly.
- **Doubt:** Destructive. Breaks existing `src/scripts/` as a reference.
- **Failure mode:** Path references in files (`__file__.parent.parent / "hardcode_dependencies"`) break if moved.

**Hypothesis 2:** Change all imports to `from src.scripts.config import Config`.
- **Doubt:** Requires modifying every existing PMS1 file's internal imports. Spec says "no changes to internal logic."
- **Failure mode:** Huge diff for no logic value. Fragile if files are ever moved back.

**Hypothesis 3:** `__path__` extension in `src/__init__.py`.
- **Verify:** `__path__.append("src/scripts")` makes `from src.config` resolve to `src/scripts/config.py`. Python's import machinery searches all entries in `__path__` for submodules.
- **Doubt:** Could cause dual-import if something also imports `src.scripts.config`. In practice, nobody does.
- **Failure mode:** None observed. This is standard Python namespace-extension pattern.

**Decision:** Hypothesis 3. `src/__init__.py` appends `scripts/` to `__path__`. Zero changes to existing PMS1 imports. New harness code is a real subpackage at `src/harness/`.

**Path reference fix:** `sekei.py` and `config.py` use `Path(__file__).resolve().parent.parent / "hardcode_dependencies"`. With files at `src/scripts/sekei.py`, `parent.parent = src/`. Symlinked `src/hardcode_dependencies/` → original `Poony_Multiretrieval_S1/hardcode_dependencies/`.

---

## D2: Config.get_llm_profile() — doesn't exist yet

**Forensics:**
- Specs (02, 07) call `config.get_llm_profile("anthropic_orchestrator")` and `config.get_llm_profile("anthropic_sonnetmed")`
- Existing Config has no such method. Profile loading lives in `llm.py` (`_load_profiles`, `_resolve_profile`)
- `anthropic_sonnetmed` exists in `llm_profiles.yaml`. `anthropic_orchestrator` does not.

**Decision:** Added `get_llm_profile(name)` to Config directly. Loads YAML, looks up name. Simple, spec-aligned. Added `anthropic_orchestrator` profile (Sonnet 4.6, no thinking, max_tokens=4096) to `llm_profiles.yaml`.

---

## D3: IndexManager vs load_index — API mismatch

**Forensics:**
- Spec 06 sketch uses `from src.index_store import load_index`
- Actual API: `IndexManager(config).load_or_build()` — no standalone `load_index()`

**Decision:** tool_pms1.py uses `IndexManager(config).load_or_build()` inside `_load_index()`. Double-check locking preserved as spec'd.

---

## D4: Orchestrator model choice

**Forensics:**
- Spec says `config.get_llm_profile("anthropic_orchestrator")` but doesn't define the profile
- Orchestrator needs tool use. Thinking not needed (concise routing decisions).
- Sonnet 4.6 is sufficient and cheaper than Opus 4.8.

**Decision:** `anthropic_orchestrator` profile = Sonnet 4.6, thinking=false, max_tokens=4096. Can upgrade to Opus if orchestrator decisions prove too shallow.

---

## D5: terminal_router _styled_print lock safety

**Forensics:**
- `_styled_print` is called from `.print()` while holding `_lock` (when buffering), and also from `_ui_loop` flush without holding `_lock` (when flushing buffer after input).
- `rich.Console.print()` is documented as thread-safe (internal lock).

**Hypothesis:** `_styled_print` can be called with or without the outer lock held because Console has its own internal locking.
- **Verify:** `rich.Console.__init__` creates `self._lock = threading.RLock()`. All print methods acquire it.
- **Doubt:** Double-locking (our `_lock` + Console's `_lock`) could deadlock if acquisition order varies. But our lock is always acquired FIRST (outer), Console's SECOND (inner). Consistent order = no deadlock.

**Decision:** Safe. No change needed.

---

## D6: tool_pteca.py imports `_router` directly

**Forensics:**
- Spec 10 says PTECA uses `_router.start_spinner("PTECA")` before LLM calls
- `_router` is the module-level singleton in `terminal_router.py`
- Importing a module-level private var across modules is dirty

**Decision:** Accepted. `_router` is a singleton, effectively public despite the underscore. Alternative (adding `get_router()` function) is ceremony for no benefit. The spec explicitly shows this pattern.

---

## D7: session_dir path — relative vs absolute

**Forensics:**
- Spec says `PSOAS/temp/sessions/YYYYMMDDHHMMSS/`
- If run from PSOAS_Jul15yolu/, this creates `temp/sessions/...` relative to cwd
- If run from elsewhere, session dir goes somewhere unexpected

**Decision:** Used relative path `temp/sessions/{ts}` to match spec. `psoas.py` should be run from project root. Acceptable for MVP. Could absolutize later if needed.

---

## D8: Post-move restructure — tools/ subdir + PMS1 nested in scripts/

**Forensics:**
- User moved `src/tool_pms1.py` → `src/tools/tool_pms1.py`, same for tool_pteca
- User moved PMS1 code into `src/scripts/Poony_Multiretrieval_S1/` (full project copy with its own `src/`, `hardcode_dependencies/`, `data/`, etc.)
- `src/scripts/` retains PSOAS-modified copies: `config.py` (has `get_llm_profile`), `stencil2chart.py` (has `channel` param), `llm.py`, `llm_profiles.yaml`
- PMS1 originals at `src/scripts/Poony_Multiretrieval_S1/src/` are unmodified — previous print migrations and `serialize_stencil` were lost (those edits were on the old `src/scripts/` copies that got moved/deleted)

**Changes required:**
1. `src/tools/__init__.py` created (was missing — `src.tools.*` imports wouldn't resolve)
2. `src/__init__.py` `__path__` extended with third entry: `scripts/Poony_Multiretrieval_S1/src/`
   - Priority: `src/` → `src/scripts/` → `src/scripts/Poony_Multiretrieval_S1/src/`
   - PSOAS-modified copies shadow PMS1 originals (config.py, stencil2chart.py)
   - PMS1-only modules (sekei, orchestrator, pto, stencil, index_store, ingest) resolve from the deepest path
3. `execute_tool.py` lazy imports: `src.tool_pms1` → `src.tools.tool_pms1`, `src.tool_pteca` → `src.tools.tool_pteca`
4. Re-applied all 3 print migrations + `serialize_stencil` to PMS1 originals at `Poony_Multiretrieval_S1/src/`

**Doubt:** `Poony_Multiretrieval_S1/src/__init__.py` exists in that directory. When Python scans `__path__`, does it re-execute that `__init__.py`?
- **Verify:** No. `__path__` entries are just search directories for submodule lookup. Python doesn't re-import `__init__.py` from additional `__path__` entries — the `src` package is already initialized from `src/__init__.py`.
- **Confirmed safe.**

**Doubt:** Two `config.py` files — `src/scripts/config.py` (modified, has `get_llm_profile`) and `Poony_Multiretrieval_S1/src/config.py` (original). Which wins?
- `src/scripts/` is earlier in `__path__` → modified version wins. ✓
- PMS1 internal imports (`from src.config import Config`) also get the modified version. ✓

**Path references:** `Poony_Multiretrieval_S1/src/sekei.py` uses `Path(__file__).resolve().parent.parent / "hardcode_dependencies"`. Since `__file__` = `.../Poony_Multiretrieval_S1/src/sekei.py`, `parent.parent` = `.../Poony_Multiretrieval_S1/` which has `hardcode_dependencies/`. ✓ No symlink needed for PMS1 files.

**Decision:** All 4 changes applied. Import chain verified with smoke tests. The `src/hardcode_dependencies` symlink from D1 is still needed for `src/scripts/config.py` (which uses `parent.parent = src/`).

---

## D9: Config path defaults — `Path("./data")` resolves relative to cwd, not project

**Forensics:**
- PMS1 launched via harness: `psoas.py` → `run_harness()` → `execute_tool("run_pms1")` → `tool_pms1.py` → `Config.from_env()`
- `Config.data_dir` defaulted to `Path("./data")` — relative to cwd
- `Config.validate()` silently `mkdir`s missing dirs (line 86-91), so wrong cwd creates an empty `data/` elsewhere
- `scan_files` then finds 0 files → "No documents found in data/. Index will be empty."
- Three `Config` path fields affected: `data_dir`, `index_dir`, `output_dir`

**Hypothesis 1:** Fix in `Poony_Multiretrieval_S1/src/config.py` (PMS1's own config).
- **Verify:** Added `_PROJECT_ROOT = Path(__file__).resolve().parent.parent` and used `_PROJECT_ROOT / "data"` etc.
- **Doubt:** Which `config.py` does the harness actually import?
- **Failure mode:** `src/__init__.py` `__path__` priority (D8): `src/scripts/config.py` shadows `Poony_Multiretrieval_S1/src/config.py`. The fix was applied to the wrong file — the shadowed copy. Harness never sees it.

**Hypothesis 2:** Fix in `src/scripts/config.py` (the PSOAS-modified copy that actually wins import resolution).
- **Verify:** `from src.config import Config` resolves to `src/scripts/config.py` per `__path__` order in D8.
- This is the file the harness uses. `data/` lives at `src/scripts/Poony_Multiretrieval_S1/data/`.
- `_PMS1_ROOT = Path(__file__).resolve().parent / "Poony_Multiretrieval_S1"` → correct absolute path.
- **Doubt:** Does this break PMS1 standalone mode (`app.py`)?
  - No. `app.py` adds `Poony_Multiretrieval_S1/` to `sys.path`, so `from src.config import Config` there imports `Poony_Multiretrieval_S1/src/config.py` (its own copy, fixed separately with `_PROJECT_ROOT`). Two configs, two import contexts, no conflict.

**Decision:** Hypothesis 2. Applied `_PMS1_ROOT` anchor in `src/scripts/config.py`. Also applied `_PROJECT_ROOT` anchor in `Poony_Multiretrieval_S1/src/config.py` for standalone mode. Both copies now use absolute paths derived from `__file__`.

**Files changed:**
- `src/scripts/config.py`: `_PMS1_ROOT = Path(__file__).resolve().parent / "Poony_Multiretrieval_S1"`, all three path defaults use it
- `src/scripts/Poony_Multiretrieval_S1/src/config.py`: `_PROJECT_ROOT = Path(__file__).resolve().parent.parent`, all three path defaults use it
- `src/scripts/Poony_Multiretrieval_S1/app.py`: `--data-dir`/`--index-dir` default to `None`, resolved against `_project_root` when not explicitly passed

---

## D10: Matplotlib NSWindow crash — thread-unsafe macOS backend

**Forensics:**
- `stencil2chart()` runs inside `ThreadPoolExecutor` worker thread (dispatched by `_dispatch_parallel` in agent_loop)
- Matplotlib's default macOS backend (`macosx`) calls `NSWindow initWithContentRect:` which Apple requires on the main thread
- Worker thread → `NSInternalInconsistencyException` → `abort()`
- We never display interactive plots — only save SVGs to disk

**Decision:** `matplotlib.use("Agg")` before any pyplot import in `stencil2chart.py`. Agg = Anti-Grain Geometry, headless raster renderer. Writes files without touching AppKit. Thread-safe. No GUI needed for SVG output.

---

## D11: EOF detection through terminal_router — spec vs implementation gap

**Forensics:**
- Spec 11 pseudo-code shows `except EOFError` around `orchestrator_out.input("")`
- `ToolChannel.input()` → `_router.input()` → puts request in `_input_queue`, blocks on `AnswerSlot.wait()`
- EOF (Ctrl+D) is caught inside `_ui_loop` (terminal_router.py:158-175), which sets `_is_dead = True`, returns `""` to the slot, then exits the UI thread
- `ToolChannel.input()` never raises `EOFError` — it returns `""`
- If `_is_dead` is True, subsequent `_router.input()` calls return `""` immediately (terminal_router.py:103-104)
- Without detection: EOF → returns "" → empty-input hint → loop calls input() → returns "" instantly → hint → infinite loop

**Hypothesis 1:** Modify `ToolChannel.input()` to raise EOFError when `_is_dead`.
- **Doubt:** Changes terminal_router.py (spec 11 says "no new files", only agent_loop + psoas modified). Also breaks tool threads that call `.input()` during parallel dispatch — they'd need try/except too.
- **Failure mode:** Every tool's `.input()` call would need exception handling. Spec 09 (ask_user) doesn't anticipate this.

**Hypothesis 2:** Return a sentinel string (e.g. `"__EOF__"`) from `input()` when dead.
- **Doubt:** Fragile. User could theoretically type `"__EOF__"`. Coupling by magic string.

**Hypothesis 3:** Check `_router._is_dead` after getting empty string from `input()`.
- **Verify:** `_router` is already imported in agent_loop.py (`from src.harness.terminal_router import register, console, _router`). `_is_dead` is a simple bool attribute.
- **Doubt:** Accessing private `_is_dead` from another module. But `_router` itself is already accessed this way (D6 precedent).
- **Failure mode:** Race condition? No — EOF is a terminal state. Once `_is_dead = True`, it never reverts. And the REPL prompt only runs when no tools are executing (outer loop, not inner loop), so no concurrent mutation.

**Decision:** Hypothesis 3. After `orchestrator_out.input("")` returns, check `if not user_input.strip() and _router._is_dead: break`. Distinguishes EOF (break outer loop) from empty input (print hint, continue). Consistent with D6 precedent of accessing `_router` internals.

---

## D12: Transcript enrichment — choke point capture for tool logs + stored vars

**Forensics:**
- Transcript only captured stream C (tool result strings sent to LLM). Two streams missing:
  - Stream A: tool operational logs (`channel.print()` calls in PMS1, PTECA, S2C)
  - Stream B: stored variable data (`registry.store()` calls — stencils, chart_inputs)
- All tool output funnels through two choke points: `ToolChannel.print()` and `registry.store()`. No per-tool changes needed.

**Hypothesis:** Add capture buffers at each choke point. Harvest after tool dispatch. Pass to `_dump_turn`.

**Stream A — ToolChannel._log:**
- Added `_log: list[str]` to `ToolChannel.__init__`. `print()` appends msg.
- `harvest_logs()` iterates all registered channels, returns `{label: [msgs]}`, clears all logs.
- **Doubt — ORCHESTRATOR channel:** `orchestrator_out.print()` also goes through `ToolChannel.print()`. Would duplicate LLM text (already in transcript as response content).
  - **Resolution:** `harvest_logs()` skips `ORCHESTRATOR` label in return dict, but still clears its `_log` to prevent unbounded growth.
- **Doubt — Thread safety:** Each tool call gets a unique channel (PMS1-BestBuy, PTECA-Amcor, S2C-1). No concurrent writes to same `_log`. Harvest runs from main thread after `ThreadPoolExecutor` context manager exits (all threads done). No lock needed.
- **Doubt — Channel reuse across follow-ups:** `register()` is idempotent. Second `run_pms1("Best Buy")` gets same channel. But `harvest_logs()` clears `_log` after each turn, so no stale data bleeds across turns.

**Stream B — registry._recent:**
- Added `_recent: list[tuple[str, str, Any]]` to `OpaqueRegistry`. `store()` appends `(handle, desc, data)` inside existing `_lock`.
- `harvest_recent()` drains list under lock, returns batch. Called from main thread after dispatch.
- **Doubt — Thread safety:** `store()` already holds `self._lock` (RLock). `_recent.append()` added inside that critical section. Parallel tool threads contend on the lock but don't corrupt state.
- **Doubt — Data reference vs copy:** `_recent` stores same reference as `_registry`. Not copied. Transcript dump (`json.dumps`) happens immediately after harvest, before any mutation opportunity. Safe.

**Wiring — harvest at call site, not inside dispatch:**
- `_dispatch_parallel` stays unchanged (returns only `tool_results`).
- `harvest_logs()` and `registry.harvest_recent()` called in agent_loop after dispatch returns, before `_dump_turn`.
- **Why not inside `_dispatch_parallel`:** Separation of concerns. Dispatch just dispatches. Transcript concerns stay in agent_loop.
- **Timing verified:** `ThreadPoolExecutor.__exit__` waits for all futures → all `channel.print()` and `registry.store()` calls completed → harvest captures exactly this turn's data.

**_dump_turn changes:**
- New optional params: `tool_logs: dict | None = None`, `stored_vars: list | None = None`.
- Existing call sites (end_turn, max_tokens) pass neither → defaults to None → guard clauses skip.
- Tool_use call site passes both → transcript gets full audit trail.

**Decision:** Implemented as described. Three files changed: `terminal_router.py` (ToolChannel._log + harvest_logs), `opaque_registry.py` (_recent + harvest_recent), `agent_loop.py` (_dump_turn + call site wiring). Zero changes to tool code (tool_pms1.py, tool_pteca.py, stencil2chart.py).

---

## D13: PMS1 internal modules hardcode bare "PMS1" channel — logs not grouped by firm

**Forensics:**
- `orchestrator.py:36`, `pto.py:35`, `stencil.py:20` all do `_pto_out = register("PMS1")` at module import time
- `tool_pms1.py` creates firm-specific channel `register(f"PMS1-{firm}")` and passes to `run_pms1_pipeline`
- Internal modules ignore the passed channel, print to bare "PMS1" channel
- Transcript shows bare "PMS1" section with pto_judge logs + warnings, separate from "PMS1-Best Buy" section
- PTECA does NOT have this problem — self-contained, uses `ch` directly

**Hypothesis 1:** Thread channel through function signatures (orchestrator.run, pto internals, serialize_stencil).
- **Doubt:** Violates "no changes to internal logic" from D8. Invasive — many function signatures change across 3 files deep in the PMS1 pipeline.

**Hypothesis 2:** Thread-local channel override.
- **Verify:** Each tool call runs in its own worker thread via `_dispatch_parallel` → `ThreadPoolExecutor`. Thread-local state is isolated per thread.
- `override_channel("PMS1", ch)` in `run_pms1_pipeline` before calling pipeline. Internal modules call `_pto_out.print(msg)` → `ToolChannel.print()` checks `_get_override(self)` → finds override for label "PMS1" → redirects to "PMS1-Best Buy" channel.
- `clear_override("PMS1")` in `finally` block.
- **Doubt — Recursion:** Could "PMS1-Best Buy" channel itself be overridden? No. Override matches exact label "PMS1". "PMS1-Best Buy" label doesn't match.
- **Doubt — Thread safety:** `threading.local()` guarantees per-thread isolation. Two parallel PMS1 calls (Best Buy + Amcor) each set their own thread-local override. No cross-contamination.
- **Doubt — _log target:** When overridden, `msg` appends to the TARGET channel's `_log` (e.g. "PMS1-Best Buy"), not the source channel's. `harvest_logs()` then groups these logs correctly under "PMS1-Best Buy". Bare "PMS1" channel's `_log` stays empty.

**Decision:** Hypothesis 2. Added `_thread_overrides = threading.local()`, `override_channel()`, `clear_override()`, `_get_override()` to terminal_router.py. Modified `ToolChannel.print()` and `ToolChannel.input()` to check overrides. Wrapped `run_pms1_pipeline` body in `override_channel/clear_override`. Zero changes to PMS1 internal modules (orchestrator.py, pto.py, stencil.py).

---

## D14: PTECA v2 — multi-stencil, always-ask, comparison charts

**Forensics:**
- PTECA v1 accepted one stencil per call. Orchestrator called it once per firm → no comparison charts possible.
- PTECA v1 auto-finalized when query was clear ("If the user's intent is clear, skip pteca_ask_user"). User wanted interactive chart planning always.
- User wants ASCII chart previews in options, custom layout input, iterative confirm cycle.

**Changes — contract:**
- `run_pteca(stencil, query, firm)` → `run_pteca(stencils, query)`. `firm` dropped — embedded in each stencil's `"firm"` field.
- `finalize` schema: `metrics: [str]` → `metrics: [{firm: str, metric: str}]`. Added explicit `periods: [str]` per chart for period control.
- Orchestrator calls PTECA ONCE with ALL stencil handles, not once per firm.

**Changes — behavior (system prompt):**
- ALWAYS asks user via `pteca_ask_user` before finalizing. Even single-firm.
- Presents 2-3 layout options with ASCII chart sketches (generated by LLM, not code).
- Accepts free-form custom requests. Redraws preview, confirms before finalize.
- Detects period mismatches, offers intersect/pad-0/gap options.
- MAX_TURNS bumped 4 → 8 for extra round-trips.

**Changes — _resolve_handles (execute_tool.py):**
- Added `elif isinstance(value, list)` branch to resolve `$var_N` items within arrays.
- **Doubt:** Could break other tools? No — no other tool sends lists. Safe.

**Changes — _exec_pteca (execute_tool.py):**
- Extracts firms from stencil dicts: `[s["firm"] for s in stencils]`.
- Channel label: `"PTECA-" + "+".join(firms)` (e.g. "PTECA-Best Buy+Boeing").
- **Doubt:** `s["firm"]` exists? Verified — `serialize_stencil` always includes `firm` field.

**Changes — _build_chart_inputs (tool_pteca.py):**
- Multi-stencil lookup: `{firm: {metric: row}}` index.
- Period alignment: for each chart's explicit periods, looks up values per firm. Missing periods padded with 0 (user-approved dummy value for plotting).
- Legend names: multi-firm → "Firm - Metric". Single-firm → bare metric name.
- **Doubt — stencil2chart with 0 padding:** 0 is a valid numeric value, matplotlib plots it normally. Not a gap, but a deliberate dummy. Acceptable per user's instruction.

**Changes — orchestrator (system_prompt.py):**
- `run_pteca` schema: `stencil: str` → `stencils: [str]`, dropped `firm`.
- Workflow step 3: "run_pteca ONCE with ALL stencils". Explicit instruction not to pre-decide chart layout.
- Example updated to show multi-firm call.

**Files changed:** `tool_pteca.py` (rewrite), `execute_tool.py` (_resolve_handles + _exec_pteca), `system_prompt.py` (schema + prompt).

---

## D15: Session-scoped file I/O tools — write_session_md + read_session_md

**Forensics:**
- Orchestrator had no file I/O capability. Couldn't write reports, embed chart SVG refs in MDs, or dump variable data into documents.
- User wants session-scoped markdown only — not arbitrary filesystem access.

**Design — write_session_md:**
- Params: `filename` (relative to session_dir), `content` (markdown string), `mode` ("write"|"append").
- `{{embed:$var_N}}` markers in content are resolved via `re.sub` → `registry.resolve()` → JSON code block in-place. Orchestrator controls exact placement without seeing the full JSON (no context bloat).
- **Doubt — `_resolve_handles` collision:** Could `_resolve_handles` accidentally resolve something in the params? No. `filename` doesn't start with `$var_`. `content` doesn't start with `$var_` (it's a full markdown string). `mode` is "write"/"append". None trigger resolution.
- **Doubt — `{{embed:...}}` regex safety:** Pattern `\{\{embed:(\$var_\d+)\}\}` is non-greedy by anchoring on `\}\}`. Two markers in one string resolve independently. Verified.
- **Path traversal safety:** `(session_dir / filename).resolve()` then `is_relative_to(session_dir.resolve())`. Catches `../../etc/passwd`. Python 3.11 — `is_relative_to` available.

**Design — read_session_md:**
- Params: `filename` (relative to session_dir).
- Returns file content as string. Same path traversal check.
- **Doubt — context bloat from large reads:** Files are session-scoped (written by orchestrator itself). Bounded size. Acceptable.

**Decision:** Two tools, two handlers in execute_tool.py, two entries in dispatch table, two schemas in TOOL_DEFINITIONS. No new files.
