# Parallelize PMS1 Batches

Run PTO batches in parallel, collect all cell failures (no early-exit),
then run PUMBA Dailo in parallel for all failed batches.

## Architecture

### Before (current — sequential)

```
orchestrator.run()
│
│  for batch in plan.batches:     ← sequential
│      retrieve(batch)
│      judge(batch)
│      parse(batch)               ← raises ValueError on FIRST bad cell
│          │
│          └── PUMBA(batch)       ← blocks next batch
│              re-judge
│              parse
│  next batch...
│
│  compute_stencil(all_values)
```

### After (target — two-phase parallel)

```
orchestrator.run()
│
│  Phase 1: PTO — all batches in parallel
│  ════════════════════════════════════════
│  ThreadPoolExecutor(max_workers=len(batches))
│    ├── batch A: retrieve → judge → check ALL cells
│    ├── batch B: retrieve → judge → check ALL cells
│    └── batch C: retrieve → judge → check ALL cells
│
│  Collect:
│    ok_values    = {A1: CellResult, A2: CellResult, ...}
│    failed_info  = [
│      BatchFailure(batch=B, succeeded={B2: val}, failed=[(B1,"Revenue"),(B3,"COGS")], judge_output={...}),
│      BatchFailure(batch=C, succeeded={}, failed=[(C1,"Revenue")], judge_output={...}),
│    ]
│
│  Phase 2: PUMBA — all failed batches in parallel
│  ════════════════════════════════════════════════
│  ThreadPoolExecutor(max_workers=len(failed_info))
│    ├── Dailo for batch B (FULL batch request — all metrics)
│    └── Dailo for batch C (FULL batch request — all metrics)
│
│  Re-judge each, parse, merge
│
│  Phase 3: merge + compute_stencil
│  ════════════════════════════════
│  all_values = ok_values | pumba_values
│  compute_stencil(plan, all_values)
```

### Why PUMBA gets the full batch (not just failed cells)

Batches are grouped by statement + period. A correct chunk should
contain ALL metrics in the batch. PUMBA searches for better chunks,
not individual cell patches. Sending the full batch:
- Gives Dailo the full context to find the right filing section
- Allows the re-judge to cross-validate all metrics from the same chunk
- Preserves the original judge output alongside PUMBA's, enabling
  future PTO-vs-PUMBA cross-reference per cell

## Input contract

### orchestrator.run() — unchanged signature

```python
def run(
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
) -> dict[str, CellResult]:
```

No external API change. Parallelism is internal.

### pto_judge_to_stencil() — new return type

```python
# NEW dataclass
@dataclass
class JudgeStencilResult:
    """Full judge parse result — partial successes + all failures."""
    values: dict[str, PTOCellResult]   # cells that succeeded
    failures: list[tuple[str, str]]    # [(cell_id, metric), ...] that failed
    judge_output: dict                 # raw judge JSON (preserved for cross-ref)
```

New signature:

```python
def pto_judge_to_stencil(
    judge_output: dict,
    cell_map: dict[str, str],
    result: PTOBatchResult,
) -> JudgeStencilResult:
```

**Breaking change:** Currently returns `dict[str, PTOCellResult]` and
raises `ValueError` on failure. New version returns `JudgeStencilResult`
and never raises — failures are in `result.failures`.

ALL callers must switch from `try/except ValueError` to checking
`result.failures`:

| File | Lines | Context |
|---|---|---|
| `orchestrator.py` | 103, 106, 159, 162 | PTO + PUMBA fallback paths |
| `gradio_ui.py` | 401, 411 | Gradio web UI (production code!) |
| `eval/test_sekei_orchestrate_pto_stencil.py` | 170, 181 | Integration test |
| `eval/test_pumba.py` | 253, 256 | PUMBA test |
| `test_pumba_cli.py` (project root) | 123, 126 | CLI test |

Before:
```python
try:
    batch_values = pto_judge_to_stencil(...)
except ValueError:
    # handle failure
```
After:
```python
stencil_result = pto_judge_to_stencil(...)
if stencil_result.failures:
    # handle failure
batch_values = stencil_result.values
```

### run_pumba() — unchanged signature

```python
def run_pumba(
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    config: Config,
    channel: ToolChannel | None = None,
) -> list[str]:
```

Receives the FULL batch request (all metrics). No trimming.

