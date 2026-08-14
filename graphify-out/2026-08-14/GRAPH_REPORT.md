# Graph Report - .  (2026-08-10)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1133 nodes · 2410 edges · 65 communities (55 shown, 10 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 101 edges (avg confidence: 0.57)
- Token cost: 5,086 input · 752 output

## Community Hubs (Navigation)
- LLM Backend Clients
- Document Ingestion Pipeline
- Document Chunking Logic
- Agent Execution Loop
- BM25 Search Index
- Retrieval Evaluation Tests
- Financial Document Loading
- Pipeline UI and Visualization
- System Prompts and Tracing
- Extraction Dispatcher Loop
- Index and App Management
- Extraction and Validation Runners
- Query Engine Configuration
- Tabular Retrieval Logic
- Retrieval Ranking and Fusion
- Data Merging and Persistence
- Terminal UI and Output
- LLM Mocking Utilities
- Top-level Orchestration
- Tool Communication Channels
- Terminal I/O Routing
- Global Configuration Constants
- OpenAI API Integration
- Stencil Finalization Testing
- Verdict Validation Testing
- Anthropic API Integration
- Safe Arithmetic Evaluation
- Chart Rendering Utilities
- Chunk Overlap Injection
- Topological Stencil Sorting
- Phase 2 Integration Tests
- Pipeline Entry Points
- Gemini API Integration
- Live Agent Integration Tests
- Opaque Variable Registry
- Batch Extraction Planning
- File System Utilities
- Stencil Structure Assignment
- Chart Generation Tests
- Ingest Integration Tests
- Formula Calculation Logic
- Fiscal Period Expansion
- Formula Reference Parsing
- Test Logging Utilities
- Chunk Context Tests
- Extraction Schema Validation
- PUMBA CLI Testing
- Ingest Output Verification
- Chunk Metadata Validation
- Extraction Error Analysis
- Markdown Trace Export
- File Index Building Tests
- Chunk Storage Verification
- Index Structure Validation
- Path Resolution Tests
- Pipeline Logic Bugs
- DeepSeek Model Evaluation
- Fiscal Calendar Resolution
- System Architecture

## God Nodes (most connected - your core abstractions)
1. `Config` - 42 edges
2. `ToolChannel` - 40 edges
3. `PTOBatchRequest` - 38 edges
4. `OpenAICompatibleLLM` - 38 edges
5. `load_sysprompt()` - 32 edges
6. `TraceBuffer` - 32 edges
7. `register()` - 31 edges
8. `LLMBackend` - 31 edges
9. `IndexManager` - 28 edges
10. `run_leng_caller()` - 23 edges

## Surprising Connections (you probably didn't know these)
- `EvalCase` --uses--> `Config`  [INFERRED]
  tests/eval_pto_judge.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `FakeChannel` --uses--> `Config`  [INFERRED]
  tests/test_m4_integration.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `AutoChannel` --uses--> `Config`  [INFERRED]
  tests/test_pms2_m1_live.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `AutoChannel` --uses--> `Config`  [INFERRED]
  tests/test_pms2_m3b_live.py → src/scripts/Poony_Multiretrieval_S1/src/config.py
- `ChannelFactory` --uses--> `Config`  [INFERRED]
  tests/test_pms2_m3b_live.py → src/scripts/Poony_Multiretrieval_S1/src/config.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **PMS2 Extraction Failure Modes** — tests_pms2_failuremodes_root_cause_a, tests_pms2_failuremodes_root_cause_b, tests_pms2_failuremodes_root_cause_c, tests_pms2_failuremodes_root_cause_d [EXTRACTED 1.00]
- **Leng Agent Performance Evaluation** — tests_eval_runs_leng_eval_v4flash_20260727_211309, tests_eval_runs_leng_eval_v4pro_20260727_211158, leng_extraction_agent [INFERRED 0.80]

## Communities (65 total, 10 thin omitted)

### Community 0 - "LLM Backend Clients"
Cohesion: 0.06
Nodes (61): ABC, _get(), _get_api_key(), get_orchestrator_llm(), get_pms2_batch_planner_llm(), get_pms2_fiscal_cal_llm(), get_pms2_leng_llm(), get_pms2_mapper_llm() (+53 more)

### Community 1 - "Document Ingestion Pipeline"
Cohesion: 0.05
Nodes (64): _build_frontmatter(), _clean_markdown_tables(), _clean_picture_placeholders(), _clean_single_table(), _cleanup_orphans(), _convert_msg_file(), _convert_with_docling(), _convert_with_markitdown() (+56 more)

