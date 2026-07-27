# Debug Trace System

Always-on recording of all non-deterministic events (LLM calls,
retrieval results, user interactions) during tool execution. One
file per component per batch, written to `tests/debug/{session_ts}/`.
Purpose: hand files to Claude (or human) to diagnose pipeline
failures without re-running.

## Architecture

### What gets captured (non-deterministic only)

| Event type | Where it occurs | What's recorded |
|---|---|---|
| LLM call | sekei, pto_judge, pto_hyde, pumba dailo/gulei/leng, pteca | Full messages array, raw response text, model name. Note: `complete_with_usage()` records text blocks only; thinking blocks are captured only via `call_with_tools()` path (which uses `_serialize_llm_response`) |
| Retrieval | pto_retrieve | Top chunks (full text, scores, metadata), runner-ups, method_log |
| User interaction | ask_user, pteca_ask_user, PUMBA exhaustion | Question asked, answer received |

**Not captured** (deterministic from LLM output):
- SekeiPlan (parsed from sekei response)
- JudgeStencilResult (parsed from judge response)
- chart_input (built from PTECA finalize args)
- compute_stencil formula evaluation

### Mechanism: thread-local TraceBuffer

```
execute_tool("run_pms1", {firm, query})
  │
  ├── tool_pms1.py: set_current_trace(TraceBuffer("Sekei", firm))
  │     └── sekei() → LLMBackend.complete_with_usage()
  │                      │
  │                      └── [auto-capture] trace = get_current_trace()
  │                          if trace: trace.record_llm_call(...)
  │     flush → tests/debug/{ts}/BestBuy_Sekei.md
  │     clear_current_trace()
  │
  ├── orchestrator.run()
  │     Phase 1: ThreadPoolExecutor — per PTO batch
  │       ├── thread 0: set_trace(PTO, firm, batch=0)
  │       │     pto_retrieve() → trace.record_retrieval(batch_result)
  │       │     pto_judge()   → [auto-capture via LLMBackend]
  │       │     flush → tests/debug/{ts}/BestBuy_PTO_batch0.md
  │       │
  │       └── thread 1: set_trace(PTO, firm, batch=1)
  │             ...
  │             flush → tests/debug/{ts}/BestBuy_PTO_batch1.md
  │
  │     Phase 2: ThreadPoolExecutor — per PUMBA batch
  │       └── thread: set_trace(PUMBA, firm, batch=1)
  │             Dailo loop → [auto-capture]
  │             ├── Gulei ThreadPool → [propagated trace]
  │             │     └── Leng ThreadPool → [propagated trace]
  │             flush → tests/debug/{ts}/BestBuy_PUMBA_batch1.md
  │
  └── return stencil (normal path, unchanged)

execute_tool("run_pteca", {stencils, query})
  │
  ├── set_trace(PTECA)
  │     agent loop turns → [auto-capture]
  │     pteca_ask_user → trace.record_user_interaction(q, a)
  │     flush → tests/debug/{ts}/PTECA.md
  │
  └── return chart_inputs (normal path, unchanged)
```

### Thread-local storage (same pattern as TerminalRouter override_channel)

```python
# trace.py
import threading

_trace_local = threading.local()

def set_current_trace(trace: TraceBuffer) -> None:
    _trace_local.current = trace

def get_current_trace() -> TraceBuffer | None:
    return getattr(_trace_local, "current", None)

def clear_current_trace() -> None:
    _trace_local.current = None
```

### PUMBA nested thread propagation

PUMBA spawns sub-ThreadPoolExecutors for Gulei and Leng workers.
Thread-locals don't propagate to child threads. Fix: wrap child
thread submits with trace propagation.

```
PUMBA batch thread  ← trace set here
  └── Dailo loop    ← same thread, trace works
        └── spawn_gulei handler
              └── Gulei ThreadPoolExecutor
                    ├── file0 thread  ← NO trace without propagation
                    │     └── Leng ThreadPoolExecutor
                    │           ├── chunk0 thread  ← NO trace without propagation
```

Resolution: public helper that propagates parent trace to child thread:

```python
def with_trace(parent_trace, fn):
    """Wrap fn so child thread inherits parent's TraceBuffer."""
    def wrapper(*args, **kwargs):
        set_current_trace(parent_trace)
        try:
            return fn(*args, **kwargs)
        finally:
            clear_current_trace()
    return wrapper

# Usage in pumba.py:
parent_trace = get_current_trace()
futures = [pool.submit(with_trace(parent_trace, gulei_worker), file)
           for file in files]
```

`TraceBuffer.events` uses a `Lock` since multiple child threads
append concurrently to the same buffer. Same thread-safety pattern
as `OpaqueRegistry._recent`.

## Input contract

### TraceBuffer

```python
@dataclass
class TraceBuffer:
    component: str              # "Sekei", "PTO", "PUMBA", "PTECA"
    firm: str | None = None
    batch_idx: int | None = None
    events: list[dict] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def record_llm_call(
        self,
        messages: list[dict],
        response: str,
        model: str,
        label: str = "",        # e.g. "pto_judge", "pumba_leng"
    ) -> None:
        """Append LLM call event. Thread-safe."""
        with self._lock:
            self.events.append({
                "type": "llm_call",
                "label": label,
                "model": model,
                "messages": messages,
                "response": response,
            })

    def record_retrieval(
        self,
        batch_result: PTOBatchResult,
    ) -> None:
        """Append retrieval event with full chunk text."""
        with self._lock:
            chunks = []
            for i, cwn in enumerate(batch_result.top_chunks):
                chunks.append({
                    "rank": i,
                    "node_id": cwn.node.node_id,
                    "score": cwn.score,
                    "text": cwn.node.text,
                    "metadata": cwn.node.metadata,
                })
            self.events.append({
                "type": "retrieval",
                "batch_id": batch_result.batch_id,
                "method_log": {
                    "hyde_good": batch_result.method_log.hyde_good,
                    "hyde_bad": batch_result.method_log.hyde_bad,
                    "metadata_filters": batch_result.method_log.metadata_filters,
                },
                "chunks": chunks,
                "runner_up_count": len(batch_result.runner_ups),
            })

    def record_user_interaction(
        self,
        question: str,
        answer: str,
    ) -> None:
        with self._lock:
            self.events.append({
                "type": "user_interaction",
                "question": question,
                "answer": answer,
            })

    def flush_to_disk(self, debug_dir: Path) -> Path:
        """Write all events to a markdown file. Returns filepath."""
        debug_dir.mkdir(parents=True, exist_ok=True)
        filename = self._build_filename()
        filepath = debug_dir / filename

        with open(filepath, "w") as f:
            f.write(self._render_markdown())

        return filepath

    def _build_filename(self) -> str:
        firm_slug = self.firm.replace(" ", "_") if self.firm else ""
        if self.batch_idx is not None:
            return f"{firm_slug}_{self.component}_batch{self.batch_idx}.md"
        elif firm_slug:
            return f"{firm_slug}_{self.component}.md"
        else:
            return f"{self.component}.md"
```

### debug_dir flow

`agent_loop.py` creates `debug_dir` alongside `session_dir`:

```python
session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
session_dir = PROJECT_ROOT / "temp" / "sessions" / session_ts
debug_dir   = PROJECT_ROOT / "tests" / "debug" / session_ts
```

`debug_dir` passed to `execute_tool()` as a new parameter.
`execute_tool` passes it to tool handlers. Tool handlers pass
it to orchestrator/sekei. Traces flush there.

## Output contract

### File structure

```
tests/debug/{session_ts}/
  ├── BestBuy_Sekei.md
  ├── BestBuy_PTO_batch0.md
  ├── BestBuy_PTO_batch1.md
  ├── BestBuy_PUMBA_batch1.md    ← includes Dailo + all Gulei + all Leng calls
  ├── Amcor_Sekei.md
  ├── Amcor_PTO_batch0.md
  ├── PTECA.md
  └── S2C_1.md                   ← NOT generated (S2C skipped — no LLM calls)
```

Session timestamp matches `temp/sessions/{session_ts}/` so debug
output and session assets are cross-referenced by directory name.