## Output contract

### orchestrator.run() — unchanged

Returns `dict[str, CellResult]`. Same as before. Parallelism is
transparent to callers.

### Phase 1 results

```python
@dataclass
class BatchOutcome:
    """Result of one PTO batch attempt."""
    batch: SekeiBatch
    request: PTOBatchRequest
    cell_map: dict[str, str]
    stencil_result: JudgeStencilResult   # partial success + failures
    pto_result: PTOBatchResult | None    # retrieval output (for re-judge)
    error: Exception | None              # non-ValueError crash (judge JSON garbled, etc.)
    user_exit: bool = False              # True if user chose "exit" in PUMBA prompt
```

Phase 1 produces `list[BatchOutcome]`. Orchestrator partitions into:
- `ok`: `outcome.stencil_result.failures == []` and `outcome.error is None`
- `needs_pumba`: `outcome.stencil_result.failures != []`
- `crashed`: `outcome.error is not None` (judge garbled JSON, etc.)

### Phase 2 results

PUMBA returns `list[str]` (node_ids) per failed batch, same as today.
Re-judge produces another `JudgeStencilResult`. If still has failures →
user prompt ("skip or exit") via TerminalRouter stdin broker.

## Dependencies

| Component | Role | Thread-safety |
|---|---|---|
| `VectorStoreIndex` | Read-only during PTO/PUMBA | Safe — dict reads are GIL-protected |
| `Config` | Read-only | Safe — frozen after construction |
| `TerminalRouter` | Print + input for all threads | Safe — `_input_queue` + `_lock` + `_ui_loop` already serializes stdin. Multiple threads can call `ch.print()` and `ch.input()` concurrently. Built for this. |
| `LLMBackend` instances | One per thread (factory creates new) | Safe — no shared state |
| `register()` | Creates ToolChannel per label | Safe — each batch gets unique label |

## File locations

```
MODIFIED:
  src/scripts/Poony_Multiretrieval_S1/src/pto.py
      - Add JudgeStencilResult dataclass
      - Change pto_judge_to_stencil() to return JudgeStencilResult
        instead of raising ValueError on first failure
      - Harden pto_judge() JSON parsing: aggressive extraction
        of outermost {…} from LLM preamble/epilogue noise

  src/scripts/Poony_Multiretrieval_S1/src/orchestrator.py
      - Add BatchOutcome dataclass (local to this module)
      - Replace sequential for loop with two-phase ThreadPoolExecutor
      - Phase 1: parallel PTO
      - Phase 2: parallel PUMBA for failed batches
      - Phase 3: handle missing cells, then compute_stencil

  src/scripts/Poony_Multiretrieval_S1/src/gradio_ui.py
      - Update pto_judge_to_stencil caller (lines 397-418):
        - bv = pto_judge_to_stencil(...) → sr = pto_judge_to_stencil(...)
        - bv.items() → sr.values.items()
        - Remove except ValueError clause (no longer raised)
        - Add if sr.failures: check after merge (sets st["error"])
        - pto_judge JSONDecodeError now falls to except Exception
          → phase="error" instead of old "judged" (more correct)
        - Partial values preserved when some metrics fail

  eval/test_sekei_orchestrate_pto_stencil.py
      - Update pto_judge_to_stencil caller (line 170):
        try/except ValueError → check stencil_result.failures

  eval/test_pumba.py
      - Update pto_judge_to_stencil caller (line 253):
        try/except ValueError → check stencil_result.failures

  test_pumba_cli.py
      - Update pto_judge_to_stencil caller (line 123):
        try/except ValueError → check stencil_result.failures

UNCHANGED:
  src/scripts/Poony_Multiretrieval_S1/src/pumba.py
      - run_pumba() signature unchanged
      - Each Dailo instance is self-contained
  src/harness/terminal_router.py
      - Stdin broker already built (_input_queue + _ui_loop)
      - No changes needed
  src/scripts/llm.py
      - Factory functions create new backend per call
      - Thread-safe by design (new instance per factory call, no caching)
  src/scripts/Poony_Multiretrieval_S1/src/sekei.py
      - Runs before parallelism, produces plan
```

## Implementation

### pto.py changes

