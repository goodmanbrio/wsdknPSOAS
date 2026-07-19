# 14a — Parallelize PMS Batches: noDiscretion

Decision log for Spec 14 implementation. Records design ambiguities
encountered, forensic reasoning cycles, and final resolutions.

---

## D1: `JudgeStencilResult` placement in pto.py

**Ambiguity:** Where to insert the new dataclass in pto.py — before or
after the judge functions?

**Forensics:** `JudgeStencilResult` references `PTOCellResult` (its
`values` field). `pto_judge_to_stencil` returns it. Both types need
to exist before `pto_judge_to_stencil` is defined (line ~850). If
placed after the judge functions, forward references would fail at
runtime (Python evaluates top-down).

**Verify:** `PTOCellResult` is at line 691. `pto_judge_to_stencil`
is at line 850+. Placing `JudgeStencilResult` right after
`PTOCellResult` (line ~699) satisfies both constraints.

**Resolution:** Inserted after `PTOCellResult`, before the Judge
section. No forward reference issues.

---

## D2: `pto_judge` aggressive JSON extraction — scope of fix

**Ambiguity:** How aggressive should the fallback be? Spec shows
find-outermost-`{…}`. But what about nested JSON, or JSON arrays?

**Forensics:** Judge output is always a flat JSON object keyed by
metric name (one level deep). `text.find("{")` + `text.rfind("}")`
correctly extracts the outermost `{…}` even if the LLM wraps it in
prose. Array output would need `[…]` handling but judge never returns
arrays.

**Doubt:** What if LLM outputs two separate JSON objects? `rfind("}")`
grabs everything from first `{` to last `}` — if there are two
objects, this would produce invalid JSON (`{...}{...}`). But
`json.loads` would fail on that, and the original JSONDecodeError
re-raises. Acceptable: the two-object case is truly garbled.

**Failure mode:** If LLM outputs `{"Revenue": {...}} and also
{"note": "..."}`, extraction gets `{"Revenue": {...}} and also
{"note": "..."}` which fails json.loads. Original error re-raises.
Correct behavior — can't disambiguate two objects.

**Resolution:** Spec-exact implementation. Single `{…}` extraction,
re-raise on failure. Sufficient for the known failure mode
(prose-after-fence from Anthropic thinking models).

---

## D3: `pto_judge_to_stencil` — `.get("source_chunk", -1)` vs `entry["source_chunk"]`

**Ambiguity:** Old code used `entry["source_chunk"]` (KeyError on
missing key). New code uses `.get("source_chunk", -1)`. Is this a
behavioral change?

**Forensics:** If judge LLM marks `sufficient: true` but omits
`source_chunk`, old code would KeyError (crash). New code returns
-1, which fails the `chunk_idx < 0` check, appending to failures
instead of crashing. Strictly better — the cell fails gracefully
instead of killing the pipeline.

**Resolution:** Use `.get("source_chunk", -1)` per spec. Graceful
degradation on malformed judge output.

---

## D4: Orchestrator-level "skip or exit" prompt removed

**Ambiguity:** Old code (orchestrator.py:167-176) prompted "PUMBA
chunks also insufficient. 'skip' to skip batch, 'exit' to abort"
after re-judge failed on PUMBA chunks. In parallel mode, this
interactive prompt can't work — multiple threads would be at
different stages.

**Forensics:** This prompt was the SECOND user interaction for a
failed batch (first being run_pumba's internal "skip or exit" at
exhaustion/max-turns). The old flow was:
  1. PTO fails → PUMBA runs → Dailo exhausts files → "skip or exit"
  2. User chooses "skip" → PUMBA returns empty → orchestrator re-judges → re-judge fails → SECOND "skip or exit"
  
Step 2 is redundant in the new flow because:
- If PUMBA returns empty (user chose "skip"), `_run_pumba_batch`
  returns empty outcome. No re-judge happens. Cell gets NaN in
  Phase 2.5.
- If PUMBA returns chunks but re-judge fails (Exception), cell
  gets NaN in Phase 2.5. No need for second prompt.

**Verify:** run_pumba (pumba.py:754-760, 772-778) already has its
own "skip or exit" prompt. Users still have control.

**Doubt:** Removing the prompt means the user can't abort the
ENTIRE pipeline when a specific batch's re-judge fails. But
`user_exit=True` from run_pumba's "exit" still aborts everything
(Phase 2 checks `any(o.user_exit ...)`). The lost capability is
specifically aborting on re-judge failure, which is a very narrow
edge case (PUMBA found chunks, but judge still garbled JSON).

**Resolution:** Removed the orchestrator-level prompt. NaN filling
handles it. The user can still abort via run_pumba's internal prompt.

---

## D5: PUMBA channel labels — include period + statement

**Ambiguity:** Old code used `PUMBA-{firm}`. With parallel PUMBA,
same firm could have multiple batches running simultaneously (e.g.
FY22 income_statement AND FY22 balance_sheet).

**Forensics:** `register()` is idempotent — same label → same
ToolChannel. Two threads printing to `PUMBA-Best Buy` would
interleave output with the same label. Not a crash, but confusing.

**Verify:** Spec specifies `PUMBA-{firm} {period} {statement}`.
Each batch gets a unique label. TerminalRouter's `_styled_print`
uses the prefix before `-` for style lookup — `PUMBA-Best Buy FY22
income_statement` → prefix `PUMBA` → red style. Correct.

**Resolution:** Labels include period + statement per spec.

---

## D6: PUMBA values overwrite PTO partial successes