### File format (LLM-readable markdown)

System prompts NOT included — they are known constants in
`system_prompt.py`, `pto.py`, `pumba.py`, etc. Not worth the
duplication. The debug file captures what varies between runs.

```markdown
# PTO Batch 0 — Best Buy
**Query:** gross margins FY2022-2023
**Batch metrics:** Revenue, Cost of Goods Sold
**Period:** FY2023 | **Statement:** Income Statement

---

## Retrieval
**Method:** BM25 + cosine hybrid | **HyDE good:** Revenue Best Buy 2023
**Chunks returned:** 5 | **Runner-ups:** 12

### Chunk 0 — node_abc123 (score: 0.847)
**File:** Best_Buy/10K_2023.md | **Section:** Income Statement | **Type:** table

[full chunk text — no truncation]

### Chunk 1 — node_def456 (score: 0.721)
**File:** Best_Buy/10K_2023.md | **Section:** Notes to Financial Statements | **Type:** table

[full chunk text — no truncation]

...

---

## LLM Call 1: pto_hyde
**Model:** claude-haiku-4-5

### Prompt
**[user]**
[full message content]

### Response
[full raw response text]

---

## LLM Call 2: pto_judge
**Model:** claude-sonnet-4-6 (thinking: 5000)

### Prompt
**[user]**
[full message content]

### Response
[full raw response text; thinking blocks included only for call_with_tools() path]
```

PUMBA files follow the same format but contain many more LLM calls
(Dailo turns + Gulei calls + Leng calls), all in chronological
order within the same file:

```markdown
# PUMBA Batch 1 — Best Buy
...

## LLM Call 1: dailo (turn 1)
...

## LLM Call 2: dailo (turn 2)
...

## LLM Call 3: gulei_pai (file: 10K_2023_tables.md)
...

## LLM Call 4: leng (chunk 0 of 10K_2023_tables.md)
...

## LLM Call 5: leng (chunk 1 of 10K_2023_tables.md)
...

## LLM Call 6: gulei_sau (file: 10K_2023_tables.md)
...
```

PTECA file captures all agent loop turns:

```markdown
# PTECA
**Query:** gross margins FY2022-2023
**Stencils:** Best Buy (5 rows, 2 periods), Amcor (5 rows, 2 periods)

---

## LLM Call 1: pteca (turn 1)
**Model:** claude-sonnet-4-6

### Prompt
**[user]**
[formatted stencil input + query]

### Response
[tool_use: pteca_ask_user with question]

---

## User Interaction
**Question:** [question shown to user]
**Answer:** [user's response]

---

## LLM Call 2: pteca (turn 2)
...

### Response
[tool_use: finalize with chart decisions]
```

### On exception: red BUMMER message

If a tool raises, `execute_tool` catches it, flushes whatever the
trace captured before the crash, and prints via the tool's channel:

```python
except Exception as e:
    trace = get_current_trace()
    if trace:
        filepath = trace.flush_to_disk(debug_dir)
        channel.print(
            f"[BUMMER] {tool_name} failure logged → {filepath}",
            style="bold red",
        )
    clear_current_trace()
    return f"Error: {e}"
```

The `[BUMMER]` message appears in red+bold via rich styling.
Error string still returned to orchestrator as today — no
behavioral change to the agent loop.

Note: this catch is for unexpected exceptions only. The normal
flow always flushes traces — `[BUMMER]` is the visual cue that
something crashed, not the trigger for writing the trace.

## Instrumentation points

### LLM chokepoints (2 instrumentation points in llm.py)

All backends follow one canonical pattern:
**`complete_with_usage()` is the real implementation;
`complete()` is sugar that discards usage.**

This means trace recording has exactly ONE entry point per
backend for complete-style calls: `complete_with_usage()`.

