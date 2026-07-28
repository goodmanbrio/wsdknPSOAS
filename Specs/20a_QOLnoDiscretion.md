# 20a discretionary decisions

1. **`channel` vs `leng_ch` for plan table.**
   Called `_print_plan_table(plan, job_stencil, channel)` with the
   dispatcher-level `channel` (label `[PMS2-disp-{firm}]`), not
   `leng_ch`. Rationale: the plan table is a BP routing decision,
   not a Leng execution event. Printing it under `[PMS2-leng-{firm}]`
   would be wrong label context. Spec says this explicitly; I followed it.

2. **`_period_sort_key` imported from `sekei_loop`, not duplicated or moved.**
   Three options: import from `sekei_loop`, duplicate inline, or move
   to `stencil_topo`. Chose import per spec's explicit instruction.
   Moving to `stencil_topo` would be cleaner (it's stencil
   infrastructure, not sekei-specific) but is out of spec scope.
   Coupling `leng_caller` → `sekei_loop` is the accepted tradeoff.

3. **`hit_count` in Leng counter uses truthy `leng_output.get("cells")`.**
   This is a pre-filter approximation — it counts chunks with a
   non-empty cells dict before hallucination filtering and before
   malformed detection. The final "N hits from M Leng calls" line
   uses post-filtered `len(hits)`, so the two numbers can differ.
   Spec explicitly calls this out as intentional; live counter is an
   approximation. I followed spec.

4. **`written_count` increments on both "written" AND "skipped" outcomes.**
   Matches `this_run_found` logic (both go into found). Label says
   "written" which is a slight misnomer for "skipped" outcomes.
   Spec explicitly accepts this: "Label says 'written' which is an
   approximation — acceptable for a live counter."

5. **`done_count` incremented after try/except block.**
   Crashed futures still advance the counter (counter reaches total
   even if some futures throw). Spec explicitly requires this.

6. **`_COUNTER_INTERVAL = 25` hardcoded, not in Config.**
   Spec explicitly rejects Config bloat for this constant: "it's a
   one-line constant edit." No discretion exercised here.

7. **`try/finally` wraps the entire `ThreadPoolExecutor` block (both submit loop and as_completed loop).**
   `start_spinner` is called before `with ThreadPoolExecutor`, and
   `stop_spinner` is in `finally`. This means the spinner stops even
   if submit throws (extremely unlikely). Ensures no spinner leak.
   Spec showed this structure; I matched it exactly.
