# PSOAS

**Poony Sophomore Orchestrated Analyst Strapon**

Harness that wraps PMS1, PTECA, stencil2chart (and future tools) as
LLM-orchestrated tools with CLI user interaction at any depth.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    psoas.py (entry)                      │
│                         │                               │
│                    ┌────▼────┐                          │
│                    │  REPL   │  outer interactive loop   │
│                    └────┬────┘                          │
│                         │                               │
│               ┌─────────▼──────────┐                   │
│               │    agent_loop      │                   │
│               │  (orchestrator LLM)│                   │
│               └──┬─────┬──────┬───┘                   │
│                  │     │      │                        │
│         ┌────────▼┐ ┌──▼───┐ ┌▼────────────┐         │
│         │  PMS1   │ │PTECA │ │stencil2chart │         │
│         │(tool)   │ │(tool)│ │   (tool)     │         │
│         └────┬────┘ └──────┘ └──────────────┘         │
│              │                                         │
│         ┌────▼────┐                                    │
│         │ PUMBA   │  3-tier LLM fallback retrieval     │
│         └─────────┘                                    │
│                                                        │
│    TerminalRouter ← all I/O goes through this          │
│    OpaqueRegistry ← $var_N handles (context control)   │
└─────────────────────────────────────────────────────────┘
```

## Components

| Component | Location | Description |
|---|---|---|
| Harness | `src/harness/` | agent_loop, execute_tool, opaque_registry, terminal_router, system_prompt |
| PMS1 pipeline | `src/scripts/Poony_Multiretrieval_S1/src/` | Sekei → PTO → Stencil. Single-firm financial data retrieval |
| PUMBA | `src/scripts/Poony_Multiretrieval_S1/src/pumba.py` | Dailo/Gulei/Leng 3-tier LLM retrieval fallback when PTO BM25 fails |
| PTECA | `src/tools/tool_pteca.py` | Multi-stencil chart planning agent (internal agent loop, always-ask) |
| stencil2chart | `src/scripts/stencil2chart.py` | Matplotlib rendering with direct-line labels |
| LLM backend | `src/scripts/llm.py` | Model-agnostic factory: Anthropic, OpenAI, Gemini via `call_with_tools()` |
| Tool wrappers | `src/tools/` | tool_pms1.py, tool_pteca.py |

## Usage

```bash
# Interactive REPL
python src/psoas.py

# Single query
python src/psoas.py "Chart Best Buy and Amcor gross margins for FY2022 and FY2023"
```

## Dependencies

```bash
pip install -r requirements.txt
```

Requires API keys (env vars):
- `ANTHROPIC_API_KEY` — orchestrator LLM (default)
- `OPENAI_API_KEY` — OpenAI-compat backends (DeepSeek, etc.)

## Key design decisions

- **Opaque handles** (`$var_N`): Tool results stored in registry, only handles passed to orchestrator LLM. Prevents context bloat.
- **TerminalRouter**: All I/O through `register("LABEL")` → ToolChannel. No raw print/input anywhere.
- **Sequential execution**: Multi-firm = orchestrator calls PMS1 N times. Parallel designed but not MVP-critical.
- **Model-agnostic**: LLMBackend normalizes tool calling across Anthropic/OpenAI/Gemini APIs.

## Specs

Full design specs in `Specs/`. See `Specs/00_SPEC_INDEX.md` for index.
All specs are reflective (document what IS, reconciled with implementation as of 2026-07-16).

---

## File layout

```
src/
├── psoas.py                  # entry point
├── harness/
│   ├── agent_loop.py         # orchestrator while-loop + stop_reason dispatch
│   ├── execute_tool.py       # tool dispatcher
│   ├── opaque_registry.py    # $var_N store/resolve
│   ├── terminal_router.py    # Rich-based I/O routing
│   ├── system_prompt.py      # orchestrator prompt assembly
│   └── trace.py              # debug trace buffer
├── scripts/
│   ├── llm.py                # LLMBackend factory
│   ├── config.py             # PSOAS-modified config
│   ├── stencil2chart.py      # chart renderer
│   └── Poony_Multiretrieval_S1/src/
│       ├── orchestrator.py   # PMS1 pipeline orchestrator
│       ├── sekei.py          # query → cell plan
│       ├── pto.py            # BM25 retrieval + LLM judge
│       ├── stencil.py        # cell grid data structure
│       └── pumba.py          # 3-tier LLM fallback
└── tools/
    ├── tool_pms1.py          # PMS1 tool contract
    └── tool_pteca.py         # PTECA tool contract
```
