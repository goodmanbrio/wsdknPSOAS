# `_build_kwargs` System Prompt Fix

Separate from spec 13 (model-agnostic tool calling) to allow
independent rollback if PMS1 quality regresses.

## Problem

`AnthropicLLM._build_kwargs()` folds the system prompt into the
user message when thinking is enabled:

```python
# Current behavior (thinking=True branch):
user_text = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
kwargs = {
    "model": self._model,
    "max_tokens": self._max_tokens,
    "messages": [{"role": "user", "content": user_text}],
}
```

This was a workaround for older Anthropic API versions. The current
API supports `system=` with extended thinking. The workaround means
Sekei and PTO judge see their system prompts as user text, not as
the `system=` parameter the API is designed for.

## Fix

```python
def _build_kwargs(self, prompt: str, system_prompt: str | None) -> dict:
    kwargs: dict = {
        "model": self._model,
        "max_tokens": self._max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    if self._thinking:
        if "opus-4-8" in self._model:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": "high"}
        else:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

    return kwargs
```

Remove the `if self._thinking` / `else` branching that folds system
into user message. Use `system=` parameter unconditionally, add
thinking kwargs unconditionally when `self._thinking is True`.

## Affected callers

Only `AnthropicLLM.complete()` → `_build_kwargs()`. Specifically:

- `sekei.py` → `get_sekei_llm(config)` → `AnthropicLLM.complete()`
  Profile: `anthropic_opushighthink` (opus-4-8, thinking=True, budget=10000)

- `pto.py` → `get_pto_judge_llm(config)` → `AnthropicLLM.complete()`
  Profile: `anthropic_opusmedthink` (opus-4-8, thinking=True, budget=5000)

`call_with_tools()` (spec 13) is NOT affected — it builds its own
kwargs dict and always uses `system=`. The two paths are independent.

## Risk

System prompt placement affects model attention patterns. Prompts
tuned with system-in-user-message may produce different outputs when
moved to `system=`. Expected to be neutral-to-positive, but must be
verified empirically on Sekei and PTO judge outputs.

**Verification:** run the existing eval suite
(`eval/test_sekei_orchestrate_pto_stencil.py`) before and after.
Compare stencil output quality. If regression, revert this spec only.

## Rollback

Revert this one file change. Spec 13 (`call_with_tools()`) is
unaffected — it always uses `system=` regardless of `_build_kwargs`.

## File locations

```
MODIFIED:
  src/scripts/llm.py    _build_kwargs() only — remove thinking-mode
                         system prompt folding, use system= for all cases
```
