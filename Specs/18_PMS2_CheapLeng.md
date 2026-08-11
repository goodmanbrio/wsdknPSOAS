# Spec 18: Cheap Leng — DeepSeek V4 for Extraction

## Problem

Leng + Validator are volume calls. Haiku 4.5 output pricing
($5.00/1M) is untenable at scale.

Measured from Demo 1 traces (`tests/debug/20260727_151755/`):
**616 Leng** `structured_complete` calls (327 LITE + 289 Inno),
**130 Validator** `call_with_tools` calls (81 LITE + 49 Inno).
Each Leng call: ~2-4k input, ~300 output. Each Validator:
~3-5k input, ~800 output.

```
             Haiku 4.5        DeepSeek V4 Pro    DeepSeek V4 Flash
             $/1M in/out      $/1M in/out        $/1M in/out
             $1.00 / $5.00    $0.435 / $0.87     $0.14 / $0.28

Leng 616×:
  Input      $1.85            $0.80              $0.26
  Output     $0.92            $0.16              $0.05
Validator 130×:
  Input      $0.52            $0.23              $0.07
  Output     $0.52            $0.09              $0.03
             ──────           ──────             ──────
  TOTAL      $3.81            $1.28              $0.41
  vs Haiku   baseline         3.0× cheaper       9.3× cheaper
```

Cache hits on Haiku drop input to $0.10/1M but output stays at
$5.00/1M. DeepSeek wins on output regardless of caching.

**Note:** V4 Pro Validator with `thinking: true` will increase
Validator output tokens from ~800 to ~3.3k (thinking + verdict).
DeepSeek thinking tokens count toward max_tokens (unlike
Anthropic), so profile uses max_tokens: 5000 to avoid cutoff.
This adds ~$0.27 to the V4 Pro total ($1.28 → ~$1.55), still
2.5× cheaper than Haiku. V4 Flash Validator has no thinking.

## Scope

1. `OpenAICompatibleLLM.structured_complete()` — new method
2. DeepSeek Leng/Validator profiles in `llm_profiles.yaml`
3. Config swap: `pms2_leng_profile`, `pms2_validator_profile`
4. Eval harness: known chunks → compare Haiku vs DeepSeek
   extraction accuracy + cost
5. Gemini batched Leng — architecture sketch for future
   (not built in this spec)

## What's NOT in scope

- Prompt tuning (temporal mismatch, price target vs share
  price, GAAP/non-GAAP). Those are extraction quality problems
  documented in `tests/PMS2_FailureModes.md`, orthogonal to
  model swap.
- Sekei, Mapper, BatchPlanner, FiscalCalResolver model swaps.
  Those are low-volume calls where model quality matters more
  than cost.

---

## 1. OpenAICompatibleLLM.structured_complete()

### File: `src/scripts/llm.py`

`structured_complete()` only exists on `AnthropicLLM` (Spec 17
scoped all PMS2 roles to Anthropic). Swapping Leng to DeepSeek
requires this method on `OpenAICompatibleLLM`.

Validator uses `call_with_tools()` which already works on
`OpenAICompatibleLLM` (Spec 13, incl. tool-as-text retry). Only
Leng uses `structured_complete()`.

### Call path (trace this before touching anything)

```
leng_caller.py:195  leng_backend = get_pms2_leng_llm(config)
                         ↓
llm.py:1072         def get_pms2_leng_llm → _get(config, "pms2_leng_profile")
                         ↓
config.py:80        pms2_leng_profile = "anthropic_hayasui"
                      → CHANGE TO "deepseek_v4pro_leng"
                         ↓
llm.py:963          _make_llm(profile) → provider="deepseek"
                      → returns OpenAICompatibleLLM(...)
                         ↓
leng_caller.py:119  leng_backend.structured_complete(...)
                      → CURRENTLY RAISES NotImplementedError
                      → THIS IS WHAT WE BUILD
```

**Config import path:** PMS2 files use `from src.scripts.config
import Config` (file: `src/scripts/config.py`). `llm.py` uses
`from src.config import Config` which also resolves to
`src/scripts/config.py` via `src/__init__.py`'s `__path__`
extension. Same file, but Python loads them as two distinct
module objects (`src.config` vs `src.scripts.config`). No
practical impact (see § "Config module identity").
**Edit `src/scripts/config.py`** for PMS2 profile swaps.

### Implementation

Same tool-call shim as Anthropic. Single tool `structured_output`
with schema as `parameters`. Force `tool_choice`. Two failure
modes to handle:

