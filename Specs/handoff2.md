---
Created: 2026-08-13
Updated: 2026-08-13
Last checked: 2026-08-13
---

# handoff2 — build Bunragman sekei skeleton (stubbed tools)

## 1. Global goal

OSHA (research pipeline) revision for PSOAS — the retrieval/synthesis pipeline
that answers open-ended research questions against the document corpus.
Bunragman is a source-scoped router sitting above OSHA: it splits a query
across per-source (firm/broker/dir/sector, whatever the query calls for)
retrieval paths, one Bunragman agent per source, so no single shared
retrieval budget lets one source drown out another.

## 2. Session goal

Build sekei — Bunragman's top-level dispatcher/reconciler — as real code.
Control flow matches `Specs/wsnBunragMan.md`'s process diagram (`SEKEI`
through `RECONCILE`). Everything sekei calls this pass — discovery tool, OSHA
call, BunNav call, and the Bunragman per-source agent itself — is a stub: a
fake function with a hardcoded input/output, not a real implementation. Real
tools are being built separately (delegated elsewhere); the skeleton doesn't
wait on them, and stubs get swapped for real calls later.

This isn't a new pattern in this codebase: `src/scripts/PMS2/sekei_loop.py`
was built the same way — its own docstring stages it as "M1 scope: ask_user +
finalize_stencil. run_mapper stubbed. M2: run_mapper wired to real mapper
agent loops." Build Bunragman's sekei to that same staged shape.

## 3. What sekei needs

- One new LLM role, sekei only — nothing else in this pass gets a real model
  call, including the Bunragman per-source agent. Model-agnostic and
  abstracted through config exactly like the existing `orchestrator_profile`
  role — no model name hardcoded into sekei's own code, only referenced by
  profile name. New entry in `src/scripts/llm_profiles.yaml` (DeepSeek,
  tool-calling — same shape as `orchestrator_profile`), a matching new
  `bunragman_sekei_profile` field in `src/scripts/config.py`, and a new
  `get_bunragman_sekei_llm()` factory in `src/scripts/llm.py`. Dedicated
  entry, not a reuse of the existing `orchestrator_profile` role.
- Sekei's own logic: call discovery tool (stub) → decide proceed/fail on its
  result → fan out one stubbed Bunragman-agent call per resolved source →
  collect each stub's returned summary filepath → once all are in, reconcile
  (real LLM call) into the final answer.

## 4. Explicitly out of scope this pass

- PMS2 — untouched, no changes, no porting.
- Real discovery tool, real OSHA file-scoping, real BunNavHarness call — all
  stubbed, delegated elsewhere.
- Real Bunragman per-source agent (partition / conflict-decision /
  summary-writing) — stubbed, no LLM call.
- Everything in `wsnBunragMan.md`'s open-items list (conflict schema,
  conflict flag format, discovery tool design, max-chunks assumption) — none
  of it blocks a stubbed skeleton; revisit once real tools land.

## 5. Confirmed next step

Build sekei to the diagram in `Specs/wsnBunragMan.md`, with the LLM role from
section 3 wired in and every external call stubbed per section 4. If the
diagram and this doc ever disagree, the diagram is the source of truth for
control flow — this doc is just the build-order note.

## 6. Files to read

- `Specs/wsnBunragMan.md` — diagram, source of truth for control flow.
- `Specs/23_OSHAg.md` — OSHA's own Gate 1 spec; what sekei is dispatching
  work into underneath the Bunragman layer.
- `question_sets/failSystematicLog.md` — the recurring failures motivating
  this revision in the first place (starvation under a shared chunk cap,
  xlsx chunking corruption, etc.) — worth a skim for why this shape exists.
- `src/scripts/PMS2/sekei_loop.py` — direct precedent: staged stub→real
  build, same pattern this session needs.
- `src/scripts/research/__init__.py` — `run_research_pipeline()`, the entry
  point sekei wraps.
- `src/harness/execute_tool.py:282` — `_exec_research()`, how OSHA is
  dispatched today.
- `src/harness/opaque_registry.py` — the handle-storage convention other
  tool results already use; relevant if sekei's stub-to-real tool calls
  should follow it.
- `src/scripts/config.py` — where `bunragman_sekei_profile` gets added.
- `src/scripts/llm.py` — where `get_bunragman_sekei_llm()` gets added.
- `src/scripts/llm_profiles.yaml` — where the new DeepSeek profile entry
  goes.
- `sysprompts/pms2_sekei/` — sysprompt precedent, same `load_sysprompt()`
  convention Bunragman's sekei will need.
