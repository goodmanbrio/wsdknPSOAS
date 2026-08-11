# 12a_PUMBAnoDiscretion — Design decisions & ambiguity resolution

Implementation notes for pumba.py from spec 12_PUMBA.md.

## D1: spawn_gulei filename validation — two-tier approach

**Ambiguity:** Spec says "Drop invalid filenames silently" and also
says "normalize each entry to basename via Path(f).name." But what if
a filename exists on disk (list_dir showed it) yet has NO table chunks
in the docstore index? e.g. a text-only file or a file that wasn't
ingested.

**Resolution:** Two-tier validation:
1. Normalize to basename via `Path(f).name` (handles Dailo passing
   full relative paths like `Consumer Discretionary/BESTBUY/file.md`)
2. Check against `file_table_index` (pre-computed dict), NOT raw
   docstore scan. If filename has no table chunks, it's "invalid"
   for PUMBA's purposes — Gulei would get 0 chunks and return None
   anyway, so we skip the LLM call entirely.

Invalid filenames are still added to the blacklist so Dailo doesn't
re-pick them. This is the right behavior — they're "surveyed" even
though no Gulei ran.

**Failure mode considered:** If the file was ingested but had zero
table chunks (all text chunks), `file_table_index.get(fname)` returns
`[]`, which means `fname not in file_table_index` is False — it IS
in the index, just empty. Actually wait — `file_table_index` is built
with `.setdefault(fname, []).append(...)`, so files with zero table
chunks simply won't have a key. Correct behavior confirmed.

## D2: spawn_gulei truncation note — dead code

**Ambiguity:** Spec says if Dailo passes >6 files, truncate to first
6 and include a note. But the truncation happens BEFORE blacklist
filtering, and the result formatter runs after. Where does the note
go?

**Resolution:** Truncate first, then filter blacklist, then run Guleis.
The "(truncated to 6)" note is appended to the tool result string
after `_format_spawn_gulei_result` — informational for Dailo. In
practice: truncation happens at `files = files[:6]`, then blacklist
filter, then validation. The truncation note check uses `len(files)`
AFTER truncation, so it's always False. This is dead code in the
current implementation. Not harmful — left as defensive code for
if the truncation logic ever changes.

## D3: Dailo thinking kwargs — opus-4-8 detection

**Ambiguity:** Spec checks `"opus-4-8" in dailo_model` to decide
between adaptive thinking and budget-based thinking. What if the
profile specifies a non-Anthropic model?

**Resolution:** Non-issue. Dailo uses `anthropic.Anthropic()` SDK —
it can ONLY call Anthropic models. If someone sets pumba_dailo_profile
to a deepseek profile, the `messages.create(model="deepseek-chat")`
call will fail with model-not-found. Acceptable — spec explicitly
states Dailo is raw SDK until LLMBackend gains tools support.

## D4: Channel creation — register() in orchestrator vs run_pumba

**Ambiguity:** Spec shows BOTH:
1. `pumba_ch = register(f"PUMBA-{request.firm}")` in orchestrator
2. `ch = channel or register("PUMBA")` fallback in run_pumba

**Resolution:** Implemented both. Orchestrator always passes the
firm-specific channel (`[PUMBA-Best Buy]` in terminal). The fallback
`register("PUMBA")` exists for standalone testing. `_style_for` in
terminal_router.py does `label.split("-")[0]` to look up the base
style from LABEL_STYLES.

## D5: response.content thinking blocks preservation

**Ambiguity:** With adaptive thinking (opus-4-8), response.content
may contain thinking blocks alongside tool_use and text blocks. Spec:
"MUST be preserved in conversation history."

**Resolution:** `tool_blocks` filter (`b.type == "tool_use"`) skips
thinking blocks during dispatch. Full `response.content` (including
thinking blocks) is appended verbatim to messages. No special
handling needed.

## D6: Import location for anthropic SDK

**Resolution:** `import anthropic` inside `run_pumba()` body, not
module top level. Same lazy-import pattern as pto.py's llm factories.
Defers import until PUMBA is called, localizes the only
provider-specific dependency.

## D7: file_table_index pre-computation scope

**Ambiguity:** Pre-computed index iterates ALL docstore entries.
Safe synchronously?

**Resolution:** Yes. Single pass over `index.docstore.docs`
(in-memory dict). Even 10k entries <10ms. Alternative (scan per
Gulei) = 6 parallel full-scans under GIL, which spec warns against.
Memory overhead negligible (tuples of short strings).

## D8: batch_values scope after try/except

**Ambiguity:** `batch_values` is set inside try block OR inside
inner try block (PUMBA path). After except, convert step reads it.

**Resolution:** Python scoping is function-level, not block-level.
`batch_values` set in any branch visible after except. All failure
paths `continue` before reaching convert step, so batch_values is
always set when reached.

## D9: Duplicate VectorStoreIndex import in orchestrator

**Observation:** orchestrator.py already imports
`from llama_index.core import VectorStoreIndex` (line 22). The new
PUMBA integration adds `from llama_index.core.schema import
NodeWithScore`. These are sibling imports from the same package —
no conflict.

## D10: PTOBatchResult.batch_id for PUMBA-constructed result

**Ambiguity:** When orchestrator builds PTOBatchResult for PUMBA
chunks, it uses `request.batch_id`. This is the same batch_id as
the failed PTO attempt. Is this correct?

**Resolution:** Yes. The batch_id identifies the Sekei batch, not
the retrieval attempt. PUMBA is an alternative retrieval for the
SAME batch. Judge needs the same batch_id to map results back to
cells.

## D11: score assignment for PUMBA NodeWithScore

**Ambiguity:** Spec doesn't explicitly define scores for PUMBA
chunks. PTO uses BM25/fusion scores. What score should PUMBA chunks
get?

**Resolution:** Used `float(len(pumba_node_ids) - i)` — simple
rank-based scoring (3.0, 2.0, 1.0 for 3 chunks). These scores are
only used for ordering within the result, not for threshold
decisions. Judge doesn't use scores for extraction decisions.
