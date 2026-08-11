# Spec 19: Sekei Delegates — Move Period/Firm/Granularity Parsing from Orchestrator to Sekei

## Problem

Orchestrator LLM is responsible for parsing firms, periods,
and granularity from user queries and passing them as structured
args to `run_pms2(firms, query, periods, granularity)`. This
fails in practice:

1. **Format mismatch**: Orchestrator produced `"3QFY2025"`
   (starts with "3Q"), `_expand_periods` expects `"Q3FY2025"`
   (starts with "Q3"). The `startswith("Q1","Q2","Q3","Q4")`
   guard misses it → period gets 4x expanded into garbage:
   `"Q13QFY2025"`, `"Q23QFY2025"`, `"Q33QFY2025"`, `"Q43QFY2025"`.
   5 quarters × 4 sub-periods = 20 nonsense period codes.

2. **No format enforcement**: The tool schema description only
   shows annual examples (`['FY2025', 'FY2026', 'FY2027']`).
   No quarterly format spec. LLM freestyles.

3. **No validation**: `_expand_periods` is a routing heuristic,
   not a validator. `_exec_pms2` passes args verbatim. No regex.
   No rejection of malformed strings. Silent garbage-in
   garbage-out.

4. **Architectural mismatch**: The orchestrator decides
   periods/granularity, but Sekei is the agent that actually
   confirms the stencil with the user. Period logic belongs
   where the confirmation happens.

## Solution

Remove `firms`, `periods`, and `granularity` from `run_pms2`
tool schema. Sekei parses all three from the raw query string,
confirms with user in Turn 1, and outputs canonical format
strings via `finalize_stencil`. Python validates format via
regex at finalize time.

The orchestrator becomes a pure dispatcher: recognize "this is
a PMS2 query" and forward.

```
BEFORE:
  Orchestrator → parses firms, periods, granularity
               → calls run_pms2(firms, query, periods, granularity)
               → _expand_periods() prefix heuristic (no validation)
               → Sekei receives expanded periods, trusts blindly

AFTER:
  Orchestrator → calls run_pms2(query)
               → Sekei parses firms, periods, granularity from query
               → Sekei confirms with user in Turn 1
               → finalize_stencil(firms, periods, granularity, rows)
               → Python regex validates period format
               → Python validates period-granularity consistency
```

## Canonical period formats

These are the ONLY valid period strings. Regex:
`^(Q[1-4]|H[12])?FY\d{4}$`

| Granularity | Format | Examples |
|-------------|--------|----------|
| annual | `FY{year}` | `FY2024`, `FY2025` |
| quarterly | `Q{n}FY{year}` | `Q1FY2025`, `Q3FY2026` |
| half | `H{n}FY{year}` | `H1FY2025`, `H2FY2026` |

Consistency rule: all periods must match the declared
granularity. `granularity="quarterly"` + `"FY2025"` → rejected.

## Target UX

```
python src/psoas.py
> LITE primary seg rev (laser?) for fy24-26 + capex + gross margin
[ORCHESTRATOR] PMS2 query, fwding to sekei
[PMS2-Sekei] hi, u want this?
  Firms: LITE (Lumentum)
  Periods: FY2024, FY2025, FY2026
  Granularity: annual
  Currency: USD
  Metrics: Laser Revenue (retrieve), CAPEX (retrieve),
           Revenue (helper), Gross Profit (helper),
           Gross Margin (compute = GP/Rev)
  Sector folders: 0 Optical/
  Confirm? [y / edit]
> y
[PMS2-Sekei] running mapper...
[PMS2-Sekei] stencil preview:
  | # | Metric | Type | Formula | Role | Unit |
  ...
  Confirm? [y / edit]
> y
[PMS2-StencilFinalizer] Stencil finalized: 5 rows × 3 periods = 15 cells.
  3 ans metrics: Laser Revenue, CAPEX, Gross Margin.
  Topo sort OK. Formulas rewritten to Rn notation.
```

---

## File-by-file changes

Read each file BEFORE editing. Trace the call path. Do NOT
guess — verify every downstream consumer.

### 1. `src/harness/system_prompt.py` — run_pms2 tool schema

Remove `firms`, `periods`, `granularity` from properties and
required. Only `query` remains.

**BEFORE:**
```python
"properties": {
    "firms": { ... },
    "query": { ... },
    "periods": { ... },
    "granularity": { ... },
},
"required": ["firms", "query", "periods", "granularity"],
```