1. **No tool_use block** (Anthropic's failure mode): model returns
   text instead of tool call. Retry.
2. **Tool-as-text** (DeepSeek-specific, ~11%): model emits tool
   call as text in `content`, `finish_reason: "stop"`.
   Detect + retry. Already handled in `call_with_tools()` —
   extract the same logic.

```python
# Add to OpenAICompatibleLLM class in src/scripts/llm.py
# Place AFTER _parse_openai_response() (line ~436), BEFORE
# the AnthropicLLM class (line ~441). NOT at line 296 — that's
# between call_with_tools and its private helpers.
# _STRUCTURED_MAX_RETRIES is a CLASS ATTRIBUTE (indented under class),
# not module-level. Same pattern as _TOOL_TEXT_MAX_RETRIES (line 257).

_STRUCTURED_MAX_RETRIES = 3  # 3 attempts total (2 retries)

def structured_complete(
    self, prompt, schema, system_prompt=None, label="",
    web_search=False,
) -> dict:
    """Structured JSON via tool-call shim (OpenAI-compatible).

    No thinking — extraction call. Even if profile has
    thinking=True, structured_complete skips it (same as
    Anthropic's impl which says "No thinking block").

    web_search: not supported on DeepSeek. Silently ignored.
    Only FiscalCalResolver uses web_search=True, and that
    stays on Anthropic — never routed here.
    """
    tool = {
        "type": "function",
        "function": {
            "name": "structured_output",
            "description": "Return structured data matching schema",
            "parameters": schema,
        },
    }
    # NOTE: Anthropic tool format uses {"name": ..., "input_schema": ...}
    # OpenAI format uses {"type": "function", "function": {"name": ..., "parameters": ...}}
    # Do NOT copy from AnthropicLLM.structured_complete — format is different.

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # NOTE on tool_choice format:
    #   Anthropic: {"type": "tool", "name": "structured_output"}
    #   OpenAI:    {"type": "function", "function": {"name": "structured_output"}}
    # Do NOT mix these up. DeepSeek uses OpenAI format.

    for attempt in range(self._STRUCTURED_MAX_RETRIES):
        kwargs = {
            "model": self._model,
            "messages": messages,
            "tools": [tool],
            "tool_choice": {
                "type": "function",
                "function": {"name": "structured_output"},
            },
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            # No thinking — skip extra_body even if self._thinking
        }

        resp = self._client.chat.completions.create(**kwargs)

        if not resp.choices:
            continue

        message = resp.choices[0].message

        # Happy path: tool_calls array present
        if message.tool_calls:
            for tc in message.tool_calls:
                if tc.function.name == "structured_output":
                    try:
                        result = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        break  # retry
                    # Trace
                    from src.harness.trace import get_current_trace
                    trace = get_current_trace()
                    if trace:
                        trace.record_llm_call(
                            messages=[{"role": "user", "content": prompt}],
                            response=json.dumps(result),
                            model=self._model,
                            label=label,
                        )
                    return result

        # Failure mode 2: tool-as-text (DeepSeek V4 Pro).
        # Content contains tool call as plaintext JSON. Try to
        # extract the JSON object before retrying.
        #
        # Safety: Leng output schema has no freeform string fields
        # with user content that could contain braces. All string
        # fields (denom, unit) are from controlled sets. The
        # brace-depth parser is safe for this schema. If we ever
        # add freeform string fields to the Leng schema, switch
        # to regex extraction or a proper JSON parser.
        content = message.content or ""
        if "structured_output" in content or "{" in content:
            try:
                start = content.index("{")
                depth = 0
                end = len(content) - 1
                for i, c in enumerate(content[start:], start):
                    if c == "{": depth += 1
                    elif c == "}": depth -= 1
                    if depth == 0:
                        end = i
                        break
                json_str = content[start:end + 1]
                result = json.loads(json_str)
                if isinstance(result, dict):
                    from src.harness.trace import get_current_trace
                    trace = get_current_trace()
                    if trace:
                        trace.record_llm_call(
                            messages=[{"role": "user", "content": prompt}],
                            response=json.dumps(result),
                            model=self._model,
                            label=f"{label}:text-recovery",
                        )
                    return result
            except (json.JSONDecodeError, ValueError):
                pass  # retry

    raise ValueError(
        f"structured_complete: no valid tool call after "
        f"{self._STRUCTURED_MAX_RETRIES} attempts "
        f"(model: {self._model})"
    )
```

### Why no JSON mode / response_format?

DeepSeek's OpenAI-compatible API supports
`response_format: {"type": "json_object"}` but:

1. No schema enforcement — just guarantees valid JSON, not
   schema-conformant JSON. Leng's schema has specific field
   requirements (`found`, `cells` map, nested value/denom/unit).
2. Tool-call shim gives schema enforcement via `parameters`.
3. Consistent pattern with Anthropic implementation.

### Known: `found` field often missing

Real Haiku traces (`tests/debug/20260727_151755/PMS2-lv-LITE.md`)
show Haiku omits the `found` field entirely. Responses look
like `{"cells": {"A1": {...}}}` (hit) or `{"cells": {}}` (miss).
Never `{"found": true, "cells": {...}}`.

`leng_caller.py:307` already handles this:
```python
found = leng_output.get("found", bool(leng_cells))
```

DeepSeek will likely do the same. Do NOT add redundant handling
for missing `found` — it's already covered. The eval ground
truth should also treat `found` as optional (grade on cells
map contents, not on `found` flag).

### Test

```python
# Unit test: mock OpenAI client, verify tool_choice forcing
def test_structured_complete_forces_tool_choice():
    # Verify kwargs passed to client include correct tool_choice
    # Verify tool format is OpenAI (type: function), NOT Anthropic
    ...

# Unit test: tool-as-text recovery
def test_structured_complete_text_recovery():
    # Mock client returns content='{"found": true, "cells": {"A1": ...}}'
    # with no tool_calls array. Verify recovery works.
    ...

# Integration test: real DeepSeek API
def test_structured_complete_deepseek_v4pro():
    # See M1 eval harness — integration tests are the eval cases.
    ...
```

---

## 2. DeepSeek profiles

### File: `src/scripts/llm_profiles.yaml`

Add after existing DeepSeek profiles (before legacy aliases,
after line ~130). Only 3 new profiles — `deepseek_v4flash_leng`
already exists (reuse it):

```yaml
# ── DeepSeek V4 for PMS2 Leng/Validator ──────────────────────────

deepseek_v4pro_leng:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.0         # deterministic extraction
  thinking: false           # no thinking for cheap extraction
  max_tokens: 3000          # Leng output is small (found + cells map)

deepseek_v4pro_validator:
  provider: deepseek
  model: deepseek-v4-pro
  temperature: 0.0
  thinking: true            # Validator needs judgment (cross-ref chunk vs claim)
  max_tokens: 5000          # thinking (~2.5k) + verdicts (~800-1.5k). DeepSeek
                            # thinking tokens count toward max_tokens (unlike
                            # Anthropic). 4000 risks cutoff on high-cell hits
                            # (10+ cells). 5000 gives margin.

## NOTE: deepseek_v4flash_leng ALREADY EXISTS (line 125).
## Used by PUMBA (pumba_leng_profile in config.py line 68).
## Params are identical — reuse it. Do NOT add a duplicate.

deepseek_v4flash_validator:
  provider: deepseek
  model: deepseek-v4-flash
  temperature: 0.0
  thinking: false
  max_tokens: 3000
```

Validator on V4 Pro with thinking: Validator does judgment
(literal match, period/timeframe check, denom correction).
$0.435/$0.87 is still 5.7× cheaper than Haiku output.
Thinking mode should improve denom correction and period
matching — the two most common Validator mistakes
(PMS2_FailureModes.md root causes A, D). `max_tokens: 5000`
(not 4000) because DeepSeek thinking tokens consume
max_tokens — with 2.5k thinking + 1.5k verdict, 4000 risks
truncation on high-cell hits.

V4 Flash Validator: worth testing but might be too dumb for
the cross-referencing task. 13B active params vs V4 Pro's 49B.

### Thinking mode interaction with Validator

V4 Pro Validator has `thinking: true`. This means
`call_with_tools()` sends `extra_body: {"thinking": ...}`.
Multi-turn echo-back of `reasoning_content` is already handled
in `_messages_to_openai()` (Spec 13). Trace this path:

```
Validator turn 1:
  → DeepSeek responds: reasoning_content + tool_call(submit_verdicts)
  → _parse_openai_response captures reasoning as {"type": "reasoning"} block
  → validator_loop.py:336-341: submit_verdicts found → _handle_submit_verdicts
    → returns cell_outcomes IMMEDIATELY (line 341)
    → assistant message NEVER appended to messages
    → multi-turn echo-back NEVER triggered
```

Only if Validator hits turn 2 (unknown tool or end_turn on turn 1)
does multi-turn reasoning echo-back apply. In that case:
```
  → response.to_assistant_message() includes reasoning block in raw_content
  → _messages_to_openai extracts it as reasoning_content field
  → DeepSeek V4 requires reasoning_content: "" on tool-call turns
    (already handled at llm.py line 373-374)
```

This all works. No changes needed.

### DeepSeek thinking tokens count toward max_tokens

Unlike Anthropic (where `thinking_budget` is a separate ceiling),
DeepSeek V4 Pro's thinking tokens consume `max_tokens`. If the
model uses 2.5k thinking tokens with `max_tokens: 4000`, only
1.5k remains for the actual tool call output. For typical
Validator calls (3-5 cells, ~800 token verdict), this is fine.
For high-cell-count hits (10+), the response may truncate
(`finish_reason: "length"` → `stop_reason: "max_tokens"`),
and `validator_loop.py` would not find `submit_verdicts` in
`response.tool_calls`. The Validator would exhaust `_MAX_TURNS`
and return `[]` (treated as miss). No crash, just missed
extraction. Profile `max_tokens: 5000` gives margin.

---

## 3. Config swap

### File: `src/scripts/config.py` (NOT `src/scripts/Poony_Multiretrieval_S1/src/config.py`)

The swap is one line per role (lines 80-81):

```python
# Before (Haiku):
pms2_leng_profile: str = "anthropic_hayasui"
pms2_validator_profile: str = "anthropic_hayasui"

# After (DeepSeek V4 Pro):
pms2_leng_profile: str = "deepseek_v4pro_leng"
pms2_validator_profile: str = "deepseek_v4pro_validator"
```

No code changes in `leng_caller.py` or `validator_loop.py`. The
factory functions (`get_pms2_leng_llm`, `get_pms2_validator_llm`)
go through `_get(config, ...)` → `_make_llm()` → dispatches on
`provider` field → returns `OpenAICompatibleLLM` for deepseek.

### Sysprompts

`load_sysprompt(role, profile)` resolves to
`sysprompts/{role}/{profile}.md`. When profile changes from
`"anthropic_hayasui"` to `"deepseek_v4pro_leng"`, it looks for
`sysprompts/pms2_leng/deepseek_v4pro_leng.md`. **If this file
doesn't exist, `load_sysprompt` raises `FileNotFoundError` and
the pipeline crashes on the first Leng call.** This is a
hard crash, not a graceful degradation.

Create these 4 files by copying the Haiku prompts verbatim:

```
cp sysprompts/pms2_leng/anthropic_hayasui.md \
   sysprompts/pms2_leng/deepseek_v4pro_leng.md

cp sysprompts/pms2_leng/anthropic_hayasui.md \
   sysprompts/pms2_leng/deepseek_v4flash_leng.md

cp sysprompts/pms2_validator/anthropic_hayasui.md \
   sysprompts/pms2_validator/deepseek_v4pro_validator.md

cp sysprompts/pms2_validator/anthropic_hayasui.md \
   sysprompts/pms2_validator/deepseek_v4flash_validator.md
```

The leng prompts use `{{firm}}`, `{{valid_denoms}}`,
`{{valid_units}}`, `{{fiscal_calendar}}` template vars. The
validator prompts use `{{valid_denoms}}`, `{{valid_units}}`,
`{{fiscal_calendar}}` only (NO `{{firm}}`). Do NOT add
`{{firm}}` to the validator prompts — the validator's
`load_sysprompt` call doesn't pass `firm`, and unresolved
`{{firm}}` would crash with `ValueError`. These are
model-agnostic — the same instructions work for DeepSeek.
**Do not modify the prompt content.** If DeepSeek-specific
prompt tuning is ever needed, that's a later pass.

---

## 4. Eval harness

### Design

The eval answers: "does DeepSeek extract as well as Haiku?"

Three axes: **accuracy** (did it find the right values?),
**safety** (did it hallucinate wrong values?), **cost** (how
much cheaper?).

### Ground truth creation

Source: the Demo 1 Leng trace at
`tests/debug/20260727_151755/PMS2-lv-LITE.md`. This file has
every Leng call from the LITE run: the prompt (cell descriptions
+ chunk text) and Haiku's response. Cross-reference each
response against the actual chunk text to hand-label ground
truth. Similarly `PMS2-lv-Innolight.md` for Chinese chunks.

Each test case is a JSON dict:

```json
{
    "id": "lite_mizuho_mktcap",
    "file_path": "LITE/Notes/Mizuho_LITE.md",
    "chunk_index": 5,
    "chunk_text": "... (paste from trace) ...",
    "cells_to_search": ["A1", "B1", "A2", "B2", "A5", "B5"],
    "cell_descriptions": "Firm: LITE\n  A1 = Market Cap, FY2025...",
    "expected_cells": {
        "A1": {"value": 17163, "denom": "mn", "unit": "USD"},
        "A5": {"value": 242.07, "denom": "units", "unit": "USD"}
    },
    "expected_misses": ["B1", "A2", "B2", "B5"],
    "notes": "Mizuho Nov 2025. MktCap is spot not FY projection. SharePrice is spot."
}
```

**NOTE:** `expected_cells` does NOT include a `found` field.
Haiku omits it (see § "Known: found field often missing").
Grade on cell contents only.

**File:** `tests/eval_data/leng_cases.json` (array of cases)

Target: 15-20 test cases. Mix of:
- Clear hits (value obviously in chunk, correct period)
- Clear misses (value not in chunk at all)
- Ambiguous (value present but wrong period/unit/denom)
- Tables (pipe-delimited with headers like "$ in millions")
- Prose (inline mentions like "$2.1 billion in revenue")
- Chinese-language chunks (Innolight)

### Eval runner

**File:** `tests/eval_pms2_leng.py`

```
Usage:
  python tests/eval_pms2_leng.py [--providers haiku,v4pro,v4flash]
                                  [--cases tests/eval_data/leng_cases.json]
                                  [--out tests/eval_runs/]
```

Follow the pattern in `tests/eval_pto_judge.py` for structure
(sys.path setup, markdown output, per-case reporting). But
simpler — no retrieve step, no index. Just:

Per provider, per test case:
1. Build the `OpenAICompatibleLLM` or `AnthropicLLM` from profile
2. Load the sysprompt via `load_sysprompt("pms2_leng", profile)`
3. Build the Leng prompt (cell descriptions + chunk text) in the
   same format as `leng_caller._run_single_leng()` (line 110-116)
4. Call `backend.structured_complete(prompt, LENG_OUTPUT_SCHEMA, system_prompt=...)`
5. Compare `result["cells"]` against `expected_cells`
6. Record: hit/miss per cell, value accuracy, denom match,
   unit match, latency, token usage (from OpenAI SDK response)

### Constructing the Leng prompt

The eval must construct the prompt identically to
`leng_caller._run_single_leng()`:
```python
prompt = (
    f"{cell_descriptions}\n\n"
    f"{fiscal_calendar_text}\n\n"       # empty string for annual
    f"--- CHUNK (node_id: {node_id}) ---\n"
    f"{chunk_text}\n"
    f"--- END CHUNK ---"
)
```

The eval can hardcode `fiscal_calendar_text = ""` for annual
cases and use a test `node_id`. The `cell_descriptions` and
`chunk_text` come directly from the eval case JSON (copied from
trace files).

### Metrics

**Per-cell:**
- **True positive**: cell in expected AND in result, value correct
  (within 1% tolerance for float rounding)
- **False positive**: cell in result but NOT in expected
  (hallucinated extraction)
- **False negative**: cell in expected but NOT in result (missed)
- **Denom error**: cell found, value correct, but denom wrong
  (e.g. "bn" instead of "mn" — would cause 1000× error after
  normalization)
- **Unit error**: cell found but unit wrong (would be rejected
  by Validator anyway, but wastes a Validator call)

**Aggregate:**
- Precision = TP / (TP + FP)
- Recall = TP / (TP + FN)
- Denom accuracy = correct denom / total found
- Cost per case (from token usage × provider pricing)

### Token usage extraction

`OpenAICompatibleLLM.structured_complete()` must expose token
usage for the eval. The OpenAI SDK response has `resp.usage`
with `prompt_tokens` and `completion_tokens`. Two options:

a) Add a `structured_complete_with_usage()` variant (like
   `complete_with_usage()`).