```python
# ── MODIFIED pto_judge — aggressive JSON extraction ─────────────

def pto_judge(
    request: PTOBatchRequest,
    result: PTOBatchResult,
    config: Config,
) -> dict:
    """Judge LLM call: extract values from retrieved chunks.

    One call per batch. Returns parsed JSON dict keyed by metric name.
    Strips markdown fences and preamble/epilogue prose before parsing.
    """
    import json
    import re
    from src.llm import get_pto_judge_llm

    user_prompt = _build_judge_user_prompt(request, result)
    llm = get_pto_judge_llm(config)
    raw, usage = llm.complete_with_usage(user_prompt, system_prompt=_build_judge_system())

    if usage:
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        _pto_out.print(f"[pto_judge] batch={request.batch_id} tokens in={inp} out={out}")

    # Parse JSON — strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Aggressive extraction: LLM may wrap JSON in prose
        # ("Sure! Here are the results: {...}")
        # Find outermost {…} and try again
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise  # truly garbled — re-raise original


# ── NEW dataclass (after PTOCellResult) ──────────────────────────

@dataclass
class JudgeStencilResult:
    """Full judge parse — partial successes + all failures."""
    values: dict[str, PTOCellResult]
    failures: list[tuple[str, str]]   # [(cell_id, metric), ...]
    judge_output: dict                # raw judge JSON


# ── MODIFIED pto_judge_to_stencil ────────────────────────────────

def pto_judge_to_stencil(
    judge_output: dict,
    cell_map: dict[str, str],
    result: PTOBatchResult,
) -> JudgeStencilResult:
    """Map judge output to cell-keyed results.

    Does NOT raise on failure. Collects all failures and returns
    them alongside partial successes.
    """
    values: dict[str, PTOCellResult] = {}
    failures: list[tuple[str, str]] = []

    judge_lower = {k.lower(): v for k, v in judge_output.items()}

    for cell_id, metric in cell_map.items():
        entry = judge_lower.get(metric.lower())

        if entry is None:
            failures.append((cell_id, metric))
            continue

        if not entry.get("sufficient"):
            failures.append((cell_id, metric))
            continue

        chunk_idx = entry.get("source_chunk", -1)
        if chunk_idx < 0 or chunk_idx >= len(result.top_chunks):
            failures.append((cell_id, metric))
            continue

        chunk = result.top_chunks[chunk_idx]
        node_id = chunk.node.node_id

        values[cell_id] = PTOCellResult(
            value=float(entry["answer"]),
            source=node_id,
            denomination=entry.get("denomination", ""),
            unit=entry.get("unit", ""),
        )

    return JudgeStencilResult(
        values=values,
        failures=failures,
        judge_output=judge_output,
    )
```

### orchestrator.py changes

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
# Add to existing imports:
#   from src.sekei import SekeiPlan, SekeiBatch
#   from src.pto import ..., JudgeStencilResult

# ── Phase 1: parallel PTO ────────────────────────────────────────

def _run_pto_batch(
    batch: SekeiBatch,
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
) -> BatchOutcome:
    """One PTO lane: retrieve → judge → parse. Thread-safe."""
    metrics = [plan.cells[cid].metric for cid in batch.cells]
    request = PTOBatchRequest(
        batch_id=batch.id,
        firm=plan.firm,
        period=batch.period,
        statement=batch.statement,
        metrics=metrics,
        retrieve_target=batch.retrieve_target,
    )
    cell_map = {cid: plan.cells[cid].metric for cid in batch.cells}

    try:
        pto_result = pto_retrieve(request, index, config)
        judge_output = pto_judge(request, pto_result, config)
        stencil_result = pto_judge_to_stencil(
            judge_output, cell_map, pto_result
        )
        return BatchOutcome(
            batch=batch, request=request, cell_map=cell_map,
            stencil_result=stencil_result, pto_result=pto_result,
            error=None,
        )
    except Exception as e:
        # Judge JSON garbled, API error, etc.
        return BatchOutcome(
            batch=batch, request=request, cell_map=cell_map,
            stencil_result=JudgeStencilResult({}, [], {}),
            pto_result=None, error=e,
        )