- `AnthropicLLM`: already works this way (no change needed).
- `OpenAICompatibleLLM`: refactored so `complete_with_usage()`
  is the real implementation (hits API, extracts `resp.usage`
  from the OpenAI response object — it's already available).
  `complete()` becomes just `return self.complete_with_usage(...)[0]`.
  This also means OpenAI-compatible callers now get real token
  usage data (previously got empty `{}`).

For `call_with_tools()`: separate trace recording in each
subclass override (all providers implement it independently).
This is a distinct code path that doesn't flow through
`complete_with_usage()`.

```python
# In LLMBackend.complete_with_usage() [both backends], after getting response:
trace = get_current_trace()
if trace:
    trace.record_llm_call(
        messages=[{"role": "user", "content": prompt}],
        response=text,
        model=self._model,
        label=label,  # new optional param, default ""
    )

# In *.call_with_tools(), after getting response:
# Same pattern — separate trace point (different code path).
```

`label` is a new optional parameter on `complete_with_usage()`
and `call_with_tools()` (and by extension `complete()`, which
delegates to `complete_with_usage()`).
Callers pass descriptive labels:

| Caller | Method called | Label |
|---|---|---|
| `sekei.py` | `complete_with_usage()` | `"sekei"` |
| `pto.py` (HyDE) | `complete()` | `"pto_hyde"` |
| `pto.py` (judge) | `complete_with_usage()` | `"pto_judge"` |
| `pumba.py` (Dailo) | `call_with_tools()` | `"dailo"` |
| `pumba.py` (GuleiPai) | `complete()` | `"gulei_pai"` |
| `pumba.py` (GuleiSau) | `complete()` | `"gulei_sau"` |
| `pumba.py` (Leng) | `complete()` | `"leng"` |
| `tool_pteca.py` | `call_with_tools()` | `"pteca"` |

If no label provided, the trace still captures — label defaults
to empty string. This means future LLM calls added anywhere in
the codebase are auto-captured as long as a trace context is active,
even without an explicit label. Labels are cosmetic (for the
markdown headings), not structural.

### Retrieval recording (1 modification in orchestrator.py)

```python
# In orchestrator.py _run_pto_batch(), after pto_retrieve() returns PTOBatchResult:
trace = get_current_trace()
if trace:
    trace.record_retrieval(batch_result)
```

### PUMBA thread propagation (2 modifications in pumba.py)

```python
# Before Gulei ThreadPoolExecutor submit:
parent_trace = get_current_trace()
futures = [pool.submit(with_trace(parent_trace, _run_gulei), ...)
           for file in files]

# Before Leng ThreadPoolExecutor submit (inside _run_gulei):
parent_trace = get_current_trace()
futures = [pool.submit(with_trace(parent_trace, _run_leng), ...)
           for chunk in chunks]
```

### Trace set-points (5 locations)

| Location | Component | Set/flush pattern |
|---|---|---|
| `tool_pms1.py` before `sekei()` | Sekei | set → sekei → flush → clear |
| `orchestrator.py` inside `_run_pto_batch()` | PTO | set → retrieve+judge → flush → clear |
| `orchestrator.py` inside `_run_pumba_batch()` | PUMBA | set → dailo loop → flush → clear |
| `tool_pteca.py` before agent loop | PTECA | set → loop → flush → clear |
| `execute_tool.py` exception handler | any | flush whatever was captured → clear |

All set-points use `try/finally` to guarantee `clear_current_trace()`:

```python
trace = TraceBuffer("PTO", firm=firm, batch_idx=batch_idx)
set_current_trace(trace)
try:
    pto_retrieve(...)
    pto_judge(...)
finally:
    trace.flush_to_disk(debug_dir)
    clear_current_trace()
```

## Dependencies

| Component | Role | Changes |
|---|---|---|
| `llm.py` | Universal LLM chokepoint | Refactor OpenAI backend: `complete_with_usage()` becomes canonical (hits API, extracts usage), `complete()` delegates to it. Add `label` param + trace recording in `complete_with_usage()` and `call_with_tools()` only |
| `pto.py` | Label threading | Add `label=` to LLM calls (hyde, judge) |
| `pumba.py` | Thread propagation | Wrap Gulei/Leng submits with `with_trace()` |
| `tool_pms1.py` | Sekei trace set-point | Add set/flush/clear around `sekei()` call |
| `orchestrator.py` | PTO + PUMBA trace set-points | Add set/flush/clear inside batch runners |
| `tool_pteca.py` | PTECA trace set-point | Add set/flush/clear around agent loop |
| `execute_tool.py` | Exception-path flush, debug_dir plumbing | Flush on crash + `[BUMMER]` message |
| `agent_loop.py` | debug_dir creation | Create `tests/debug/{session_ts}/` alongside session_dir |
| `.gitignore` | Exclude debug output | Add `tests/debug/` |

## File locations

```
NEW:
  src/harness/trace.py
      - TraceBuffer dataclass
      - set_current_trace / get_current_trace / clear_current_trace
      - with_trace() helper for child thread propagation (public, not underscore-prefixed)

MODIFIED:
  src/scripts/llm.py
      - OpenAICompatibleLLM: refactor so complete_with_usage() is the real impl
        (hits API, extracts resp.usage). complete() becomes: return self.complete_with_usage(...)[0]
      - AnthropicLLM: no structural change (already follows this pattern)
      - Add optional `label` param to complete_with_usage() and call_with_tools()
        (complete() passes it through to complete_with_usage())
      - Trace recording in complete_with_usage() only — single point for all complete-style calls
      - Trace recording in call_with_tools() — separate path, needs its own trace point
      - OpenAI callers now get real token usage data (previously got empty {})

  src/scripts/Poony_Multiretrieval_S1/src/pto.py
      - Add label="pto_hyde" to HyDE LLM call
      - Add label="pto_judge" to judge LLM call

  src/scripts/Poony_Multiretrieval_S1/src/pumba.py
      - Import with_trace from trace.py
      - Wrap Gulei ThreadPoolExecutor submits with with_trace
      - Wrap Leng ThreadPoolExecutor submits with with_trace
      - Add label="dailo"/"gulei_pai"/"gulei_sau"/"leng" to LLM calls

  src/tools/tool_pms1.py
      - Import TraceBuffer, set/get/clear from trace.py
      - Add Sekei trace set-point (set → sekei → flush → clear)
      - Pass debug_dir to orchestrator.run()

  src/scripts/Poony_Multiretrieval_S1/src/orchestrator.py
      - Import TraceBuffer, set/get/clear from trace.py
      - Add debug_dir param to run()
      - Add PTO trace set-point inside _run_pto_batch
      - Add trace.record_retrieval() after pto_retrieve() in _run_pto_batch
      - Add PUMBA trace set-point inside _run_pumba_batch

  src/tools/tool_pteca.py
      - Import TraceBuffer, set/get/clear from trace.py
      - Add PTECA trace set-point around agent loop

  src/harness/execute_tool.py
      - Accept debug_dir param
      - Pass debug_dir to _exec_pms1, _exec_pteca handlers
      - Add exception-path flush with [BUMMER] red message

  src/harness/agent_loop.py
      - Import/define PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
      - session_dir uses PROJECT_ROOT / "temp" / "sessions" / session_ts (absolute)
      - session_ts format: "%Y%m%d_%H%M%S" (with underscore)
      - Create debug_dir = PROJECT_ROOT / "tests" / "debug" / session_ts
      - Pass debug_dir to execute_tool()

  .gitignore
      - Add tests/debug/

UNCHANGED:
  src/harness/terminal_router.py   — no involvement
  src/harness/opaque_registry.py   — no involvement
  src/harness/system_prompt.py     — no involvement
  src/scripts/stencil2chart.py     — S2C skipped (no LLM calls)
  src/scripts/config.py            — no involvement
  src/scripts/llm_profiles.yaml    — no involvement
```

## Failure modes

### Trace flush fails (disk full, permissions)

`flush_to_disk` can raise `OSError`. This must not kill the tool
execution. Wrap flush in try/except, log warning via channel, and
continue. The tool's actual return value is more important than
the debug trace.

### Trace grows very large (PUMBA with many files)

A PUMBA batch scanning 20 files × 12 chunks × Leng calls = 240
LLM calls in one trace. Each Leng response is small (Haiku,
~100 tokens). Full chunk text in retrieval events is the bulk.
Estimated worst case: ~2MB per PUMBA trace file. Acceptable for
debug output that's gitignored.

### Concurrent flushes to same file

Cannot happen. Each trace has a unique filename (firm + component +
batch_idx). Multiple PTO batch threads write to different files.
Multiple Gulei/Leng threads append to the SAME TraceBuffer (via
propagated parent trace) which flushes ONCE from the parent thread
after all child futures complete.

### `label` param not passed by new LLM callers

Future code that calls `LLMBackend.complete()` without `label=`
still gets captured — label defaults to `""`. The markdown heading
shows `## LLM Call N: (unlabeled)`. Visible enough to prompt adding
a label, but not a failure.

## Resolved questions

1. **Always capture vs. on-failure only:** Always capture, always
   dump. Disk is cheap, debug visibility is not. `tests/debug/`
   is gitignored.

2. **System prompts in debug output:** No. They're known constants.
   Not worth the duplication per file.

3. **Opaque registry var vs. direct disk write:** Direct disk write.
   No reason to route through the registry — the orchestrator LLM
   never needs to see or pass debug traces.

4. **stencil2chart traces:** Skipped. S2C makes zero LLM calls.
   Failure is either a matplotlib error (stack trace suffices) or
   gap detection (already logged via ToolChannel).

5. **Thread-local vs. explicit trace param:** Thread-local. Matches
   existing `override_channel` pattern. Auto-captures future LLM
   calls without modifying their signatures. Trade-off: child
   threads need explicit propagation via `with_trace`, but this
   only applies to PUMBA's nested pools (2 locations).

6. **debug_dir path:** `tests/debug/{session_ts}/` using the same
   timestamp as `temp/sessions/{session_ts}/`. Cross-referenceable.

7. **What is deterministic:** SekeiPlan, JudgeStencilResult,
   chart_input, compute_stencil output — all derived from LLM
   responses via deterministic parsing. Not captured. If the LLM
   response is in the trace, the parsed output is reproducible.

## Implementation design decisions

### Ambiguity 1: OpenAI `complete_with_usage()` refactoring

**Forensics:** `OpenAICompatibleLLM.complete()` directly hit the API.
`AnthropicLLM.complete()` already delegated to `complete_with_usage()`.
Two different patterns = two trace recording points needed per backend.

**Hypothesis:** Refactor OpenAI so `complete_with_usage()` is canonical
(hits API, extracts `resp.usage`), `complete()` delegates to it.
Single trace point per backend for all complete-style calls.

**Verify:** OpenAI SDK `ChatCompletion` objects always have `.usage`
with `prompt_tokens` and `completion_tokens`. Confirmed via SDK docs.
DeepSeek (OpenAI-compatible) also returns these fields.

**Decision:** Refactored. OpenAI callers now get real token usage data
(previously got empty `{}`). This is a free bonus from the structural
change.

### Ambiguity 2: `label` parameter threading

**Forensics:** Spec says add `label` to `complete_with_usage()` and
`call_with_tools()`, and `complete()` passes through. But the abstract
`LLMBackend` base class defines the signatures.

**Hypothesis:** Add `label` as optional `str = ""` to all three methods
on the ABC. Subclasses get it for free via kwargs.

**Doubt:** Adding `label` to `complete()` changes the abstract method
signature — will callers break?

**Verify:** All callers use keyword args or positional `prompt` +
`system_prompt`. `label` as keyword-only with default `""` is
backwards-compatible. Checked every callsite:
- `sekei.py:414` — `llm.complete_with_usage(query, system_prompt=...)`
- `pto.py:447` — `llm.complete(user_prompt, system_prompt=...)`
- `pto.py:845` — `llm.complete_with_usage(user_prompt, system_prompt=...)`
- `pumba.py` — `.complete(prompt, system_prompt=...)`

None pass positional args beyond the first two. Safe.

**Decision:** Added `label: str = ""` to `complete()`,
`complete_with_usage()`, and `call_with_tools()` on `LLMBackend` ABC
and all subclasses.

### Ambiguity 3: GeminiLLM trace recording

**Forensics:** Spec says "both backends" for the structural refactoring
and trace recording, referring to OpenAI + Anthropic. GeminiLLM is a
third backend not mentioned.

**Decision:** Added trace recording to GeminiLLM's `complete()` and
`call_with_tools()`. Trivial cost, eliminates a coverage gap.

### Ambiguity 4: `debug_dir` plumbing — function param vs module state

**Forensics:** `execute_tool(name, params)` is called by
`_safe_execute(tc)` in `agent_loop.py`, which is called by
`_dispatch_parallel(tool_calls)`. Adding a param means threading
it through the closure.

**Decision:** Module-level `_debug_dir` with `set_debug_dir()`,
called alongside `set_session_dir()` in `agent_loop.py`. This is
what the spec means by "plumbing" — the important thing is that
debug_dir reaches the handlers, not the specific mechanism.

### Ambiguity 5: `batch_idx` extraction from batch ID

**Forensics:** SekeiBatch.id is a string like `"A_12"`. TraceBuffer
takes `batch_idx: int | None`. The spec's filename convention uses
`batch0`, `batch1`, etc.

**Decision:** Use `int(batch.id.split("_")[-1]) if "_" in batch.id
else 0`. This gives unique filenames per batch (since row numbers
differ between batches). Not sequential batch indices, but the
filenames are unique and self-documenting.

### Ambiguity 6: `_serialize_llm_response()` location

**Forensics:** Both `AnthropicLLM` and `GeminiLLM` need to serialize
`LLMResponse` for trace recording in their `call_with_tools()`.
The serializer is the same logic.

**Decision:** `@staticmethod` on `OpenAICompatibleLLM`. It's defined
first in the file, both other backends reference it as
`OpenAICompatibleLLM._serialize_llm_response(result)`. Not the
cleanest OOP but avoids adding methods to the ABC for implementation
concerns. Works because all three classes are in the same file.

### Ambiguity 7: Exception handling overlap — `execute_tool` vs `_safe_execute`

**Forensics:** `_safe_execute` in `agent_loop.py` already catches
exceptions from `execute_tool()`. Spec adds a try/except to
`execute_tool()` itself for trace flushing + [BUMMER].

**Decision:** Both layers remain. `execute_tool` catches handler
crashes (has trace context to flush). `_safe_execute` catches
everything else (defense-in-depth). No behavioral conflict.

### Ambiguity 8: Orchestrator-level `ask_user` trace recording

**Forensics:** Spec lists "ask_user" in the user interaction capture
table. But the orchestrator's `ask_user` runs via `execute_tool("ask_user")`
which is dispatched by the outer agent loop — no trace context is active
at that level.

**Decision:** No trace recording added to `_exec_ask_user`. The spec's
table lists it as a capturable event TYPE, but it only fires when a
trace context is active. Orchestrator-level ask_user has no component
trace.

### Ambiguity 9: `_render_markdown()` — thinking block truncation

**Forensics:** `_serialize_llm_response()` truncates thinking/reasoning
blocks to 500 chars. Full thinking blocks can be 10k+ chars.

**Decision:** `_serialize_llm_response()` truncates to 500 chars for
the `response` field stored in events. This is what gets rendered in
the markdown. Trade-off: full thinking would make PUMBA traces very
large (many Dailo turns). 500 chars captures the gist. If full thinking
is needed, increase the truncation limit or remove it — the
`_render_markdown` method faithfully renders whatever is in the event.

### Ambiguity 10: `flush_to_disk` failure handling

**Forensics:** Spec says "Wrap flush in try/except, log warning via
channel, and continue." But flush happens in multiple locations:
- `tool_pms1.py` (Sekei) — has channel access
- `orchestrator.py` (PTO/PUMBA batch threads) — no channel in scope
- `tool_pteca.py` — has channel access
- `execute_tool.py` (exception path) — has console access

**Decision:** All flush sites wrapped in try/except OSError.
- Sites with channel access: log `[warn]` via channel.
- Sites without (orchestrator batch threads): silently swallow.
  The tool execution continues regardless — the debug trace is
  secondary to the actual pipeline result.
