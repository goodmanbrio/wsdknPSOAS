# PSOAS Spec Index

**Poony Sophomore Orchestrated Analyst Strapon**

Harness that wraps PMS1, PTECA, stencil2chart (and future tools) as
LLM-orchestrated tools with CLI user interaction at any depth.

---

## Spec order (build order)

### Layer 0: Infrastructure

| # | Spec | What it covers | Status |
|---|---|---|---|
| 01 | `01_terminal_router.md` | TerminalRouter, ToolChannel, register(), buffering, stdin brokering | DONE |
| 10 | `10_cli_cosmetics.md` | rich integration: label colors, spinners, panels, styled prompts | DONE |

### Layer 1: Harness skeleton

| # | Spec | What it covers | Status |
|---|---|---|---|
| 02 | `02_agent_loop.md` | The while loop, stop_reason dispatch, message accumulation | DONE |
| 03 | `03_opaque_registry.md` | _store/_resolve, variable handles, what leaks to LLM vs what doesn't, inspect_var tool | DONE |
| 04 | `04_system_prompt.md` | Orchestrator system prompt, what it knows about tools, handle routing instructions | DONE |
| 05 | `05_execute_tool.md` | execute_tool dispatch, how tools are registered, error handling, how results are returned | DONE |

### Layer 1b: Session lifecycle

| # | Spec | What it covers | Status |
|---|---|---|---|
| 11 | `11_repl_loop.md` | Outer REPL: interactive prompt, follow-ups, session state persistence, exit handling | DONE |

### Layer 1c: PMS1-internal retrieval

| # | Spec | What it covers | Status |
|---|---|---|---|
| 12 | `12_PUMBA.md` | PUMBA fallback: Dailo/Gulei/Leng 3-tier LLM retrieval when PTO BM25 fails, orchestrator.py integration | DONE |

### Layer 1d: LLM abstraction

| # | Spec | What it covers | Status |
|---|---|---|---|
| 13 | `13_ModelAgnosticToolportEtCentralize.md` | Extend LLMBackend with call_with_tools(), normalize tool I/O across Anthropic/OpenAI/Gemini, centralize agent_loop + PTECA through llm.py factory | DONE |

### Layer 2: Tool contracts (one per tool)

Every process that PSOAS can call needs a tool contract spec.
A tool contract defines: JSON schema (name, description, input_schema),
execute_tool branch, input/output types, ToolChannel label, and
what internal user interaction (if any) it does.

"Toolified" = has a tool contract spec + execute_tool branch. Base
functionality may exist but is not callable by the harness until
toolified.

| # | Spec | Base built? | Toolified? | Status |
|---|---|---|---|---|
| 06 | `06_tool_pms1.md` | Yes (`Poony_Multiretrieval_S1/src/`) | Yes | DONE |
| 07 | `07_tool_pteca.md` | Yes (`src/tools/tool_pteca.py`) | Yes | DONE |
| 08 | `08_tool_stencil2chart.md` | Yes (`src/scripts/stencil2chart.py`) | Yes | DONE |
| 09 | `09_tool_askuser.md` | N/A (orchestrator-level only) | Yes | DONE |

**Additional tools (no separate spec — live in `execute_tool.py`):**

| Tool | Description | Status |
|---|---|---|
| `write_session_md` | Write markdown to session dir with `{{embed:$var_N}}` marker resolution | DONE |
| `read_session_md` | Read file from session dir | DONE |
| `inspect_var` | Registry inspection (specced in `03_opaque_registry.md`) | DONE |

### Layer 4: Observability + Iteration

| # | Spec | What it covers | Status |
|---|---|---|---|
| 15 | `15_DebugDaPipelineEtQOL.md` | Always-on debug trace: thread-local TraceBuffer captures all LLM calls + retrieval results, writes per-component per-batch markdown to tests/debug/{session_ts}/ | TODO |
| 16 | `16_SyspromptKaisen.md` | Externalize all system prompts to sysprompts/{role}/{profile}.md, template var resolution, hard crash on missing file/unresolved vars | TODO |

### Build order rationale

```
01 terminal_router     ← dependency of everything (all I/O goes through it)
       │
       ▼
02 agent_loop          ← skeleton: while loop, stop_reason, messages
03 opaque_registry     ← plugs into agent_loop (tool results → handles)
04 system_prompt       ← what the orchestrator LLM is told
05 execute_tool        ← dispatcher that calls tools, uses registry + router
       │
       ▼
06-09 tool contracts   ← each tool plugs into execute_tool
                         can be built/specced independently
                         order: pms1 first (already built), then
                         stencil2chart (already built), then pteca (new)
```

---

## Spec template

Every spec follows this structure:

```markdown
# <Component Name>

<1-line purpose>

## Architecture

<ASCII diagram of component internals + how it connects to neighbors>

## Input contract

<Types, fields, constraints, examples>

## Output contract

<Types, fields, constraints, examples>

## Dependencies

<What this component imports / requires to exist>

## File location

<Where the .py lives>

## Ideal demo

<CLI transcript showing this component in action end-to-end>

## Open questions

<Unresolved design decisions, if any>
```

---

## Ideal demo (full PSOAS run, end-to-end)