def run(
    plan: SekeiPlan,
    index: VectorStoreIndex,
    config: Config,
) -> dict[str, CellResult]:
    all_values: dict[str, CellResult] = {}

    # ── Phase 1: PTO all batches in parallel ─────────────────
    outcomes: list[BatchOutcome] = []

    with ThreadPoolExecutor(max_workers=max(len(plan.batches), 1)) as pool:
        futures = {
            pool.submit(_run_pto_batch, b, plan, index, config): b
            for b in plan.batches
        }
        for future in as_completed(futures):
            outcomes.append(future.result())

    # Partition results
    ok_outcomes = []
    pumba_needed = []
    crashed = []

    for outcome in outcomes:
        if outcome.error is not None:
            crashed.append(outcome)
        elif outcome.stencil_result.failures:
            pumba_needed.append(outcome)
        else:
            ok_outcomes.append(outcome)

    # Merge successful PTO values
    for outcome in ok_outcomes:
        for cell_id, pcr in outcome.stencil_result.values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )
        # Also merge partial successes from failed batches
    for outcome in pumba_needed:
        for cell_id, pcr in outcome.stencil_result.values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )

    # Log failures
    for outcome in pumba_needed:
        failed_cells = ", ".join(
            f"{m} ({cid})" for cid, m in outcome.stencil_result.failures
        )
        _pto_out.print(
            f"PTO batch {outcome.request.batch_id} failed cells: "
            f"{failed_cells}. Falling back to PUMBA..."
        )
    for outcome in crashed:
        _pto_out.print(
            f"PTO batch {outcome.request.batch_id} crashed: "
            f"{outcome.error}"
        )

    # ── Phase 2: PUMBA for all failed batches in parallel ────
    if pumba_needed:
        def _run_pumba_batch(outcome: BatchOutcome) -> BatchOutcome:
            pumba_ch = register(
                f"PUMBA-{outcome.request.firm} {outcome.request.period} {outcome.request.statement}"
            )
            try:
                pumba_node_ids = run_pumba(
                    outcome.request, index, config, channel=pumba_ch
                )
            except ValueError:
                # User chose "exit" — mark and let other threads finish
                return BatchOutcome(
                    batch=outcome.batch,
                    request=outcome.request,
                    cell_map=outcome.cell_map,
                    stencil_result=JudgeStencilResult({}, [], {}),
                    pto_result=None,
                    error=None,
                    user_exit=True,
                )
            if not pumba_node_ids:
                # User chose "skip" — empty outcome so Phase 2 loop
                # doesn't re-merge/re-log stale PTO results
                return BatchOutcome(
                    batch=outcome.batch,
                    request=outcome.request,
                    cell_map=outcome.cell_map,
                    stencil_result=JudgeStencilResult({}, [], {}),
                    pto_result=None,
                    error=None,
                )

            pumba_top_chunks = []
            for i, nid in enumerate(pumba_node_ids):
                node = index.docstore.docs.get(nid)
                if node is None:
                    continue
                pumba_top_chunks.append(
                    NodeWithScore(
                        node=node,
                        score=float(len(pumba_node_ids) - i),
                    )
                )
            if not pumba_top_chunks:
                return BatchOutcome(
                    batch=outcome.batch,
                    request=outcome.request,
                    cell_map=outcome.cell_map,
                    stencil_result=JudgeStencilResult({}, [], {}),
                    pto_result=None,
                    error=None,
                )

            pumba_result = PTOBatchResult(
                batch_id=outcome.request.batch_id,
                top_chunks=pumba_top_chunks,
                runner_ups=[],
                method_log=PTOMethodLog(
                    hyde_good="PUMBA",
                    hyde_bad="",
                    metadata_filters={"method": "pumba_fallback"},
                ),
            )

            try:
                judge_output = pto_judge(
                    outcome.request, pumba_result, config
                )
                stencil_result = pto_judge_to_stencil(
                    judge_output, outcome.cell_map, pumba_result
                )
            except Exception as e:
                # Judge garbled JSON on PUMBA chunks — can't recover.
                # Return empty outcome; cells become NaN in Phase 2.5.
                return BatchOutcome(
                    batch=outcome.batch,
                    request=outcome.request,
                    cell_map=outcome.cell_map,
                    stencil_result=JudgeStencilResult({}, [], {}),
                    pto_result=pumba_result,
                    error=e,
                )
            return BatchOutcome(
                batch=outcome.batch,
                request=outcome.request,
                cell_map=outcome.cell_map,
                stencil_result=stencil_result,
                pto_result=pumba_result,
                error=None,
            )

        pumba_outcomes: list[BatchOutcome] = []

        with ThreadPoolExecutor(
            max_workers=len(pumba_needed)
        ) as pool:
            futures = {
                pool.submit(_run_pumba_batch, o): o
                for o in pumba_needed
            }
            for future in as_completed(futures):
                outcome = future.result()
                pumba_outcomes.append(outcome)
                sr = outcome.stencil_result

                # Merge PUMBA successes
                for cell_id, pcr in sr.values.items():
                    all_values[cell_id] = CellResult(
                        value=pcr.value, source=pcr.source,
                        denomination=pcr.denomination, unit=pcr.unit,
                    )

                # Report Phase 2 errors and remaining failures
                if outcome.error is not None:
                    _pto_out.print(
                        f"PUMBA re-judge crashed for batch "
                        f"{outcome.request.batch_id}: {outcome.error}"
                    )
                elif sr.failures:
                    failed = ", ".join(
                        f"{m} ({cid})" for cid, m in sr.failures
                    )
                    _pto_out.print(
                        f"PUMBA also failed for batch "
                        f"{outcome.request.batch_id}: {failed}"
                    )

        # User chose "exit" in any PUMBA prompt → abort pipeline
        if any(o.user_exit for o in pumba_outcomes):
            raise ValueError("User aborted (exit)")

    # ── Phase 2.5: NaN-fill missing retrieve cells ────────
    for cell_id, cell in plan.cells.items():
        if cell.type == "retrieve" and cell_id not in all_values:
            _pto_out.print(
                f"⚠ {cell_id} ({cell.metric}) missing — NaN placeholder"
            )
            all_values[cell_id] = CellResult(
                value=float("nan"), source="FAILED",
                denomination="", unit="",
            )

    # ── Phase 3: stencil ─────────────────────────────────────
    filled = compute_stencil(plan, all_values)
    return filled