### Community 2 - "Document Chunking Logic"
Cohesion: 0.06
Nodes (51): SentenceSplitter, _build_chunk_prefix(), _build_fpi(), _build_parent_nodes(), _build_recursive_splitter(), _build_semantic_splitter(), _chunk_documents(), _detect_section() (+43 more)

### Community 3 - "Agent Execution Loop"
Cohesion: 0.06
Nodes (43): _dispatch_parallel(), _dump_turn(), Config, Path, agent_loop.py — Outer REPL + inner agent loop. Wraps the orchestrator while-…, Execute tool calls in parallel. Returns result dicts in order. Result format:…, Append one turn to the transcript file., Execute one tool call, catch exceptions. (+35 more)

### Community 4 - "BM25 Search Index"
Cohesion: 0.06
Nodes (37): BM25Retriever, Path, bm25_index.py — BM25 keyword-search index over docstore chunks. Builds a…, Return the chunk text for a node_id., Return the metadata dict for a node_id., SHA-256 of docstore.json, used for staleness detection., Build BM25 index from docstore.json and persist to disk., Try to load cached BM25 index from disk. Returns True if the cached index is… (+29 more)

### Community 5 - "Retrieval Evaluation Tests"
Cohesion: 0.10
Nodes (40): get_current_trace(), load_cases(), main(), print_judge_result(), print_retrieval_result(), Config, Path, Run PUMBA on a test case, check if returned chunks contain expected values. No… (+32 more)

### Community 6 - "Financial Document Loading"
Cohesion: 0.09
Nodes (38): _chunk_documents(), _clean_table_text(), _extract_tables(), _extract_text_excluding_tables(), load_and_chunk_documents(), _load_documents(), _merge_stub_headers(), Config (+30 more)

### Community 7 - "Pipeline UI and Visualization"
Cohesion: 0.12
Nodes (34): Config, Run Sekei on a single test case, return results dict., run_one(), Config, Run full pipeline on one saved case. Verbose dump., run_one(), gradio_ui.py — S1 Pipeline Visualizer for Poony Multiretrieval S1. Visualizes…, Horizontal flex row of batch cards. Content depends on batch phase. (+26 more)

### Community 8 - "System Prompts and Tracing"
Cohesion: 0.11
Nodes (26): load_sysprompt(), sysprompts.py — Load externalized system prompts from .md files. Lookup key is…, Load and resolve a system prompt .md file. Raises: FileNotFoundError: if…, clear_current_trace(), trace.py — Always-on debug trace system. Thread-local TraceBuffer captures non-…, Wrap fn so child thread inherits parent's TraceBuffer., Append LLM call event. Thread-safe., Append retrieval event with full chunk text. Thread-safe. batch_result:… (+18 more)

### Community 9 - "Extraction Dispatcher Loop"
Cohesion: 0.09
Nodes (29): _describe_missing_cells(), DispatcherState, _get_null_ans_cells(), _prune_satisfied_helpers(), Config, Path, Phase 1: Dispatcher — per-firm extraction loop. M3b scope: full loop +…, Look up firm's fiscal year-end, return period->date mapping. 3 attempts via… (+21 more)

### Community 10 - "Index and App Management"
Cohesion: 0.09
Nodes (21): main(), create_ui(), Build and return the Gradio Blocks app., IndexManager, Config, Path, TextNode, VectorStoreIndex (+13 more)

