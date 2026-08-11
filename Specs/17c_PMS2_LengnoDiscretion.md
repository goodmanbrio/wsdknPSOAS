# M4 Leng/Validator Failure Modes & Failsafes

Every failure mode found during M4 integration testing. Each
entry: what broke, why, how it was fixed, what it looks like
in production if the fix is removed.

---

## 1. Haiku omits `found` field → all hits silently dropped

**Severity**: Critical — 0% extraction rate

**Symptom**: 25 Leng calls against LITE earnings release, 0
hits. Revenue literally "$808.4 million" in chunk 0.

**Root cause**: LENG_OUTPUT_SCHEMA requires `found: boolean`.
Haiku frequently returns `{"cells": {"A1": {...}}}` without
`found`. Code defaulted `found = leng_output.get("found", False)`
→ always False → all hits discarded before Validator spawn.

**Fix** (`leng_caller.py`):
```python
found = leng_output.get("found", bool(leng_cells))
```
Infer hit from non-empty `cells` map. Matches spec: "If
`found: true` but `cells: {}`, treat as miss."

**Without fix**: Pipeline completes with 0 cells filled.
No errors, no crashes. Silent total failure.

---

## 2. Verbose Leng prompt → Haiku over-cautious

**Severity**: Critical — 0% hit rate even after fix #1

**Symptom**: With `found` fallback, still 0 hits. Haiku
returns `{"cells": {}, "found": false}` on chunks with
obvious financial data.

**Root cause**: Original prompt was 2800 chars of detailed
rules. The "Anti-hallucination: When unsure, report
`found: false`. A miss is cheap; a wrong value is expensive."
instruction made Haiku interpret uncertainty as reason to
skip everything. Haiku has limited attention on cheap
extraction calls — verbose rules cause paralysis.

**Fix** (`sysprompts/pms2_leng/anthropic_hayasui.md`):
Shortened to ~1200 chars. Direct, imperative, concrete
examples. No editorializing about costs.

Before: 2800 chars → 0/25 hits
After: 1200 chars → 6/25 hits

**Without fix**: 0% extraction rate. Pipeline produces
empty stencils.

---

## 3. No `tool_choice` → 14% Leng crash rate

**Severity**: Critical — silent data loss at scale

**Symptom**: 5/37 chunks crashed with `"structured_complete:
no tool_use block after 2 attempts"`. Haiku returned text
blocks instead of calling the `structured_output` tool.

**Root cause**: `structured_complete()` in `llm.py` wasn't
setting `tool_choice`. Haiku is free to respond with a text
block instead of calling the tool. The 2-retry logic just
replays the same request — if Haiku decided not to use the
tool on attempt 1, no reason it would on attempt 2.

**Fix** (`llm.py`):
```python
if not web_search:
    kwargs["tool_choice"] = {"type": "tool", "name": "structured_output"}
```
Can't force when `web_search=True` — model needs to call
`web_search` first (FiscalCalResolver is the only caller).

Before: 5/37 crashed (14%), 8 hits
After: 0/37 crashed (0%), 12 hits

**Without fix**: ~14% of chunks silently lost. Each lost
chunk = missed financial data. At 100 chunks per firm,
~14 chunks lost per run. Compounds with multi-firm runs.

---

## 4. Non-canonical denoms → silent rejection

**Severity**: Medium — correct values rejected

**Symptom**: Haiku returns `denom: "million"` instead of
`"mn"`. `_handle_submit_verdicts` rejects with `"unknown
denom 'million'"`. Value was correct, denom label was wrong.

**Root cause**: Spec has `_UNIT_ALIASES` (RMB→CNY) for unit
normalization but no equivalent for denoms. Haiku returns
non-canonical denom strings ~10% of the time: `"million"`,
`"M"`, `"per share"`, `"billions"`, `"percent"`.