b) Just have the eval wrap the call and capture usage from
   the response separately.

Prefer (b) — don't bloat the interface. The eval can
monkey-patch or use the trace system (TraceBuffer already
records LLM calls). Alternatively, the eval can compute
cost from token counts logged in the trace output.

### Output

Markdown report per run at `tests/eval_runs/`:

```markdown
# Leng Eval: deepseek_v4pro_leng — 2026-07-25 14:30

## Summary
| Metric     | Value |
|------------|-------|
| Cases      | 20    |
| Precision  | 0.91  |
| Recall     | 0.85  |
| Denom acc  | 0.94  |
| Avg cost   | $0.0003/case |
| Avg latency| 1.2s  |

## Per-case results
| Case | Expected | Found | TP | FP | FN | Denom | Notes |
|------|----------|-------|----|----|----|-------|-------|
| ...  | ...      | ...   | ...| ...| ...| ...   | ...   |

## Failures
### Case 7: LITE/Company/results.md chunk 12
Expected A1=17163 mn USD, got A1=17.163 bn USD
→ denom error (mn vs bn), value correct if rescaled
```

### Eval for Validator (separate, M1b)

Validator eval is harder — it's an agent loop, not a single
call. Stub approach: build a minimal harness that:

1. Takes a Leng output (from the Leng eval or hardcoded)
2. Builds a `job_stencil` with the relevant rows
3. Calls `run_validator()` directly with a real DeepSeek backend
4. Checks `cell_outcomes` against expected write/reject verdicts