**AFTER:**
```python
"properties": {
    "query": {
        "type": "string",
        "description": (
            "Full user query including firms, metrics, "
            "and periods. e.g. 'LITE, Innolight gross margin, "
            "revenue FY2025-FY2027 quarterly'"
        ),
    },
},
"required": ["query"],
```

### 2. `sysprompts/orchestrator/deepseek_v4pro_orchestrator.md`

Delete the "PMS2 extraction parameters" section (lines 77-101
approx). Replace with minimal forwarding instruction. Update
examples.

**DELETE** the section starting "Before calling run_pms2,
always confirm firms, periods, and granularity..." through
"After user confirms, call run_pms2 with structured params."

**REPLACE WITH:**
```markdown
## PMS2 extraction

run_pms2 takes only a query string. Pass the user's full
request — Sekei handles firm identification, period parsing,
granularity, metric decomposition, and user confirmation
internally. Do not pre-parse firms, periods, or granularity.

One call per request. If the user wants mixed granularity
("LITE quarterly, Innolight annual"), Sekei will clarify.
```

**UPDATE example** (approx line 66):
```markdown
  You call: run_pms2(query="Gross Margin, EBITDA for LITE, COHR. FY25-FY27.")
```
Remove the "2 firms extracted" from result description if it
references firms-as-input.

### 3. `src/harness/execute_tool.py` — `_exec_pms2`

Only read `query` from params. Pass to simplified
`run_pms2_pipeline`.

**BEFORE:**
```python
def _exec_pms2(params: dict) -> str:
    firms = params["firms"]
    query = params["query"]
    periods = params["periods"]
    granularity = params["granularity"]
    channel = register("PMS2")
    display_stencils = run_pms2_pipeline(
        firms=firms, query=query, periods=periods,
        granularity=granularity, session_dir=..., ...
    )
```

**AFTER:**
```python
def _exec_pms2(params: dict) -> str:
    query = params["query"]
    channel = register("PMS2")
    display_stencils = run_pms2_pipeline(
        query=query, session_dir=_session_dir,
        channel=channel, config=_config, debug_dir=_debug_dir,
    )
```

Rest of function (handle loop, return string) unchanged — it
reads firms from `stencil["firm"]`, not from params.

### 4. `src/scripts/PMS2/pms2.py`

**DELETE:** `_expand_periods`, `_Q_PREFIXES`, `_H_PREFIXES`.

**CHANGE `run_pms2_pipeline` signature:**

Remove `firms`, `periods`, `granularity` args. After
`run_sekei` returns, read these from the work stencil.

```python
def run_pms2_pipeline(
    query: str,
    session_dir: Path,
    channel: ToolChannel | None = None,
    config: Config | None = None,
    debug_dir: Path | None = None,
) -> list[dict]:
```

**BEFORE sekei call:**
```python
channel.print(
    f"PMS2 pipeline: {len(firms)} firms, "
    f"{len(expanded_periods)} periods, {granularity}."
)
```

**AFTER:**
```python
channel.print("PMS2 pipeline starting.")
```

**Sekei call — remove firms/periods/granularity args:**
```python
work_stencil, ans_stencil, job_stencils, file_inventories = run_sekei(
    query=query,
    config=config,
    channel=sekei_channel,
    session_dir=session_dir,
    debug_dir=debug_dir,
    top_level_dirs=top_level_dirs,
)
```

**AFTER sekei returns — read from stencils:**
```python
firms = work_stencil["firms"]
expanded_periods = work_stencil["periods"]
granularity = work_stencil["granularity"]
```

**Dispatcher call — remove granularity/periods args:**

`run_dispatcher` reads these from `job_stencil` internally
(see §7 below). Remove from call site.

### 5. `src/scripts/PMS2/sekei_loop.py`

This is the largest change. Multiple sub-changes.

#### 5a. `run_sekei` signature

Remove `firms`, `expanded_periods`, `granularity`. Register
channel as `"PMS2-Sekei"`.

```python
def run_sekei(
    query: str,
    config: Config,
    channel: ToolChannel,
    session_dir: Path,
    debug_dir: Path | None = None,
    top_level_dirs: list[str] | None = None,
) -> tuple[dict, dict, list[dict], dict]:
```

#### 5b. `load_sysprompt` call

