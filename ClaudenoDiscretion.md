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