This tests the Leng+Validator combination for DeepSeek. Can
reuse the same eval cases — just add expected verdicts to each
case.

Lower priority than Leng eval. If Leng eval looks good on
DeepSeek, run the full pipeline (M0) and spot-check Validator
behavior from traces. Only build formal Validator eval if
traces show systematic DeepSeek-specific Validator failures.

---

## 5. Gemini batched Leng (future — NOT built in this spec)

### The insight

Gemini Flash input: $0.125-0.30/1M (cheapest of all).
Current Leng: 1 chunk per call → system prompt + cell
descriptions duplicated 300×.

Batched alternative: stuff N chunks into one call. Amortize
shared context. Output is sparse (only hits reported).

```
Current (616 calls × 1 chunk, per Demo 1):
  Input:  616 × (2k sysprompt + 0.5k cells + 1.5k chunk) = 2.5M tokens
  Output: 616 × 0.3k = 185k tokens

Batched (13 calls × ~50 chunks):
  Input:  13 × (2k sysprompt + 0.5k cells + 75k chunks) = 1.0M tokens
  Output: 13 × 2k (only hits) = 26k tokens
```

At Gemini Flash $0.30/$0.75:
- Current: $0.75 input + $0.14 output = $0.89
- Batched: $0.30 input + $0.02 output = $0.32