Remove `firms`, `periods`, `granularity` kwargs. Only pass
`query`, `top_level_dirs`, `valid_units`.

**BEFORE:**
```python
sys_prompt = load_sysprompt(
    "pms2_sekei", config.pms2_sekei_profile,
    query=query,
    firms=json.dumps(firms),
    periods=json.dumps(expanded_periods),
    granularity=granularity,
    top_level_dirs=json.dumps(top_level_dirs or []),
    valid_units=json.dumps(valid_units),
)
```

**AFTER:**
```python
sys_prompt = load_sysprompt(
    "pms2_sekei", config.pms2_sekei_profile,
    query=query,
    top_level_dirs=json.dumps(top_level_dirs or []),
    valid_units=json.dumps(valid_units),
)
```

**CRITICAL**: `load_sysprompt` uses `str.replace()` for
`{{placeholder}}` and raises `ValueError` if any `{{...}}`
remain unresolved. Extra kwargs are silently ignored. So:
removing kwargs is safe, but removing template vars from the
.md file while keeping kwargs is also safe. The danger is
removing kwargs but leaving `{{firms}}` in the template →
`ValueError` crash.

Update template AND kwargs in lockstep.

#### 5c. Remove `pipeline_firms` and `expanded_periods` closure vars

**DELETE:**
```python
pipeline_firms = firms
```

**UPDATE `_handle_finalize` call** — remove `pipeline_firms`
and `expanded_periods` args:

```python
data, status = _handle_finalize(
    tc.input, file_inventories, session_dir, channel,
)
```

#### 5d. `finalize_stencil` tool schema — add `granularity`

Add `granularity` to SEKEI_TOOLS finalize_stencil schema:

```python
"granularity": {
    "type": "string",
    "enum": ["annual", "quarterly", "half"],
    "description": (
        "Period granularity. Must match period format: "
        "annual=FY{year}, quarterly=Q{n}FY{year}, "
        "half=H{n}FY{year}."
    ),
},
```

Add to `"required": ["firms", "periods", "granularity", "rows"]`.

#### 5e. `_handle_finalize` — new signature + validation

**NEW signature:**
```python
def _handle_finalize(
    params: dict,
    file_inventories: dict,
    session_dir: Path,
    channel: ToolChannel,
) -> tuple[tuple | None, str]:
```

**DELETE** the old firm validation (subset check against
`pipeline_firms`, missing firms warning).

**REPLACE** period validation with:

```python
import re

_PERIOD_RE = re.compile(r"^(Q[1-4]|H[12])?FY\d{4}$")

# --- In _handle_finalize: ---

# Validate granularity
granularity = params.get("granularity")
if granularity not in ("annual", "quarterly", "half"):
    return None, (
        f"Error: granularity must be 'annual', 'quarterly', "
        f"or 'half'. Got: {granularity!r}"
    )

# Validate period format (regex)
for p in submitted_periods:
    if not _PERIOD_RE.match(p):
        return None, (
            f"Error: period '{p}' invalid. "
            f"Use canonical format: FY{{year}} for annual, "
            f"Q{{n}}FY{{year}} for quarterly, "
            f"H{{n}}FY{{year}} for half. "
            f"e.g. Q3FY2025, not 3QFY2025."
        )

# Validate period-granularity consistency
_GRAN_PREFIX = {
    "annual": "",
    "quarterly": "Q",
    "half": "H",
}
expected_prefix = _GRAN_PREFIX[granularity]
for p in submitted_periods:
    prefix_part = p[:p.index("FY")]
    if granularity == "annual" and prefix_part:
        return None, (
            f"Error: granularity='annual' but period '{p}' "
            f"has prefix '{prefix_part}'. Use 'FY{{year}}' only."
        )
    if granularity != "annual" and not prefix_part.startswith(expected_prefix):
        return None, (
            f"Error: granularity='{granularity}' but period "
            f"'{p}' doesn't start with '{expected_prefix}'. "
            f"All periods must match granularity."
        )
```

**ADD** period chronological sort (replaces the old
`[p for p in expanded_periods if p in submitted_periods]`
ordering):

```python
def _period_sort_key(p: str) -> tuple[int, int]:
    """Q3FY2025 → (2025, 3). FY2025 → (2025, 0). H2FY2025 → (2025, 2)."""
    fy_idx = p.index("FY")
    year = int(p[fy_idx + 2:])
    prefix = p[:fy_idx]
    sub = int(prefix[1]) if prefix else 0
    return (year, sub)

periods = sorted(submitted_periods, key=_period_sort_key)
```

