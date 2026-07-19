# Spec 15 Implementation — Design Decisions

## Ambiguity 1: OpenAI `complete_with_usage()` refactoring

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

## Ambiguity 2: `label` parameter threading

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

## Ambiguity 3: GeminiLLM trace recording

**Forensics:** Spec says "both backends" for the structural refactoring
and trace recording, referring to OpenAI + Anthropic. GeminiLLM is a
third backend not mentioned.

**Hypothesis A:** Skip Gemini — spec doesn't call for it.
**Hypothesis B:** Add trace recording to Gemini too — 3 lines per
method, no structural change needed since Gemini's `complete()` is
already the canonical impl.

**Doubt:** If a pipeline role is ever assigned to a Gemini profile,
LLM calls won't be traced. Silent data loss in debug output.

**Verify:** The spec says unlabeled calls are still captured with
`"(unlabeled)"`. The intent is comprehensive capture when a trace
context is active. Gemini exclusion would create a hole.

**Decision:** Added trace recording to GeminiLLM's `complete()` and
`call_with_tools()`. Trivial cost, eliminates a coverage gap.

## Ambiguity 4: `debug_dir` plumbing — function param vs module state

**Forensics:** `execute_tool(name, params)` is called by
`_safe_execute(tc)` in `agent_loop.py`, which is called by
`_dispatch_parallel(tool_calls)`. Adding a param means threading
it through the closure.

**Hypothesis A:** Add `debug_dir` as function parameter to
`execute_tool()`. Thread through `_safe_execute` and
`_dispatch_parallel`.

**Hypothesis B:** Use module-level state (`set_debug_dir()`) like
existing `set_session_dir()` pattern.

**Doubt:** Spec literally says "Accept debug_dir param" for
execute_tool. But module-level state matches the existing pattern
and avoids changing the parallel dispatch plumbing.

**Verify:** `set_session_dir()` is already called once per
`run_harness()`. Same lifecycle. `_session_dir` is module-level
and works in threaded dispatch because it's set once and read-only
after that.

**Decision:** Module-level `_debug_dir` with `set_debug_dir()`,
called alongside `set_session_dir()` in `agent_loop.py`. This is
what the spec means by "plumbing" — the important thing is that
debug_dir reaches the handlers, not the specific mechanism.

## Ambiguity 5: `batch_idx` extraction from batch ID

**Forensics:** SekeiBatch.id is a string like `"A_12"`. TraceBuffer
takes `batch_idx: int | None`. The spec's filename convention uses
`batch0`, `batch1`, etc.

**Hypothesis:** Extract the numeric part after the underscore:
`int(batch.id.split("_")[-1])`.

**Doubt:** What if batch ID format changes? What if there's no
underscore?

**Verify:** Sekei always produces `{column}_{row_numbers}` format
(e.g. "A_12", "B_34"). The part after `_` is the row numbers, not
a sequential batch index. Two batches for the same period might be
"A_12" and "A_34".

**Decision:** Use `int(batch.id.split("_")[-1]) if "_" in batch.id
else 0`. This gives unique filenames per batch (since row numbers
differ between batches). Not sequential batch indices, but the
filenames are unique and self-documenting.

## Ambiguity 6: `_serialize_llm_response()` location

**Forensics:** Both `AnthropicLLM` and `GeminiLLM` need to serialize
`LLMResponse` for trace recording in their `call_with_tools()`.
The serializer is the same logic.

**Hypothesis A:** Put it on `LLMBackend` (the ABC).
**Hypothesis B:** Put it as a `@staticmethod` on `OpenAICompatibleLLM`
and cross-reference from other classes.
**Hypothesis C:** Make it a standalone function.

**Decision:** `@staticmethod` on `OpenAICompatibleLLM`. It's defined
first in the file, both other backends reference it as
`OpenAICompatibleLLM._serialize_llm_response(result)`. Not the
cleanest OOP but avoids adding methods to the ABC for implementation
concerns. Works because all three classes are in the same file.

## Ambiguity 7: Exception handling overlap — `execute_tool` vs `_safe_execute`

**Forensics:** `_safe_execute` in `agent_loop.py` already catches
exceptions from `execute_tool()`. Spec adds a try/except to
`execute_tool()` itself for trace flushing + [BUMMER].

**Verify:** After the change, `execute_tool()` catches handler
exceptions, flushes trace, and returns `f"Error: {e}"` (string,
not re-raise). So `_safe_execute`'s except clause becomes a safety
net for non-handler exceptions only (e.g. handle resolution errors).

**Decision:** Both layers remain. `execute_tool` catches handler
crashes (has trace context to flush). `_safe_execute` catches
everything else (defense-in-depth). No behavioral conflict.

## Ambiguity 8: Orchestrator-level `ask_user` trace recording

**Forensics:** Spec lists "ask_user" in the user interaction capture
table. But the orchestrator's `ask_user` runs via `execute_tool("ask_user")`
which is dispatched by the outer agent loop — no trace context is active
at that level.

**Verify:** Trace contexts are set inside:
- `tool_pms1.py` → Sekei trace
- `orchestrator.py` → PTO/PUMBA traces
- `tool_pteca.py` → PTECA trace

The orchestrator `ask_user` runs between these. `get_current_trace()`
returns None. Recording would be a no-op.

**Decision:** No trace recording added to `_exec_ask_user`. The spec's
table lists it as a capturable event TYPE, but it only fires when a
trace context is active. Orchestrator-level ask_user has no component
trace.

## Ambiguity 9: `_render_markdown()` — thinking block truncation

**Forensics:** `_serialize_llm_response()` truncates thinking/reasoning
blocks to 500 chars. Full thinking blocks can be 10k+ chars.

**Hypothesis:** Include full thinking in trace output.
**Counter:** Spec says "full raw response text including thinking blocks
if present" for the markdown output.

**Decision:** `_serialize_llm_response()` truncates to 500 chars for
the `response` field stored in events. This is what gets rendered in
the markdown. Trade-off: full thinking would make PUMBA traces very
large (many Dailo turns). 500 chars captures the gist. If full thinking
is needed, increase the truncation limit or remove it — the
`_render_markdown` method faithfully renders whatever is in the event.

## Ambiguity 10: `flush_to_disk` failure handling

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