At DeepSeek V4 Flash $0.14/$0.28:
- Current: $0.35 input + $0.05 output = $0.40

Batched Gemini is cheaper than unbatched DeepSeek V4 Flash.
But requires:

1. **Schema change**: output must be per-chunk (map of
   chunk_id → {found, cells}), not flat.
2. **Different parallelism**: 6 big calls instead of 300
   small parallel ones. Latency per call higher. Total
   wall-clock time might be similar or worse.
3. **Failure blast radius**: one failed call loses 50 chunks
   instead of 1. Need retry at batch level.
4. **GeminiLLM.structured_complete()**: not implemented.
   Gemini native structured output (`response_schema`) is
   cleaner than tool-call shim — actual schema enforcement.
5. **Eval**: separate test cases for batched extraction
   quality. Attention degradation over long contexts is a
   real risk — does the model miss values in chunk 47 of 50?

### Recommendation

Build DeepSeek swap first (this spec). Measure cost savings.
If DeepSeek V4 Flash quality is acceptable, the 9.3× savings
may be sufficient and Gemini batched Leng becomes unnecessary
complexity. If V4 Flash quality is poor and V4 Pro's 3.0×
savings isn't enough, Gemini batched Leng is the next lever.
(Note: Validator thinking tokens increase V4 Pro cost to
~$1.55 total, 2.5× cheaper than Haiku — see § "DeepSeek
thinking tokens count toward max_tokens".)

---

## Build order

```
T0 ──► M0 ──► M1 ──► M2
```

### T0: OpenAICompatibleLLM.structured_complete()

**File:** `src/scripts/llm.py` — add `structured_complete()`
method to `OpenAICompatibleLLM` class. Place AFTER
`_parse_openai_response()` (line ~436), BEFORE the
`AnthropicLLM` class (line ~441). NOT at line 296 — that's
between `call_with_tools` and its private helpers. Also add
`_STRUCTURED_MAX_RETRIES = 3` as class attribute (indented
under the class, same pattern as `_TOOL_TEXT_MAX_RETRIES`).

**Test:** Unit test with mock. Integration test with real
DeepSeek API (V4 Pro + V4 Flash) against `LENG_OUTPUT_SCHEMA`
(importable from `src.scripts.PMS2.leng_caller`).

**Also build:** Cell-value shape guard in `_run_single_leng`
(`leng_caller.py` line ~125). INSIDE the `if isinstance(cells,
dict):` branch, BEFORE the `return node_id, result` line:
```python
        if isinstance(cells, dict):
            valid = all(isinstance(v, dict) and "value" in v for v in cells.values())
            if not valid:
                continue  # retry (malformed cell values)
            return node_id, result
```
**WARNING:** This guard goes INSIDE the if-branch, not after it.
If placed after the if-block, the `return` on line 127 already
fired and the guard never runs. A junior will likely get this
wrong — the existing code has `return` inside the `if`.

This protects against DeepSeek returning flat values instead
of `{value, denom, unit}` objects inside the cells map. Without
this, `_build_validator_input` crashes on `cell_data.get('value')`
when `cell_data` is an int (before the Validator LLM call even
fires). See § "additionalProperties in Leng schema" for full
analysis.

**Traps:**
- Tool format is OpenAI (`{"type": "function", "function": {...}}`),
  NOT Anthropic (`{"name": ..., "input_schema": ...}`).
- `tool_choice` format is OpenAI
  (`{"type": "function", "function": {"name": ...}}`),
  NOT Anthropic (`{"type": "tool", "name": ...}`).
- Do NOT pass `extra_body: {thinking: ...}` even if
  `self._thinking` is True. Leng is a cheap extraction call.
- The `web_search` param is accepted but ignored. Only
  FiscalCalResolver uses it, and that stays on Anthropic.
- `additionalProperties` in the Leng schema: DeepSeek may not
  enforce nested object structure. The cell-value shape guard
  catches this. See § "additionalProperties in Leng schema".
- **DO NOT SKIP:** Update the base class
  `LLMBackend.structured_complete()` error message (llm.py
  line 170) — it currently says "Only AnthropicLLM implements
  this for PMS2." Change to "Override in AnthropicLLM or
  OpenAICompatibleLLM." This is a 1-line edit but juniors
  will forget it, and the stale message will confuse future
  debugging when GeminiLLM hits it.

**Done when:** `structured_complete()` returns valid dict
matching Leng schema on 5+ test prompts with real DeepSeek API.
Tool-as-text recovery works on simulated failures.

### M0: Profiles + config + sysprompts

**Files:**
- `src/scripts/llm_profiles.yaml` — 3 new profiles
  (`deepseek_v4flash_leng` already exists, reuse it)
- `src/scripts/config.py` lines 80-81 — swap profile names
- 4 new sysprompt files (cp from anthropic_hayasui.md)

**Dependency:** T0 must be done first (structured_complete
must exist or Leng crashes).

**Test:** `run_pms2_pipeline` completes with DeepSeek Leng +
Validator. No crashes. Cells get filled.

**Traps:**
- Missing sysprompt file = hard crash (`FileNotFoundError`
  from `load_sysprompt`). Create all 4 files before running.
- Edit the right config: `src/scripts/config.py`, not the
  PMS1 copy at `src/scripts/Poony_Multiretrieval_S1/src/config.py`.
- `DEEPSEEK_API_KEY` must be in `.env` (check `PROVIDER_CONFIG`
  in `llm.py` line 41: `"deepseek": {"env_var": "DEEPSEEK_API_KEY", ...}`).
  Pipeline will crash with `ValueError: DEEPSEEK_API_KEY not set`
  if missing.

**Done when:** Full pipeline runs on DeepSeek. Trace files
show `deepseek-v4-pro` or `deepseek-v4-flash` as model name.
Compare filled cell count vs Haiku baseline (should be roughly
comparable).

### M1: Eval harness

**Files:**
- `tests/eval_data/leng_cases.json` — 15-20 ground truth cases
- `tests/eval_pms2_leng.py` — eval runner

**Dependency:** T0 done. The eval runner needs
`structured_complete` (T0). Ground truth creation needs trace
data — but trace files already exist from the Haiku Demo 1 run.
M0 is NOT required for ground truth creation.

**Ground truth source:** trace files at
`tests/debug/20260727_151755/PMS2-lv-LITE.md` and
`PMS2-lv-Innolight.md`. These already exist (from prior Haiku
pipeline run). Each "LLM Call N" section has the prompt and
response. Copy chunk text + cell descriptions from the prompt,
then hand-verify the response against the chunk to create
ground truth. Do NOT fabricate test cases from imagination —
use real prompts + real chunk text from traces.

**Test:** Run eval across all 3 providers: `anthropic_hayasui`
(Haiku baseline), `deepseek_v4pro_leng`, `deepseek_v4flash_leng`.
Generate comparison report.

**Done when:** Report exists with precision/recall/denom
accuracy/cost per provider. Decision made: which DeepSeek
model to use for Leng, which for Validator.

### M2: Commit + config finalize

**Build:** Confirm or adjust profile choices based on M1 eval
results. Defaults already point to DeepSeek from M0 — if eval
shows V4 Flash is acceptable for Leng, swap from V4 Pro to
V4 Flash. If eval shows quality regression, revert to Haiku.
Update `Specs/00_SPEC_INDEX.md`.

**Done when:** Profile choices validated by eval. Pipeline runs
clean on 2+ consecutive runs.

---

## Failure modes specific to DeepSeek

### Tool-as-text (Leng)

DeepSeek V4 Pro emits tool calls as plaintext ~11% of the time
(deepseek-ai/DeepSeek-V3#1244). `structured_complete()` handles
this with JSON extraction from content + retry loop.

Leng currently fires ~600 calls (616 in Demo 1). At 11%
failure rate before recovery: ~68 would need recovery. With
the retry + text extraction, effective failure rate should be
<1%. Worst case ignoring text recovery: 3 attempts x 89%
success = 0.13% final failure. With text recovery (which
salvages most tool-as-text cases), effective rate is far
lower — closer to 0.001%.

If `structured_complete` does fail (all 3 attempts), `ValueError`
propagates to `_run_single_leng` (leng_caller.py:118-133).
The `ThreadPoolExecutor` in leng_caller catches this at line
288-290: `except Exception as e: channel.print(f"⚠ Leng chunk
{nid} crashed: {e}")` → recorded as leng_error, pipeline
continues. No crash.

### Tool-as-text (Validator)

Validator uses `call_with_tools()` which already has the
3-attempt retry loop (llm.py line 279-284). `submit_verdicts` schema is
more complex (array of verdict objects) — failure rate might be
higher. The existing retry loop + validator_loop's 2-turn max
covers this. Worst case: Validator exhausts turns → treated as
miss → LengCaller continues. No pipeline crash.

Trace the crash path:
```
DeepSeek emits submit_verdicts as text (3 retries)
→ call_with_tools returns LLMResponse with stop_reason="end_turn"
  and text containing "submit_verdicts(...)"
→ validator_loop.py:322: stop_reason == "end_turn" → error message appended
→ turn_counter increments → loop continues
→ turn 2: same thing? → turn_counter = 2 >= _MAX_TURNS
→ line 359: return []  (empty cell_outcomes = treated as miss)
→ leng_caller.py: no cell_outcomes from this validator → continue
```

Safe. No crash, no bad writes. Just a missed extraction.

### Thinking mode quirks (Validator only)

DeepSeek V4 Pro thinking mode requires:
- `reasoning_content: ""` echoed on tool-call turns (already
  handled in `_messages_to_openai` at llm.py lines 371-374).
- `extra_body: {"thinking": {"type": "enabled"}}` on create
  call (already handled in `call_with_tools` at llm.py line 272).
- `structured_complete` explicitly skips thinking (Leng doesn't
  need it). Only Validator uses thinking via `call_with_tools`.
- **max_tokens budget:** DeepSeek thinking tokens consume
  max_tokens (unlike Anthropic). Profile uses max_tokens: 5000
  (not 4000) to avoid truncation. See "DeepSeek thinking
  tokens count toward max_tokens" above.

### Concurrency / rate limits

`pms2_leng_max_workers: 100`. DeepSeek rate limits depend on
plan tier. If rate-limited, the `ThreadPoolExecutor` naturally
queues — the OpenAI SDK has built-in retry on HTTP 429.

If DeepSeek plan has low RPM ceiling, reduce
`pms2_leng_max_workers` in config. No code change needed.

Symptom: slow pipeline with many "429 Too Many Requests" in
stderr. Fix: lower `pms2_leng_max_workers` to 20-30.

### V4 Flash limitations

- No thinking mode (13B active params, not enough for CoT)
- May struggle with complex table structures
- May hallucinate cell IDs (Haiku already does this ~5%,
  `leng_caller.py` lines 314-329 has prefix recovery for it)
- Denom detection might be worse (requires reading table
  headers like "$ in millions" and inferring)

These are all eval questions, not blockers. The pipeline has
guardrails (Validator rejects, compare-and-swap, denom
normalization) that catch extraction errors downstream.

### `tool_choice` + thinking incompatibility

DeepSeek V4 Pro MAY reject `tool_choice` forcing when thinking
mode is enabled (some OpenAI-compat providers do). This only
affects `structured_complete`, which explicitly skips thinking.
`call_with_tools` (Validator) does NOT use `tool_choice` — the
model decides voluntarily to call `submit_verdicts`.

If `tool_choice` + `thinking` does error on a future profile
change, the fix is: don't pass both. `structured_complete`
already doesn't. `call_with_tools` already doesn't use
`tool_choice`. Non-issue for current code.

### `additionalProperties` in Leng schema

`LENG_OUTPUT_SCHEMA` uses `additionalProperties` on the `cells`
field (dynamic keys = cell IDs). OpenAI strict mode requires
`additionalProperties: false` on all objects, which would break
dynamic keys. DeepSeek's tool schema parsing is OpenAI-compatible
but typically more lenient about `additionalProperties`.

**Risk:** DeepSeek might silently ignore the nested schema for
`cells` values (value/denom/unit structure) and return flat
values or malformed objects.

**Verification:** T0 integration test must confirm DeepSeek
returns `{"cells": {"A1": {"value": ..., "denom": ..., "unit": ...}}}`
not `{"cells": {"A1": 17163}}` or `{"cells": [{"A1": ...}]}`.
If DeepSeek flattens the nested schema, the `_run_single_leng`
malformed retry (checking `isinstance(cells, dict)`) catches
the array case but NOT the flat-value case. In that case, the
Validator receives `leng_cells` where values aren't dicts
and crashes in `_build_validator_input` (line 253) —
`cell_data.get('value')` on an int — BEFORE the Validator
LLM call even fires. `_handle_submit_verdicts` would also
crash on `verdict.get('value')` comparisons, but the crash
in `_build_validator_input` comes first.

**Mitigation if it happens:** Change Leng schema to use
`properties` with explicit cell ID keys. Impractical (cell IDs
are dynamic). Better: validate returned cells shape in
`_run_single_leng` before returning. Check that each cell
value is a dict with `value`, `denom`, `unit` keys:

```python
# In _run_single_leng (leng_caller.py), INSIDE the
# `if isinstance(cells, dict):` branch, BEFORE `return`.
# Current code:
#     if isinstance(cells, dict):
#         return node_id, result        ← guard goes BEFORE this
#
# After edit:
        cells = result.get("cells", {})
        if isinstance(cells, dict):
            # Cell-value shape guard: each value must be a dict with "value" key
            valid = all(
                isinstance(v, dict) and "value" in v
                for v in cells.values()
            )
            if not valid:
                continue  # retry (malformed cell values)
            return node_id, result
```

**Recommendation:** Add this guard regardless — it protects
against any model returning structurally invalid cell objects.
Cheap check, prevents Validator crashes.

### Nested retry loops (cost amplification)

`_run_single_leng` retries 3 times (1 + `_LENG_MALFORMED_RETRIES=2`).
Each retry calls `structured_complete` which retries 3 times
internally (`_STRUCTURED_MAX_RETRIES=3`). Worst case: **9 API
calls per chunk** before giving up.

At DeepSeek V4 Pro pricing ($0.435/$0.87 per 1M), 9 calls ×
~3k tokens = ~$0.03 per failed chunk. Across 300 chunks, if
10% need max retries = 30 × $0.03 = $0.90 added cost. Still
much cheaper than Haiku baseline. Not a problem.

But: the outer retry (`_run_single_leng`) checks for `cells`
being a dict. The inner retry (`structured_complete`) checks
for a valid tool call response. These are **different failure
modes** — outer catches malformed *content*, inner catches
malformed *transport*. The outer loop handles Haiku's known
"cells is a string" bug. DeepSeek may have different
malformation patterns. The cell-value shape guard above covers
the gap.

### `web_search` parameter mismatch

Base class `LLMBackend.call_with_tools()` accepts `web_search:
bool = False`. `OpenAICompatibleLLM.call_with_tools()` does
NOT — its signature is `(self, messages, system_prompt, tools,
max_tokens=None, label="")`.

Validator never passes `web_search`, so this isn't a runtime
bug. But if someone adds `web_search=True` to a DeepSeek call,
Python raises `TypeError: unexpected keyword argument`.

**Not a blocker.** Only FiscalCalResolver uses `web_search`,
and that stays on Anthropic. But if DeepSeek ever needs
`web_search`, the signature must be updated.

### Config module identity (cosmetic, not blocking)

`llm.py` imports `from src.config import Config`.
PMS2 files import `from src.scripts.config import Config`.
Both resolve to the same .py file (`src/scripts/config.py`)
via `src/__init__.py`'s `__path__` extension, but Python
loads them as different modules (`src.config` vs
`src.scripts.config`), creating two distinct `Config` classes.

This doesn't break anything — `_get(config, profile_attr)` uses
`getattr()` which works on any object. But `isinstance(cfg,
Config)` checks would fail across the boundary. No such
checks exist in current code. Not a blocker.

### Existing Haiku bug: `denom: "float"` → rejected

Haiku returns `"denom": "float"` for ratio metrics (P/E,
EV/EBITDA). Trace evidence:
```
{"cells": {"B10": {"value": 50.2, "denom": "float", "unit": "float"}}}
```

`"float"` is not in `DENOM_FACTORS` (`denom_reconcile.py`)
and not in `_DENOM_ALIASES`. The Validator's
`_handle_submit_verdicts` hits:
```python
factor = DENOM_FACTORS.get(denom)
if factor is None:
    → "rejected" with "unknown denom 'float'"
```

This is an existing Haiku prompt bug (not a DeepSeek concern).
Ratios should use `denom: "units"` (factor=1). But it means:
1. Eval ground truth for ratio cells must expect `denom: "units"`
2. A model that correctly returns `denom: "units"` for P/E
   would score BETTER than Haiku on these cells
3. This isn't a reason to change denom_reconcile — adding
   `"float": 1` would mask the prompt bug and create a
   real problem if someone extracts a percentage as
   `denom: "float"` (should be `"%"` = factor 0.01)

Not in scope for this spec. Document for later prompt tuning.

### Trace recording in `structured_complete`

The spec's `structured_complete` implementation records traces
on success. On failure (all retries exhausted), no trace is
recorded — it just raises `ValueError`. This means failed
Leng calls appear in traces only via the `leng_caller.py`
exception handler (`"⚠ Leng chunk {nid} crashed: {e}"`), not
as LLM call entries.

For debugging, consider recording the last failed response in
the trace before raising. But not essential — the error message
in the trace is sufficient to identify which chunks failed.