```

## Failure modes

### Multiple PUMBA instances requesting user input simultaneously

PUMBA Dailo calls `ch.input("skip or exit")` when exhausted.
With parallel Dailos, multiple threads may hit this simultaneously.

**Mitigation:** TerminalRouter's stdin broker already handles this.
`_input_queue` serializes all input requests. UI loop shows
`[N questions pending — answering 1 of N]`. Each thread blocks
on its `AnswerSlot.wait()` until its turn. No code changes needed.

### pto_judge garbled JSON (JSONDecodeError)

`pto_judge()` calls the LLM and parses its JSON output. If the LLM
returns invalid JSON, `json.loads()` raises `JSONDecodeError` (subclass
of `ValueError`). In the current code, this is OUTSIDE the try block
(line 99) — intentionally, because PUMBA can't fix garbled JSON.

**Pre-existing bug (confirmed by eval):** The current fence-stripping
regex (`re.sub(r"\s*```$", "", text)`) uses `$` to anchor the closing
fence at end-of-string. Anthropic models with thinking enabled
(Sonnet/Opus via `anthropic_sonnetmedthink`, `anthropic_opusmedthink`)
stochastically append prose AFTER the closing fence:

```
```json
{"Revenue": {"sufficient": true, ...}}
```

**Notes:**
- **Revenue**: Net sales of $14,694M ...
- **Gross Profit**: Not present in any retrieved chunk...
```

The `$` anchor doesn't match because the ` ``` ` is mid-string, not
at end. Fence stripping never fires. `json.loads` eats the trailing
prose and raises JSONDecodeError.

**Root cause:** Anthropic's thinking mode folds the system prompt
("JSON only, no explanation") into the user message
(AnthropicLLM._build_kwargs, llm.py:428), weakening the instruction.
The model satisfies its reasoning drive via the thinking block but
still sometimes adds explanatory notes in the text block.

DeepSeek at temp=0 never exhibits this — it outputs bare JSON.
The bug is stochastic and model-dependent, which is why it was
not caught until the model switch from DeepSeek to Anthropic.

**Eval evidence:** `tests/eval_pto_judge.py` case `amcor_is_fy2023`
— Sonnet 4.6 returned valid JSON inside fences followed by
`**Notes:**` prose. The aggressive `{...}` extraction recovered
the JSON successfully. See `tests/eval_runs/` for full output.

