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
| 11 | `11_repl_loop.md` | Outer REPL: interactive prompt, follow-ups, session state persistence, exit handling | TODO |

### Layer 1c: PMS1-internal retrieval

| # | Spec | What it covers | Status |
|---|---|---|---|
| 12 | `12_PUMBA.md` | PUMBA fallback: Dailo/Gulei/Leng 3-tier LLM retrieval when PTO BM25 fails, orchestrator.py integration | TODO |

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
| 06 | `06_tool_pms1.md` | Yes (`Poony_Multiretrieval_S1/src/`) | No | DONE |
| 07 | `07_tool_pteca.md` | No | No | DONE |
| 08 | `08_tool_stencil2chart.md` | Yes (`stencil2chart.py`) | No | DONE |
| 09 | `09_tool_askuser.md` | N/A (orchestrator-level only) | No | DONE |

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

[ORCHESTRATOR] Running PTECA for Best Buy stencil...

[PTECA] Stencil has 5 rows: Revenue, Gross Profit, Gross Margin,
        Net Income, Net Profit Margin.
[PTECA] User asked for gross margins only. Drop Revenue, Gross Profit,
        Net Income, Net Profit Margin?
> yes but keep net margin too

[PTECA] Keeping: Gross Margin, Net Profit Margin.
[PTECA] Both are %. 1 chart.
[PTECA] Done.

[ORCHESTRATOR] Running PTECA for Amcor stencil...

[PTECA] Same structure. Keeping Gross Margin, Net Profit Margin?
> yes

[PTECA] Done.

[ORCHESTRATOR] Rendering charts...

[ORCHESTRATOR] Saved: PSOAS/temp/sessions/20260714170844/output/stencil2charted_1.svg
[ORCHESTRATOR] Saved: PSOAS/temp/sessions/20260714170844/output/stencil2charted_2.svg
[ORCHESTRATOR] Done. 2 charts saved.
```

---

## What already exists

| Component | Location | Base built? | Toolified? | Notes |
|---|---|---|---|---|
| PMS1 pipeline | `Poony_Multiretrieval_S1/src/` (sekei, pto, orchestrator, stencil) | Yes | No | Needs tool wrapper: JSON schema, execute_tool branch, ToolChannel integration, serialize_stencil helper |
| stencil2chart | `Poony_Multiretrieval_S1/src/stencil2chart.py` | Yes | No | Needs tool wrapper. Currently uses raw `print()`/`input()` for gap prompt — must migrate to ToolChannel |
| stencil2chart README | `Poony_Multiretrieval_S1/README_stencil2chart.md` | N/A | N/A | Input/output contract doc |
| PTECA | not built | No | No | Designed in conversation. Internal agent loop. |
| Terminal router | not built | No | N/A | Designed, sketch in CLI routing doc |
| Harness (agent loop, registry, execute_tool) | not built | No | N/A | Designed, sketch in harness doc |

### Reference docs (teaching, not specs)

| Doc | Location | What it covers |
|---|---|---|
| Harness design v1→v2 | `Poony_Multiretrieval_S1/idea/0_PMSetPTOetChartasHarnessTool.md` | Tool loop tutorial, v1 naive → v2 opaque handles + sub-tool CLI |
| CLI routing design | `Poony_Multiretrieval_S1/idea/0_CLIrouting.md` | TerminalRouter design, buffering, stdin brokering, implementation sketch |

---

## Handoff notes

- This session designed stencil2chart (built + tested), PTECA (designed),
  harness architecture (v1 naive → v2 opaque handles + sub-tool CLI),
  terminal router (designed with implementation sketch).
- Teaching docs in `Poony_Multiretrieval_S1/idea/` explain the WHY and
  evolution (v1→v2). Specs in this directory are the WHAT — contracts
  to build from.
- PMS1 is a single-firm pipeline. Multi-firm = orchestrator calls PMS1
  N times. PMS1 itself doesn't change.
- PTECA is an internal agent loop (its own messages list, while loop,
  askUserQuestion tool). Not a dumb function.
- All tools use TerminalRouter via `register("LABEL")` → ToolChannel.
  No raw `print()`/`input()` anywhere.
- Opaque variable handles (`$var_N`) prevent context bloat in
  orchestrator LLM. `_store`/`_resolve` registry in harness.py.
- Sequential execution for MVP. Parallel (threading + stdin broker)
  is designed in terminal_router but not MVP-critical.
