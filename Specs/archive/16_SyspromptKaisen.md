# Externalized System Prompts

Move all system prompt strings from .py files into
`sysprompts/{role}/{profile_name}.md`. Lookup key is
`(role, profile_name)` — switch model profile in config,
get a different prompt tuned for that model. Dynamic content
via `{{placeholder}}` template vars resolved at load time.
Hard crash on missing file or unresolved vars.

## Implementation order (STRICT)

1. **Prerequisites** — Config pass-through to PMS1 + PTECA period fix.
   Commit these separately before touching prompts.
2. **Create `src/harness/sysprompts.py`** — the loader function.
3. **Create `sysprompts/` directory** — all 9 .md files. For each
   file, extract prompt text from the current .py source, apply
   brace unescaping where needed (see Migration hazards), insert
   `{{template_vars}}`. Do NOT delete the .py constants yet.
4. **Verify** — for each .md file, call `load_sysprompt(role, profile,
   **all_vars)` and diff the output against what the old Python code
   produces at runtime. Must be identical. Pay special attention to
   JSON examples (single braces) and template vars (double braces).
5. **Migrate call sites** — one file at a time, switch from inline
   constant/function to `load_sysprompt`. Run existing tests after
   each file to catch breakage early.
6. **Delete old constants** — remove the inline prompt strings from
   .py files. Only after all call sites are migrated.
7. **Fix test files** — update imports and call signatures in
   test_pumba.py and eval_pto_judge.py.
8. **Fix sys.path in PMS1 standalone scripts** — gradio_ui.py, app.py,
   test_sekei_orchestrate_pto_stencil.py, test_pto.py, run_sekei_eval.py.
   Match test_pumba.py's pattern: PSOAS root first, then PMS1 root.
   (PMS1-local config.py and llm_profiles.yaml stay — stale but harmless.)

## Architecture

```
config.py says:  pto_judge_profile = "deepseek_v4pro_highalloc_temp0"
                                          │
pto.py calls:  load_sysprompt("pto_judge", config.pto_judge_profile,
                               denomination_values=denom_str,
                               unit_values=unit_str)
                                          │
                                          ▼
             sysprompts/pto_judge/deepseek_v4pro_highalloc_temp0.md
                                          │
                    read file, resolve {{...}} placeholders
                    crash if unresolved vars remain
                                          │
                                          ▼
             llm.complete(user_msg, system_prompt=resolved_text)
```

Switching to Anthropic (or any other provider):
```
config.py says:  pto_judge_profile = "anthropic_opusmedthink"
                                          │
             sysprompts/pto_judge/anthropic_opusmedthink.md   ← different prompt
                                          │
             if file doesn't exist → FileNotFoundError
             forces you to write the prompt before switching
```

### What moves out of .py

| Role | Current location | Template vars |
|---|---|---|
| orchestrator | `system_prompt.py` `SYSTEM_PROMPT` | none |
| sekei | `sekei.py` `_SEKEI_SYSTEM_TEMPLATE` (`.format()`) | `{{retrievable_line_items}}`, `{{statement_keys}}` |
| pto_judge | `pto.py` `_build_judge_system()` (f-string) | `{{denomination_values}}`, `{{unit_values}}` |
| pto_hyde | `pto.py` `_HYDE_GOOD_SYSTEM` (raw string) | none |
| pteca | `tool_pteca.py` `PTECA_SYSTEM_PROMPT` (raw string) | none |
| pumba_dailo | `pumba.py` `DAILO_SYSTEM_TEMPLATE` (`%`-formatting) | `{{firm}}`, `{{period}}`, `{{metrics}}`, `{{statement}}` |
| pumba_gulei_pai | `pumba.py` `GULEI_PAI_SYSTEM` (raw string) | none |
| pumba_gulei_sau | `pumba.py` `GULEI_SAU_SYSTEM` (raw string) | none |
| pumba_leng | `pumba.py` `LENG_SYSTEM` (raw string) | none |

### What stays in .py

- `TOOL_DEFINITIONS` in `system_prompt.py` — JSON schemas, structural
- `DAILO_TOOLS` in `pumba.py` — JSON schemas for API tool params
- `PTECA_TOOLS` in `tool_pteca.py` — JSON schemas for API tool params
- All user-message construction (judge prompt with chunks, etc.)
- Template var sourcing (loading YAML for sekei reference data,
  loading JSON for pto_judge denomination/unit enums,
  building per-request context strings for Dailo)

## Input contract

### load_sysprompt()