**Mitigation (new):** `pto_judge` now does aggressive JSON extraction.
On initial `json.loads` failure, finds outermost `{…}` in the
response and retries. Catches:
- Prose preamble before JSON (`"Sure! Here are the results: {...}"`)
- Prose epilogue after closing fence (`"```\n\n**Notes:**..."`)
- Fences not at string boundaries

Does NOT catch:
- JSON syntax errors (missing/trailing commas, single quotes)
- max_tokens truncation (incomplete JSON object)
- No JSON at all (pure prose)

When extraction also fails, the original JSONDecodeError re-raises.
In the parallel version, `_run_pto_batch` wraps everything in a
generic `except Exception`, so JSONDecodeError lands in `crashed`
not `pumba_needed`. Correct behavior — no PUMBA fallback for
parse failures.

### Thread pool starvation from PUMBA user prompts

If a PUMBA thread calls `ch.input()` and blocks waiting for the user,
it holds its ThreadPoolExecutor slot. With `max_workers=len(pumba_needed)`,
this is fine — all threads are launched, and blocking one doesn't
prevent others from running. The ThreadPool has enough workers for
all tasks.

### Race condition on all_values dict

`all_values` is only written from the main thread (after futures
complete and are collected). No concurrent writes. Safe.

### pto_retrieve calling LLM APIs concurrently

Multiple `pto_retrieve()` calls hit the embedding model and BM25
concurrently. The llama-index VectorStoreIndex is thread-safe for
read operations. The LLM calls (HyDE) go through separate
`LLMBackend` instances (factory creates new per call). No shared
client state.

### User "exit" in parallel PUMBA hangs process

`run_pumba()` raises `ValueError` if user types "exit" at the
exhaustion/max-turns prompt. In parallel mode, this ValueError
propagates out of `_run_pumba_batch`, then `future.result()` re-raises
it in the main thread. The main thread crashes, but other PUMBA
threads may still be blocked on `ch.input()` — nobody is processing
the input queue, so they hang indefinitely.

**Resolution:** Integrated into `_run_pumba_batch` implementation
above. `run_pumba()` wrapped in try/except ValueError. On "exit",
returns `BatchOutcome(user_exit=True)` with empty stencil. After
Phase 2 futures complete, main thread checks
`any(o.user_exit for o in pumba_outcomes)` and raises ValueError.
Clean shutdown: all threads finish naturally, no orphans.

### Channel label collision in parallel PUMBA

Labels include period + statement: `PUMBA-{firm} {period} {statement}`
(e.g., `PUMBA-Best Buy FY22 income_statement` vs
`PUMBA-Best Buy FY22 balance_sheet`). Each parallel PUMBA instance
gets a distinct label — no collision even for same firm+period.

### PTO batch results arriving out of order

`as_completed()` returns futures in completion order, not submission
order. Batch C might finish before batch A. This is fine — results
are merged by cell_id into `all_values`, order doesn't matter.

But terminal output will show batches completing out of order:
`[PMS1-Best Buy] Batch C done` before `Batch A done`. This is
correct behavior for parallel execution — users expect it.

### Partial PTO success + PUMBA for same batch

If PTO succeeds for cells A1,A2 but fails for A3, the partial
successes (A1,A2) are merged into `all_values` BEFORE PUMBA runs.
PUMBA then runs for the full batch (all metrics). The re-judge
may produce different values for A1,A2 than PTO did.

**Design decision (deferred):** Should PUMBA values overwrite PTO
partial successes? Current spec: yes, PUMBA re-judge overwrites.
Alternative: only write cells that PTO failed. Trade-off:
- Overwrite: simpler, PUMBA chunk may be more accurate for all cells
- Keep PTO partials: preserves PTO's judgment for cells it deemed sufficient

Current spec overwrites because the PUMBA chunk is likely from a
different (better) source document, and consistency within a batch
matters more than preserving partial PTO results.

### compute_stencil crashes on missing cells

`compute_stencil()` (stencil.py line 59-64) raises `ValueError` if
any retrieve cell is missing from `all_values`:
```python
if cell.type == "retrieve":
    if cell_id not in values:
        raise ValueError(f"Missing value for retrieve cell {cell_id}")
```

This kills the entire pipeline if ANY cell fails both PTO and PUMBA.
The current sequential code has the same bug (skipped batches cause
crash), but parallelism makes it more likely because:
- `crashed` outcomes (garbled judge JSON) are silently dropped
- PUMBA may fail for some cells

**Resolution:** Option B — insert NaN placeholders. PUMBA already
prompts "skip or exit" when it fails (pumba.py:787-798, 808-816).
If the user chose "skip", the decision is made — no need to ask
again. Orchestrator inserts `CellResult(value=float("nan"),
source="FAILED", denomination="", unit="")` for all cells that
both PTO and PUMBA failed (or that the user skipped). compute_stencil
sees all cells present and doesn't crash. Compute cells that
reference NaN inputs propagate NaN through their formulas.

**compute_stencil fix required:** `eval(expr)` crashes on NaN
because `str(float("nan"))` produces `"nan"` which is not a Python
builtin. `eval("100 / nan")` raises `NameError`. Fix: inject NaN
into eval namespace:

```python
# stencil.py — in compute_stencil, change:
computed_val = eval(expr)
# to:
import math

computed_val = eval(expr, {"nan": float("nan"), "inf": float("inf"), "__builtins__": {}})
if isinstance(computed_val, float) and math.isnan(computed_val):
    # Find which input cells were NaN
    refs = re.findall(r"[A-Z]\d+", cell.formula)
    nan_inputs = [
        r for r in refs
        if r in resolved and math.isnan(resolved[r].value)
    ]
    _pto_out.print(
        f"⚠ {cell_id} = {cell.formula} → NaN "
        f"(failed input cells: {', '.join(nan_inputs)})"
    )
```

This makes `nan` and `inf` available in the eval scope. Arithmetic
with NaN propagates: `nan / 100 → nan`, `100 + nan → nan`. `inf`
is needed because `1.0 / 0.0 → inf` in Python — a compute cell
dividing by zero (e.g., margin when revenue=0) produces `inf`,
which downstream compute cells would reference. `__builtins__: {}`
is a safety measure (already best practice for eval). The NaN log message names the
specific input cells that failed retrieval, so the user knows
exactly which upstream retrieve cells caused the computed NaN.

**stencil2chart fix required:** `_check_gaps()` only checks
`if v is None` to detect missing data points. NaN values pass
through uncaught — gap prompt never fires, matplotlib silently
breaks the line. Fix: add `math.isnan()` check:

```python
# stencil2chart.py — in _check_gaps, change:
if v is None:
# to:
import math
if v is None or (isinstance(v, float) and math.isnan(v)):
```

This ensures gap detection catches both None and NaN values.

**serialize_stencil fix required:** `json.dumps(float("nan"))`
produces `"NaN"` which is NOT valid JSON (RFC 8259). Downstream
`json.loads()` crashes with `JSONDecodeError`. Fix: convert NaN
and inf to None before JSON serialization:

```python
# stencil.py — in serialize_stencil, after computing value:
import math
if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
    value = None
```

This means JSON consumers see `null` for failed cells instead of
invalid `NaN`/`Infinity` literals.

**File locations (additional):**
```
MODIFIED (NaN support):
  src/scripts/Poony_Multiretrieval_S1/src/stencil.py
      - Inject nan + inf into eval namespace in compute_stencil
      - Convert NaN/inf to None in serialize_stencil (JSON safety)
  src/scripts/stencil2chart.py
      - Add math.isnan check in _check_gaps
  src/scripts/Poony_Multiretrieval_S1/src/stencil2chart.py
      - ALSO add math.isnan check in _check_gaps (second copy of this file)
```

## Resolved questions

1. **PUMBA values vs PTO partial values:** PUMBA overwrites.
   When PUMBA re-judges the full batch, its values replace any
   partial PTO successes. Simpler, and the PUMBA chunk is likely
   from a better source anyway.

2. **Channel labels:** `PUMBA-{firm} {period} {statement}`. Distinguishes
   parallel PUMBA instances even for same firm+period with different
   statements (e.g., `PUMBA-Best Buy FY22 income_statement` vs
   `PUMBA-Best Buy FY22 balance_sheet`).

3. **Crashed batches:** pto_judge now does aggressive JSON extraction
   (outermost `{…}`) before giving up. If truly garbled: log, skip,
   NaN-fill. PUMBA can't fix garbled JSON — it's LLM misbehavior,
   not a retrieval problem.

4. **Max workers:** Use `len(batches)` directly. DeepSeek V4 is
   designed for high throughput. Typical plans have 2-5 batches.
   Not worth adding a config knob for a hypothetical.
