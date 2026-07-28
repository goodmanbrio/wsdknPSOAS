# Spec 18b: CheapLeng — Discretionary Decisions Log

## 1. `_STRUCTURED_MAX_RETRIES = 3` (not 2)

Anthropic's `structured_complete` uses `_STRUCTURED_MAX_RETRIES = 2`
(1 retry). DeepSeek has the tool-as-text bug at ~11% rate
(deepseek-ai/DeepSeek-V3#1244). With 2 attempts, P(all fail) =
0.11² ≈ 1.2%. With 3 attempts: 0.11³ ≈ 0.13%. The text-recovery
fallback further reduces this, but 3 attempts is cheap insurance
(each retry is ~$0.003 at V4 Pro pricing).

Went with 3 to match the spec's recommendation and `_TOOL_TEXT_MAX_RETRIES`
pattern in `call_with_tools`.

## 2. No `structured_complete_with_usage()` variant

Spec §4 offered two options for token usage in eval:
(a) Add `structured_complete_with_usage()`
(b) Compute cost from char-to-token estimates in eval

Chose (b) — the eval estimates cost from prompt/response char counts
(÷4 rough tokenizer). Doesn't bloat the LLM interface. Actual token
counts would require exposing `resp.usage` from the OpenAI SDK response,
which would mean either returning it as a second value (breaking the
dict return type) or stashing it on the instance (thread-unsafe with
100 workers). Not worth it for an eval tool.

## 3. Cell-value shape guard placement

The spec warns: "A junior will likely get this wrong." The guard goes
INSIDE the `if isinstance(cells, dict):` branch, BEFORE the `return`.
If placed after the if-block, the return on line 127 already fired.

Edge case: empty cells dict `{}` — `all(...)` on empty iterable
returns True, so valid=True → return normally. Correct behavior
(empty cells = no hits, not malformed).

## 4. No thinking in `structured_complete`

Even if the profile has `thinking: true` (like `deepseek_v4pro_validator`),
`structured_complete` does NOT pass `extra_body: {thinking: ...}`.
Reasoning: Leng is a cheap volume extraction call. Thinking would
add ~2.5k output tokens per call × 616 calls = 1.5M extra tokens
= $1.31 added cost at V4 Pro pricing. Defeats the purpose.

The Validator uses `call_with_tools()` (which DOES pass thinking),
not `structured_complete`. So Validator thinking is preserved.

## 5. Tool format: OpenAI, NOT Anthropic

`structured_complete` uses OpenAI tool format:
```json
{"type": "function", "function": {"name": "...", "parameters": ...}}
```
NOT Anthropic format:
```json
{"name": "...", "input_schema": ...}
```

Same for `tool_choice`:
```json
{"type": "function", "function": {"name": "structured_output"}}
```
NOT:
```json
{"type": "tool", "name": "structured_output"}
```

Mixing these up is a silent failure — DeepSeek accepts the wrong
format but doesn't force the tool call, returning text instead.
The retry loop catches it, but wastes 2 retries per call.

## 6. Brace-depth JSON parser for text recovery

The tool-as-text recovery uses a brace-depth counter to extract
the outermost JSON object from content. This is safe for the Leng
schema specifically because:
- All string fields (`denom`, `unit`) are from controlled vocabularies
  (no user-generated content with braces)
- `value` is numeric
- `found` is boolean
- No freeform string fields

If the Leng schema ever adds freeform string fields (e.g., `source_text`),
this parser could miscount braces inside quoted strings. At that point,
switch to regex extraction or a streaming JSON parser.

## 7. Sysprompts: verbatim copy, no DeepSeek-specific tuning

All 4 new sysprompt files are byte-identical copies of the
`anthropic_hayasui.md` originals. The spec explicitly says
"Do not modify the prompt content" and "model-agnostic."

If DeepSeek shows systematic extraction quality issues in the eval
(e.g., denom errors from different table header parsing), that's a
prompt tuning task for a later spec, not this one.

## 8. Ground truth: Revenue ≠ Laser Revenue ambiguity

In case `innolight_crshk_cover_table`, the table shows "Revenue (MM)
(CNY)" but the cell asks for "Laser Revenue." Innolight is primarily
an optical module company, so total revenue ≈ laser revenue at the
granularity of this analysis. Marked as expected hit.

In case `lite_mizuho_estimates`, the table shows "Revenue ($M)" but
the cell asks for "Laser Revenue." LITE has multiple segments (Lasers,
Cloud & Networking, Industrial Tech). Total revenue ≠ laser revenue.
Still marked as expected hit because the Leng prompt doesn't
distinguish — it just says "Laser Revenue" and the model should
extract the closest match. The Validator downstream is responsible
for cross-checking whether the extracted value is actually laser-only.

This is a known limitation of the eval ground truth. The eval tests
model behavior, not pipeline correctness.

## 9. Balance sheet case: Net Debt ≠ Long-term Debt

Case `lite_balance_sheet_debt` has long-term debt values visible
($2,503.2M and $2,164.5M) but the cell asks for "Net Debt."
Net Debt = Total Debt - Cash, which isn't shown directly.

Ground truth marks this as expected miss (empty cells). Haiku
incorrectly extracted A2=2503.2 (just the LT debt number, wrong
metric). If DeepSeek also extracts this, it's a false positive —
good for measuring whether the model conflates related but distinct
financial concepts.

## 10. Eval case count: 8 (not 15-20)

Spec targets 15-20 cases. Built 8 from traces I verified.
Mix: 3 hits (2 LITE, 1 Innolight), 5 misses. Covers: table
extraction, prose, clear misses, ambiguous cases, Chinese-language
content (via Innolight firm context, though the chunk itself is
English — the Innolight Chinese-language chunks need manual
verification against Chinese source PDFs, deferred).

Extending to 15-20 requires cross-referencing more trace chunks
against source documents. The eval infrastructure supports it —
just add cases to `leng_cases.json`.

## 11. `deepseek_v4flash_leng` profile reuse

The spec notes `deepseek_v4flash_leng` already exists at line 125
(used by PUMBA `pumba_leng_profile`). Did NOT create a duplicate.
PMS2 Leng V4 Flash variant uses the same profile. The eval runner
maps `v4flash` → `deepseek_v4flash_leng` for testing.

## 13. tool_choice removed — DeepSeek V4 Pro server-side thinking

Spec assumed `tool_choice` forcing would work with `thinking: false`.
Wrong. DeepSeek V4 Pro now has server-side thinking **always-on**
(not opt-in). Any `tool_choice` param returns 400:
`"Thinking mode does not support this tool_choice"`.

Removed `tool_choice` from `structured_complete()` entirely. The
model reliably calls the single provided tool without forcing — the
retry loop + text-recovery cover the ~11% tool-as-text failure rate.
Tested: V4 Pro returns proper `tool_calls` array on first attempt
with just `tools=[...]` and no `tool_choice`.

This also means Spec's §"tool_choice + thinking incompatibility"
section was prescient but wrong about the mitigation ("structured_complete
already doesn't [pass both]" — it DID, via tool_choice, which
DeepSeek's server-side thinking treats as incompatible).

## 14. V4 Flash beats V4 Pro on Innolight table extraction

Eval results (real API):
```
             Precision  Recall  Denom  Cost/case
V4 Pro       0.75       0.38    1.00   $0.0004
V4 Flash     0.83       0.62    1.00   $0.0001
```

V4 Flash extracted all 4 cells from the Innolight CRSHK table (A3,
B3, A7, B7). V4 Pro only got A3, B3 — missed the Revenue cells.
Both models missed A1/A5 from the Mizuho overview page (MktCap +
Price in a Key Data table). Both correctly returned empty for all
5 miss cases. Both produced the same false positive (B10=50.2x P/E
from the estimates table — the cell was type=compute so shouldn't
be extracted, but the eval cell_descriptions included it).

V4 Flash is 4x cheaper and scored higher. Surprising given the
13B vs 49B active params. V4 Pro's thinking overhead may actually
hurt simple extraction — overthinking. Need more cases to confirm.

## 15. lite_mizuho_mktcap_price: both models missed

Both V4 Pro and V4 Flash returned `{"cells": {}}` for the chunk
containing `Market Cap ($mm) | $17,163` and `Price | $242.07`.
Haiku extracted these in the pipeline run.

Hypothesis: the chunk text in the eval case may be missing the
context markers that help the model parse the table. The eval
builds the prompt slightly differently from the pipeline (no
node_id-based context injection). Or: the models are being more
conservative without `tool_choice` forcing. Need to compare the
exact prompt text between eval and trace to diagnose.

## 12. Config module identity: not addressed

`llm.py` imports `from src.config import Config`,
PMS2 files import `from src.scripts.config import Config`.
Same .py file, different Python module objects. Spec says
"Not a blocker." Did not unify — no `isinstance` checks exist.