**Fix** (`denom_reconcile.py`):
```python
_DENOM_ALIASES = {
    "million": "mn", "millions": "mn", "M": "mn", "m": "mn",
    "billion": "bn", "billions": "bn", "B": "bn", "b": "bn",
    "thousand": "k", "thousands": "k", "K": "k",
    "trillion": "tn", "trillions": "tn", "T": "tn",
    "percent": "%", "percentage": "%", "pct": "%",
    "basis points": "bps", "bp": "bps",
    "per share": "units", "per_share": "units", "unit": "units",
}
```
Applied in `_handle_submit_verdicts` alongside `_UNIT_ALIASES`.

**Without fix**: ~10% of correct extractions rejected.
BP sees rejection in `searched_files`, wastes a retry
iteration re-searching the same file. Worst case: cell
stays null forever because every extraction attempt uses
a non-canonical denom.

---

## 5. Hallucinated cell IDs + prefix recovery

**Severity**: Medium — correct data lost

**Symptom**: Leng returns `{"A1_Revenue_Q3FY2026": {...}}`
instead of `{"A1": {...}}`. Value and metadata are correct.
Cell ID is wrong.

**Root cause**: Haiku invents compound cell IDs by
concatenating the cell ID with metric name and period.
Happens on ~20% of hits. The LENG_OUTPUT_SCHEMA says
`additionalProperties` keyed by cell_id, but Haiku doesn't
restrict keys to the cell IDs it was given.

**Two-layer fix** (`leng_caller.py`):

Layer 1 — filter: drop IDs not in `valid_cell_ids`.
```python
if cid in valid_cell_ids:
    filtered[cid] = v
```

Layer 2 — prefix recovery: if `A1_Revenue_Q3FY2026`
starts with valid ID `A1` followed by non-alphanumeric
char, recover as `A1`.
```python
if (cid.startswith(valid_id)
        and len(cid) > len(valid_id)
        and not cid[len(valid_id)].isalnum()):
    recovered = valid_id
```

Guard: `A11` does NOT recover to `A1` (next char `1` is
alphanumeric — could be a real cell `A11`).

**Without fix (no filter)**: Hallucinated IDs reach Validator
→ Validator passes them through (no valid-ID list to compare)
→ `_handle_submit_verdicts` rejects ("cell not in stencil")
→ wasted API call + polluted `searched_files.rejections`.

**Without fix (filter only, no recovery)**: Correct data
silently dropped. Innolight Chinese extraction test showed
Haiku extracted `value: 6432, denom: "mn", unit: "CNY"` —
completely correct — but used ID `A1_FY2020_Revenue`. Filter
drops it. Cell stays null.

---

## 6. `value=None` → TypeError crash

**Severity**: Critical — kills Validator thread

**Symptom**: `TypeError: unsupported operand type(s) for *:
'NoneType' and 'int'` in `_handle_submit_verdicts` at
`absolute_value = value * factor`.

**Root cause**: Haiku sends `action: "write"` with
`value: null`. Schema says `value: {type: number}` but
`tool_choice` doesn't guarantee schema compliance on
nested fields. `structured_complete` forces the tool call,
not the input validity.

**Fix** (`validator_loop.py`):
```python
if not isinstance(value, (int, float)) or value is None:
    reason = f"non-numeric value: {repr(value)}"
    cell_outcomes.append((cell_id, "rejected", reason))
    continue
```

**Without fix**: Validator thread crashes → caught by
LengCaller `except Exception` → logged as `"⚠ Validator
crashed"` → ALL cells from that Leng hit lost (not just
the bad one).

---

## 7. `value="N/A"` → megabyte garbage written to stencil

**Severity**: Critical — stencil corruption

**Symptom**: `stencil["values"]["A1"]` contains
`"N/AN/AN/A..."` repeated 1,000,000 times (~3MB string).

**Root cause**: Python string multiplication: `"N/A" * 1_000_000`
produces a megabyte string instead of crashing. Code reaches
`absolute_value = value * factor` where `value = "N/A"` and
`factor = 1_000_000`. Python happily multiplies.

**Fix**: Same guard as #6 — `isinstance(value, (int, float))`
catches strings before the multiply.

