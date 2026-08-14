<!--
Date created: 2026-08-10
Date updated: 2026-08-10
Date surroundings last checked: 2026-08-10
-->

# PMS2 callsites — current state (pre-migration)

Every LLM call in PMS2, with its three swap-relevant dependencies: sysprompt file,
tool/backend capability, provider branch. Deterministic post-processing that only
consumes the final stencil (`merge_compute.py`, `stencil_safe_math.py`,
`stencil_topo.py`) is out of scope — it doesn't call a model, so it isn't touched
by a backend swap.

Sources: `grep -rn "pms2_<role>_profile" src/`, `graphify explain "<function>"`,
direct read of `pms2.py`, `dispatcher.py`, `batch_planner.py`, `leng_caller.py`,
`validator_loop.py`, `llm.py`, `sysprompts.py`.

Legend: rectangle = process/action · pill = start/end · red diamond = decision/branch ·
blue parallelogram = hardcoded config/resource feeding a decision.

## LLM callsites — dependency graph

```mermaid
graph TD
    classDef decision fill:#e74c3c,color:#fff,stroke:#7b241c
    classDef resource fill:#2e86de,color:#fff,stroke:#1b4f72
    classDef process fill:#f4f4f4,stroke:#888,color:#111
    classDef llmcall fill:#f4f4f4,stroke:#333,stroke-width:2px,color:#111
    classDef terminator fill:#fff,stroke:#333,color:#111
    classDef gap stroke:#e74c3c,stroke-dasharray:4 3,color:#111

    START(["pms2.py:180<br/>run_pms2_pipeline"]):::terminator

    %% ── LLM call nodes (all 6 PMS2 roles) ──────────────────────────
    SEKEI["sekei_loop.py:168 run_sekei<br/>call_with_tools · Anthropic today"]:::llmcall
    MAPPER["mapper.py:131 run_mapper_for_firm<br/>call_with_tools · Anthropic today"]:::llmcall
    FISCAL["dispatcher.py:110 _resolve_fiscal_calendar<br/>structured_complete(web_search=True) · Anthropic today"]:::llmcall
    BATCH["batch_planner.py:249 run_batch_planner<br/>call_with_tools · Anthropic today"]:::llmcall
    LENG["leng_caller.py:95 _run_single_leng<br/>structured_complete · DeepSeek already"]:::llmcall
    VALID["validator_loop.py:268 run_validator<br/>structured_complete · DeepSeek already"]:::llmcall

    START --> SEKEI
    SEKEI -->|"sekei_loop.py:414<br/>tool call"| MAPPER
    START -.->|"dispatcher.py:253, per firm"| FISCAL
    FISCAL -->|"dispatcher.py:307"| BATCH
    BATCH -->|"batch_planner.py:370<br/>tool call run_leng_caller"| LENG
    LENG -->|"leng_caller.py:168<br/>per leng hit"| VALID

    %% ── Provider branch (shared) ────────────────────────────────────
    PROVIDER_CFG[/"llm.py:39-45<br/>PROVIDER_CONFIG<br/>provider to base_url map"/]:::resource
    MAKELLM{"llm.py:1075 _make_llm<br/>provider branch"}:::decision
    PROVIDER_CFG --> MAKELLM
    ANTHROPIC_CLS["llm.py:553 AnthropicLLM"]:::process
    OAICOMPAT_CLS["llm.py:177 OpenAICompatibleLLM<br/>(deepseek/openai/kimi)"]:::process
    MAKELLM --> ANTHROPIC_CLS
    MAKELLM --> OAICOMPAT_CLS

    CFG_SEKEI[/"config.py:76<br/>pms2_sekei_profile"/]:::resource
    CFG_MAPPER[/"config.py:77<br/>pms2_mapper_profile"/]:::resource
    CFG_FISCAL[/"config.py:78<br/>pms2_fiscal_cal_profile"/]:::resource
    CFG_BATCH[/"config.py:79<br/>pms2_batch_planner_profile"/]:::resource
    CFG_LENG[/"config.py:80<br/>pms2_leng_profile"/]:::resource
    CFG_VALID[/"config.py:81<br/>pms2_validator_profile"/]:::resource

    SEKEI --> CFG_SEKEI --> MAKELLM
    MAPPER --> CFG_MAPPER --> MAKELLM
    FISCAL --> CFG_FISCAL --> MAKELLM
    BATCH --> CFG_BATCH --> MAKELLM
    LENG --> CFG_LENG --> MAKELLM
    VALID --> CFG_VALID --> MAKELLM

    %% ── Sysprompt branch (shared, all but fiscal_cal) ───────────────
    LOADSYS{"sysprompts.py:20 load_sysprompt(role, profile)<br/>path = sysprompts/role/profile.md"}:::decision
    SYS_SEKEI[/"sysprompts/pms2_sekei/<br/>anthropic_opushighthink.md (only file)"/]:::resource
    SYS_MAPPER[/"sysprompts/pms2_mapper/<br/>anthropic_hayasui.md (only file)"/]:::resource
    SYS_BATCH[/"sysprompts/pms2_batch_planner/<br/>anthropic_sonnetmedthink.md (only file)"/]:::resource
    SYS_LENG[/"sysprompts/pms2_leng/<br/>deepseek_v4flash_leng.md + others"/]:::resource
    SYS_VALID[/"sysprompts/pms2_validator/<br/>deepseek_v4pro_validator.md + others"/]:::resource

    SEKEI -->|"sekei_loop.py:190"| LOADSYS
    MAPPER -->|"mapper.py:146"| LOADSYS
    BATCH -->|"batch_planner.py:286"| LOADSYS
    LENG -->|"leng_caller.py:199"| LOADSYS
    VALID -->|"validator_loop.py:291"| LOADSYS
    LOADSYS --> SYS_SEKEI
    LOADSYS --> SYS_MAPPER
    LOADSYS --> SYS_BATCH
    LOADSYS --> SYS_LENG
    LOADSYS --> SYS_VALID

    FISCAL -.->|"dispatcher.py:146<br/>prompt inlined, no sysprompt file"| WEBPROMPT[/"dispatcher.py:146<br/>web_prompt string"/]:::resource

    %% ── Tool/backend capability branch (fiscal_cal only) ────────────
    WEBSEARCH{"llm.py:802 structured_complete<br/>web_search=True branch"}:::decision
    FISCAL -->|"dispatcher.py:162-167<br/>3 attempts, dispatcher.py:159"| WEBSEARCH
    WEBSEARCH -->|"AnthropicLLM: web_search_20250305<br/>tool, llm.py:679"| ANTHROPIC_CLS
    WEBSEARCH -.->|"GAP — OpenAICompatibleLLM:<br/>flag accepted, silently ignored,<br/>llm.py:456. STUB per approved decision."| OAICOMPAT_CLS
    class WEBSEARCH gap
```