**ADD** channel registration for StencilFinalizer. The summary
print at the end of `_handle_finalize` should use a
`"PMS2-StencilFinalizer"` channel:

```python
finalizer_ch = register("PMS2-StencilFinalizer")
finalizer_ch.print(summary)
```

**PASS `granularity`** to `_assign_structure`:
```python
work, ans, jobs = _assign_structure(
    sorted_rows, periods, submitted_firms, granularity
)
```

#### 5f. Firm validation (simplified)

Replace the old `submitted_firms ⊆ pipeline_firms` check with
a simple non-empty check. The mapper already fails if firm
directories don't exist.

```python
submitted_firms = params.get("firms", [])
if not submitted_firms:
    return None, "Error: firms list is empty."
```

Keep the existing per-row firm validation (every row's firm ∈
submitted_firms) — that stays unchanged.

### 6. `src/scripts/PMS2/stencil_topo.py` — store granularity

**`_assign_structure` signature — add `granularity`:**

```python
def _assign_structure(
    sorted_rows: list[dict],
    periods: list[str],
    firms: list[str],
    granularity: str,
) -> tuple[dict, dict, list[dict]]:
```

**Store in `work_stencil`:**
```python
work_stencil = {
    "firms": firms,
    "periods": periods,
    "granularity": granularity,  # NEW
    "col_letters": col_letters,
    ...
}
```

**Store in each `job_stencil`:**
```python
job_stencils.append({
    "firm": firm,
    "periods": periods,
    "granularity": granularity,  # NEW
    "col_letters": col_letters,
    ...
})
```

### 7. `src/scripts/PMS2/dispatcher.py`

**Remove `granularity` and `periods` from `run_dispatcher`
signature.** Read from `job_stencil` internally.

**BEFORE:**
```python
def run_dispatcher(
    firm, job_stencil, file_inventory, granularity, periods,
    channel, config, docstore, file_path_index, debug_dir=None,
) -> dict:
```

**AFTER:**
```python
def run_dispatcher(
    firm, job_stencil, file_inventory,
    channel, config, docstore, file_path_index, debug_dir=None,
) -> dict:
```

**At top of function body:**
```python
granularity = job_stencil["granularity"]
periods = job_stencil["periods"]
```

Rest of function unchanged — it already uses local
`granularity` and `periods` variables.

### 8. `sysprompts/pms2_sekei/anthropic_opushighthink.md`

**DELETE** the "Your inputs (pre-structured, do NOT
re-confirm)" section. Remove `{{firms}}`, `{{periods}}`,
`{{granularity}}` template placeholders.

**ADD** new section:

```markdown
## Your inputs

- **Query**: {{query}}

Parse from this query:
- **Firms**: identify company names/tickers. Cross-check
  against top-level directories (below) for exact matches.
  If a firm has no matching directory, flag to user.
- **Periods**: determine fiscal year range. Output canonical
  format strings ONLY:
  - Annual: `FY{year}` (e.g. `FY2025`)
  - Quarterly: `Q{n}FY{year}` (e.g. `Q3FY2025`)
  - Half-yearly: `H{n}FY{year}` (e.g. `H1FY2025`)
  - **NEVER** use `3QFY2025`, `3Q25`, `FY25`, or any other
    format. `Q3FY2025` is correct, `3QFY2025` is wrong.
  - Year is always 4 digits.
  - Quarter ordering within a fiscal year: Q1 → Q2 → Q3 → Q4.
    "Q3FY2025 through Q3FY2026" = Q3FY2025, Q4FY2025,
    Q1FY2026, Q2FY2026, Q3FY2026.
- **Granularity**: `annual`, `quarterly`, or `half`. Infer
  from context. Default annual unless user specifies otherwise.
  If user requests mixed granularity ("LITE quarterly,
  Innolight annual"), clarify — one granularity per pipeline
  call.

## What YOU decide (with user confirmation)

1. **Firms** — parsed from query, confirmed with user
2. **Periods and granularity** — parsed from query, confirmed
3. **Reporting currency per firm** — propose from domain knowledge
4. **Metric decomposition** — which helper rows and formulas
5. **Sector folder inclusion** — which 0-prefixed folders
6. **The complete stencil** — all rows, all firms, all formulas
```

