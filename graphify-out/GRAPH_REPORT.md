# Graph Report - wagasyanohimitunaLAG-osha  (2026-08-14)

## Corpus Check
- 143 files · ~136,155 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1710 nodes · 3311 edges · 123 communities (108 shown, 15 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 166 edges (avg confidence: 0.59)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `f4e4e884`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- llm.py
- 00_Ingest.py
- 01_Chunk.py
- execute_tool.py
- BM25Retriever
- PTOBatchRequest
- ingest.py
- test_sekei_orchestrate_pto_stencil.py
- load_sysprompt
- ToolChannel
- IndexManager
- test_m4_integration.py
- ValueError
- pto_judge_diagnostic
- pto.py
- run_phase2
- Terminal UI and Output
- LLM Mocking Utilities
- Config
- bunragman/sekei.py
- terminal_router.py
- Config
- OpenAICompatibleLLM
- Stencil Finalization Testing
- scanner.py
- AnthropicLLM
- Safe Arithmetic Evaluation
- Chart Rendering Utilities
- Chunk Overlap Injection
- PMS1 + PTECA + stencil2chart as Harness Tools
- Phase 2 Integration Tests
- Pipeline Entry Points
- test_zako_unit.py
- AutoChannel
- Opaque Variable Registry
- answer_sheet_contract.py
- File System Utilities
- _topo_sort_rows
- Chart Generation Tests
- Ingest Integration Tests
- Formula Calculation Logic
- Fiscal Period Expansion
- _parse_formula_refs
- Test Logging Utilities
- Chunk Context Tests
- _run_single_leng
- register
- Ingest Output Verification
- Chunk Metadata Validation
- Extraction Error Analysis
- Provider: `deepseek_v4flash_leng`
- File Index Building Tests
- Chunk Storage Verification
- Index Structure Validation
- Path Resolution Tests
- LLM-only Confirmation Gate Bug
- DeepSeek Model Evaluation
- Fiscal Calendar Resolution
- System Architecture
- ZakoSession
- eval_pms2_leng.py
- PMS — Poony Multiretrieval System (S1)
- Selector
- POS — Poony Oneshot Semantic: qualitative retrieval lane spec
- Omaya RAG Eval Agent
- BunragmanSelector
- anthropic_opushighthink.md
- Mac Studio Infrastructure Setup — PMS1/PUS Pipeline
- stencil2chart.py — Input / Output Contract
- CLI Terminal Router
- Ideal research answer: [short title]
- Provider: `deepseek_v4pro_leng`
- Filing Conventions Brief — Metadata Enrichment Agent Handoff
- load_and_chunk_documents
- Optical sector question set — 3 mock analyst sessions
- PMS2 — Poony Multiretrieval System 2
- bunragman_agent_summarize/deepseek_v4pro_bunragman_agent.md
- ._incremental_update
- handoff2 — build Bunragman sekei skeleton (stubbed tools)
- Gate 1 — Manual Graph: System Intention
- handoff1 — OSHA Gate 1 refresh, Bunragman design session
- ._compute_file_hashes
- deepseek_v4pro_orchestrator.md
- pms2_mapper/anthropic_hayasui.md
- pto_hyde/deepseek_v4flash_temp0.md
- pto_judge/deepseek_v4pro_highalloc_temp0.md
- pumba_dailo/deepseek_v4pro_highalloc_temp0.md
- sekei/deepseek_v4pro_highalloc.md
- 24_OSHAg_SubqueryVerify.md
- PMS2 callsites — current state (pre-migration)
- anthropic_sonnetmedthink.md
- pms2_validator/anthropic_hayasui.md
- deepseek_v4flash_validator.md
- deepseek_v4pro_validator.md
- deepseek_v4pro_pteca.md
- wsnBunragMan.md
- _extract_text_excluding_tables
- _tag_chunk_indices
- Pilot question set — LITE bull-case price target session
- Sekei Eval — 2026-07-01 01:57
- wsnBunragMan_simplified.md

## God Nodes (most connected - your core abstractions)
1. `Config` - 52 edges
2. `ToolChannel` - 45 edges
3. `load_sysprompt()` - 40 edges
4. `PTOBatchRequest` - 38 edges
5. `OpenAICompatibleLLM` - 38 edges
6. `LLMBackend` - 35 edges
7. `TraceBuffer` - 32 edges
8. `register()` - 31 edges
9. `ZakoSession` - 29 edges
10. `IndexManager` - 28 edges

## Surprising Connections (you probably didn't know these)
- `EvalCase` --uses--> `Config`  [INFERRED]
  tests/eval_pto_judge.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `TerminalChannel` --uses--> `Config`  [INFERRED]
  tests/run_zako_real.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `PrintChannel` --uses--> `Config`  [INFERRED]
  tests/test_bunragman_agent_live.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `AutoChannel` --uses--> `Config`  [INFERRED]
  tests/test_bunragman_e2e.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `PrintChannel` --uses--> `Config`  [INFERRED]
  tests/test_bunragman_sekei_stub.py → src/scripts/Poony_Multiretrieval_S1/src/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **PMS2 Extraction Failure Modes** — tests_pms2_failuremodes_root_cause_a, tests_pms2_failuremodes_root_cause_b, tests_pms2_failuremodes_root_cause_c, tests_pms2_failuremodes_root_cause_d [EXTRACTED 1.00]
- **Leng Agent Performance Evaluation** — tests_eval_runs_leng_eval_v4flash_20260727_211309, tests_eval_runs_leng_eval_v4pro_20260727_211158, leng_extraction_agent [INFERRED 0.80]

## Communities (123 total, 15 thin omitted)

### Community 0 - "llm.py"
Cohesion: 0.08
Nodes (47): ABC, _get(), _get_api_key(), get_bunragman_sekei_group_llm(), get_bunragman_sekei_llm(), get_orchestrator_llm(), get_pms2_batch_planner_llm(), get_pms2_fiscal_cal_llm() (+39 more)

### Community 1 - "00_Ingest.py"
Cohesion: 0.06
Nodes (56): _build_frontmatter(), _clean_markdown_tables(), _clean_picture_placeholders(), _clean_single_table(), _cleanup_orphans(), _convert_msg_file(), _convert_with_docling(), _convert_with_markitdown() (+48 more)

### Community 2 - "01_Chunk.py"
Cohesion: 0.05
Nodes (59): SentenceSplitter, _build_chunk_prefix(), _build_fpi(), _build_parent_nodes(), _build_recursive_splitter(), _build_semantic_splitter(), _chunk_documents(), _detect_section() (+51 more)

### Community 3 - "execute_tool.py"
Cohesion: 0.07
Nodes (38): _dispatch_parallel(), _dump_turn(), Config, Path, agent_loop.py — Outer REPL + inner agent loop. Wraps the orchestrator while-…, Execute tool calls in parallel. Returns result dicts in order. Result format:…, Append one turn to the transcript file., Execute one tool call, catch exceptions. (+30 more)

### Community 4 - "BM25Retriever"
Cohesion: 0.05
Nodes (47): get_research_decomposer_llm(), Research decomposer LLM — splits open-ended question into sub-questions., BM25Retriever, Path, bm25_index.py — BM25 keyword-search index over docstore chunks. Builds a…, Return the chunk text for a node_id., Return the metadata dict for a node_id., Set of all distinct relative file_path values in the searchable index. (+39 more)

### Community 5 - "PTOBatchRequest"
Cohesion: 0.10
Nodes (41): get_current_trace(), load_cases(), main(), print_judge_result(), print_retrieval_result(), Config, Path, Run PUMBA on a test case, check if returned chunks contain expected values. No… (+33 more)

### Community 6 - "ingest.py"
Cohesion: 0.18
Nodes (19): _clean_table_text(), _extract_tables(), _load_documents(), Document, Path, ingest.py — Financial-document-aware loading and chunking. Handles PDF (via…, Read a plain-text file. Splits on double-newlines as rough sections., Read a markdown file, splitting on headers to keep tables with context.… (+11 more)

### Community 7 - "test_sekei_orchestrate_pto_stencil.py"
Cohesion: 0.08
Nodes (50): Config, Run Sekei on a single test case, return results dict., run_one(), dict_to_plan(), load_saved_cases(), main(), Config, Path (+42 more)

### Community 8 - "load_sysprompt"
Cohesion: 0.06
Nodes (37): load_sysprompt(), sysprompts.py — Load externalized system prompts from .md files. Lookup key is…, Load and resolve a system prompt .md file. Raises: FileNotFoundError: if…, Path, Write all events to a markdown file. Returns filepath., Render all events as LLM-readable markdown., Append LLM call event. Thread-safe., Append retrieval event with full chunk text. Thread-safe. batch_result:… (+29 more)

### Community 9 - "ToolChannel"
Cohesion: 0.06
Nodes (43): _get_override(), Return thread-local override for this channel's label, or None., Bound to a label. Tool code calls .print() and .input() on this., ToolChannel, _handle_override_firm_currency(), _handle_run_leng_caller(), Config, Lock (+35 more)

### Community 10 - "IndexManager"
Cohesion: 0.16
Nodes (11): main(), create_ui(), Build and return the Gradio Blocks app., IndexManager, Config, VectorStoreIndex, Read file_hashes.json; return empty dict if missing or corrupt., Owns the vector-index lifecycle: build, persist, load, and incremental refresh… (+3 more)

### Community 11 - "test_m4_integration.py"
Cohesion: 0.07
Nodes (23): _handle_submit_verdicts(), Lock, Process all verdicts from one Validator. Per verdict: unit check, denom…, config(), docstore(), FakeChannel, file_path_index(), fixture (+15 more)

### Community 12 - "ValueError"
Cohesion: 0.22
Nodes (6): Load a named profile from llm_profiles.yaml., Raise ValueError if required settings are missing., Raise ValueError if required settings are missing., canonicalize_source_identifier(), Resolve a user-provided top-level folder path to an exact identifier., ValueError

### Community 13 - "pto_judge_diagnostic"
Cohesion: 0.25
Nodes (10): Exception, _build_judge_user_prompt(), Assemble the user prompt for judge LLM call., EvalCase, main(), pto_judge_diagnostic(), Config, Like pto_judge but returns (raw_text, parsed_dict_or_None, error_or_None).… (+2 more)

### Community 14 - "pto.py"
Cohesion: 0.09
Nodes (35): NodeWithScore, load_cases(), main(), print_result(), Path, Check if expected values appear in top chunk texts. Returns {metric: "PASS…, Full dump: method_log, hard filter stats, chunks, runner-ups, verdict., validate_result() (+27 more)

### Community 15 - "run_phase2"
Cohesion: 0.13
Nodes (17): _fill_ans_stencil(), _merge_jobs_into_work(), _persist_phase2(), Path, Phase 2: merge job values + compute formula cells. T0 scope:…, Extract per-firm display stencils from work stencil. Only ans-marked rows…, Extract ans-marked values from work stencil into ans stencil. Uses work stencil…, Write work + ans stencils to disk. Job stencils are ephemeral. (+9 more)

### Community 16 - "Terminal UI and Output"
Cohesion: 0.12
Nodes (15): Console, Group, AnswerSlot, _is_table_line(), Look up style by longest prefix match. 'PMS2-leng-Apple' tries: 'PMS2-leng-…, Thread-safe container for passing an answer from the UI thread back to the…, Singleton. Created once at module import. Owns all terminal I/O., Start a spinner. Called before LLM calls. Stops any existing spinner first. (+7 more)

### Community 17 - "LLM Mocking Utilities"
Cohesion: 0.12
Nodes (17): _make_mock_openai_llm(), _mock_empty_response(), _mock_text_response(), _mock_tool_call_response(), Build OpenAICompatibleLLM with mocked OpenAI client., Build a mock OpenAI response with a proper tool_calls array., Build a mock OpenAI response with text content (no tool_calls)., Build a mock OpenAI response with no choices. (+9 more)

### Community 18 - "Config"
Cohesion: 0.14
Nodes (30): Run PUMBA → judge → compare against expected values., run_pumba_judge_test(), Config, All configuration for the query engine in one place. Override defaults by…, Create a Config from optional overrides. API keys are resolved per-provider at…, BatchOutcome, Config, Path (+22 more)

### Community 19 - "bunragman/sekei.py"
Cohesion: 0.06
Nodes (41): bunragman — source-scoped router sitting above OSHA. Splits a query across per-…, _call_bunnavharness_stub(), call_bunragman(), _call_osha(), _call_osha_stub(), _partition_files(), Config, Path (+33 more)

### Community 20 - "terminal_router.py"
Cohesion: 0.09
Nodes (32): clear_override(), _is_separator(), override_channel(), _pipe_table_to_rich(), terminal_router.py — Singleton that owns all terminal I/O. No tool or harness…, Set a thread-local override: prints/inputs on the channel with the given exact…, Remove a thread-local override set by override_channel()., Convert pipe-table lines to a rich.Table. (+24 more)

### Community 21 - "Config"
Cohesion: 0.10
Nodes (19): Config, config.py — Single source of truth for all tunable parameters. Every component…, Create a Config from optional overrides. API keys are resolved per-provider at…, All configuration for the query engine in one place. Override defaults by…, _build_validator_input(), Config, Validator agent loop and submit_verdicts handler. T0 scope:…, Build the initial user message for the Validator. (+11 more)

### Community 22 - "OpenAICompatibleLLM"
Cohesion: 0.19
Nodes (4): OpenAICompatibleLLM, Generic OpenAI-compatible chat completions backend., Serialize LLMResponse to string for trace recording., Structured JSON via tool-call shim (OpenAI-compatible). No thinking —…

### Community 23 - "Stencil Finalization Testing"
Cohesion: 0.19
Nodes (8): _handle_finalize(), Process finalize_stencil tool call. Returns (data, status_string). data =…, Sekei drops a firm → warning printed, not error., Minimal ToolChannel stub for testing., Full 22-row stencil: 2 firms × 11 rows, 3 periods., Sekei submits periods in wrong order → canonical order preserved., _StubChannel, TestHandleFinalize

### Community 24 - "scanner.py"
Cohesion: 0.10
Nodes (31): DirEntry, display_label(), expand_selection(), Inventory, _iter_entries(), _NodeResult, _posix_relative(), Path (+23 more)

### Community 25 - "AnthropicLLM"
Cohesion: 0.10
Nodes (7): AnthropicLLM, GeminiLLM, Like complete(), but also returns a usage dict. Default: delegates to…, Anthropic Claude backend with configurable extended thinking. When…, True if model supports adaptive thinking (4.6+). 4.5 and earlier only support…, Return structured JSON matching schema via tool-call shim. Single tool call…, Google Gemini backend via google-genai SDK.

### Community 26 - "Safe Arithmetic Evaluation"
Cohesion: 0.22
Nodes (5): _eval_node(), AST-walked arithmetic eval. Only +, -, *, / allowed. Used by stencil_topo.py…, Evaluate arithmetic expression. Only +, -, *, / allowed. Wraps SyntaxError ->…, _safe_math_eval(), TestSafeMathEval

### Community 27 - "Chart Rendering Utilities"
Cohesion: 0.20
Nodes (12): _check_gaps(), _nudge_labels(), _prompt_gaps(), Path, stencil2chart.py — Render a display table as a line chart (SVG). Takes a plain…, Render chart_input as a line chart SVG. Args: chart_input: Display table dict…, Collapse to filesystem-safe slug: non-word chars → _, runs collapsed, edges…, Scan series for None values. Return list of gap descriptions. (+4 more)

### Community 28 - "Chunk Overlap Injection"
Cohesion: 0.26
Nodes (7): _inject_overlap(), Overlap injection for chunked documents. Post-chunking pass: injects +/-1k char…, Add +/-1k char overlap from neighboring chunks, per file. Groups nodes by…, _make_mock_nodes(), MockNode, T0 unit tests for PMS2. Pure Python, no LLM, no data files. Tests prove each…, TestInjectOverlap

### Community 29 - "PMS1 + PTECA + stencil2chart as Harness Tools"
Cohesion: 0.06
Nodes (34): CLI multiplexing, Ground up: what is a tool-use loop, Implementation, Not a breaking change, Opaque variable handles (context bloat prevention), Path to PUS (future, not MVP), PMS1 + PTECA + stencil2chart as Harness Tools, PTECA is an internal agent loop (+26 more)

### Community 30 - "Phase 2 Integration Tests"
Cohesion: 0.19
Nodes (8): _build_phase2_fixtures(), Build work/ans/job stencils with simulated Leng-filled values. Uses the…, Full Phase 2 on canonical 2-firm stencil., One dispatcher crashed — only surviving firm merges., All dispatchers crashed — empty completed_jobs., Leng found EV/EBITDA=10.2 directly from analyst note. Merge puts 10.2 into the…, Smoke test: pms2.py successfully imports run_phase2., TestRunPhase2

### Community 31 - "Pipeline Entry Points"
Cohesion: 0.23
Nodes (10): opaque_registry.py — In-memory key-value store for large tool outputs. Prevents…, _check_index_staleness(), _newest_mtime(), Config, Path, PMS2 pipeline entry point. T0 scope: _expand_periods only. M3a:…, Run full PMS2 pipeline. M3a scope: Phase 0 (Sekei) only. Returns display…, Return the newest mtime across all files in directory (recursive). Returns None… (+2 more)

### Community 32 - "test_zako_unit.py"
Cohesion: 0.18
Nodes (22): DiscoveryResult, Any, Run one blocking standalone Zako discovery session., zako_discover(), make_config(), Config, Path, Deterministic Zako contract tests. These tests use temporary names-only fixture… (+14 more)

### Community 34 - "Opaque Variable Registry"
Cohesion: 0.27
Nodes (3): OpaqueRegistry, Any, Drain and return variables stored since last harvest. Returns [(handle,…

### Community 35 - "answer_sheet_contract.py"
Cohesion: 0.15
Nodes (23): _assign_local_citation_labels(), _attach_standalone_citations(), CitationAssignments, encode_query_identifier(), expand_answer_sheet_for_summary(), _extract_atomic_references(), format_answer_sheet(), _normalize_draft() (+15 more)

### Community 36 - "File System Utilities"
Cohesion: 0.24
Nodes (8): _handle_list_dir(), _handle_report_dirs(), Path, Validate dirs, walk, return (file_inventory, status). Returns (inventory,…, Walk directories, collect file inventory with filetype., List contents of a directory under data_dir. Returns newline-separated listing., _walk_dirs(), TestWalkDirs

### Community 37 - "_topo_sort_rows"
Cohesion: 0.13
Nodes (12): _assign_structure(), Assign row numbers, rewrite formulas, build stencils. Rows are firm-specific…, Topo sort all rows, per-firm independently, concatenate. Firm order in output…, _topo_sort_rows(), _make_canonical_raw_rows(), Unfilled retrieve -> compute stays null -> ans shows null., EV/EBITDA (depth 2) depends on EV and EBITDA (depth 1), which depend on…, Leng stored 32.8 (denom miss — should be 0.328 for a ratio). Formula gives… (+4 more)

### Community 38 - "Chart Generation Tests"
Cohesion: 0.27
Nodes (8): _check_gaps(), _prompt_gaps(), Path, stencil2chart.py — Render a display table as a line chart (SVG). Takes a plain…, Scan series for None or NaN values. Return list of gap descriptions., Print gap report and prompt user for action. Returns 1, 2, or 3., Render chart_input as a line chart SVG. Args: chart_input: Display table dict…, stencil2chart()

### Community 39 - "Ingest Integration Tests"
Cohesion: 0.27
Nodes (9): docstore(), fpi(), _import_script(), manifest(), fixture, Path, M0 integration + unit tests for PMS2 ingest pipeline. Runs 00_Ingest + 01_Chunk…, Run both scripts once. Incremental = instant if already done. (+1 more)

### Community 40 - "Formula Calculation Logic"
Cohesion: 0.36
Nodes (3): _compute_formula_cells(), Fill compute cells whose value is still None. Iterates rows in ascending row…, TestComputeFormulaCells

### Community 41 - "Fiscal Period Expansion"
Cohesion: 0.36
Nodes (3): _expand_periods(), Expand FY periods by granularity. Already-expanded pass through. Guard: annual…, TestExpandPeriods

### Community 42 - "_parse_formula_refs"
Cohesion: 0.23
Nodes (6): _parse_formula_refs(), Topological sort, formula rewrite, and stencil structure assignment. Pure…, Extract metric names from {MetricName} or {MetricName}[-k] syntax. Cross-column…, Topological sort for one firm's rows. Returns rows in sorted order. Raises…, _topo_sort_firm(), TestParseFormulaRefs

### Community 43 - "Test Logging Utilities"
Cohesion: 0.28
Nodes (5): load_test_cases(), main(), Path, Write to both stdout and a file., Tee

### Community 45 - "_run_single_leng"
Cohesion: 0.24
Nodes (7): Run one Leng structured_complete call for one chunk. Returns (node_id,…, _run_single_leng(), Tests that _run_single_leng rejects flat cell values., Cells like {"A1": 17163} instead of {"A1": {"value": ...}} → retry., Empty cells dict {} should pass (valid — no hits)., All retries return flat values → _malformed_cells=True., TestLengCellValueShapeGuard

### Community 46 - "register"
Cohesion: 0.21
Nodes (11): Register (or retrieve) a ToolChannel for the given label. Idempotent: same…, register(), config.py — Single source of truth for all tunable parameters. Every component…, index_store.py — Persistent vector-index lifecycle with change detection. Uses…, build_request_from_args(), build_request_from_case(), load_cases(), main() (+3 more)

### Community 49 - "Extraction Error Analysis"
Cohesion: 0.33
Nodes (6): DeepSeek V4 Flash, Leng Extraction Agent, Leng Eval v4flash 2026-07-27, Temporal Mismatch (First-Write-Wins), Semantic Confusion (Price Target vs Share Price), EPS Source Inconsistency

### Community 50 - "Provider: `deepseek_v4flash_leng`"
Cohesion: 0.09
Nodes (22): Cross-provider comparison, innolight_call19_datacenter_miss (2.8s, ~$0.0001), innolight_call19_datacenter_miss (4.7s, ~$0.0004), innolight_call20_crshk_cover (12.6s, ~$0.0012), innolight_call20_crshk_cover (9.7s, ~$0.0004), Leng Eval — 2026-07-27 21:23:56, lite_call1_risks_miss (2.7s, ~$0.0001), lite_call1_risks_miss (3.3s, ~$0.0004) (+14 more)

### Community 65 - "ZakoSession"
Cohesion: 0.27
Nodes (6): The configured source root cannot be safely scanned., ScanError, RuntimeError, Own one complete blocking discovery interaction., _SessionAbort, ZakoSession

### Community 66 - "eval_pms2_leng.py"
Cohesion: 0.15
Nodes (18): _load_profiles(), Path, Look up profile by name. All params come from the profile — no overrides., _resolve_profile(), Denomination factors and unit aliases. Constants only. Imported by…, build_leng_prompt(), grade_case(), load_cases() (+10 more)

### Community 67 - "PMS — Poony Multiretrieval System (S1)"
Cohesion: 0.11
Nodes (17): API keys, Architecture, compute_stencil: denomination/unit aware computation, Filing convention registry, Gradio UI (gradio_ui.py), hardcode_dependencies/ — centralized reference data, Judge retry loop, Key dependencies on reference data (+9 more)

### Community 68 - "Selector"
Cohesion: 0.13
Nodes (12): Standalone Zako discovery public API., Any, Protocol, Injected selector dependency used by the Zako session., Document the callable shape while keeping test doubles lightweight., Selector, selector_from_callable(), Any (+4 more)

### Community 69 - "POS — Poony Oneshot Semantic: qualitative retrieval lane spec"
Cohesion: 0.12
Nodes (16): Data structures, How it fits in the pipeline, Key differences from PTO, Not specified yet (deferred), Orchestrator changes, POS pipeline (mirrors PTO, 3 stages), POS — Poony Oneshot Semantic: qualitative retrieval lane spec, POSBatch (+8 more)

### Community 70 - "Omaya RAG Eval Agent"
Cohesion: 0.12
Nodes (15): Advanced RAG techniques, Architecture reference, Bandaid vs surgical, Behavioral rules, Change impact levels, Eval taxonomy, Fix philosophy, Key files (+7 more)

### Community 71 - "BunragmanSelector"
Cohesion: 0.18
Nodes (12): BunragmanSelector, parse_selection(), The strict, single-call Bunragman selector boundary for Zako., The model response violates the bare JSON-array contract., Production adapter using one raw completion per discovery attempt., Parse and validate one strict raw Bunragman selection response., SelectionFormatError, Explicit names-only Bunragman evaluation for Zako. This file is intentionally… (+4 more)

### Community 72 - "anthropic_opushighthink.md"
Cohesion: 0.12
Nodes (15): ans flag, Critical constraints, Formula syntax, Metric decomposition — domain knowledge, Row types, Rows are firm-specific, Same scaffold across firms, Stencil design rules (+7 more)

### Community 73 - "Mac Studio Infrastructure Setup — PMS1/PUS Pipeline"
Cohesion: 0.13
Nodes (14): EMAIL VERSION — Meeting Agenda / Pre-Read, Gate 1: Device Trust / Conditional Access, Gate 2: DLP / Data Loss Prevention, Gate 3: Network, Gate 4: Authentication / App Registration, Gate 5: Outbound Egress to Specific Domains, Gate 6: General / Miscellaneous, Later (not this meeting) (+6 more)

### Community 74 - "stencil2chart.py — Input / Output Contract"
Cohesion: 0.13
Nodes (14): Config, Constraints, Example usage, Fields, Function signature, Gap handling (missing values), Harness adaptation needed, Input: `chart_input` dict (+6 more)

### Community 75 - "CLI Terminal Router"
Cohesion: 0.15
Nodes (12): CLI Terminal Router, Existing code changes needed, Full architecture diagram, How the blocking works (implementation sketch), How tools use it, Input flow (one question), Integration with harness tool loop, Multiple questions pending (+4 more)

### Community 76 - "Ideal research answer: [short title]"
Cohesion: 0.17
Nodes (11): Answer, Answer Template:, Citation audit, Derived math, Expected-answer chunk coverage, Ideal research answer: [short title], Independent QA, Interpretation and limits (+3 more)

### Community 77 - "Provider: `deepseek_v4pro_leng`"
Cohesion: 0.17
Nodes (11): innolight_crshk_cover_table, innolight_datacenter_miss, Leng Eval — 2026-07-27 21:09:35, lite_balance_sheet_debt, lite_cpo_revenue_miss, lite_mizuho_estimates, lite_mizuho_mktcap_price, lite_risks_miss (+3 more)

### Community 78 - "Filing Conventions Brief — Metadata Enrichment Agent Handoff"
Cohesion: 0.18
Nodes (10): Agent behavioral rules, Approach — filing convention detection, Context, Filing Conventions Brief — Metadata Enrichment Agent Handoff, Illustrated examples (3 of ~30 conventions), Problem, SEC 10-K, SEC 10-Q (+2 more)

### Community 79 - "load_and_chunk_documents"
Cohesion: 0.20
Nodes (11): _chunk_documents(), load_and_chunk_documents(), _merge_stub_headers(), Config, Walk data/, load supported files, and chunk them for indexing. Returns a list…, Merge consecutive stub header docs into the next substantive doc. _read_md…, Detect pipe-delimited tables in text Documents and split them out as…, Split large table Documents at internal sub-header boundaries. Financial tables… (+3 more)

### Community 80 - "Optical sector question set — 3 mock analyst sessions"
Cohesion: 0.20
Nodes (9): Notes on this set, Optical sector question set — 3 mock analyst sessions, Session A — LITE / COHR / CIEN: segment, valuation, market-share comps, Session B — CY/FY25-27 growth comparison: optical business of Furukawa, LITE, COHR, Session C — Sumitomo Electric: capacity only, Session D — Sector TAM and market structure, Session E — Price-target evolution across brokers and time (LITE, with a COHR comparison), Session F — LITE quarterly earnings trajectory (4 consecutive real quarters) (+1 more)

### Community 81 - "PMS2 — Poony Multiretrieval System 2"
Cohesion: 0.20
Nodes (9): Agent loop pattern, Architecture, Data layout, File map, Key mechanisms, LLM roles, PMS2 — Poony Multiretrieval System 2, Stencil model (+1 more)

### Community 82 - "bunragman_agent_summarize/deepseek_v4pro_bunragman_agent.md"
Cohesion: 0.20
Nodes (9): Answer, Answer Template:, Citation audit, Derived math, Expected-answer chunk coverage, Independent QA, Interpretation and limits, Retrieved-chunk ledger (+1 more)

### Community 83 - "._incremental_update"
Cohesion: 0.25
Nodes (5): TextNode, Scan data/ and re-index only files that changed, appear, or disappeared., Load and chunk only the specified file paths, attaching ref_doc_id. We…, Write file_hashes.json to the index directory., Save index and file-hash registry to disk.

### Community 84 - "handoff2 — build Bunragman sekei skeleton (stubbed tools)"
Cohesion: 0.25
Nodes (7): 1. Global goal, 2. Session goal, 3. What sekei needs, 4. Explicitly out of scope this pass, 5. Confirmed next step, 6. Files to read, handoff2 — build Bunragman sekei skeleton (stubbed tools)

### Community 85 - "Gate 1 — Manual Graph: System Intention"
Cohesion: 0.29
Nodes (6): Frontmatter, Gate 1 — Manual Graph: System Intention, Gate 1 — Process View (same scope, decision/process/resource abstraction), Node table (reproducible via `graphify explain "<name>"`), Observations at this resolution (not failure modes yet — Gate 7 territory, noted only for later gates), Problem space (partial — two bullets only, not full Gate 2)

### Community 86 - "handoff1 — OSHA Gate 1 refresh, Bunragman design session"
Cohesion: 0.29
Nodes (6): 1. Global goal, 2. Session goal, 3. Narrative, 4. Concrete findings worth flagging forward, 5. Confirmed next step, handoff1 — OSHA Gate 1 refresh, Bunragman design session

### Community 87 - "._compute_file_hashes"
Cohesion: 0.29
Nodes (5): Path, SHA-256 for every supported file in data/., Capture hashes of all currently-indexed files., Compute the SHA-256 hex digest of a file (fast for files up to ~50 MB)., _sha256_file()

### Community 88 - "deepseek_v4pro_orchestrator.md"
Cohesion: 0.29
Nodes (6): Behavior, Guardrails, Opaque variable handles, Sub-tool user interaction, Typical workflow, Your tools

### Community 89 - "pms2_mapper/anthropic_hayasui.md"
Cohesion: 0.33
Nodes (5): Directory layout, How to navigate, Instructions from Sekei, Rules, Your task

### Community 90 - "pto_hyde/deepseek_v4flash_temp0.md"
Cohesion: 0.33
Nodes (5): Output format, Rules, What a table chunk looks like in our corpus, Why this matters, Your task

### Community 91 - "pto_judge/deepseek_v4pro_highalloc_temp0.md"
Cohesion: 0.33
Nodes (5): Denomination and unit extraction, Input, Output format, Rules, Task

### Community 92 - "pumba_dailo/deepseek_v4pro_highalloc_temp0.md"
Cohesion: 0.33
Nodes (5): Context, Data directory structure, Rules, Strategy, Your tools

### Community 93 - "sekei/deepseek_v4pro_highalloc.md"
Cohesion: 0.33
Nodes (5): Cell naming convention (STRICT — like Excel), Output schema (JSON only, no explanation), Reference: retrievable line items, Rules, Your task

### Community 94 - "24_OSHAg_SubqueryVerify.md"
Cohesion: 0.40
Nodes (4): Frontmatter, Gate 1 — Manual Graph: Imagined Replacement (nothing below is built yet), Problem space, Solution space (idea only)

### Community 95 - "PMS2 callsites — current state (pre-migration)"
Cohesion: 0.40
Nodes (4): LLM callsites — dependency graph, PMS2 callsites — current state (pre-migration), Process view (same scope, decision/process/resource abstraction), Validation

### Community 96 - "anthropic_sonnetmedthink.md"
Cohesion: 0.40
Nodes (4): Context, Output, Rules, Your task

### Community 97 - "pms2_validator/anthropic_hayasui.md"
Cohesion: 0.40
Nodes (4): Fiscal calendar, Output, Rules, Your task

### Community 98 - "deepseek_v4flash_validator.md"
Cohesion: 0.40
Nodes (4): Fiscal calendar, Output, Rules, Your task

### Community 99 - "deepseek_v4pro_validator.md"
Cohesion: 0.40
Nodes (4): Fiscal calendar, Output, Rules, Your task

### Community 100 - "deepseek_v4pro_pteca.md"
Cohesion: 0.40
Nodes (4): Finalize format, Rules, What you MUST do, What you receive

### Community 101 - "wsnBunragMan.md"
Cohesion: 0.50
Nodes (3): Bunragman low level, Musing, Process diagram

### Community 102 - "_extract_text_excluding_tables"
Cohesion: 0.50
Nodes (4): _extract_text_excluding_tables(), Extract page text blocks, excluding regions captured as tables. If tables are…, True if two rectangles overlap., _rects_overlap()

### Community 103 - "_tag_chunk_indices"
Cohesion: 0.50
Nodes (4): Reverse-lookup a directory name against FIRM_SYNONYMS. e.g. "BESTBUY" → found…, Add chunk_index, fiscal_year, dir_implied_firm, dir_implied_sector., _resolve_dir_implied_firm(), _tag_chunk_indices()

## Knowledge Gaps
- **271 isolated node(s):** `Frontmatter`, `Problem space (partial — two bullets only, not full Gate 2)`, `Node table (reproducible via `graphify explain "<name>"`)`, `Observations at this resolution (not failure modes yet — Gate 7 territory, noted only for later gates)`, `Gate 1 — Process View (same scope, decision/process/resource abstraction)` (+266 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Config` to `llm.py`, `execute_tool.py`, `BM25Retriever`, `PTOBatchRequest`, `ingest.py`, `test_sekei_orchestrate_pto_stencil.py`, `load_sysprompt`, `ToolChannel`, `IndexManager`, `test_m4_integration.py`, `ValueError`, `pto_judge_diagnostic`, `pto.py`, `bunragman/sekei.py`, `Config`, `OpenAICompatibleLLM`, `scanner.py`, `AnthropicLLM`, `test_zako_unit.py`, `AutoChannel`, `Test Logging Utilities`, `register`, `Selector`, `BunragmanSelector`?**
  _High betweenness centrality (0.126) - this node is a cross-community bridge._
- **Why does `_chunk_documents()` connect `01_Chunk.py` to `ValueError`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Why does `OpenAICompatibleLLM` connect `OpenAICompatibleLLM` to `llm.py`, `File System Utilities`, `_topo_sort_rows`, `Formula Calculation Logic`, `Fiscal Period Expansion`, `_parse_formula_refs`, `test_m4_integration.py`, `_run_single_leng`, `run_phase2`, `LLM Mocking Utilities`, `Config`, `Stencil Finalization Testing`, `AnthropicLLM`, `Safe Arithmetic Evaluation`, `Chunk Overlap Injection`, `Phase 2 Integration Tests`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 36 inferred relationships involving `Config` (e.g. with `AnthropicLLM` and `GeminiLLM`) actually correct?**
  _`Config` has 36 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `PTOBatchRequest` (e.g. with `BatchOutcome` and `Config`) actually correct?**
  _`PTOBatchRequest` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `OpenAICompatibleLLM` (e.g. with `Config` and `MockNode`) actually correct?**
  _`OpenAICompatibleLLM` has 19 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Frontmatter`, `Problem space (partial — two bullets only, not full Gate 2)`, `Node table (reproducible via `graphify explain "<name>"`)` to the rest of the system?**
  _271 weakly-connected nodes found - possible documentation gaps or missing edges._