## Process view (same scope, decision/process/resource abstraction)

Same 6 LLM callsites, redrawn as process/decision/resource instead of function-to-function
calls. Every decision that gates a repeat LLM call is included (loop caps, retries) since
those are backend-relevant — not deterministic post-processing.

```mermaid
flowchart TD
    START(["run_pms2_pipeline entry<br/>pms2.py:180"]) --> P1

    P1["Sekei: agent-loop LLM call (process)<br/>sekei_loop.py:168"]
    P1 --> D_TURNS{"turn_counter < _MAX_TURNS=8?<br/>(decision — hardcode loop cap)<br/>sekei_loop.py:28,217"}
    D_TURNS -->|"yes, tool call = run_mapper"| P2
    D_TURNS -->|"no, exhausted, no finalize"| ERR1(["RuntimeError — hard crash<br/>sekei_loop.py:370"])
    D_TURNS -->|"finalize_stencil called"| DONE1(["work_stencil, ans_stencil,<br/>job_stencils (data)"])

    P2["Mapper: agent-loop LLM call (process)<br/>mapper.py:131"]
    P2 --> P1

    DONE1 --> P3
    P3["Fiscal Calendar Resolver:<br/>extraction LLM call (process)<br/>dispatcher.py:110"]
    P3 --> D_WEB{"backend implements<br/>web_search tool?<br/>(decision — hardcode by backend class)"}
    D_WEB -->|"AnthropicLLM: yes,<br/>native web_search_20250305 tool<br/>llm.py:679"| P3OK["real FY lookup via web search (process)"]
    D_WEB -.->|"OpenAICompatibleLLM: flag accepted,<br/>silently ignored, no search performed<br/>llm.py:456 — GAP, stubbed per approved decision"| P3GAP["model guesses FY dates<br/>from parametric memory, no signal (process)"]
    P3OK --> D_RETRY
    P3GAP --> D_RETRY
    D_RETRY{"attempt < _FISCAL_CAL_MAX_RETRIES=3<br/>AND calendar empty?<br/>(decision — hardcode retry loop)<br/>dispatcher.py:24,159"}
    D_RETRY -->|"yes, retry"| P3
    D_RETRY -->|"calendar found"| DONE3(["calendar dict (data)"])
    D_RETRY -->|"3 attempts exhausted"| P3ASK["ask_user fallback,<br/>then 1 more extraction call,<br/>no web_search (process)<br/>dispatcher.py:184-204"]
    P3ASK --> DONE3B(["calendar dict or None (data)"])

    DONE3 --> P4
    DONE3B --> P4
    P4["Batch Planner: agent-loop LLM call (process)<br/>batch_planner.py:249"]
    P4 --> D_TURNS2{"turn_counter < _MAX_TURNS=4?<br/>(decision — hardcode loop cap)<br/>batch_planner.py:22,311"}
    D_TURNS2 -->|"yes, tool call = run_leng_caller"| P5
    D_TURNS2 -->|"no, exhausted<br/>batch_planner.py:438-441"| EXH(["status=bp_exhausted —<br/>dispatcher loop stops, firm<br/>returns partially filled"])
    P5["Leng: extraction LLM call,<br/>per chunk (process)<br/>leng_caller.py:95"]
    P5 --> D_MALFORM{"attempt < 1+_LENG_MALFORMED_RETRIES=2<br/>AND cells not parseable as dict?<br/>(decision — hardcode retry,<br/>known failure mode)<br/>leng_caller.py:51,118"}
    D_MALFORM -->|"yes, retry"| P5
    D_MALFORM -->|"parseable, or retries exhausted<br/>(marks _malformed_cells, empty cells)"| P6
    P6["Validator: extraction LLM call,<br/>per leng hit (process)<br/>validator_loop.py:268"]
    P6 --> DONE4(["job_stencil cells filled (data,<br/>mutated under stencil_lock)"])
    DONE4 -.->|"loop: null ans-cells remain?<br/>dispatcher.py:288"| P4

    %% ── shared provider resource fan-in ──────────────────────────
    RES_PROVIDER[/"PROVIDER_CONFIG<br/>(resource, hardcoded map)<br/>llm.py:39-45"/]
    RES_CFG_SEKEI[/"pms2_sekei_profile string<br/>(resource, hardcode)<br/>config.py:76"/]
    RES_CFG_MAPPER[/"pms2_mapper_profile string<br/>(resource, hardcode)<br/>config.py:77"/]
    RES_CFG_FISCAL[/"pms2_fiscal_cal_profile string<br/>(resource, hardcode)<br/>config.py:78"/]
    RES_CFG_BATCH[/"pms2_batch_planner_profile string<br/>(resource, hardcode)<br/>config.py:79"/]
    RES_CFG_LENG[/"pms2_leng_profile string<br/>(resource, hardcode)<br/>config.py:80"/]
    RES_CFG_VALID[/"pms2_validator_profile string<br/>(resource, hardcode)<br/>config.py:81"/]
    RES_YAML[/"llm_profiles.yaml<br/>(resource, disk file —<br/>provider+model+params per profile name)"/]

    D_PROVIDER{"profile name resolves to<br/>provider (decision — hardcode)<br/>llm.py:1075 _make_llm"}
    RES_PROVIDER --> D_PROVIDER
    RES_YAML --> D_PROVIDER
    RES_CFG_SEKEI --> D_PROVIDER
    RES_CFG_MAPPER --> D_PROVIDER
    RES_CFG_FISCAL --> D_PROVIDER
    RES_CFG_BATCH --> D_PROVIDER
    RES_CFG_LENG --> D_PROVIDER
    RES_CFG_VALID --> D_PROVIDER
    P1 -.-> D_PROVIDER
    P2 -.-> D_PROVIDER
    P3 -.-> D_PROVIDER
    P4 -.-> D_PROVIDER
    P5 -.-> D_PROVIDER
    P6 -.-> D_PROVIDER
    D_PROVIDER -->|"anthropic — sekei, mapper,<br/>fiscal_cal, batch_planner (today)"| P_VIA_ANTHROPIC["call proceeds via AnthropicLLM (process)<br/>has web_search tool, no tool-call-as-text quirk<br/>llm.py:553"]
    D_PROVIDER -->|"deepseek — leng, validator (today)"| P_VIA_DEEPSEEK["call proceeds via OpenAICompatibleLLM (process)<br/>no web_search tool, ~11% tool-call-as-text,<br/>own retry built in, llm.py:177,274-276"]

    %% ── shared sysprompt resource fan-in ──────────────────────────
    D_SYSPROMPT{"role+profile resolves to<br/>sysprompt path (decision — hardcode)<br/>sysprompts.py:20"}
    RES_SYS_SEKEI[/"sysprompts/pms2_sekei/<br/>anthropic_opushighthink.md<br/>(resource — only file in dir)"/]
    RES_SYS_MAPPER[/"sysprompts/pms2_mapper/<br/>anthropic_hayasui.md<br/>(resource — only file in dir)"/]
    RES_SYS_BATCH[/"sysprompts/pms2_batch_planner/<br/>anthropic_sonnetmedthink.md<br/>(resource — only file in dir)"/]
    RES_SYS_LENG[/"sysprompts/pms2_leng/<br/>deepseek_v4flash_leng.md + others<br/>(resource — DeepSeek variant exists)"/]
    RES_SYS_VALID[/"sysprompts/pms2_validator/<br/>deepseek_v4pro_validator.md + others<br/>(resource — DeepSeek variant exists)"/]
    P1 -.-> D_SYSPROMPT
    P2 -.-> D_SYSPROMPT
    P4 -.-> D_SYSPROMPT
    P5 -.-> D_SYSPROMPT
    P6 -.-> D_SYSPROMPT
    D_SYSPROMPT -->|"role=sekei"| P_READ_SEKEI["read file at computed path (process)"]
    D_SYSPROMPT -->|"role=mapper"| P_READ_MAPPER["read file at computed path (process)"]
    D_SYSPROMPT -->|"role=batch_planner"| P_READ_BATCH["read file at computed path (process)"]
    D_SYSPROMPT -->|"role=leng"| P_READ_LENG["read file at computed path (process)"]
    D_SYSPROMPT -->|"role=validator"| P_READ_VALID["read file at computed path (process)"]
    RES_SYS_SEKEI --> P_READ_SEKEI
    RES_SYS_MAPPER --> P_READ_MAPPER
    RES_SYS_BATCH --> P_READ_BATCH
    RES_SYS_LENG --> P_READ_LENG
    RES_SYS_VALID --> P_READ_VALID

    RES_FISCAL_PROMPT[/"web_prompt string, inlined<br/>(resource — NOT an externalized file,<br/>no swap point like the other 5 roles)<br/>dispatcher.py:146"/]
    RES_FISCAL_PROMPT -.-> P3

    RES_SCHEMA[/"LENG_OUTPUT_SCHEMA<br/>(resource, hardcode JSON schema)<br/>leng_caller.py:26"/]
    RES_DENOM[/"DENOM_FACTORS, units.json<br/>(resource, hardcode lookup tables)<br/>denom_reconcile.py:6,<br/>hardcode_dependencies/units.json"/]
    RES_SCHEMA -.-> P5
    RES_DENOM -.-> P5
    RES_DENOM -.-> P6

    classDef decision fill:#d32f2f,stroke:#7f1d1d,color:#fff
    classDef resource fill:#1565c0,stroke:#0d3f73,color:#fff
    class D_TURNS,D_WEB,D_RETRY,D_TURNS2,D_MALFORM,D_PROVIDER,D_SYSPROMPT decision
    class RES_PROVIDER,RES_CFG_SEKEI,RES_CFG_MAPPER,RES_CFG_FISCAL,RES_CFG_BATCH,RES_CFG_LENG,RES_CFG_VALID,RES_YAML,RES_SYS_SEKEI,RES_SYS_MAPPER,RES_SYS_BATCH,RES_SYS_LENG,RES_SYS_VALID,RES_FISCAL_PROMPT,RES_SCHEMA,RES_DENOM resource
```

## Validation

- TODO: `mapper.py` (`run_mapper_for_firm`) has no live/E2E test, unit-test coverage only (`tests/test_pms2_unit.py`) — regression risk for any future swap, unaddressed.
