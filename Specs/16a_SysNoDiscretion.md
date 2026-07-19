# Spec 16 — No Discretion Notes

Decisions made during implementation without user input.

## 1. Prereq 1: Config pass-through

**Decision**: Added `config: Config | None = None` as LAST positional param (after `debug_dir`) to `run_pms1_pipeline` for backwards compat with any callers using positional args.

**Why not required param**: Other standalone scripts (gradio_ui, app.py) call `run_pms1_pipeline` without config — they rely on `Config.from_env()` default. Making it required would break them.

## 2. Brace escaping verification

**Forensic cycle**:
- Hypothesis: sekei template has `{{...}}` for JSON + `{reference}` for format vars
- Verified: in .md, JSON braces → single `{`, template vars → double `{{retrievable_line_items}}`
- Doubt: could `{{embed:$var_N}}` in orchestrator .md trigger unresolved-var regex?
- Verified: regex `\{\{(\w+)\}\}` requires word chars only — colon and `$` in `embed:$var_N` prevent match. Safe.
- Test: ran all 9 files through load_sysprompt with full var resolution — all pass.

## 3. pumba.py system_prompt threading

**Decision**: Added `system_prompt: str = ""` default to `_run_leng`, `_run_gulei_pai`, `_run_gulei_sau`. Default empty string means if someone calls them without passing a prompt (edge case: isolated testing without config), they get an empty system prompt rather than a crash.

**Alternative rejected**: Making it required — would break any ad-hoc REPL usage of these internal functions. The empty default is harmless (LLM just doesn't get a system prompt) and the real call path always passes the loaded value.

## 4. PTECA period normalization

**Decision**: `_normalize_period` uses regex `^FY\d{2}$` — only normalizes exact 2-digit FY codes. Does NOT touch `Q1 FY21`, `FY2021`, or any non-matching format.

**Failure mode acknowledged**: `FY99` → `FY2099`. Irrelevant — no pre-2000 filings in corpus.

## 5. sys.path fix for test_pto.py

**Decision**: Changed `_project_root = Path(__file__).resolve().parent` (which was eval/ dir, WRONG) to `Path(__file__).resolve().parent.parent` (PMS1 root). This is a bug fix — test_pto.py was resolving to eval/ as its project root, meaning its `.env` loading and data path calculations were wrong.

## 6. NoDiscretion path

User specified `/Users/.../PSOAS_Jul15yolu/Specs/16aSysNoDiscretion` — that directory does not exist (only `PSOAS_Jul17_Deepseek` and `PSOAS_Jul17_Anthropic` exist under Omaya/). Wrote to current project's `Specs/16a_SysNoDiscretion.md` instead.