**Without fix**: Stencil contains garbage. Phase 2 formula
computation tries arithmetic on a string → crash or nonsense.
If the stencil is persisted, it corrupts the JSON file.
Debugging: you'd see a 3MB `work_stencil.json` and wonder
what happened.

---

## 8. Missing `value`/`denom`/`unit` keys → KeyError crash

**Severity**: Medium — kills Validator thread

**Symptom**: `KeyError: 'value'` when Haiku sends
`{"cell_id": "A1", "action": "write", "denom": "mn"}` (no
`value` key). Same for missing `denom` or `unit`.

**Root cause**: Code used `verdict["value"]` (dict access)
instead of `verdict.get("value")`. Schema requires these
fields for `action: "write"`, but Haiku doesn't always comply.

**Fix** (`validator_loop.py`):
```python
value = verdict.get("value")
denom = verdict.get("denom")
unit = verdict.get("unit")
```
Plus explicit `None` / type checks before proceeding.

**Without fix**: Same as #6 — Validator thread crash, all
cells from that hit lost.

---

## 9. Missing `verdicts` key / non-list verdicts → crash

**Severity**: Medium — kills Validator thread

**Symptom**: `KeyError: 'verdicts'` when Haiku omits the
key entirely. `TypeError` when `verdicts` is a string.

**Root cause**: Code used `params["verdicts"]`. Haiku can
fumble the top-level schema.

**Fix** (`validator_loop.py`):
```python
verdicts = params.get("verdicts")
if not verdicts or not isinstance(verdicts, list):
    return [], "Error: verdicts missing or not a list"
```

Also guarded `verdict.get("cell_id")` and
`verdict.get("action")` with fallback.

**Without fix**: Crash absorbed by LengCaller try/except,
but all cells from that chunk lost.

---

## 10. Duplicate file entries in BP plan → cell loss

**Severity**: Medium — silent data loss

**Symptom**: BP produces plan with two entries for the
same file: `[{file: "A.md", cells: ["A1"]}, {file: "A.md",
cells: ["A3"]}]`. After LengCaller processes: only `A3`
survives. `A1` silently dropped.

**Root cause**: `plan_entry_cells[fp] = cells` — simple
dict assignment. Second entry overwrites first.

**Fix** (`leng_caller.py`):
```python
if fp in plan_entry_cells:
    for c in cells:
        if c not in plan_entry_cells[fp]:
            plan_entry_cells[fp].append(c)
else:
    plan_entry_cells[fp] = list(cells)
```

**Without fix**: Cells from the first plan entry for a
duplicated file are silently dropped. Those cells never
get searched in that file. If BP doesn't retry, they
stay null.

---

## Verified safe (no fix needed)

| Concern | Why safe |
|---|---|
| TraceBuffer concurrent writes | Has `_lock: Lock`, all mutation methods acquire it |
| `searched_files` concurrent mutation | Written sequentially after ThreadPoolExecutor exits |
| `job_stencil` TOCTOU on reads during writes | Reads hit immutable fields (`rows`, `periods`); `values` only read under `stencil_lock` in compare-and-swap |
| `override_firm_currency` concurrent with extraction | Same-turn guard prevents co-firing with `run_leng_caller` |
| Anthropic client thread safety | `httpx.Client` is thread-safe; shared backend across N threads is fine |
| Rate limiting (configurable concurrency) | `pms2_leng_max_workers` (default 100) and `pms2_validator_max_workers` (default 50) in `config.py`. SDK default 2 retries with exponential backoff; crashes absorbed by LengCaller try/except |
| `structured_complete` truncated output | Leng output ~80 tokens; 3000 max_tokens budget is sufficient. Truncation → no tool_use block → retry → crash → absorbed |
| Validator: unknown action (not "write"/"reject") | Falls through to write path. Schema enum prevents non-canonical actions at API level. Low risk |
| Chinese cross-lingual extraction | Haiku 4.5 reads Chinese natively. No-number chunks correctly return empty. Tested against Innolight 1H21 presentation |
| Validator denom correction | Haiku correctly reads "($ in millions)" and overrides Leng's wrong denom. Tested all 3 scenarios (correct, wrong denom, hallucinated value) |