**UPDATE Turn 1 instructions** to include firms + periods +
granularity in the bundled confirmation:

```markdown
**Turn 1: ask_user** — Bundle all clarifications into ONE question:
- Firms: list parsed firms, cross-reference with directories
- Periods and granularity: list expanded periods, confirm
- Sector folders: ...
- Reporting currencies: ...
- Metric disambiguation: ...
```

**UPDATE `finalize_stencil` instructions** — mention
granularity is now a required field.

### 9. `src/scripts/PMS2/README.md`

Update to reflect new `run_pms2(query)` single-arg interface.

---

## Downstream verification (pre-verified, do NOT skip)

These files read periods/granularity from stencil dicts, NOT
from function args. No changes needed:

| File | How it accesses periods/granularity |
|------|------------------------------------|
| `leng_caller.py` | `job_stencil["periods"]` (line 71) |
| `merge_compute.py` | `work_stencil["periods"]` (line 158) |
| `batch_planner.py` | `job_stencil["periods"]` via cell descriptions |
| `stencil_safe_math.py` | No period/granularity usage |

The ONLY file that receives `granularity`/`periods` as
function args (besides the ones we're changing) is
`_resolve_fiscal_calendar` in `dispatcher.py`. After §7,
dispatcher reads these from `job_stencil` and passes to
`_resolve_fiscal_calendar` — no signature change needed for
that function.

---

## Failure modes to guard against

### FM-1: Template var mismatch (crash)

`load_sysprompt` raises `ValueError` if `{{placeholder}}`
remains unresolved. If sekei sysprompt still has `{{firms}}`
after removing the kwarg → crash on first sekei call.

**Guard**: Update sysprompt template AND `load_sysprompt()`
kwargs in lockstep (§5b + §8). Run unit test that calls
`load_sysprompt("pms2_sekei", ...)` with new kwargs before
proceeding.

### FM-2: Test call sites (5 locations, all TypeError)

| File | Line | Old signature |
|------|------|---------------|
| `test_pms2_unit.py:24` | imports `_expand_periods` | `ImportError` |
| `test_pms2_unit.py:813` | imports `_handle_finalize` | Signature change |
| `test_pms2_unit.py:836,923,942,962,989,1010` | 6× `_handle_finalize(params, pipeline_firms=, expanded_periods=, ...)` | `TypeError` |
| `test_pms2_m1_live.py:205` | `run_sekei(firms=, expanded_periods=, granularity=, ...)` | `TypeError` |
| `test_pms2_m3b_live.py:137` | `run_pms2_pipeline(firms=, periods=, granularity=, ...)` | `TypeError` |
| `test_pms2_m3b_live.py:370` | `run_dispatcher(granularity=, periods=, ...)` | `TypeError` |

### FM-3: Sekei parses periods wrong (silent wrong data)

Sekei LLM might output `"FY24"` (2-digit year), skip a
quarter in a range, or use wrong format. Regex catches format
errors. User confirmation in Turn 1 catches range errors.

Sysprompt must include explicit format examples and FY quarter
ordering explanation.

### FM-4: Missing granularity in stencil (KeyError)

If `_assign_structure` doesn't store `granularity` →
`work_stencil["granularity"]` → KeyError in `pms2.py` and
`dispatcher.py`.

**Guard**: Unit test `_assign_structure` output includes
`granularity` key.

### FM-5: Period sort bug (wrong column order)

Sort function parses `Q3FY2025` → `(2025, 3)`. If parsing
fails on edge case → wrong column ordering.

**Guard**: Unit test sort function with quarterly cross-FY
range.

### FM-6: Stale orchestrator sysprompt (LLM confusion)

If orchestrator sysprompt examples still show
`run_pms2(firms=..., periods=..., granularity=...)`, LLM may
pass extra args. `_exec_pms2` would ignore them (only reads
`params["query"]`), but confusing.

**Guard**: Update examples (§2).

### FM-7: LLM omits granularity in finalize (recoverable)

If Sekei LLM doesn't include `granularity` in
`finalize_stencil` call: Anthropic API rejects (missing
required field) → LLM retries. Explicit None check in
`_handle_finalize` as backup.

---

## Build order

Strict sequence. Do NOT parallelize steps 1-4. Each step must
pass its tests before proceeding.

### Step 1: Unit-testable pure functions (no LLM)

Files: `stencil_topo.py`, `pms2.py` (delete `_expand_periods`)

1. Add `granularity` param to `_assign_structure` in
   `stencil_topo.py`. Store in work_stencil and job_stencils.
2. Delete `_expand_periods`, `_Q_PREFIXES`, `_H_PREFIXES`
   from `pms2.py`.
3. Add `_PERIOD_RE` regex and `_period_sort_key` function to
   `sekei_loop.py` (module-level, used by `_handle_finalize`).

**Unit tests (write first, then implement):**
```
TestAssignStructureGranularity:
  - test_granularity_stored_in_work_stencil
  - test_granularity_stored_in_each_job_stencil

TestPeriodValidation:
  - test_valid_annual_periods
  - test_valid_quarterly_periods
  - test_valid_half_periods
  - test_reject_3Q_format  (the original bug)
  - test_reject_2digit_year
  - test_reject_Q5
  - test_reject_bare_year

TestPeriodGranularityConsistency:
  - test_annual_with_Q_prefix_rejected
  - test_quarterly_with_bare_FY_rejected
  - test_half_with_Q_prefix_rejected

TestPeriodSortKey:
  - test_annual_sort
  - test_quarterly_sort_within_fy
  - test_quarterly_sort_cross_fy
  - test_half_sort

TestLoadSyspromptSync:
  - test_sekei_sysprompt_renders_without_error
    (call load_sysprompt with new kwargs, assert no ValueError)
```

**Run:** `python -m pytest tests/test_pms2_unit.py -x -v`

Delete `TestExpandPeriods` class and its import. Update all 6
`_handle_finalize` call sites in `TestHandleFinalize`.

### Step 2: Signature changes (no LLM, wiring only)

Files: `sekei_loop.py`, `dispatcher.py`, `pms2.py`,
`execute_tool.py`, `system_prompt.py`

1. Update `_handle_finalize` signature (remove `pipeline_firms`,
   `expanded_periods`; add regex validation, granularity
   validation, period sort, consistency check).
2. Update `run_sekei` signature (remove `firms`,
   `expanded_periods`, `granularity`).
3. Update `run_pms2_pipeline` signature (remove `firms`,
   `periods`, `granularity`; read from work_stencil after
   sekei returns).
4. Update `run_dispatcher` signature (remove `granularity`,
   `periods`; read from job_stencil).
5. Update `_exec_pms2` (only read `query`).
6. Update `run_pms2` tool schema in `system_prompt.py`.
7. Register channels: `"PMS2-Sekei"` in run_sekei,
   `"PMS2-StencilFinalizer"` in _handle_finalize.

**Run:** `python -m pytest tests/test_pms2_unit.py -x -v`
(all updated tests must pass)

### Step 3: Sysprompts (text changes)

Files: `sysprompts/pms2_sekei/anthropic_opushighthink.md`,
`sysprompts/orchestrator/deepseek_v4pro_orchestrator.md`,
`src/scripts/PMS2/README.md`

1. Rewrite sekei sysprompt per §8.
2. Update orchestrator sysprompt per §2.
3. Update README per §9.

**Run:** `python -m pytest tests/test_pms2_unit.py -x -v`
(template sync test from Step 1 catches mismatches)

### Step 4: Live test updates

Files: `tests/test_pms2_m1_live.py`,
`tests/test_pms2_m3b_live.py`

1. Update `run_sekei` call in `test_pms2_m1_live.py` to new
   signature. This test feeds pre-scripted answers via
   AutoChannel — the answers need updating since Sekei now
   asks about firms/periods in Turn 1 (previously it didn't).
2. Update `run_pms2_pipeline` and `run_dispatcher` calls in
   `test_pms2_m3b_live.py`.

**Run live tests (requires API keys, already in .env):**
```bash
python tests/test_pms2_m1_live.py
python tests/test_pms2_m3b_live.py
```

These hit real Anthropic/DeepSeek APIs. Expect ~$0.50-1.00
per run. If Sekei fails to parse periods from the demo query,
the sysprompt needs tuning (adjust format examples, not code).

### Step 5: Smoke test (optional, full pipeline)

Run psoas.py interactively with a quarterly query to verify
the original bug is fixed:

```bash
python src/psoas.py
> LITE gross margin Q3FY2025 through Q3FY2026 quarterly
```

Verify: no "Q13QFY2025" garbage. Sekei asks to confirm 5
quarterly periods. finalize_stencil produces clean stencil.