**Ambiguity:** If PTO succeeds for cells A1, A2 but fails for A3,
PUMBA re-judges the full batch (all metrics). PUMBA may produce
different values for A1, A2 than PTO did. Should PUMBA overwrite?

**Forensics:** PTO partial successes are merged in the main thread
BEFORE PUMBA runs (Phase 1 merge). PUMBA results merge in Phase 2.
Because Python dict assignment overwrites, PUMBA values naturally
replace PTO values for any overlapping cell_ids.

**Doubt:** PTO's chunk may have been more accurate for A1 than
PUMBA's chunk. By overwriting, we lose PTO's judgment for cells it
deemed sufficient.

**Counter-doubt:** PUMBA runs because PTO failed for SOME cells.
The PUMBA chunk is from a different (presumably better) source.
Consistency within a batch matters — having A1 from one chunk and
A2 from another is more confusing than having all cells from the
same PUMBA-selected chunk.

**Resolution:** PUMBA overwrites per spec. Simpler, and
within-batch consistency is more important than preserving partial
PTO results.

---

## D7: NaN propagation through compute cells

**Ambiguity:** `eval("nan / 100")` would raise `NameError` because
`nan` is not a Python builtin. The spec prescribes injecting `nan`
and `inf` into eval namespace.

**Forensics:** `str(float("nan"))` produces `"nan"`. After cell ref
substitution, a formula like `A3/A1` where A1 is NaN becomes
`nan/51761.0`. Without namespace injection, `eval` raises NameError.

**Verify:** `eval("nan / 100", {"nan": float("nan"), ...})` → `nan`.
Correct. `nan + 51761` → `nan`. `100 / nan` → `nan`. All NaN
arithmetic propagates per IEEE 754. Python's float follows IEEE 754.

**Edge case:** `0.0 / 0.0` → `nan` in Python (not ZeroDivisionError,
because Python floats follow IEEE 754... actually wait, Python
DOES raise ZeroDivisionError for `0.0 / 0.0`). Hmm.

**Re-verify:** Python `0.0 / 0.0` raises ZeroDivisionError.
But `float("nan") / 0.0` also raises ZeroDivisionError. And
`1.0 / 0.0` raises ZeroDivisionError (Python does NOT produce inf
from division by zero — it raises). So `inf` in the namespace is
only useful if a value is explicitly set to inf somewhere, which
shouldn't happen in normal operation.

**Resolution:** Injected both `nan` and `inf` per spec. `inf` is
cheap insurance even if unlikely to be needed. Added
`__builtins__: {}` as eval safety measure (best practice, prevents
arbitrary code execution in formulas).

---

## D8: NaN → None conversion placement in serialize_stencil

**Ambiguity:** Should NaN → None happen before or after the compute
cell percentage conversion (`round(value * 100, 4)`)?

**Forensics:**
- If BEFORE: `value = None`, then `round(None * 100, 4)` raises
  TypeError.
- If AFTER: `value = round(nan * 100, 4)` → `nan` (NaN arithmetic
  propagates), then `value = None`. Correct.

**Resolution:** NaN → None check placed AFTER all value
transformations, immediately before `row_data[row]["values"][col]`
assignment. NaN flows through `round()` naturally.

---

## D9: test_pumba.py — splitting try/except

**Ambiguity:** Old code had one `try` wrapping both `pto_judge` and
`pto_judge_to_stencil`, catching `ValueError`. After change,
`pto_judge_to_stencil` never raises but `pto_judge` still can
(JSONDecodeError, a ValueError subclass).

**Forensics:** Keeping `except ValueError` around just `pto_judge`
preserves the JUDGE_FAIL return for garbled JSON. Then check
`stencil_result.failures` separately for partial extraction failures.

**Doubt:** Should I also update `for cell_id, pcr in batch_values.items():`
downstream? Yes — `batch_values` no longer exists, it's now
`stencil_result.values`.

**Resolution:** Split the try to wrap only `pto_judge`. Check
`stencil_result.failures` after. Updated downstream reference from
`batch_values.items()` to `stencil_result.values.items()`.

---

## D10: gradio_ui.py — except ValueError removal

**Ambiguity:** Removing `except ValueError` means JSONDecodeError
from `pto_judge` (called via `st["judge_future"].result()`) now
falls to `except Exception` → `st["phase"] = "error"`.

**Forensics:** Old behavior: JSONDecodeError → `st["phase"] = "judged"`,
`st["judge_output"] = {}`. This was arguably wrong — the judge
didn't successfully run, so "judged" phase is misleading.
New behavior: → `st["phase"] = "error"`. More correct.

**Side effect:** Old code set `st["judge_output"] = {}` in the
ValueError handler. New code preserves whatever `st["judge_output"]`
was set to. If `pto_judge` raises before `st["judge_output"] = jo`,
the key won't exist. If it raises after (impossible since
`jo = st["judge_future"].result()` is where the raise happens),
it would be set. The raise happens AT `st["judge_future"].result()`,
BEFORE `st["judge_output"] = jo`. So `judge_output` key stays absent.
The except Exception handler doesn't touch it. Downstream code must
handle missing `judge_output` — checking existing gradio_ui code,
`_render_batch_cards` only accesses `st.get("judge_output", {})`.
Safe.

**Resolution:** Removed `except ValueError` per spec. Phase = "error"
is more correct for judge failures.

---

## D11: test_pumba_cli.py — deleted file

**Ambiguity:** Spec lists `test_pumba_cli.py` as needing updates.
Git status shows it as deleted (` D test_pumba_cli.py`).

**Resolution:** Skipped. File no longer exists.