```python
# src/harness/sysprompts.py
import re
from pathlib import Path

SYSPROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "sysprompts"

def load_sysprompt(role: str, profile: str, **template_vars) -> str:
    """Load and resolve a system prompt .md file.

    Raises:
        FileNotFoundError: if sysprompts/{role}/{profile}.md missing
        ValueError: if unresolved {{...}} vars remain after substitution
    """
    path = SYSPROMPTS_DIR / role / f"{profile}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"No sysprompt: {path}\n"
            f"Create sysprompts/{role}/{profile}.md or change profile"
        )
    text = path.read_text()
    for key, value in template_vars.items():
        text = text.replace(f"{{{{{key}}}}}", str(value))
    # Crash on unresolved vars
    unresolved = re.findall(r"\{\{(\w+)\}\}", text)
    if unresolved:
        raise ValueError(
            f"Unresolved template vars in {path}: {unresolved}"
        )
    return text
```

No caching. File reads ~0.1ms on 2KB. LLM calls that follow are
seconds. Not worth the complexity.

### Template var resolution

`.replace()` on a ~1-3KB string. Cost: negligible. No regex engine,
no Jinja, no templating library. Just string substitution.

The `{{...}}` syntax was chosen because:
- Unlikely to appear in natural language prompt text
- Visually obvious in .md files
- Single-pass `.replace()` handles it

## Output contract

### Directory structure