### Community 11 - "Extraction and Validation Runners"
Cohesion: 0.08
Nodes (29): _build_cell_descriptions(), _build_fiscal_calendar_text(), Config, Lock, Path, Run one Leng structured_complete call for one chunk. Returns (node_id,…, Run one Validator for one Leng hit. Returns (file_path, cell_outcomes)., Execute extraction plan: chunk fetch -> Leng x N -> Validator per hit. Returns… (+21 more)

### Community 12 - "Query Engine Configuration"
Cohesion: 0.11
Nodes (23): dict_to_plan(), load_saved_cases(), main(), Path, Convert saved sekei_output dict → SekeiPlan., Config, config.py — Single source of truth for all tunable parameters. Every component…, All configuration for the query engine in one place. Override defaults by… (+15 more)

### Community 13 - "Tabular Retrieval Logic"
Cohesion: 0.13
Nodes (26): Exception, _all_statement_titles(), _build_judge_system(), _build_judge_user_prompt(), _deterministic_hyde(), _generate_hyde(), _llm_hyde_good(), pto_judge() (+18 more)

### Community 14 - "Retrieval Ranking and Fusion"
Cohesion: 0.12
Nodes (25): NodeWithScore, load_cases(), main(), print_result(), Path, Check if expected values appear in top chunk texts. Returns {metric: "PASS…, Full dump: method_log, hard filter stats, chunks, runner-ups, verdict., validate_result() (+17 more)

### Community 15 - "Data Merging and Persistence"
Cohesion: 0.12
Nodes (18): _fill_ans_stencil(), _merge_jobs_into_work(), _persist_phase2(), Path, Phase 2: merge job values + compute formula cells. T0 scope:…, Extract per-firm display stencils from work stencil. Only ans-marked rows…, Extract ans-marked values from work stencil into ans stencil. Uses work stencil…, Write work + ans stencils to disk. Job stencils are ephemeral. (+10 more)

### Community 16 - "Terminal UI and Output"
Cohesion: 0.12
Nodes (15): Console, Group, AnswerSlot, _is_table_line(), Look up style by longest prefix match. 'PMS2-leng-Apple' tries: 'PMS2-leng-…, Thread-safe container for passing an answer from the UI thread back to the…, Singleton. Created once at module import. Owns all terminal I/O., Start a spinner. Called before LLM calls. Stops any existing spinner first. (+7 more)

### Community 17 - "LLM Mocking Utilities"
Cohesion: 0.12
Nodes (17): _make_mock_openai_llm(), _mock_empty_response(), _mock_text_response(), _mock_tool_call_response(), Build OpenAICompatibleLLM with mocked OpenAI client., Build a mock OpenAI response with a proper tool_calls array., Build a mock OpenAI response with text content (no tool_calls)., Build a mock OpenAI response with no choices. (+9 more)

### Community 18 - "Top-level Orchestration"
Cohesion: 0.19
Nodes (20): BatchOutcome, Config, Path, VectorStoreIndex, orchestrator.py — Top-level pipeline: Sekei plan → PTO lanes → filled stencil.…, PUMBA fallback for one failed batch. Thread-safe., Execute PTO lanes for all batches and evaluate the stencil. Two-phase parallel:…, Result of one PTO or PUMBA batch attempt. (+12 more)

### Community 19 - "Tool Communication Channels"
Cohesion: 0.13
Nodes (15): _get_override(), Return thread-local override for this channel's label, or None., Bound to a label. Tool code calls .print() and .input() on this., ToolChannel, _build_chart_inputs(), _format_pteca_input(), _normalize_period(), Config (+7 more)

### Community 20 - "Terminal I/O Routing"
Cohesion: 0.15
Nodes (17): clear_override(), _is_separator(), override_channel(), _pipe_table_to_rich(), terminal_router.py — Singleton that owns all terminal I/O. No tool or harness…, Set a thread-local override: prints/inputs on the channel with the given exact…, Remove a thread-local override set by override_channel()., Convert pipe-table lines to a rich.Table. (+9 more)

### Community 21 - "Global Configuration Constants"
Cohesion: 0.12
Nodes (13): Config, config.py — Single source of truth for all tunable parameters. Every component…, Create a Config from optional overrides. API keys are resolved per-provider at…, Load a named profile from llm_profiles.yaml., Raise ValueError if required settings are missing., All configuration for the query engine in one place. Override defaults by…, Denomination factors and unit aliases. Constants only. Imported by…, _build_validator_input() (+5 more)

### Community 22 - "OpenAI API Integration"
Cohesion: 0.16
Nodes (5): OpenAICompatibleLLM, Generic OpenAI-compatible chat completions backend., Serialize LLMResponse to string for trace recording., Structured JSON via tool-call shim (OpenAI-compatible). No thinking —…, ToolCall

### Community 23 - "Stencil Finalization Testing"
Cohesion: 0.19
Nodes (8): _handle_finalize(), Process finalize_stencil tool call. Returns (data, status_string). data =…, Sekei drops a firm → warning printed, not error., Minimal ToolChannel stub for testing., Full 22-row stencil: 2 firms × 11 rows, 3 periods., Sekei submits periods in wrong order → canonical order preserved., _StubChannel, TestHandleFinalize

### Community 24 - "Verdict Validation Testing"
Cohesion: 0.18
Nodes (6): _handle_submit_verdicts(), Lock, Process all verdicts from one Validator. Per verdict: unit check, denom…, Verify first-write-wins when two Validators race for same cell., test_compare_and_swap_race(), TestHandleSubmitVerdicts

### Community 25 - "Anthropic API Integration"
Cohesion: 0.20
Nodes (4): AnthropicLLM, Anthropic Claude backend with configurable extended thinking. When…, True if model supports adaptive thinking (4.6+). 4.5 and earlier only support…, Return structured JSON matching schema via tool-call shim. Single tool call…

### Community 26 - "Safe Arithmetic Evaluation"
Cohesion: 0.22
Nodes (5): _eval_node(), AST-walked arithmetic eval. Only +, -, *, / allowed. Used by stencil_topo.py…, Evaluate arithmetic expression. Only +, -, *, / allowed. Wraps SyntaxError ->…, _safe_math_eval(), TestSafeMathEval

### Community 27 - "Chart Rendering Utilities"
Cohesion: 0.20
Nodes (12): _check_gaps(), _nudge_labels(), _prompt_gaps(), Path, stencil2chart.py — Render a display table as a line chart (SVG). Takes a plain…, Render chart_input as a line chart SVG. Args: chart_input: Display table dict…, Collapse to filesystem-safe slug: non-word chars → _, runs collapsed, edges…, Scan series for None values. Return list of gap descriptions. (+4 more)

### Community 28 - "Chunk Overlap Injection"
Cohesion: 0.26
Nodes (7): _inject_overlap(), Overlap injection for chunked documents. Post-chunking pass: injects +/-1k char…, Add +/-1k char overlap from neighboring chunks, per file. Groups nodes by…, _make_mock_nodes(), MockNode, T0 unit tests for PMS2. Pure Python, no LLM, no data files. Tests prove each…, TestInjectOverlap

### Community 29 - "Topological Stencil Sorting"
Cohesion: 0.22
Nodes (7): Topological sort, formula rewrite, and stencil structure assignment. Pure…, Topological sort for one firm's rows. Returns rows in sorted order. Raises…, Topo sort all rows, per-firm independently, concatenate. Firm order in output…, _topo_sort_firm(), _topo_sort_rows(), EV/EBITDA (depth 2) depends on EV and EBITDA (depth 1), which depend on…, TestTopoSortRows

### Community 30 - "Phase 2 Integration Tests"
Cohesion: 0.19
Nodes (8): _build_phase2_fixtures(), Build work/ans/job stencils with simulated Leng-filled values. Uses the…, Full Phase 2 on canonical 2-firm stencil., One dispatcher crashed — only surviving firm merges., All dispatchers crashed — empty completed_jobs., Leng found EV/EBITDA=10.2 directly from analyst note. Merge puts 10.2 into the…, Smoke test: pms2.py successfully imports run_phase2., TestRunPhase2

### Community 31 - "Pipeline Entry Points"
Cohesion: 0.23
Nodes (10): opaque_registry.py — In-memory key-value store for large tool outputs. Prevents…, _check_index_staleness(), _newest_mtime(), Config, Path, PMS2 pipeline entry point. T0 scope: _expand_periods only. M3a:…, Run full PMS2 pipeline. M3a scope: Phase 0 (Sekei) only. Returns display…, Return the newest mtime across all files in directory (recursive). Returns None… (+2 more)

### Community 32 - "Gemini API Integration"
Cohesion: 0.21
Nodes (3): GeminiLLM, Like complete(), but also returns a usage dict. Default: delegates to…, Google Gemini backend via google-genai SDK.

### Community 33 - "Live Agent Integration Tests"
Cohesion: 0.21
Nodes (8): AutoChannel, main(), M1 live test — Sekei agent loop with real Anthropic API. Runs run_sekei N times…, Execute one run_sekei call. Returns (work, ans, jobs, file_inv, elapsed,…, Duck-typed ToolChannel that auto-answers ask_user questions., Validate stencil against ground truth expectations. Returns (pass, errors)., run_one(), validate_stencil()

### Community 34 - "Opaque Variable Registry"
Cohesion: 0.27
Nodes (3): OpaqueRegistry, Any, Drain and return variables stored since last harvest. Returns [(handle,…

### Community 35 - "Batch Extraction Planning"
Cohesion: 0.24
Nodes (10): _handle_override_firm_currency(), _handle_run_leng_caller(), Config, Lock, Path, Batch Planner agent loop — file routing + extraction dispatch. M4:…, Override currency for ALL non-float rows of a firm. Atomic., Real run_leng_caller: delegates to leng_caller.run_leng_caller(). Returns… (+2 more)

### Community 36 - "File System Utilities"
Cohesion: 0.24
Nodes (8): _handle_list_dir(), _handle_report_dirs(), Path, Validate dirs, walk, return (file_inventory, status). Returns (inventory,…, Walk directories, collect file inventory with filetype., List contents of a directory under data_dir. Returns newline-separated listing., _walk_dirs(), TestWalkDirs

### Community 37 - "Stencil Structure Assignment"
Cohesion: 0.22
Nodes (7): _assign_structure(), Assign row numbers, rewrite formulas, build stencils. Rows are firm-specific…, _make_canonical_raw_rows(), Leng stored 32.8 (denom miss — should be 0.328 for a ratio). Formula gives…, Build the canonical 2-firm test stencil (11 rows per firm). Per firm: depth 0…, Full 2-firm walkthrough: 11 rows/firm, 3 periods., TestAssignStructure

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

### Community 42 - "Formula Reference Parsing"
Cohesion: 0.36
Nodes (3): _parse_formula_refs(), Extract metric names from {MetricName} or {MetricName}[-k] syntax. Cross-column…, TestParseFormulaRefs

### Community 43 - "Test Logging Utilities"
Cohesion: 0.28
Nodes (5): load_test_cases(), main(), Path, Write to both stdout and a file., Tee

### Community 45 - "Extraction Schema Validation"
Cohesion: 0.25
Nodes (5): Tests that _run_single_leng rejects flat cell values., Cells like {"A1": 17163} instead of {"A1": {"value": ...}} → retry., Empty cells dict {} should pass (valid — no hits)., All retries return flat values → _malformed_cells=True., TestLengCellValueShapeGuard

### Community 46 - "PUMBA CLI Testing"
Cohesion: 0.39
Nodes (7): build_request_from_args(), build_request_from_case(), load_cases(), main(), Config, Re-run pto_judge on PUMBA chunks, compare with ground truth., run_judge_on_pumba_chunks()

### Community 49 - "Extraction Error Analysis"
Cohesion: 0.33
Nodes (6): DeepSeek V4 Flash, Leng Extraction Agent, Leng Eval v4flash 2026-07-27, Temporal Mismatch (First-Write-Wins), Semantic Confusion (Price Target vs Share Price), EPS Source Inconsistency

### Community 50 - "Markdown Trace Export"
Cohesion: 0.33
Nodes (3): Path, Write all events to a markdown file. Returns filepath., Render all events as LLM-readable markdown.

### Community 55 - "Pipeline Logic Bugs"
Cohesion: 0.67
Nodes (3): finalize_stencil, run_sekei, LLM-only Confirmation Gate Bug

## Knowledge Gaps
- **6 isolated node(s):** `PSOAS Architecture`, `run_sekei`, `FiscalCalResolver`, `Leng Eval v4pro 2026-07-27`, `DeepSeek V4 Flash` (+1 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Config` connect `Query Engine Configuration` to `Gemini API Integration`, `LLM Backend Clients`, `Live Agent Integration Tests`, `BM25 Search Index`, `Retrieval Evaluation Tests`, `Financial Document Loading`, `Pipeline UI and Visualization`, `System Prompts and Tracing`, `Extraction Dispatcher Loop`, `Index and App Management`, `Test Logging Utilities`, `Extraction and Validation Runners`, `Tabular Retrieval Logic`, `Retrieval Ranking and Fusion`, `Top-level Orchestration`, `OpenAI API Integration`, `Anthropic API Integration`?**
  _High betweenness centrality (0.190) - this node is a cross-community bridge._
- **Why does `OpenAICompatibleLLM` connect `OpenAI API Integration` to `LLM Backend Clients`, `File System Utilities`, `Stencil Structure Assignment`, `Formula Calculation Logic`, `Fiscal Period Expansion`, `Formula Reference Parsing`, `Query Engine Configuration`, `Extraction Schema Validation`, `Data Merging and Persistence`, `LLM Mocking Utilities`, `Stencil Finalization Testing`, `Verdict Validation Testing`, `Safe Arithmetic Evaluation`, `Chunk Overlap Injection`, `Topological Stencil Sorting`, `Phase 2 Integration Tests`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Why does `IndexManager` connect `Index and App Management` to `Retrieval Evaluation Tests`, `Query Engine Configuration`, `Tabular Retrieval Logic`, `Retrieval Ranking and Fusion`, `PUMBA CLI Testing`, `Terminal I/O Routing`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 26 inferred relationships involving `Config` (e.g. with `AnthropicLLM` and `GeminiLLM`) actually correct?**
  _`Config` has 26 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `PTOBatchRequest` (e.g. with `BatchOutcome` and `Config`) actually correct?**
  _`PTOBatchRequest` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `OpenAICompatibleLLM` (e.g. with `Config` and `MockNode`) actually correct?**
  _`OpenAICompatibleLLM` has 19 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PSOAS Architecture`, `run_sekei`, `FiscalCalResolver` to the rest of the system?**
  _6 weakly-connected nodes found - possible documentation gaps or missing edges._