```
$ python psoas.py "Chart Best Buy and Amcor gross margins for FY2022 and FY2023"

[ORCHESTRATOR] Understood. Running PMS1 for Best Buy...

[PMS1] Loading index...
[PMS1] Sekei planning... done. 5 cells, 2 batches.
[PMS1] PTO batch A_124 (FY2022 income_statement)... 42 chunks filtered.
[PMS1] PTO judge... tokens in=3200 out=450.
[PMS1] PTO batch B_124 (FY2023 income_statement)... 38 chunks filtered.
[PMS1] PTO judge... tokens in=3100 out=420.
[PMS1] Stencil computed. 5 cells filled.

[ORCHESTRATOR] Running PMS1 for Amcor...

[PMS1] Loading index...
[PMS1] Sekei planning... done. 5 cells, 2 batches.
[PMS1] PTO batch A_124 (FY2022 income_statement)... 0 chunks filtered.

[PMS1] Entity filter returned 0 chunks for "Amcor" in FY2022.
[PMS1] Relax fiscal year filter? [y/n]
> y

[PMS1] Relaxed. Found 31 chunks.
[PMS1] PTO judge... tokens in=2900 out=400.
[PMS1] PTO batch B_124 (FY2023 income_statement)... 29 chunks filtered.
[PMS1] PTO judge... tokens in=2800 out=390.
[PMS1] Stencil computed. 5 cells filled.

[ORCHESTRATOR] Running PTECA with both stencils...

[PTECA-Best Buy+Amcor] 2 firms: Best Buy, Amcor.
[PTECA-Best Buy+Amcor] Here are some chart layout options:

  Option A: One chart per firm (2 charts)
    Best Buy:  Gross Margin %, Net Margin %
    Amcor:     Gross Margin %, Net Margin %

  Option B: One comparison chart (1 chart)
    All firms on same axes

  Option C: Something else?

> option B, just gross margin

[PTECA-Best Buy+Amcor] Got it. 1 chart: Gross Margin comparison.
[PTECA-Best Buy+Amcor] Done.

[ORCHESTRATOR] Rendering charts...

[ORCHESTRATOR] Saved: temp/sessions/20260714170844/output/stencil2charted_1.svg
[ORCHESTRATOR] Saved: temp/sessions/20260714170844/output/stencil2charted_2.svg
[ORCHESTRATOR] Done. 2 charts saved.
```

---

## What already exists

| Component | Location | Status | Notes |
|---|---|---|---|
| PMS1 pipeline | `src/scripts/Poony_Multiretrieval_S1/src/` (sekei, pto, orchestrator, stencil) | Built + Toolified | Tool wrapper in `src/tools/tool_pms1.py`. PUMBA fallback in `pumba.py`. |
| stencil2chart | `src/scripts/stencil2chart.py` (PSOAS-modified copy) | Built + Toolified | ToolChannel param, `matplotlib.use("Agg")`, direct-line labels |
| PTECA | `src/tools/tool_pteca.py` | Built + Toolified | Multi-stencil, always-ask, ASCII chart previews (D14 rewrite) |
| Terminal router | `src/harness/terminal_router.py` | Built | Rich integration, thread-local overrides, harvest_logs |
| Harness | `src/harness/` (agent_loop, opaque_registry, execute_tool, system_prompt) | Built | Outer REPL, transcript enrichment, session I/O tools |
| PUMBA | `src/scripts/Poony_Multiretrieval_S1/src/pumba.py` | Built | Dailo/Gulei/Leng 3-tier LLM retrieval fallback |

### Reference docs (teaching, not specs)

| Doc | Location | What it covers |
|---|---|---|
| Harness design v1→v2 | `Poony_Multiretrieval_S1/idea/0_PMSetPTOetChartasHarnessTool.md` | Tool loop tutorial, v1 naive → v2 opaque handles + sub-tool CLI |
| CLI routing design | `Poony_Multiretrieval_S1/idea/0_CLIrouting.md` | TerminalRouter design, buffering, stdin brokering, implementation sketch |

---

## Spec status

All specs were originally prescriptive (written before code). They
have been reconciled with the actual implementation as of 2026-07-16.
Specs now REFLECT the architecture — they are documentation of what
IS, not what should be.

Deviations from specs during implementation are logged in:
- `ClaudenoDiscretion.md` (D1–D15): harness-wide decisions
- `Specs/12a_PUMBAnoDiscretion.md` (D1–D11): PUMBA-specific decisions

Key import resolution mechanism: `src/__init__.py` extends `__path__`
to search `src/scripts/` then `src/scripts/Poony_Multiretrieval_S1/src/`.
All `from src.XXX import ...` imports resolve through this chain.
PSOAS-modified copies (config.py, llm.py, stencil2chart.py) shadow
PMS1 originals. See D1/D8 in ClaudenoDiscretion.md.

## Handoff notes

- PMS1 is a single-firm pipeline. Multi-firm = orchestrator calls PMS1
  N times. PMS1 itself doesn't change.
- PTECA is an internal agent loop (its own messages list, while loop,
  askUserQuestion tool). Not a dumb function.
- All tools use TerminalRouter via `register("LABEL")` → ToolChannel.
  No raw `print()`/`input()` anywhere.
- Opaque variable handles (`$var_N`) prevent context bloat in
  orchestrator LLM. `_store`/`_resolve` registry in harness.py.
- PUMBA is currently PMS1-internal (called by orchestrator.py when
  PTO fails). Not yet exposed as an orchestrator-level tool — future:
  orchestrator may specify PMS1 call types to PUMBA directly.
- Sequential execution for MVP. Parallel (threading + stdin broker)
  is designed in terminal_router but not MVP-critical.