Filenames match the `*_profile` fields in `config.py`. Current
config uses DeepSeek profiles. If you switch a profile, create
the corresponding .md file (hard crash if missing — that's the point).

```
PSOAS_Jul17_Deepseek/
  sysprompts/
    orchestrator/
      deepseek_v4pro_orchestrator.md       ← personality, tool instructions, handle rules
    sekei/
      deepseek_v4pro_highalloc.md          ← planning + {{retrievable_line_items}} + {{statement_keys}}
    pto_judge/
      deepseek_v4pro_highalloc_temp0.md    ← extraction rules + {{denomination_values}} + {{unit_values}}
    pto_hyde/
      deepseek_v4flash_temp0.md            ← HyDE query generation instructions
    pteca/
      deepseek_v4pro_pteca.md              ← chart layout, always-ask, finalize format
    pumba_dailo/
      deepseek_v4pro_highalloc_temp0.md    ← navigation + {{firm}} {{period}} {{metrics}} {{statement}}
    pumba_gulei_pai/
      deepseek_v4pro_med.md                ← chunk candidate selection instructions
    pumba_gulei_sau/
      deepseek_v4pro_med.md                ← best-pick selection instructions
    pumba_leng/
      deepseek_v4flash_leng.md             ← single-chunk metric screening format
```

GuleiPai and GuleiSau share the same model profile in config
(`pumba_gulei_profile`) but have different role names, so they
resolve to different .md files:
```python
load_sysprompt("pumba_gulei_pai", config.pumba_gulei_profile)
load_sysprompt("pumba_gulei_sau", config.pumba_gulei_profile)
```

### Example .md file

`sysprompts/pto_judge/deepseek_v4pro_highalloc_temp0.md`:
```markdown
You are a financial data extraction judge. Given retrieved table
chunks from a filing, extract exact numeric values for each
requested metric.
...

## Denomination and unit extraction

denomination — the scale multiplier. MUST be exactly one of:
  {{denomination_values}}

unit — what the number measures. MUST be exactly one of:
  {{unit_values}}

## Output format
JSON only, no explanation:
{
  "Revenue": {"sufficient": true, "answer": 51761, "denomination": "mn", "unit": "USD", "source_chunk": 0},
  "Operating Income": {"sufficient": false}
}
```

Note: JSON braces in the .md are SINGLE `{` `}` — the LLM must
see valid JSON. Only template vars use double `{{...}}`. See
**Migration hazards** section below.

`sysprompts/sekei/deepseek_v4pro_highalloc.md`:
```markdown
You are a financial data retrieval planner...

## Reference: retrievable line items
{{retrievable_line_items}}

## Rules
...
- statement_key MUST be exactly one of: {{statement_keys}}.
...

## Output schema
{
  "firm": "<company name>",
  "cells": {
    "A1": {"metric": "<metric1>", "period": "FY20XX", "type": "retrieve"}
  }
}
```

`sysprompts/pumba_dailo/deepseek_v4pro_highalloc_temp0.md`:
```markdown
You are Dailo -- a retrieval coordinator. You navigate a financial
data directory to find table chunks that contain specific metrics.

## Context

You are searching for: {{metrics}}
Firm: {{firm}}
Period: {{period}}
Statement: {{statement}}

PTO (the standard retriever) already failed on this batch --
you are the fallback.

## Data directory structure
...

## Strategy
...
```

### Migration examples

**agent_loop.py** (orchestrator):
```python
# Before:
from src.harness.system_prompt import SYSTEM_PROMPT, TOOL_DEFINITIONS
# ... uses SYSTEM_PROMPT as constant at lines 204 and 304 ...

# After:
from src.harness.system_prompt import TOOL_DEFINITIONS  # keep — still needed
from src.harness.sysprompts import load_sysprompt
# Load ONCE at top of run_harness(), AFTER config is resolved:
system_prompt = load_sysprompt("orchestrator", config.orchestrator_profile)
# Then use `system_prompt` in both call sites (inner loop + checkpoint).
# Both lines 204 and 304 currently reference SYSTEM_PROMPT — replace both.
```

**sekei.py**:
```python
# Before:
# _SEKEI_SYSTEM_TEMPLATE uses .format(reference=ref, statement_keys=keys_str)
def _build_system_prompt() -> str:
    ref = _format_reference()
    keys_str = ", ".join(_STATEMENT_KEYS)
    return _SEKEI_SYSTEM_TEMPLATE.format(reference=ref, statement_keys=keys_str)

# After:
from src.harness.sysprompts import load_sysprompt
def _build_system_prompt(config: Config) -> str:
    ref = _format_reference()
    keys_str = ", ".join(_STATEMENT_KEYS)
    return load_sysprompt("sekei", config.sekei_profile,
                          retrievable_line_items=ref,
                          statement_keys=keys_str)
```

**pumba.py** (Dailo):
```python
# Before:
# DAILO_SYSTEM_TEMPLATE uses %(firm)s, %(period)s, %(metrics)s, %(statement)s
dailo_system_prompt = DAILO_SYSTEM_TEMPLATE % {
    "firm": request.firm,
    "period": request.period,
    "metrics": ", ".join(request.metrics),
    "statement": request.statement,
}

# After:
from src.harness.sysprompts import load_sysprompt
dailo_system_prompt = load_sysprompt(
    "pumba_dailo", config.pumba_dailo_profile,
    firm=request.firm,
    period=request.period,
    metrics=", ".join(request.metrics),
    statement=request.statement,
)
```

**pumba.py** (GuleiPai, GuleiSau, Leng):
```python
# Before:
raw = gulei_llm.complete(prompt, system_prompt=GULEI_PAI_SYSTEM, label="gulei_pai")
raw = gulei_llm.complete(prompt, system_prompt=GULEI_SAU_SYSTEM, label="gulei_sau")
raw = leng_llm.complete(prompt, system_prompt=LENG_SYSTEM, label="leng")

# After:
from src.harness.sysprompts import load_sysprompt

# In run_pumba(), load once BEFORE the agent loop:
gulei_pai_sys = load_sysprompt("pumba_gulei_pai", config.pumba_gulei_profile)
gulei_sau_sys = load_sysprompt("pumba_gulei_sau", config.pumba_gulei_profile)
leng_sys = load_sysprompt("pumba_leng", config.pumba_leng_profile)

# Thread prompts through function signatures — every internal function
# that currently reads a module-level constant must accept the prompt
# as a parameter instead. Call chain and new params:
#
#   run_pumba()                → loads all 3, passes to _run_guleis_parallel
#   _run_guleis_parallel(...)  → add `gulei_pai_sys, gulei_sau_sys, leng_sys`
#                                forwards all 3 to _run_single_gulei
#   _run_single_gulei(...)     → add `gulei_pai_sys, gulei_sau_sys, leng_sys`
#                                passes gulei_pai_sys to _run_gulei_pai
#                                passes gulei_sau_sys to _run_gulei_sau
#                                passes leng_sys to _run_leng
#   _run_gulei_pai(...)        → add `system_prompt: str`
#                                uses it in gulei_llm.complete(..., system_prompt=system_prompt)
#   _run_gulei_sau(...)        → add `system_prompt: str` (same pattern)
#   _run_leng(...)             → add `system_prompt: str` (same pattern)
#
# Do NOT replace the deleted module-level constants with module-level
# variables set at runtime — that is not thread-safe (multiple PMS1
# calls can run in parallel per spec 14).
```

**pto.py** (judge):
```python
# Before:
# _build_judge_system() is an f-string injecting {denom_str} and {unit_str}
def _build_judge_system() -> str:
    denom_str = " | ".join(_DENOMINATIONS)
    unit_str = " | ".join(_UNITS)
    return f"""You are a financial data extraction judge..."""

# After:
from src.harness.sysprompts import load_sysprompt
def _build_judge_system(config: Config) -> str:
    denom_str = " | ".join(_DENOMINATIONS)
    unit_str = " | ".join(_UNITS)
    return load_sysprompt("pto_judge", config.pto_judge_profile,
                          denomination_values=denom_str,
                          unit_values=unit_str)
```

**pto.py** (hyde):
```python
# Before (inside _llm_hyde_good, which already receives config):
raw = llm.complete(user_prompt, system_prompt=_HYDE_GOOD_SYSTEM, label="pto_hyde")

# After (still inside _llm_hyde_good — config is already a param):
from src.harness.sysprompts import load_sysprompt
hyde_sys = load_sysprompt("pto_hyde", config.pto_hyde_profile)
raw = llm.complete(user_prompt, system_prompt=hyde_sys, label="pto_hyde")
```

**tool_pteca.py**:
```python
# Before:
PTECA_SYSTEM_PROMPT = """You are PTECA..."""

# After:
from src.harness.sysprompts import load_sysprompt
# Load once per run_pteca() invocation, not at import time
pteca_sys = load_sysprompt("pteca", config.pteca_profile)
```

### What `system_prompt.py` becomes

```python
# Before: SYSTEM_PROMPT (big prose string) + TOOL_DEFINITIONS (list)
# After: just TOOL_DEFINITIONS

TOOL_DEFINITIONS = [
    {"name": "run_pms1", "description": "...", "input_schema": {...}},
    {"name": "run_pteca", "description": "...", "input_schema": {...}},
    {"name": "run_stencil2chart", "description": "...", "input_schema": {...}},
    {"name": "ask_user", "description": "...", "input_schema": {...}},
    {"name": "inspect_var", "description": "...", "input_schema": {...}},
    {"name": "write_session_md", "description": "...", "input_schema": {...}},
    {"name": "read_session_md", "description": "...", "input_schema": {...}},
]
```

`SYSTEM_PROMPT` constant deleted entirely. Update module docstring
to reflect it now only contains TOOL_DEFINITIONS. Keep the filename
`system_prompt.py` — renaming breaks import paths for no benefit.

## Dependencies

| Component | Role | Changes |
|---|---|---|
| `src/harness/sysprompts.py` | NEW — loader function | — |
| `sysprompts/` directory | NEW — 9 .md files | — |
| `system_prompt.py` | Delete `SYSTEM_PROMPT`, keep `TOOL_DEFINITIONS` | Shrinks dramatically |
| `agent_loop.py` | Switch from import-time constant to runtime `load_sysprompt` | Needs `config` access |
| `sekei.py` | Delete template string, rewrite `_build_system_prompt(config)` | `load_sysprompt` + 2 template vars (`retrievable_line_items`, `statement_keys`) |
| `pto.py` | Replace `_build_judge_system()` f-string + HyDE constant | Two `load_sysprompt` calls. Judge gets 2 template vars (`denomination_values`, `unit_values`) |
| `tool_pteca.py` | Replace `PTECA_SYSTEM_PROMPT` constant | One `load_sysprompt` call (no template vars) |
| `pumba.py` | Replace 4 inline prompts | Four `load_sysprompt` calls. Dailo gets 4 template vars (`firm`, `period`, `metrics`, `statement`). Others: none |
| `src/scripts/config.py` | Already has all 8 `*_profile` fields | No changes needed (NOTE: this is the OUTER config, not PMS1-local) |

## File locations

```
NEW:
  src/harness/sysprompts.py
      - load_sysprompt(role, profile, **template_vars) → str
      - SYSPROMPTS_DIR constant

  sysprompts/orchestrator/deepseek_v4pro_orchestrator.md
  sysprompts/sekei/deepseek_v4pro_highalloc.md
  sysprompts/pto_judge/deepseek_v4pro_highalloc_temp0.md
  sysprompts/pto_hyde/deepseek_v4flash_temp0.md
  sysprompts/pteca/deepseek_v4pro_pteca.md
  sysprompts/pumba_dailo/deepseek_v4pro_highalloc_temp0.md
  sysprompts/pumba_gulei_pai/deepseek_v4pro_med.md
  sysprompts/pumba_gulei_sau/deepseek_v4pro_med.md
  sysprompts/pumba_leng/deepseek_v4flash_leng.md

MODIFIED:
  src/harness/system_prompt.py
      - Delete SYSTEM_PROMPT constant
      - Keep TOOL_DEFINITIONS unchanged

  src/harness/agent_loop.py
      - Replace: from src.harness.system_prompt import SYSTEM_PROMPT
      - With: load_sysprompt("orchestrator", config.orchestrator_profile)

  src/scripts/Poony_Multiretrieval_S1/src/sekei.py
      - Delete _SEKEI_SYSTEM_TEMPLATE string constant
      - Rewrite _build_system_prompt() to accept config param and
        call load_sysprompt("sekei", config.sekei_profile,
                            retrievable_line_items=ref,
                            statement_keys=keys_str)
      - Update sekei() to pass config to _build_system_prompt(config)
      - Keep _format_reference() — builds the retrievable_line_items value
      - Keep _STATEMENT_KEYS — used to build statement_keys value
      - ⚠ BRACE HAZARD: see Migration hazards section

  src/scripts/Poony_Multiretrieval_S1/src/pto.py
      - Rewrite _build_judge_system() to call load_sysprompt
      - Replace _HYDE_GOOD_SYSTEM constant with load_sysprompt call
      - Keep _DENOMINATIONS, _UNITS — used to build template var values
      - ⚠ BRACE HAZARD for judge prompt: see Migration hazards section

  src/tools/tool_pteca.py
      - Delete PTECA_SYSTEM_PROMPT constant
      - With: load_sysprompt("pteca", config.pteca_profile)
      - ALSO (prereq 2): add _normalize_period + 2 call sites in _build_chart_inputs

  src/scripts/Poony_Multiretrieval_S1/src/pumba.py
      - Delete DAILO_SYSTEM_TEMPLATE, GULEI_PAI_SYSTEM,
        GULEI_SAU_SYSTEM, LENG_SYSTEM constants
      - Dailo: load_sysprompt with 4 per-request template vars
      - GuleiPai/GuleiSau/Leng: load_sysprompt with no template vars
      - Load Gulei/Leng prompts once in run_pumba(), pass to workers

MODIFIED (prereq only, NOT sysprompt migration):
  src/harness/execute_tool.py      — pass _config to run_pms1_pipeline (prereq 1)
  src/tools/tool_pms1.py           — accept config param (prereq 1)

MODIFIED (sys.path fix — step 8, match test_pumba.py pattern):
  src/scripts/Poony_Multiretrieval_S1/src/gradio_ui.py
      - Currently: _project_root = PMS1 root, only PMS1 on sys.path
      - Fix: add _psoas_root (PMS1 → scripts → src → PSOAS), insert PSOAS first
  src/scripts/Poony_Multiretrieval_S1/app.py
      - Currently: _project_root = PMS1 root, only PMS1 on sys.path
      - Fix: add _psoas_root, insert PSOAS first
  src/scripts/Poony_Multiretrieval_S1/eval/test_sekei_orchestrate_pto_stencil.py
      - Currently: _project_root = PMS1 root, only PMS1 on sys.path
      - Fix: add _psoas_root, insert PSOAS first
  src/scripts/Poony_Multiretrieval_S1/eval/test_pto.py
      - Currently: _project_root = eval/ dir (WRONG — should be PMS1 root)
      - Fix: _project_root = parent.parent, add _psoas_root, insert PSOAS first
  src/scripts/Poony_Multiretrieval_S1/eval/run_sekei_eval.py
      - Currently: _project_root = PMS1 root, only PMS1 on sys.path
      - Fix: add _psoas_root, insert PSOAS first

TEST FILES (update imports + call signatures):
  src/scripts/Poony_Multiretrieval_S1/eval/test_pumba.py
      - Imports LENG_SYSTEM, GULEI_PAI_SYSTEM from pumba.py → deleted
      - Imports _run_leng, _run_gulei_pai → signatures change (new system_prompt param)
      - Fix: load prompts via load_sysprompt in test setup, pass to functions
  tests/eval_pto_judge.py
      - Calls pto_mod._build_judge_system() → now requires config param
      - Fix: pass `config` (already a param of pto_judge_diagnostic) to
        _build_judge_system(config). Do NOT use Config.from_env() — that
        ignores the config already threaded through from run_eval()

UNCHANGED:
  src/scripts/config.py            — already has all profile fields
  src/scripts/llm_profiles.yaml    — model configs, not prompts
  src/harness/terminal_router.py
  src/harness/opaque_registry.py
  src/harness/trace.py             — independent (spec 15)
```

## Failure modes

### Missing .md file (FileNotFoundError)

Intentional hard crash. Forces prompt authoring before model
switch. Error message names the exact path to create:
```
FileNotFoundError: No sysprompt: /path/to/sysprompts/pto_judge/deepseek_v4pro.md
Create sysprompts/pto_judge/deepseek_v4pro.md or change profile
```

### Unresolved template vars (ValueError)

Intentional hard crash. Catches forgotten kwargs:
```
ValueError: Unresolved template vars in .../sekei/deepseek_v4pro_highalloc.md: ['retrievable_line_items']
```

Prevents sending a literal `{{retrievable_line_items}}` to the LLM
as noise.

### Template var in .md that's not a template var

If a prompt legitimately contains `{{something}}` as example text
(e.g., showing the user what template syntax looks like), the regex
will false-positive. Mitigation: use backtick-escaped `\{\{...\}\}`
in examples, or use a different example syntax. Unlikely to occur
in financial extraction prompts.

**Specific safe case:** The orchestrator prompt contains
`{{embed:$var_N}}` as documentation for the write_session_md tool.
This is safe — the unresolved-var regex `\{\{(\w+)\}\}` only
matches `{{word_chars_only}}`. The colon and dollar in
`embed:$var_N` prevent a match. No false positive. But if someone
later adds plain `{{something}}` to a prompt .md, it WILL crash.
This is by design — treat it as a feature, not a bug.

### Profile field not in config

If a new role is added but `config.py` doesn't have the
corresponding `*_profile` field, the caller has to hardcode or
pass the profile name. This is fine — adding a new role to config
is a one-line addition with a sensible default.

## Migration hazards: brace unescaping

Two source files use Python string formatting that DOUBLES curly
braces to produce literal `{` in output. When migrating these
prompts to .md files, the doubling must be selectively undone.
Get this wrong and you get either silent data corruption (LLM
sees `{{` instead of `{`) or silent template var loss (loader
can't find `{{var}}` because you wrote `{var}`).

### The rule

In the .md file:
- **JSON example braces** → SINGLE `{` `}` (LLM must see valid JSON)
- **Template vars** → DOUBLE `{{var_name}}` (loader replaces these)

### Affected files

Only **two** source files have this hazard. All others use raw
strings with no formatting — copy them verbatim.

| Source file | Formatting mechanism | JSON examples with doubled braces | Template vars to convert |
|---|---|---|---|
| `sekei.py` `_SEKEI_SYSTEM_TEMPLATE` | `.format()` | Yes — output schema (lines 119-142) | `{reference}` → `{{retrievable_line_items}}`, `{statement_keys}` → `{{statement_keys}}` |
| `pto.py` `_build_judge_system()` | f-string | Yes — output format (lines 751-756) | `{denom_str}` → `{{denomination_values}}`, `{unit_str}` → `{{unit_values}}` |

Files with NO hazard (raw strings, copy verbatim):
- `pto.py` `_HYDE_GOOD_SYSTEM` — raw string, no formatting, no doubled braces
- `tool_pteca.py` `PTECA_SYSTEM_PROMPT` — raw string
- `pumba.py` `DAILO_SYSTEM_TEMPLATE` — uses `%(key)s` formatting, no brace doubling
- `pumba.py` `LENG_SYSTEM`, `GULEI_PAI_SYSTEM`, `GULEI_SAU_SYSTEM` — raw strings
- `system_prompt.py` `SYSTEM_PROMPT` — raw string

### Before/after: sekei.py

**In Python** (`_SEKEI_SYSTEM_TEMPLATE`, `.format()` template):
```python
## Output schema (JSON only, no explanation)

```json
{{
  "firm": "<company name>",
  "cells": {{
    "A1": {{"metric": "<metric1>", "period": "FY20XX", "type": "retrieve", "statement": "<statement_key>"}},
    "A3": {{"metric": "<derived>", "period": "FY20XX", "type": "compute", "formula": "A2 / A1"}}
  }}
}}
```  # noqa

- statement_key MUST be exactly one of: {statement_keys}.
```

The `{{` and `}}` around JSON are `.format()` escaping. The
`{statement_keys}` is a `.format()` placeholder.

**In .md file** (correct migration):
```markdown
## Output schema (JSON only, no explanation)

```json
{
  "firm": "<company name>",
  "cells": {
    "A1": {"metric": "<metric1>", "period": "FY20XX", "type": "retrieve", "statement": "<statement_key>"},
    "A3": {"metric": "<derived>", "period": "FY20XX", "type": "compute", "formula": "A2 / A1"}
  }
}
```

- statement_key MUST be exactly one of: {{statement_keys}}.
```

JSON braces are now single. Template var `{{statement_keys}}`
stays doubled.

**WRONG migration 1** (copied verbatim — LLM sees doubled braces):
```markdown
{{
  "firm": "<company name>",
  ...
}}
```
LLM receives `{{` and `}}` as instructions. It mimics them in
output → `json.loads()` fails downstream. No crash at load time.

**WRONG migration 2** (un-escaped everything — template var lost):
```markdown
{
  "firm": "<company name>",
  ...
}

- statement_key MUST be exactly one of: {statement_keys}.
```
`load_sysprompt` looks for `{{statement_keys}}`, finds only
`{statement_keys}`. `.replace()` silently does nothing. The
unresolved-var regex also misses it (single braces don't match
`\{\{(\w+)\}\}`). LLM receives literal `{statement_keys}`.
No crash. Silent failure.

### Before/after: pto.py judge

**In Python** (`_build_judge_system()`, f-string):
```python
## Output format
JSON only, no explanation:
{{
  "Revenue": {{"sufficient": true, "answer": 51761, "denomination": "mn", "unit": "USD", "source_chunk": 0}},
  "Operating Income": {{"sufficient": false}}
}}

...
denomination — the scale multiplier on the raw number. MUST be exactly one of:
  {denom_str}

unit — what the number measures. MUST be exactly one of:
  {unit_str}
```

The `{{` / `}}` around JSON are f-string escaping. `{denom_str}`
and `{unit_str}` are f-string interpolation.

**In .md file** (correct migration):
```markdown
## Output format
JSON only, no explanation:
{
  "Revenue": {"sufficient": true, "answer": 51761, "denomination": "mn", "unit": "USD", "source_chunk": 0},
  "Operating Income": {"sufficient": false}
}

...
denomination — the scale multiplier on the raw number. MUST be exactly one of:
  {{denomination_values}}

unit — what the number measures. MUST be exactly one of:
  {{unit_values}}
```

JSON braces single. Template vars `{{denomination_values}}` and
`{{unit_values}}` doubled.

### Verification after migration

For every .md file created from a `.format()`/f-string source:
1. `load_sysprompt(role, profile, **all_vars)` must not raise
   ValueError (no unresolved `{{...}}` remain)
2. The resolved output must contain valid JSON examples with
   single braces (diff against what the old Python function
   produced at runtime)

## Resolved questions

1. **Lookup key:** `(role, profile_name)`. Simple, explicit, no
   magic fallbacks. Switch model → must write a new prompt file.

2. **Dynamic content:** `{{placeholder}}` resolved via `.replace()`.
   Cost: negligible. No templating library needed.

3. **Tool schemas:** Stay in Python (`TOOL_DEFINITIONS`). Only
   prose prompts move to .md.

4. **Scope:** All 9 system prompts migrated for consistency. Even
   pto_hyde and pumba_leng (unlikely to iterate) — avoids "some
   prompts are in .py, some in .md" confusion.

5. **Missing file:** Hard crash (FileNotFoundError). The whole
   point is different models need different prompts. Silent
   fallback defeats the purpose.

6. **Unresolved vars:** Hard crash (ValueError). A literal
   `{{retrievable_line_items}}` sent to the LLM is always a bug.

7. **Dailo template vars:** Per-request context (`{{firm}}`,
   `{{period}}`, `{{metrics}}`, `{{statement}}`). Tool descriptions
   in Dailo's prompt are static prose — DAILO_TOOLS schema is only
   used for API tool params, not prompt generation.

8. **Caching:** None. File reads are ~0.1ms, LLM calls are seconds.

## Integration with Spec 15 (Debug Trace) — DEFERRED, NOT IN SCOPE

**Do NOT implement this section as part of spec 16.** This documents
a future enhancement that becomes possible once sysprompts are
externalized. Implement it when spec 15 is built, not now.

Once sysprompts are externalized, the debug trace (spec 15) can
record WHICH sysprompt file was used for each LLM call. This makes
debug sessions fully reproducible — you see model + prompt file
+ messages + response in one place.

**`load_sysprompt` return type:** DO NOT change. It returns `str`.
Callers already know (role, profile) because they just called
`load_sysprompt(role, profile)` — they can construct the path
string `f"sysprompts/{role}/{profile}.md"` themselves if they need
it for trace metadata. Changing the return to a tuple breaks every
call site's ergonomics for zero benefit.

**Change to `record_llm_call` in `TraceBuffer`:** Add
`sysprompt_source` field:

```python
def record_llm_call(
    self,
    messages: list[dict],
    response: str,
    model: str,
    label: str = "",
    sysprompt_source: str = "",  # NEW — e.g. "sysprompts/pto_judge/deepseek_v4pro.md"
) -> None:
    with self._lock:
        self.events.append({
            "type": "llm_call",
            "label": label,
            "model": model,
            "sysprompt_source": sysprompt_source,
            "messages": messages,
            "response": response,
        })
```

**How it flows:** Callers already know the (role, profile) pair
because they just called `load_sysprompt(role, profile)`. They
pass the path string to the LLM call as metadata. The LLMBackend
trace recording picks it up and dumps it.

**Debug output becomes:**
```markdown
## LLM Call 2: pto_judge
**Model:** deepseek-v4-pro
**Sysprompt:** sysprompts/pto_judge/deepseek_v4pro.md

### Prompt
...
```

Now when debugging you see: this call used THIS model with THIS
prompt file and got THIS response. Full picture in one place.

## Prerequisite: Fix Config pass-through to PMS1

**Implement as part of this spec.** Not blocking for the default
happy path (Config.from_env() gives correct defaults), but without
this fix, runtime profile overrides silently fail for PMS1. Do this
FIRST before the sysprompt migration.

`tool_pms1.py` creates its own `Config.from_env()` instead of
receiving the harness Config. PTECA already receives it (via
`_config` in `execute_tool.py`). PMS1 doesn't.

**Consequence:** Runtime config overrides (e.g. passing
`Config(pto_judge_profile="deepseek_v4pro")` to `run_harness()`)
propagate to PTECA but NOT to PMS1. PMS1 always uses defaults.
This silently defeats the purpose of externalized sysprompts —
you change the profile in config but PMS1 ignores it.

**Fix (2 lines):**

```python
# tool_pms1.py — add config param (after existing debug_dir param)
def run_pms1_pipeline(firm, query, channel=None, debug_dir=None, config=None):
    config = config or Config.from_env()
    # ... rest unchanged (remove the hardcoded Config.from_env() at line 46) ...

# execute_tool.py — pass _config (same pattern as PTECA)
def _exec_pms1(params):
    stencil = run_pms1_pipeline(
        firm, query, channel=channel, debug_dir=_debug_dir, config=_config
    )
    ...
```

PMS1-local config.py & llm_profiles.yaml stay in place — stale but
harmless. They are shadowed by `src/scripts/config.py` via the
`src/__init__.py` `__path__` extension. Do NOT delete them.

## Prerequisite: Fix PTECA period alignment (DeepSeek)

**Implement as part of this spec.** Independent of the sysprompt
migration but blocks correct end-to-end testing with DeepSeek models.
Can be done in parallel with the other prerequisite.

`_build_chart_inputs` in `tool_pteca.py` does strict string
equality on period names when mapping PTECA's finalize output
back to stencil values. DeepSeek Sekei emits inconsistent period
formats across firms ("FY21" for Boeing, "FY2021" for Best Buy).
DeepSeek PTECA then picks one format for the shared X-axis.
Strict lookup fails → all values for the mismatched firm become 0.

Anthropic doesn't hit this because its Sekei is more consistent
in period formatting, or its PTECA copies exact stencil strings.

**Symptom:** Boeing series all zeros in chart_inputs despite
correct stencil values. Debug session `20260719_110243`.

**Fix (8 lines in `src/tools/tool_pteca.py`):**

```python
import re

def _normalize_period(p: str) -> str:
    """FY21 → FY2021. Passthrough if already 4-digit or non-FY."""
    m = re.match(r'^(FY)(\d{2})$', p)
    return f"FY20{m.group(2)}" if m else p
```

Then in `_build_chart_inputs`, two call sites:

```python
# Line ~431: build index with normalized keys
period_to_idx = {_normalize_period(p): i for i, p in enumerate(firm_periods)}

# Line ~434: look up with normalized chart period
idx = period_to_idx.get(_normalize_period(p))
```

**Failure modes:**
- "FY99" → "FY2099" (wrong century). Irrelevant — no pre-2000
  filings in corpus. If added later, extend regex to check `int(yy) > 50`.
- Quarterly "Q1 FY21" — regex doesn't match `^FY\d{2}$`, falls
  through to exact string. No worse than today.
- Dict collision if same stencil has both "FY21" and "FY2021" as
  distinct periods — impossible (Sekei picks one format per firm).

## Implementation design decisions

### 1. Config pass-through param order

Added `config: Config | None = None` as LAST positional param (after `debug_dir`) to `run_pms1_pipeline` for backwards compat with any callers using positional args.

Why not required: Other standalone scripts (gradio_ui, app.py) call `run_pms1_pipeline` without config — they rely on `Config.from_env()` default. Making it required would break them.

### 2. Brace escaping verification

Forensic cycle:
- Hypothesis: sekei template has `{{...}}` for JSON + `{reference}` for format vars
- Verified: in .md, JSON braces → single `{`, template vars → double `{{retrievable_line_items}}`
- Doubt: could `{{embed:$var_N}}` in orchestrator .md trigger unresolved-var regex?
- Verified: regex `\{\{(\w+)\}\}` requires word chars only — colon and `$` in `embed:$var_N` prevent match. Safe.
- Test: ran all 9 files through load_sysprompt with full var resolution — all pass.

### 3. pumba.py system_prompt threading

Added `system_prompt: str = ""` default to `_run_leng`, `_run_gulei_pai`, `_run_gulei_sau`. Default empty string means if someone calls them without passing a prompt (edge case: isolated testing without config), they get an empty system prompt rather than a crash.

Alternative rejected: Making it required — would break any ad-hoc REPL usage of these internal functions. The empty default is harmless (LLM just doesn't get a system prompt) and the real call path always passes the loaded value.

### 4. PTECA period normalization

`_normalize_period` uses regex `^FY\d{2}$` — only normalizes exact 2-digit FY codes. Does NOT touch `Q1 FY21`, `FY2021`, or any non-matching format.

Failure mode acknowledged: `FY99` → `FY2099`. Irrelevant — no pre-2000 filings in corpus.

### 5. sys.path fix for test_pto.py

Changed `_project_root = Path(__file__).resolve().parent` (which was eval/ dir, WRONG) to `Path(__file__).resolve().parent.parent` (PMS1 root). This is a bug fix — test_pto.py was resolving to eval/ as its project root, meaning its `.env` loading and data path calculations were wrong.

### 6. NoDiscretion path

User specified `/Users/.../PSOAS_Jul15yolu/Specs/16aSysNoDiscretion` — that directory does not exist (only `PSOAS_Jul17_Deepseek` and `PSOAS_Jul17_Anthropic` exist under Omaya/). Wrote to current project's `Specs/16a_SysNoDiscretion.md` instead.
