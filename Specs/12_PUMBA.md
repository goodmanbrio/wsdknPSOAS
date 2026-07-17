# PUMBA — Poony Ultimate Money Burning Acquisition

LLM-reasoned chunk retrieval fallback for when PTO's deterministic
BM25/cosine retrieval fails. Three-tier hierarchy of LLM workers
(Dailo → Gulei → Leng) navigates the raw data directory, finds the
right table chunks by reasoning about filenames and section names,
and returns real docstore node_ids to the existing pto_judge for
authoritative value extraction.

PUMBA is currently a PMS1-internal fallback called by
orchestrator.py. It has no JSON schema and no execute_tool branch.
It registers its own ToolChannel under label `"PUMBA"` for status
output (all tiers print through this one channel).

Future: the harness orchestrator may specify PMS1 call types to
PUMBA directly (e.g. force PUMBA on specific batches). Not yet
exposed as an orchestrator-level tool.

## Why PUMBA exists

PTO retrieves table chunks using hard metadata filters + BM25 keyword
ranking + destructive fusion. This works well for standard financial
statements (income statement, balance sheet, cash flow) where
deterministic HyDE signals from retrievable_line_items.yaml are
strong. But it fails on:

- Non-standard tables (segment data, geographic breakdowns, custom
  disclosures) where BM25 keywords are hard to predict
- Edge cases where the correct table is buried under noise from
  structurally similar tables across filings

When pto_judge returns insufficient for a batch, the problem is
almost always that PTO retrieved the WRONG chunks, not that the
data doesn't exist. PUMBA's job: find the RIGHT chunks by using
LLM reasoning instead of keyword matching.

## Architecture

```
orchestrator.py (modified):

  for batch in plan.batches:
      result = pto_retrieve(batch, index, config)

      judge_output = pto_judge(batch, result, config)
      |   ^ OUTSIDE try. json.JSONDecodeError propagates up.
      |     That's a judge LLM failure, not retrieval failure.
      |
      try:
          values = pto_judge_to_stencil(judge_output, cell_map, result)
      except ValueError:  <-- insufficient / missing metric (retrieval failure)
          |
          v
      pumba_node_ids = run_pumba(request, index, config)
          |
          |-- got node_ids --> build PTOBatchResult
          |                    pto_judge(batch, pumba_result)
          |                    try: pto_judge_to_stencil(...)
          |                    except: propagate ORIGINAL error
          |                    (one-shot fallback, no recursion)
          |
          |-- exhausted -----> raise / propagate failure
```

```
PUMBA INTERNALS:

+-----------------------------------------------------------------+
|  DAILO (大佬) -- LLM agent loop                                 |
|                                                                  |
|  Tools: list_dir, spawn_gulei, report_results                    |
|  State: blacklist = set()  (python-side, not in system prompt)   |
|                                                                  |
|  ROUND N:                                                        |
|    1. LLM calls list_dir to navigate data/ tree                  |
|    2. LLM picks k=6 files not in blacklist                       |
|    3. LLM calls spawn_gulei(files=[6 filenames])                 |
|    4. Python runs 6x Gulei in parallel (ThreadPoolExecutor)      |
|    5. Tool result shows full chunk text for hits, null for misses|
|       + blacklist appended at bottom                             |
|                                                                  |
|    Dailo reads chunk text in tool result, decides:               |
|      - got hits? --> call report_results(node_ids=[top 3])       |
|      - all null? --> pick next k=6 (avoid blacklisted), retry    |
|      - no files left? --> report_results(exhausted=true)         |
|                                                                  |
|  EXIT: always via report_results tool (structured, no parsing)   |
|  OUTPUT: list[node_id]  (len 1-3)                                |
+-----------------------------------------------------------------+
         |              |              |
         v              v              v
+-----------------+-----------------+-----------------+
| GULEI (file_A)  | GULEI (file_B)  | GULEI (file_C)  | ... x6
+-----------------+-----------------+-----------------+
         |
         v
+-----------------------------------------------------------------+
|  GULEI (古惑仔) -- per file, NOT an agent loop                  |
|                    two separate LLM calls, no shared memory      |
|                                                                  |
|  STEP 0 (python):                                                |
|    scan index.docstore.docs where:                               |
|      metadata["file_name"] == target filename                    |
|      metadata["chunk_type"] == "table"                           |
|    collect: list of (node_id, section, first_80_chars)           |
|    (~40-80 table chunks per 10K file)                            |
|                                                                  |
|  STEP 1: GULEI-PAI (揀) -- 1 LLM call: pick chunks              |
|    prompt: chunk summaries [idx, section, first_80_chars]        |
|    output: 12 chunk indices                                      |
|                                                                  |
|  STEP 2 (python):                                                |
|    for each picked chunk: resolve node_id -> full text           |
|    spawn 12x LENG in parallel (ThreadPoolExecutor)               |
|    each Leng gets: node_id + chunk text in prompt                |
|    collect results                                               |
|                                                                  |
|  STEP 3: branch on Leng hit count                                |
|                                                                  |
|    hits == 0 --> return None to Dailo                            |
|                                                                  |
|    hits == 1 --> return that node_id to Dailo                    |
|                  (skip GuleiSau)                                 |
|                                                                  |
|    hits > 1  --> GULEI-SAU (收) -- 1 LLM call: pick best         |
|      python resolves hit node_ids -> chunk text for prompt       |
|      prompt: full chunk text for each hit, batch context         |
|      (GuleiSau does NOT see Leng's extracted values --           |
|       those were just Leng's self-screening mechanism)           |
|      output: best node_id                                        |
|                                                                  |
|  OUTPUT: node_id OR None                                         |
+-----------------------------------------------------------------+
         |       |       |
         v       v       v
+-------------+-------------+-------------+
| LENG (chk0) | LENG (chk1) | LENG (chk2) | ... x12
+-------------+-------------+-------------+
         |
         v
+-----------------------------------------------------------------+
|  LENG (靚) -- single haiku call                                 |
|                                                                  |
|  INPUT (in prompt):                                              |
|    node_id (as label)                                            |
|    full chunk text                                               |
|    batch metrics + period + statement                            |
|                                                                  |
|  TASK: attempt to extract ALL batch metrics from chunk           |
|    the attempt IS the screening -- by trying to find each        |
|    value, Leng discovers what's missing. No separate             |
|    "is it here?" check needed.                                   |
|                                                                  |
|  LLM OUTPUT (all metrics found):                                 |
|    {"found": true,                                               |
|     "metrics": {"Revenue": 46298, "Cost of Sales": 36386}}      |
|    Python attaches node_id after parsing (LLM does NOT echo it)  |
|                                                                  |
|  LLM OUTPUT (any metric missing):                                |
|    {"found": false}                                              |
+-----------------------------------------------------------------+
```

## Opaque handle pattern

node_id is the opaque handle throughout PUMBA. docstore is the
backing store. Each tier's PYTHON RETURN VALUE is small (just
handles). Python resolves node_id -> text at each tier boundary
before injecting into the NEXT tier's prompt or tool result.

```
WHAT EACH TIER'S PYTHON FUNCTION RETURNS vs WHAT THE NEXT TIER SEES:

  Leng LLM return:        {found, metrics}                 <-- tiny
  Python attaches:        node_id (from dispatch context)
  Leng python return:     {found, node_id, metrics}       <-- tiny
  GuleiSau SEES:          node_ids + full chunk texts      <-- python expanded

  GuleiSau python return: {best: candidate_index}          <-- tiny
  Gulei python return:    node_id (str) or None            <-- tiny

  Gulei python return:    node_id (str) or None            <-- tiny
  Dailo SEES (tool result): node_ids + full chunk texts    <-- python expanded

  Dailo calls report_results: node_ids only                <-- tiny
  Orchestrator RECEIVES:  [node_id, ...]                   <-- tiny
    resolves: index.docstore.docs[nid] -> NodeWithScore
    feeds to: pto_judge (existing, unchanged)
```

Principle: python return values are tiny handles. Full text appears
in prompts/tool-results where the next consumer needs to READ it.
Python does the expansion (docstore lookup) at each boundary.

## Chunk type filter

ALL tiers filter `chunk_type="table"` only. POS (Poony Oneshot
Semantic) is a separate future system for text chunk retrieval.
PUMBA is strictly a table-chunk fallback for PTO.

## Input contract

### run_pumba()

```python
def run_pumba(
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    config: Config,
    channel: ToolChannel | None = None,
) -> list[str]:
```

| Param | Type | Description |
|---|---|---|
| `request` | `PTOBatchRequest` | Same batch request that PTO failed on. Contains firm, period, statement, metrics, retrieve_target. |
| `index` | `VectorStoreIndex` | Loaded vector store index. Used for docstore chunk access. |
| `config` | `Config` | Pipeline config. Used for data_dir path and LLM profiles. |
| `channel` | `ToolChannel \| None` | Optional channel for terminal output. Falls back to `register("PUMBA")`. |

PTOBatchRequest fields used by PUMBA:
- `firm`: drives directory navigation (which sector/firm dir)
- `period`: drives file selection (which FY)
- `statement`: context for chunk relevance (income_statement, etc)
- `metrics`: what Leng tries to extract (the screening task)
- `retrieve_target`: additional context for GuleiPai

### Channel routing — explicit param threading, NOT override_channel

PUMBA does NOT use `override_channel`. PMS1 uses it because
PMS1 internals (pto.py, orchestrator.py, stencil.py) have
module-level `_pto_out = register("PMS1")` and all run in the
SAME thread — `override_channel` is thread-local, so it works.

PUMBA's Gulei and Leng workers run in ThreadPoolExecutor threads.
`override_channel` is thread-local — worker threads don't see the
override set in the orchestrator thread. So PUMBA threads `ch`
explicitly through every function call:

```
orchestrator.py:
  pumba_ch = register(f"PUMBA-{request.firm}")
  pumba_node_ids = run_pumba(request, index, config, channel=pumba_ch)

run_pumba(channel=pumba_ch):
  ch = channel or register("PUMBA")
  _run_guleis_parallel(..., ch=ch)

_run_guleis_parallel(ch=ch):
  _run_single_gulei(..., ch=ch)

_run_single_gulei(ch=ch):
  ch.print(f"  Gulei {fname}: 62 table chunks")  ← works in worker thread
  _run_leng(...)  ← Leng does NOT print (no ch needed)
```

Leng does NOT get `ch`. Leng is a pure function: takes input,
returns JSON dict. No terminal output. Only Gulei prints status.

**Required changes to existing files:**

1. `terminal_router.py` — add to LABEL_STYLES:
```python
LABEL_STYLES = {
    "ORCHESTRATOR": "bold cyan",
    "PMS1":         "bold green",
    "PTECA":        "bold yellow",
    "S2C":          "bold magenta",
    "PUMBA":        "bold red",        # ← NEW
}
```

Terminal output shows `[PUMBA-Best Buy]`, visually distinct from
`[PMS1-Best Buy]`. The `_style_for` function strips after `-` to
look up `LABEL_STYLES["PUMBA"]`.

## Output contract

```python
list[str]  # list of node_ids, len 1-3
```

Each node_id is a real key in `index.docstore.docs`. Orchestrator
resolves them to NodeWithScore objects and builds a PTOBatchResult
for pto_judge.

### Failure recovery: ask_user instead of crash

When PUMBA fails (exhausted all files, or Judge fails on PUMBA
chunks), it does NOT crash the pipeline. Instead it asks the user
via `channel.input()` what to do — same pattern as PTECA's
`pteca_ask_user`.

```python
# When Dailo exhausts or Judge fails on PUMBA chunks:
answer = ch.input(
    "PUMBA failed to find the data. Options:\n"
    "  'skip' - skip this batch\n"
    "  'exit' - abort the entire PMS1 run\n"
    "Your choice:"
)

if answer.strip().lower() == "exit":
    raise ValueError(
        f"User aborted after PUMBA exhausted "
        f"all files for {request.firm}"
    )
return []  # skip batch (default for any non-"exit" input)
```

The orchestrator handles `[]` (empty node_ids) by skipping that
batch — the cell remains unfilled, and `compute_stencil` will
raise ValueError for missing retrieve cells. The orchestrator
can catch this and produce a partial stencil or report the gap.

This means PUMBA NEVER raises ValueError for exhaustion. The only
ValueError from PUMBA is if the user explicitly says "exit".

## Dailo internals

### Tools

```python
DAILO_TOOLS = [
    {
        "name": "list_dir",
        "description": "List files and subdirectories at a path under data/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path under data/. Use '' for root."
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "spawn_gulei",
        "description": "Survey files for relevant table chunks. Runs one Gulei worker per file in parallel. Returns per-file results with full chunk text for hits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filenames to survey (max 6). e.g. ['BESTBUY_2023_10K.md', 'BESTBUY_2022_10K.md']"
                }
            },
            "required": ["files"]
        }
    },
    {
        "name": "report_results",
        "description": "Report final chunk results. Call this when you have found chunks or exhausted all files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Node IDs of best chunks to send to Judge. Max 3. Empty if exhausted."
                },
                "exhausted": {
                    "type": "boolean",
                    "description": "True if all files have been surveyed with no results."
                }
            },
            "required": ["node_ids", "exhausted"]
        }
    }
]
```

### Dailo exit: report_results tool (resolved F1)

Dailo exits by calling `report_results`, NOT by `end_turn` with
freeform text. Same pattern as PTECA's `finalize` — structured
output, no parsing.

- Found chunks: `report_results(node_ids=["a7f2b3...", "e9d4a8..."], exhausted=false)`
- All files exhausted: `report_results(node_ids=[], exhausted=true)`
- At most 3 node_ids. Dailo ranks and picks top 3 from the
  spawn_gulei results it has already seen (full chunk text is in
  the tool result — see below).

If Dailo calls `end_turn` instead of `report_results`, inject error
message and continue loop (same guard as PTECA):
```python
if response.stop_reason == "end_turn":
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": "Error: you must call report_results. Do not end without it.",
    })
    turn_counter += 1
    continue
```

Note: Dailo returns node_ids only. Cell/value/denomination
extraction is Judge's job — Dailo has no knowledge of cells or
values. PUMBA's entire job is to find the right chunks. Judge
does the rest unchanged.

### System prompt (sketch)

System prompt is set ONCE at the start of the agent loop — it does
NOT change between turns. Blacklist state is communicated via tool
results, not system prompt templating. (Anthropic API: changing
system prompt mid-conversation makes prior assistant messages
semantically incoherent.)

```
You are Dailo -- a retrieval coordinator. You navigate a financial
data directory to find table chunks that contain specific metrics.

## Context

You are searching for: %(metrics)s
Firm: %(firm)s
Period: %(period)s
Statement: %(statement)s

PTO (the standard retriever) already failed on this batch --
you are the fallback.

## Data directory structure

data/
  <Sector>/
    <FIRM>/
      <FIRM>_<YEAR>_<DOCTYPE>.md
      <UNSTRUCTURED_FILENAME>.md
      ...

Use list_dir('') to discover available sectors. Do not assume
directory names — they may change as new companies are added.
Filenames may also be inconsistent, which is where your inference is needed. 

## Your tools

- list_dir(path): see what's in a directory
- spawn_gulei(files): send up to 6 files to workers who will search
  for the right table chunks. Returns full chunk text for hits,
  null for misses, plus a list of already-surveyed files.
- report_results(node_ids, exhausted): call this when done.
  Pass best node_ids (up to 3), or exhausted=true if nothing found.

## Strategy

1. Use list_dir to find the firm's directory
2. Pick up to 6 most promising files (consider period, filing type)
3. Call spawn_gulei with those files
4. Read the results — chunk text is shown for hits
5. If hits: pick the best (up to 3) node_ids.
   Call report_results with those node_ids.
6. If all null: pick next batch of files (avoid already-surveyed)
7. Repeat until you find chunks or exhaust all files
8. If all files exhausted: call report_results with exhausted=true

## Rules

- The spawn_gulei result lists already-surveyed files at the bottom.
  NEVER re-pick files listed there.
- Pick at most 6 files per spawn_gulei call
- You MUST call report_results to finish. Do not end without it.
- report_results node_ids: at most 3, ranked best first
```

### Blacklist propagation

Blacklist is NOT in system prompt. It accumulates naturally in
conversation history via spawn_gulei tool results:

```
# After each spawn_gulei call, the tool result includes:
"Results:
  BESTBUY_2023_10K.md: a7f2b3c1-948e-... (found chunk)
  BESTBUY_2022_10K.md: null
  BESTBUY_2021_10K.md: null
  ...

Already surveyed (do NOT re-pick):
  BESTBUY_2023_10K.md, BESTBUY_2022_10K.md, BESTBUY_2021_10K.md, ..."
```

Python maintains `blacklist: set[str]` and appends the full list
to every spawn_gulei tool result. Dailo's LLM sees it in
conversation history without system prompt mutation.

### spawn_gulei tool result format (resolved F2/F3)

The tool result string returned to Dailo must include FULL chunk
text for every hit. This is the node_id → text expansion step:
Gulei returns opaque node_ids, Python resolves each to chunk text
before formatting the tool result string that Dailo's LLM sees.

This serves two purposes:
1. Dailo can READ the chunks to rank when >3 hits
2. Dailo can call report_results with informed top-3 selection

```
spawn_gulei tool result (example with 2 hits out of 6 files):

"FILE RESULTS:

BESTBUY_2023_10K.md: FOUND
  node_id: c5acaae3-948e-4585-be99-9dfdf776a242
  section: Consolidated Statements of Earnings
  ---
  | Fiscal Years Ended          | January 28, 2023 | January 29, 2022 |
  |-----------------------------|--------------------|-------------------|
  | Revenue                     | $ 46,298           | $ 51,761          |
  | Cost of sales               | 36,386             | 40,121            |
  ---

BESTBUY_2022_10K.md: NOT FOUND
BESTBUY_2023Q4_EARNINGS.md: FOUND
  node_id: f220327d-2107-48a6-8f8b-f5965124acbb
  section: Selected Financial Data
  ---
  | | Q4 FY2023 | Q4 FY2022 |
  |---|---|---|
  | Revenue | $14,740 | $16,365 |
  ---

BESTBUY_2024Q2_10Q.md: NOT FOUND
BESTBUY_2021_10K.md: NOT FOUND
BESTBUY_2024Q2_EARNINGS.md: NOT FOUND

Already surveyed (do NOT re-pick):
  BESTBUY_2023_10K.md, BESTBUY_2022_10K.md, BESTBUY_2023Q4_EARNINGS.md,
  BESTBUY_2024Q2_10Q.md, BESTBUY_2021_10K.md, BESTBUY_2024Q2_EARNINGS.md"
```

Python builds this string:
```python
_MAX_CHUNK_CHARS_IN_TOOL_RESULT = 3000  # truncate huge table chunks
                                         # Dailo is ranking, not extracting

def _format_spawn_gulei_result(
    files: list[str],
    results: list[str | None],   # node_id or None per file
    index: VectorStoreIndex,
    blacklist: set[str],
) -> str:
    parts = ["FILE RESULTS:\n"]
    for fname, nid in zip(files, results):
        if nid is None:
            parts.append(f"{fname}: NOT FOUND\n")
            continue
        node = index.docstore.docs.get(nid)
        if node is None:
            parts.append(f"{fname}: NOT FOUND (stale node_id)\n")
            continue
        section = node.metadata.get("section", "?")
        text = node.text
        if len(text) > _MAX_CHUNK_CHARS_IN_TOOL_RESULT:
            text = text[:_MAX_CHUNK_CHARS_IN_TOOL_RESULT] + "\n[truncated]"
        parts.append(f"{fname}: FOUND")
        parts.append(f"  node_id: {nid}")
        parts.append(f"  section: {section}")
        parts.append(f"  ---")
        parts.append(text)  # NO indent — preserves pipe-table alignment
        parts.append(f"  ---\n")

    parts.append("Already surveyed (do NOT re-pick):")
    parts.append(f"  {', '.join(sorted(blacklist))}")
    return "\n".join(parts)
```

Same expansion pattern applies at every tier boundary:
- Leng → Gulei: Leng returns `{node_id: "..."}`. GuleiSau prompt
  includes full chunk text (Python resolves `docstore[nid].text`).
- Gulei → Dailo: Gulei returns `node_id`. spawn_gulei tool result
  includes full chunk text (Python resolves, as shown above).

The principle: opaque handles flow UP in return values. Full text
flows DOWN into prompts. Python does the expansion at each boundary.

### Client creation and initial message

```python
def run_pumba(
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    config: Config,
    channel: ToolChannel | None = None,
) -> list[str]:
    ch = channel or register("PUMBA")

    # ── LLM setup ──
    # Dailo: raw SDK (needs tools=). Model name from profile, NOT hardcoded.
    # Future: when LLMBackend gains complete_with_tools(), Dailo can
    # use it too and become fully provider-agnostic.
    dailo_profile = config.get_llm_profile(config.pumba_dailo_profile)
    dailo_model = dailo_profile["model"]
    dailo_max_tokens = dailo_profile.get("max_tokens", 8000)
    import anthropic  # only provider-specific import in pumba.py
    dailo_client = anthropic.Anthropic()

    # Gulei/Leng: LLMBackend via llm.py factories. Fully provider-agnostic.
    from src.llm import get_pumba_gulei_llm, get_pumba_leng_llm
    gulei_llm = get_pumba_gulei_llm(config)
    leng_llm = get_pumba_leng_llm(config)

    # Build thinking kwargs (Dailo ONLY — see model matrix section).
    # Anthropic-specific for now. When LLMBackend gains tools support,
    # this moves into the backend.
    dailo_extra_kwargs = {}
    if dailo_profile.get("thinking"):
        if "opus-4-8" in dailo_model:
            dailo_extra_kwargs["thinking"] = {"type": "adaptive"}
            dailo_extra_kwargs["output_config"] = {"effort": "high"}
        else:
            budget = dailo_profile.get("thinking_budget", 5000)
            dailo_extra_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }

    # ── Pre-compute file → table chunks index ──
    # PUMBA spawns 6 Guleis in parallel, each needing to find all
    # table chunks for its file. Without pre-computation, each Gulei
    # calls _get_file_table_chunks which iterates ALL ~3000 docstore
    # entries (36MB docstore). 6 parallel full-scans under GIL = slow.
    # Pre-compute ONCE, pass the lookup dict to all Guleis.
    file_table_index: dict[str, list[tuple[str, str, str]]] = {}
    for node_id, node in index.docstore.docs.items():
        meta = node.metadata
        if meta.get("chunk_type") != "table":
            continue
        fname = meta.get("file_name", "")
        if not fname:
            continue
        preview = node.text[:80].replace("\n", " ")
        section = meta.get("section", "")
        file_table_index.setdefault(fname, []).append(
            (node_id, section, preview)
        )

    # Build system prompt — templated ONCE with batch context.
    # Uses % formatting (NOT str.format()) because the template
    # contains literal curly braces in directory structure examples
    # that str.format() would choke on (KeyError: 'Sector').
    dailo_system_prompt = DAILO_SYSTEM_TEMPLATE % {
        "firm": request.firm,
        "period": request.period,
        "metrics": ", ".join(request.metrics),
        "statement": request.statement,
    }

    # First user message is minimal — batch context is in system prompt.
    messages = [{
        "role": "user",
        "content": "Begin searching. Use list_dir to find the firm's directory.",
    }]

    turn_counter = 0
    # ... agent loop below ...
```

### Agent loop pattern

Same pattern as PTECA (while loop, messages, tool dispatch) but
with list_dir, spawn_gulei, and report_results.

```python
    blacklist: set[str] = set()

    while turn_counter < MAX_DAILO_TURNS:
        response = dailo_client.messages.create(
            model=dailo_model,
            system=dailo_system_prompt,
            messages=messages,
            tools=DAILO_TOOLS,
            max_tokens=dailo_max_tokens,
            **dailo_extra_kwargs,  # thinking params (Dailo only)
        )

        # ── end_turn without report_results (error) ──
        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": "Error: you must call report_results. Do not end without it.",
            })
            turn_counter += 1
            continue

        # ── no tool calls (max_tokens truncation, unexpected stop) ──
        # Same guard as PTECA (spec 07 lines 500-513). Without this,
        # an empty tool_results list gets appended as user message,
        # which the Anthropic API rejects (empty content array).
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_blocks:
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": (
                    "Error: response contained no tool calls (possibly "
                    "truncated). You must call a tool: list_dir, "
                    "spawn_gulei, or report_results."
                ),
            })
            turn_counter += 1
            continue

        # ── tool_use dispatch ──
        tool_results = []
        report = None

        for tb in tool_blocks:
            if tb.name == "list_dir":
                path = tb.input["path"]
                listing = _list_data_dir(config.data_dir, path)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": listing,
                })

            elif tb.name == "spawn_gulei":
                files = tb.input["files"]
                # Normalize to basenames (Dailo may pass full paths)
                files = [Path(f).name for f in files]
                # Truncate to 6
                if len(files) > 6:
                    files = files[:6]
                # Python enforces blacklist even if Dailo re-picks
                files = [f for f in files if f not in blacklist]
                if not files:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": (
                            "All requested files already surveyed. "
                            "Pick different files or call "
                            "report_results(node_ids=[], exhausted=true)."
                        ),
                    })
                else:
                    # Validate filenames exist in docstore
                    valid_files = [
                        f for f in files if f in file_table_index
                    ]
                    invalid_files = [
                        f for f in files if f not in file_table_index
                    ]
                    if not valid_files:
                        blacklist.update(files)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": (
                                "No valid files found in index. "
                                "These filenames have no table chunks: "
                                + ", ".join(invalid_files)
                            ),
                        })
                    else:
                        results = _run_guleis_parallel(
                            valid_files, request, index,
                            file_table_index, gulei_llm, leng_llm, ch,
                        )
                        blacklist.update(valid_files)
                        blacklist.update(invalid_files)
                        n_hits = sum(
                            1 for r in results if r is not None
                        )
                        ch.print(
                            f"Round: {n_hits}/{len(valid_files)} "
                            f"files returned chunks"
                        )
                        result_str = _format_spawn_gulei_result(
                            valid_files, results, index, blacklist,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tb.id,
                            "content": result_str,
                        })

            elif tb.name == "report_results":
                # Reject if spawn_gulei was also called this turn —
                # Dailo must see spawn results before reporting.
                # Same guard pattern as PTECA (finalize + ask_user).
                has_spawn = any(b.name == "spawn_gulei" for b in tool_blocks)
                if has_spawn:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": (
                            "Error: cannot report_results in the same turn "
                            "as spawn_gulei. See the spawn results first, "
                            "then call report_results."
                        ),
                    })
                else:
                    report = tb.input
                    # Enforce max 3 node_ids (F24)
                    if "node_ids" in report:
                        report["node_ids"] = report["node_ids"][:3]
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": "Received.",
                    })

        # NOTE: response.content may contain thinking blocks (from Dailo's
        # adaptive thinking) alongside tool_use and text blocks. These MUST
        # be preserved in conversation history — the Anthropic API expects
        # them. The tool_blocks filter above already skips thinking blocks
        # when dispatching tools. No special handling needed here.
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        if report is not None:
            if report.get("exhausted"):
                ch.print(f"PUMBA exhausted all files for {request.firm}")
                answer = ch.input(
                    "PUMBA failed to find the data. Options:\n"
                    "  'skip' - skip this batch\n"
                    "  'exit' - abort the entire PMS1 run\n"
                    "Your choice:"
                )
                if answer.strip().lower() == "exit":
                    raise ValueError(
                        f"User aborted after PUMBA exhausted "
                        f"all files for {request.firm}"
                    )
                return []  # skip batch
            return report["node_ids"]  # list[str], len 1-3

        turn_counter += 1

    # Exceeded max turns — same ask_user pattern
    ch.print(f"PUMBA Dailo exceeded {MAX_DAILO_TURNS} turns")
    answer = ch.input(
        "Dailo ran out of turns. 'skip' or 'exit':"
    )
    if answer.strip().lower() == "exit":
        raise ValueError(
            f"User aborted after Dailo exceeded "
            f"{MAX_DAILO_TURNS} turns"
        )
    return []  # skip batch
```

### Guard rails

- **Max turns: 10.** Worst case: 2-3 list_dir + 3 spawn_gulei rounds
  (14 files / 6 per round). 10 is generous.
- **Max files per spawn_gulei: 6.** Enforced in tool handler.
- **Blacklist enforcement:** Python filters blacklisted files before
  passing to Gulei, even if Dailo's LLM accidentally re-picks one.
- **list_dir path sanitization:** `_list_data_dir` must resolve the
  path and validate it is under `config.data_dir`. Reject anything
  that escapes (e.g. `../../etc`). Return error string to Dailo.
- **spawn_gulei filename validation:** Handler normalizes each entry
  to basename via `Path(f).name` (Dailo may pass full paths like
  `"Consumer Discretionary/BESTBUY/BESTBUY_2023_10K.md"` from
  list_dir output, but docstore `file_name` metadata is just the
  filename). Then splits into `valid_files` (present in
  `file_table_index`) and `invalid_files` (no table chunks). Only
  valid files are sent to Gulei. Both sets are added to blacklist.
  If all invalid, return error to Dailo listing the invalid filenames.

### _list_data_dir

```python
def _list_data_dir(data_dir: Path, rel_path: str) -> str:
    """List contents of a directory under data/.

    Returns a newline-separated list of entries, each suffixed with /
    if it's a directory. Returns error string if path escapes data_dir.

    Example output:
        "Aerospace and Defense/\nConsumer Discretionary/\nIndustrials/\n..."
    or for a firm dir:
        "BESTBUY_2015_10K.md\nBESTBUY_2016_10K.md\n..."

    Filenames are basenames (no directory prefix). This matches
    docstore metadata["file_name"] which is also basename-only
    (set by ingest.py: `file_name = Path(rel_path).name`).
    """
    target = (data_dir / rel_path).resolve()
    if not str(target).startswith(str(data_dir.resolve())):
        return "Error: path escapes data directory. Use relative paths only."
    if not target.is_dir():
        return f"Error: '{rel_path}' is not a directory."
    entries = sorted(target.iterdir())
    lines = []
    for e in entries:
        if e.name.startswith("."):
            continue  # skip .DS_Store etc
        if e.is_dir():
            lines.append(f"{e.name}/")
        else:
            lines.append(e.name)
    if not lines:
        return "(empty directory)"
    return "\n".join(lines)
```

## Gulei internals

Gulei is NOT an agent loop. It is a deterministic Python function
that makes exactly 1 or 2 LLM calls depending on Leng hit count.

### _run_single_gulei (full function sketch)

```python
def _run_single_gulei(
    file_name: str,
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    file_table_index: dict[str, list[tuple[str, str, str]]],
    gulei_llm: LLMBackend,   # provider-agnostic (GuleiPai + GuleiSau)
    leng_llm: LLMBackend,    # provider-agnostic (Leng)
    ch: ToolChannel,
) -> str | None:
    """Survey one file for relevant table chunks.
    Returns node_id of best chunk, or None.
    """
    # Step 0: pull table chunks from pre-computed index (NOT docstore scan)
    table_chunks = file_table_index.get(file_name, [])
    ch.print(f"  Gulei {file_name}: {len(table_chunks)} table chunks")
    if not table_chunks:
        ch.print(f"  Gulei {file_name}: NOT FOUND (0 table chunks)")
        return None

    # Step 1: GuleiPai picks top-k chunks
    k = min(12, len(table_chunks))  # never more than available
    ch.print(f"  Gulei {file_name}: GuleiPai picking {k}...")
    picks = _run_gulei_pai(table_chunks, request, gulei_llm)
    if not picks:
        ch.print(f"  Gulei {file_name}: NOT FOUND (GuleiPai returned nothing)")
        return None

    # Step 2: spawn Lengs in parallel
    # Build (node_id, text, section) tuples for picked chunks.
    # Uses .get() for docstore access (F18: stale node_id defense).
    picked_chunks = []
    for i in picks:
        if i >= len(table_chunks):
            continue  # bounds check (F14)
        nid, section, _preview = table_chunks[i]
        node = index.docstore.docs.get(nid)
        if node is None:
            continue  # stale node_id (F18)
        picked_chunks.append((nid, node.text, section))

    ch.print(f"  Gulei {file_name}: {len(picked_chunks)} Lengs...")

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(
                _run_leng, nid, text, file_name, section,
                request, leng_llm,
            )
            for nid, text, section in picked_chunks
        ]
        leng_results = []
        for f in futures:  # preserves order
            try:
                leng_results.append(f.result())
            except Exception:
                leng_results.append({"found": False})

    hits = [r for r in leng_results if r.get("found")]
    n_lengs = len(picked_chunks)
    ch.print(f"  Gulei {file_name}: {len(hits)} Leng hits out of {n_lengs}")

    # Step 3: branch
    if len(hits) == 0:
        ch.print(f"  Gulei {file_name}: NOT FOUND")
        return None

    if len(hits) == 1:
        nid = hits[0]["node_id"]
        ch.print(f"  Gulei {file_name}: FOUND {nid[:12]}...")
        return nid

    # hits > 1: GuleiSau picks best
    ch.print(f"  Gulei {file_name}: GuleiSau picking best of {len(hits)} hits")
    best_nid = _run_gulei_sau(hits, request, index, gulei_llm)
    ch.print(f"  Gulei {file_name}: FOUND {best_nid[:12]}...")
    return best_nid
```

### Step 0: pull table chunks from pre-computed index

```python
# NOT a docstore scan. Uses pre-computed file_table_index from run_pumba().
table_chunks = file_table_index.get(file_name, [])
```

The `file_table_index` dict is built ONCE in `run_pumba()` before
the Dailo loop starts (see "Client creation" section). It maps
`file_name → list[(node_id, section, preview)]`. This avoids 6
parallel full-docstore scans (36MB) under GIL.

**Edge case: 0 table chunks.** If `file_table_index.get()` returns
an empty list (e.g. small 8K with no tables, or file not in index),
Gulei returns `None` immediately. Do NOT call GuleiPai with empty
chunk summaries.

### Step 1: GuleiPai (揀) -- pick chunks

Single LLM call. Receives chunk summaries, picks top 12.

```python
def _run_gulei_pai(
    table_chunks: list[tuple[str, str, str]],  # (node_id, section, preview)
    request: PTOBatchRequest,
    gulei_llm: LLMBackend,  # provider-agnostic, from get_pumba_gulei_llm()
) -> list[int]:
    """Pick top-k chunk indices. Returns list of valid indices, or []."""
    prompt = _build_gulei_pai_prompt(table_chunks, request)

    raw = gulei_llm.complete(prompt, system_prompt=GULEI_PAI_SYSTEM)
    parsed = _parse_json_with_fences(raw)
    if parsed is None:
        return []

    picks = parsed.get("picks", [])
    # Filter to valid range (F14)
    valid = [i for i in picks if isinstance(i, int) and 0 <= i < len(table_chunks)]
    return valid
```

```
GuleiPai prompt:

"File: BESTBUY_2023_10K.md
 Batch wants: Revenue, Cost of Sales for FY2023, income_statement.

 Table chunks in this file:
   [0] section='Consolidated Statements of Earnings'
       '| Fiscal Years Ended | January 28, 2023 | January 29...'
   [1] section='Consolidated Balance Sheets'
       '| | January 28, 2023 | January 29, 2022 |...'
   [2] section='Consolidated Statements of Cash Flows'
       '| Operating activities | | |...'
   ...

 Pick up to 12 chunk indices most likely to contain ALL requested
 metrics. Output JSON only: {"picks": [0, 4, 7, ...]}"
```

Note: node_ids are NOT shown to GuleiPai. It picks by integer
index. Python maps indices back to node_ids via the table_chunks
list from step 0.

**GuleiPai out-of-range indices:** Filter picks to valid range
`[0, len(table_chunks))`. Silently drop invalid indices. If no
valid picks remain after filtering, return `None`.

**GuleiPai JSON parse failure:** Strip markdown fences (same pattern
as `pto_judge`). If JSON still fails to parse, return `None` —
treat as "this file has nothing."

### Steps 2-3: Leng dispatch and branching

See `_run_single_gulei` full function sketch above — it contains
the authoritative implementation for Leng dispatch (ThreadPoolExecutor,
exception handling) and the hits==0/1/>1 branching logic. Do NOT
implement from any other sketch in this spec.

### GuleiSau (收) -- review & pick best

Single LLM call. Receives full chunk text for each Leng hit
(python resolves node_id -> text). Does NOT see Leng's extracted
values -- those were just Leng's self-screening mechanism.

Uses integer candidate indices, NOT raw node_ids, to avoid
truncation/matching bugs. Python maps the chosen index back to
the full node_id.

```python
def _run_gulei_sau(
    hits: list[dict],   # Leng results with found=True, each has node_id
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    gulei_llm: LLMBackend,  # provider-agnostic, same instance as GuleiPai
) -> str:
    """Pick best chunk from multiple Leng hits. Returns node_id."""
    candidates = []
    for h in hits:
        nid = h["node_id"]
        node = index.docstore.docs.get(nid)
        if node is None:
            continue
        candidates.append((nid, node.text))

    if not candidates:
        return hits[0]["node_id"]  # fallback: first hit

    prompt = _build_gulei_sau_prompt(candidates, request)
    raw = gulei_llm.complete(prompt, system_prompt=GULEI_SAU_SYSTEM)
    parsed = _parse_json_with_fences(raw)

    if parsed is None:
        return candidates[0][0]  # fallback: first candidate

    best_idx = parsed.get("best")
    if not isinstance(best_idx, int) or best_idx < 0 or best_idx >= len(candidates):
        return candidates[0][0]  # fallback: first candidate

    return candidates[best_idx][0]  # node_id
```

```
GuleiSau prompt:

"Batch wants: Revenue, Cost of Sales for FY2023, income_statement.

 Workers flagged these table chunks as containing all requested metrics:

 CANDIDATE 0:
   [full chunk text]

 CANDIDATE 1:
   [full chunk text]

 Which single candidate best answers the batch request?
 Consider: correct period, correct statement, data quality.
 Output JSON: {"best": 0}"
```

Python maps: `candidates[response["best"]]` -> full node_id.

**GuleiSau JSON parse failure:** Strip markdown fences. If JSON
still fails, fall back to returning the first Leng hit's node_id.

**GuleiSau out-of-range index:** If `best` is not a valid candidate
index, fall back to returning the first Leng hit's node_id.

## Leng internals

Single haiku LLM call. No loop, no tools. The extraction attempt
IS the screening -- by trying to find each value, Leng discovers
what's missing.

### _run_leng (function sketch)

```python
def _run_leng(
    node_id: str,
    chunk_text: str,
    file_name: str,
    section: str,
    request: PTOBatchRequest,
    leng_llm: LLMBackend,  # provider-agnostic, from get_pumba_leng_llm()
) -> dict:
    """Screen one chunk. Returns {found: bool, node_id: str, metrics: dict | None}.

    The LLM does NOT echo node_id — Python attaches it after parsing.
    This avoids haiku needing to perfectly copy a 36-char UUID.

    file_name and section are passed by the Gulei caller (from the
    pre-computed file_table_index tuples), NOT looked up from docstore.
    This avoids passing `index` to every Leng thread.
    """
    prompt = _build_leng_prompt(file_name, section, chunk_text, request)
    raw = leng_llm.complete(prompt, system_prompt=LENG_SYSTEM)
    parsed = _parse_json_with_fences(raw)  # fence-stripping helper

    if parsed is None:
        return {"found": False, "node_id": node_id}

    # Attach node_id from dispatch context (NOT from LLM output)
    parsed["node_id"] = node_id
    return parsed
```

### Prompt

```
Leng prompt:

"file: BESTBUY_2023_10K.md
 section: Consolidated Statements of Earnings
 retrieve_target: Consolidated Statements of Earnings
 ---
 | Fiscal Years Ended          | January 28, 2023 | January 29, 2022 |
 |-----------------------------|--------------------|-------------------|
 | Revenue                     | $ 46,298           | $ 51,761          |
 | Cost of sales               | 36,386             | 40,121            |
 | Gross profit                | 9,912              | 11,640            |
 ---

 Extract ALL of these metrics from the chunk above:
   Revenue, Cost of Sales
 Period: FY2023
 Statement type: income_statement

 For each metric, report its raw numeric value (no commas, no $).
 If you CANNOT find ALL listed metrics in this chunk, return found=false.

 Output JSON only:
 {"found": true, "metrics": {"Revenue": 46298, "Cost of Sales": 36386}}
 OR
 {"found": false}"
```

**Leng does NOT echo node_id.** Python already knows which node_id
each Leng was dispatched with. After parsing Leng's JSON response,
Python attaches the node_id:
```python
result = _parse_leng_json(raw_output)
result["node_id"] = original_node_id  # set by dispatcher, not LLM
```
This avoids haiku needing to perfectly copy a 36-char UUID — LLMs
are unreliable at exact string reproduction.

**Leng JSON parse failure:** Strip markdown fences. If JSON still
fails to parse, treat as `{"found": false}`. Haiku is cheap —
a parse failure on one chunk out of 12 is not worth crashing Gulei.

**Period-column disambiguation is NOT Leng's job.** Financial tables
show multiple periods in columns. Leng may extract the wrong
column's value. This is acceptable — Leng is screening for metric
PRESENCE, not authoritative extraction. Period correctness is
Judge's responsibility when it re-extracts from PUMBA's chunks.

### Why Leng extracts values even though Judge re-extracts

Leng's extracted values serve TWO purposes:

1. **Self-screening.** By forcing haiku to actually locate and report
   each value, it must engage with the chunk content. A chunk that
   "looks like" an income statement (has the right section header)
   but doesn't contain the actual numbers will fail at extraction
   time. This catches false positives that a simple "does this chunk
   contain Revenue?" yes/no would miss.

2. **Disposable.** Leng's values are NOT used downstream. GuleiSau
   doesn't see them. Judge re-extracts authoritatively from the same
   chunks with full denomination/unit handling. Leng values exist
   only to force honest screening.

## LLM call budget

```
Per PUMBA invocation (one failed batch):

  Dailo:       ~3-5 turns (agent loop, mid-tier)
  Per round:
    6x GuleiPai:    6 calls  (mid-tier, chunk summaries)
    up to 72x Leng: 72 calls (haiku, parallel within each Gulei)
    0-6x GuleiSau:  0-6 calls (mid-tier, only if >1 Leng hit per file)

  Note: Dailo ranking (>3 hits) is NOT a separate LLM call. Dailo
  reads full chunk text in spawn_gulei tool result and picks top 3
  when it calls report_results. No extra call needed.

  Round total:  ~6-12 mid-tier + up to 72 haiku + 1 Dailo turn
  Max rounds:   ceil(total_files / 6)  (e.g. 3 for Best Buy's 14 files)

  Worst case (Best Buy, 3 rounds, all exhausted):
    Dailo:     ~8 turns
    GuleiPai:  14 calls
    GuleiSau:  ~7 calls
    Leng:      168 calls (haiku)
    Total:     ~29 mid-tier + 168 haiku

  Best case (found on first round, 1 file has it):
    Dailo:     3 turns (ls, ls, spawn+report)
    GuleiPai:  6 calls
    GuleiSau:  0 calls (single Leng hit)
    Leng:      72 calls (haiku)
    Total:     ~9 mid-tier + 72 haiku
```

## Threading

```
run_pumba()                              <-- called from orchestrator
  |                                          (already in worker thread)
  +-- Dailo agent loop (sequential)
        |
        +-- spawn_gulei(files=[6])
              |
              +-- ThreadPoolExecutor(max_workers=6)
                    |
                    +-- gulei(file_1)
                    |     |
                    |     +-- GuleiPai LLM call
                    |     +-- ThreadPoolExecutor(max_workers=12)
                    |     |     +-- leng(chunk_0)  \
                    |     |     +-- leng(chunk_1)   | parallel
                    |     |     +-- ...             |
                    |     |     +-- leng(chunk_11) /
                    |     +-- GuleiSau LLM call (if >1 hit)
                    |
                    +-- gulei(file_2)  ... (parallel with file_1)
                    +-- gulei(file_3)  ...
                    +-- gulei(file_4)  ...
                    +-- gulei(file_5)  ...
                    +-- gulei(file_6)  ...
```

Nested parallelism: 6 Guleis in parallel, each with 12 Lengs in
parallel = up to 72 concurrent haiku calls in a round. Acceptable
because haiku calls are cheap and fast.

**Context window note:** Dailo's accumulated conversation grows with
each spawn_gulei result (each includes full chunk text for hits —
~500-2000 tokens per chunk). Worst case: 3 rounds × 4 hits ×
1500 tokens = ~18k tokens of chunk text in conversation history.
Well within 200k context window. Set Dailo's `max_tokens` (response
limit) conservatively (e.g. 2048) — it only outputs short tool calls.

### _run_guleis_parallel

```python
def _run_guleis_parallel(
    files: list[str],
    request: PTOBatchRequest,
    index: VectorStoreIndex,
    file_table_index: dict[str, list[tuple[str, str, str]]],
    gulei_llm: LLMBackend,
    leng_llm: LLMBackend,
    ch: ToolChannel,
) -> list[str | None]:
    """Run one Gulei per file in parallel. Returns list parallel to files:
    node_id (str) if Gulei found a chunk, None if not.

    Exceptions from individual Gulei runs are caught and mapped to None.
    One file crashing (API error, timeout) must NOT kill other files
    or the PUMBA invocation.

    ch is threaded through explicitly (NOT override_channel) because
    Gulei workers run in ThreadPoolExecutor threads, which don't
    inherit the caller's thread-local overrides.
    """
    def _safe_gulei(fname: str) -> str | None:
        try:
            return _run_single_gulei(
                fname, request, index, file_table_index,
                gulei_llm, leng_llm, ch,
            )
        except Exception:
            ch.print(f"  Gulei {fname}: CRASHED (exception), skipping")
            return None

    ch.print(f"Spawning {len(files)} Gulei workers...")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_safe_gulei, f) for f in files]
        return [f.result() for f in futures]  # preserves file order
```

Note: `futures` is a list (not dict), preserving insertion order
so `results[i]` corresponds to `files[i]`. This matters for
`_format_spawn_gulei_result` which does `zip(files, results)`.

**Thread safety:** `LLMBackend` instances (gulei_llm, leng_llm) are
created once in `run_pumba()` and shared across all Gulei/Leng
threads. The underlying clients (httpx for anthropic/openai/deepseek)
are thread-safe. This avoids creating 72+ redundant instances.
Dailo's raw SDK client is also created once and shared (only used
sequentially in the agent loop, not across threads).

### Logging (channel.print)

PUMBA receives `ch` as a parameter (e.g. `register("PUMBA-Best Buy")`)
and threads it explicitly to all internal functions. No module-level
channel, no override_channel — everything goes through the param.
Key log points a junior MUST include:

```python
# run_pumba entry:
ch.print(f"Dailo starting for {request.firm} {request.period}...")

# _run_guleis_parallel:
ch.print(f"Spawning {len(files)} Gulei workers...")

# _run_single_gulei:
ch.print(f"  Gulei {fname}: {len(table_chunks)} table chunks")
ch.print(f"  Gulei {fname}: GuleiPai picking {k}...")
ch.print(f"  Gulei {fname}: {len(hits)} Leng hits out of {n_lengs}")
# if GuleiSau:
ch.print(f"  Gulei {fname}: GuleiSau picking best of {len(hits)} hits")
# result:
ch.print(f"  Gulei {fname}: {'FOUND ' + nid[:12] if nid else 'NOT FOUND'}")

# Dailo round summary (in spawn_gulei handler, after Guleis finish):
# (shown to user, not to Dailo LLM)
n_hits = sum(1 for r in results if r is not None)
ch.print(f"Round: {n_hits}/{len(valid_files)} files returned chunks")

# Final:
ch.print(f"Returning {len(node_ids)} chunks to Judge.")
# or:
ch.print(f"PUMBA exhausted all files for {request.firm}")
```

## Integration: orchestrator.py changes

The integration replaces orchestrator.py lines 87-103 (the PTO
retrieval + judge + convert block inside the batch loop). The FULL
modified batch loop body is shown below — not just the PUMBA
addition, but the COMPLETE replacement so a junior can see exactly
where every line goes.

```python
# At top of orchestrator.py, add:
from src.pumba import run_pumba
from llama_index.core.schema import NodeWithScore  # if not already imported

# orchestrator.run() signature is UNCHANGED:
#   def run(plan, index, config) -> dict[str, CellResult]:
# _pto_out = register("PMS1") at module level (existing) is used
# for regular PMS1 output. PUMBA gets its own channel via param.
#
# Inside run(), the for-loop body (replaces lines 87-103).

    for batch in plan.batches:
        # ── Adapter: SekeiBatch → PTOBatchRequest ── (unchanged)
        metrics = [plan.cells[cid].metric for cid in batch.cells]
        request = PTOBatchRequest(
            batch_id=batch.id,
            firm=plan.firm,
            period=batch.period,
            statement=batch.statement,
            metrics=metrics,
            retrieve_target=batch.retrieve_target,
        )
        cell_map = {
            cid: plan.cells[cid].metric
            for cid in batch.cells
        }

        # ── PTO retrieval ── (unchanged)
        result = pto_retrieve(request, index, config)

        # ── Judge ──
        # CRITICAL: pto_judge is OUTSIDE the try block.
        # pto_judge does json.loads() which raises JSONDecodeError
        # (subclass of ValueError) on malformed LLM output. That's a
        # judge failure, not a retrieval failure — chunks might be
        # fine, retry could work. PUMBA can't help with garbled JSON.
        judge_output = pto_judge(request, result, config)

        # ── Parse (PUMBA triggers here on ValueError) ──
        try:
            batch_values = pto_judge_to_stencil(
                judge_output, cell_map, result
            )
        except ValueError as pto_err:
            _pto_out.print(
                f"PTO failed: {pto_err}. Falling back to PUMBA..."
            )

            # Create firm-specific PUMBA channel and pass explicitly.
            # NOT override_channel — PUMBA threads ch through params
            # because Gulei/Leng run in ThreadPoolExecutor threads
            # that don't inherit thread-local overrides.
            pumba_ch = register(f"PUMBA-{request.firm}")

            # PUMBA returns [] if user chose "skip", or node_ids
            # if chunks found. Only raises ValueError if user
            # chose "exit".
            pumba_node_ids = run_pumba(
                request, index, config, channel=pumba_ch
            )

            # Empty = user skipped this batch. Leave cells unfilled.
            if not pumba_node_ids:
                _pto_out.print(
                    f"Batch {request.batch_id} skipped (PUMBA)."
                )
                continue  # next batch

            # Build PTOBatchResult from PUMBA chunks.
            # Validate node_ids exist in docstore.
            pumba_top_chunks = []
            for i, nid in enumerate(pumba_node_ids):
                node = index.docstore.docs.get(nid)
                if node is None:
                    _pto_out.print(
                        f"PUMBA invalid node_id: {nid}, skipping"
                    )
                    continue
                pumba_top_chunks.append(
                    NodeWithScore(
                        node=node,
                        score=float(len(pumba_node_ids) - i),
                    )
                )

            if not pumba_top_chunks:
                _pto_out.print(
                    "PUMBA returned no valid chunks after validation"
                )
                continue  # skip batch

            pumba_result = PTOBatchResult(
                batch_id=request.batch_id,
                top_chunks=pumba_top_chunks,
                runner_ups=[],
                method_log=PTOMethodLog(
                    hyde_good="PUMBA",
                    hyde_bad="",
                    metadata_filters={"method": "pumba_fallback"},
                ),
            )

            # Re-run judge with PUMBA-sourced chunks.
            # If judge fails on PUMBA chunks too, ask user again.
            try:
                judge_output = pto_judge(
                    request, pumba_result, config
                )
                batch_values = pto_judge_to_stencil(
                    judge_output, cell_map, pumba_result
                )
            except ValueError as pumba_judge_err:
                _pto_out.print(
                    f"Judge failed on PUMBA chunks too: "
                    f"{pumba_judge_err}"
                )
                answer = _pto_out.input(
                    "PUMBA chunks also insufficient. "
                    "'skip' to skip batch, 'exit' to abort:"
                )
                if answer.strip().lower() == "exit":
                    raise pto_err
                _pto_out.print(
                    f"Batch {request.batch_id} skipped."
                )
                continue  # skip batch

        # ── Convert PTOCellResult → CellResult ── (unchanged)
        for cell_id, pcr in batch_values.items():
            all_values[cell_id] = CellResult(
                value=pcr.value, source=pcr.source,
                denomination=pcr.denomination, unit=pcr.unit,
            )

    # ── Stencil eval ──
    # IMPORTANT: if any batch was skipped (PUMBA exhausted + user
    # said "skip"), those cells are NOT in all_values.
    # compute_stencil raises ValueError("Missing value for retrieve
    # cell X") for each missing cell.
    # Two options for the implementer:
    #   a) Let it crash — the pipeline reports which cells are
    #      missing. Caller (tool_pms1) catches and reports to PSOAS.
    #   b) Filter plan.cells to exclude skipped batches' cells before
    #      computing. Produces a partial stencil.
    # For now: option (a) — let compute_stencil raise. The error
    # message is informative ("Missing value for retrieve cell A1")
    # and propagates through:
    #   compute_stencil raises ValueError
    #   → orchestrator.run() propagates
    #   → tool_pms1.run_pms1_pipeline() propagates
    #   → execute_tool._exec_pms1() catches, returns error string
    #   → PSOAS orchestrator LLM sees the error and decides what to do
    #     (e.g. re-run with different query, or report partial data)
    #
    # This is INTENTIONAL. A partial stencil with missing cells is
    # worse than a clear error, because downstream PTECA/stencil2chart
    # would produce charts with missing data points and no explanation.
    filled = compute_stencil(plan, all_values)
    return filled
```

## Implementation details

### LLM call patterns — model agnosticism

**Principle:** pumba.py NEVER hardcodes a model name or provider.
All model selection flows through config.py → llm_profiles.yaml →
llm.py factory functions. To swap Leng from haiku to deepseek,
change one profile name in config.py.

Two call patterns in PUMBA:

```
TIER              NEEDS tools=?    PATTERN
────              ────────────     ───────
Dailo             YES (agent loop) raw SDK, profile-driven model name
GuleiPai/Sau      NO               LLMBackend via llm.py factory
Leng              NO               LLMBackend via llm.py factory
```

**GuleiPai/GuleiSau/Leng** use `LLMBackend.complete(prompt,
system_prompt) -> str`. Fully model-agnostic — works with
anthropic, deepseek, openai, gemini today (all implemented in
llm.py). Swappable by changing profile name.

Their profiles should have LOW `max_tokens` (e.g. 1024) because
responses are short JSON (`{"picks": [0,1,2]}` or `{"found": true,
"metrics": {...}}`). High `max_tokens` wastes tokens if the model
rambles. Set this in `llm_profiles.yaml`, not in code.

**Dailo** needs `tools=` in the API call (agent loop pattern).
`LLMBackend` currently has no `tools=` support. So Dailo uses the
raw provider SDK, but reads model name + params from its profile.
The only provider-specific code is the SDK client construction and
`messages.create` call shape.

Future: extend `LLMBackend` with `complete_with_tools(prompt,
system_prompt, tools) -> response` to make Dailo provider-agnostic
too. OpenAI/DeepSeek/Gemini all support tool use. This is a real
priority but out of scope for this spec.

### Required changes to config.py

Add three dataclass fields to the Config class, after the existing
`pto_judge_profile` line (src/scripts/config.py line 57):

```python
    pto_judge_profile: str = "anthropic_opusmedthink"  # existing
    # ── PUMBA (add these 3 lines) ──
    pumba_dailo_profile: str = "anthropic_opusmedthink"
    pumba_gulei_profile: str = "anthropic_sonnetmed"
    pumba_leng_profile: str = "anthropic_hayasui"
```

All three profiles already exist in llm_profiles.yaml:
- `anthropic_opusmedthink`: opus-4-8 + 5k thinking (line 45)
- `anthropic_sonnetmed`: sonnet-4-6, no thinking (line 20)
- `anthropic_hayasui`: haiku-4-5, no thinking (line 14)

### Required changes to llm.py

Add two factory functions at the bottom of llm.py (after
`get_pto_judge_llm`, line 338). Follow the exact same `_get()`
pattern used by the existing factories:

```python
# ── PUMBA factories (add these) ──

def get_pumba_gulei_llm(config: Config) -> LLMBackend:
    """PUMBA GuleiPai/GuleiSau LLM — chunk selection and review."""
    return _get(config, "pumba_gulei_profile")


def get_pumba_leng_llm(config: Config) -> LLMBackend:
    """PUMBA Leng LLM — chunk screening (cheapest model)."""
    return _get(config, "pumba_leng_profile")
```

`_get()` is the existing generic factory at llm.py line 317:
```python
def _get(config: Config, profile_attr: str) -> LLMBackend:
    profiles = _load_profiles(config.llm_profiles_path)
    name = getattr(config, profile_attr)
    prof = _resolve_profile(profiles, name)
    return _make_llm(prof)
```

NO `get_pumba_dailo_llm` — Dailo uses raw `anthropic.Anthropic()`
SDK (needs `tools=` in the API call). Model name still comes from
`config.get_llm_profile(config.pumba_dailo_profile)`, not hardcoded.

```python
# In pumba.py — run_pumba entry point:
# The Dailo agent loop runs inline in run_pumba() (not factored
# into a separate _run_dailo function — the pre-computation,
# LLM setup, and agent loop are all in one function body).
def run_pumba(request, index, config, channel=None):
    ch = channel or register("PUMBA")

    # Dailo: raw SDK, but model from profile (not hardcoded)
    dailo_profile = config.get_llm_profile(config.pumba_dailo_profile)
    import anthropic  # <-- only provider-specific import
    dailo_client = anthropic.Anthropic()

    # Gulei/Leng: LLMBackend, fully provider-agnostic
    gulei_llm = get_pumba_gulei_llm(config)  # could be deepseek, gemini, etc.
    leng_llm = get_pumba_leng_llm(config)    # could be haiku, gemini-flash, etc.

    # ... pre-compute file_table_index, build system prompt,
    #     then Dailo agent loop runs inline (see Agent loop section) ...
```

**Thread safety:** `LLMBackend` instances are NOT guaranteed
thread-safe (OpenAI client uses httpx, anthropic client uses httpx
— both are thread-safe in practice, but the abstraction doesn't
promise it). For Gulei/Leng parallelism, create one `LLMBackend`
instance and share it. If a provider's client is not thread-safe,
the factory function can be called per-thread instead.

### Dailo system prompt construction

System prompt is built ONCE from the batch request at loop start.
It does NOT change between turns (blacklist is in tool results,
not system prompt — see blacklist propagation section above).

The authoritative system prompt template is in the "System prompt
(sketch)" section above (uses `%(metrics)s` style formatting).
The construction is shown in the "Client creation" section:

```python
dailo_system_prompt = DAILO_SYSTEM_TEMPLATE % {
    "firm": request.firm,
    "period": request.period,
    "metrics": ", ".join(request.metrics),
    "statement": request.statement,
}
```

Do NOT use f-string formatting — the template contains literal
curly braces in directory structure examples that f-strings would
choke on.

### list_dir implementation

See `_list_data_dir` implementation earlier in this spec (under
Guard rails section). It validates path stays under `config.data_dir`,
skips dotfiles, and suffixes directories with `/`.

### spawn_gulei filename handling

spawn_gulei takes **bare filenames** (e.g. `"BESTBUY_2023_10K.md"`),
NOT full paths. This matches docstore `file_name` metadata, which
is set by `ingest.py` line 72: `Path(rel_path).name` — bare filename
without directory prefix.

If Dailo passes >6 files, truncate to first 6 and include a note
in the tool result: `"(truncated to 6 files — send remaining in
next spawn_gulei call)"`.

### Terminal output — PUMBA channel

PUMBA registers one channel: `register("PUMBA")`. All tiers print
through this channel. Gulei/Leng threads call `ch.print()` from
worker threads — this is safe because TerminalRouter is designed
for cross-thread printing (Queue-based, lock-protected).

Output from concurrent Guleis will interleave. This is expected:
```
[PUMBA] Gulei BESTBUY_2023_10K.md: 62 table chunks
[PUMBA] Gulei BESTBUY_2022_10K.md: 58 table chunks
[PUMBA] 12 Lengs for BESTBUY_2023_10K.md... 2 hits
[PUMBA] Gulei BESTBUY_2024Q2_10Q.md: 31 table chunks
```
Each `ch.print()` call is one atomic styled line. No corruption.

PUMBA calls `ch.input()` ONLY on failure paths (exhausted all
files, or Dailo exceeded max turns) to let the user choose
"skip" or "exit". Normal (successful) operation has no user I/O.

Add `"PUMBA"` to `LABEL_STYLES` in `terminal_router.py`:
```python
LABEL_STYLES = {
    "ORCHESTRATOR": "bold cyan",
    "PMS1":         "bold green",
    "PTECA":        "bold yellow",
    "S2C":          "bold magenta",
    "PUMBA":        "bold red",       # <-- ADD
}
```

### Threading model

```
PSOAS agent_loop
  └─ _dispatch_parallel(ThreadPoolExecutor)      <-- PSOAS level
       └─ _exec_pms1 (worker thread)
            └─ orchestrator.run()
                 └─ pto_judge fails → run_pumba()
                      └─ Dailo agent loop (sequential, same thread)
                           └─ spawn_gulei handler
                                └─ ThreadPoolExecutor(max_workers=6)
                                     └─ gulei(file_1)
                                     |    └─ GuleiPai LLM call
                                     |    └─ ThreadPoolExecutor(max_workers=12)
                                     |         └─ leng(chunk_0..11)
                                     |    └─ GuleiSau LLM call (maybe)
                                     └─ gulei(file_2) ... (parallel)
```

Each `ThreadPoolExecutor` creates its own thread pool — they do NOT
share. Total threads per round: 1 (Dailo) + 6 (Gulei) + 72 (Leng)
= 79. All I/O bound (waiting on API calls). Python handles this
fine. Threads are short-lived (one API call each).

No shared mutable state between Gulei threads (each operates on a
different file's chunks). No shared mutable state between Leng
threads (each reads one chunk). The only shared state is:
- `index.docstore.docs` — read-only dict, thread-safe
- `ch` (ToolChannel) — `_router.print()` is lock-protected
- `dailo_client` — Dailo's raw SDK client (sequential, not shared across threads)
- `gulei_llm`, `leng_llm` — LLMBackend instances, thread-safe

### orchestrator.py modification scope

The spec requires restructuring the existing try/except in
orchestrator.py's batch loop. This is NOT just adding PUMBA code
after it — the existing code must be SPLIT:

BEFORE (current code):
```python
result = pto_retrieve(request, index, config)
judge_output = pto_judge(request, result, config)    # <-- these two
batch_values = pto_judge_to_stencil(...)              # <-- were not in try
```

AFTER (with PUMBA):
```python
result = pto_retrieve(request, index, config)
judge_output = pto_judge(request, result, config)  # OUTSIDE try
try:
    batch_values = pto_judge_to_stencil(judge_output, cell_map, result)
except ValueError as pto_err:
    # PUMBA fallback path (see Integration section)
```

`pto_judge` stays outside because `json.JSONDecodeError` (subclass
of `ValueError`) from malformed judge output is NOT a retrieval
failure — chunks might be fine. Only `pto_judge_to_stencil` raises
ValueError for retrieval failures (insufficient, missing metric).

## Dependencies

| Component | What it provides |
|---|---|
| `anthropic` SDK | Dailo agent loop only (needs `tools=`). Import localized to `run_pumba`. |
| `src.llm` | `get_pumba_gulei_llm()`, `get_pumba_leng_llm()` — provider-agnostic LLMBackend factories. `LLMBackend` type for signatures. |
| `llama_index.core` | `VectorStoreIndex`, `NodeWithScore` for docstore access |
| `src.config` | `Config` — data_dir path, LLM profile name mappings (`pumba_dailo_profile`, `pumba_gulei_profile`, `pumba_leng_profile`) |
| `src.pto` | `PTOBatchRequest` — input type (import, not modify) |
| `src.harness.terminal_router` | `register()`, `ToolChannel` for output |
| `concurrent.futures` | `ThreadPoolExecutor` for Gulei/Leng parallelism |

Does NOT import from: orchestrator, stencil, sekei, agent_loop,
execute_tool, opaque_registry. No circular deps.

orchestrator.py gains a new import: `from src.pumba import run_pumba`.

terminal_router.py needs `"PUMBA": "bold red"` added to LABEL_STYLES
(see Channel routing section).

**Config import resolution:** `pumba.py` does `from src.config import
Config`. Due to `src/__init__.py` `__path__` extension, this resolves
to the PSOAS override at `src/scripts/config.py` (NOT the PMS1
original at `Poony_Multiretrieval_S1/src/config.py`). Both point
`data_dir` to the same physical path (`Poony_Multiretrieval_S1/data/`)
but through different `_ROOT` calculations. The PSOAS override is
correct — it has `get_llm_profile()` which the PMS1 original lacks.

## File location

```
Poony_Multiretrieval_S1/src/pumba.py
```

Sibling of pto.py, orchestrator.py, sekei.py, stencil.py.

## Ideal demo

```
[PMS1] PTO batch A_12 (FY2023 income_statement)... 38 chunks filtered.
[PMS1] PTO judge... tokens in=3200 out=450.
[PMS1] [pto_judge] batch=A_12 tokens in=3200 out=450
[PMS1] PTO failed: Insufficient: metric 'Revenue' (cell A1). Retry not implemented.
[PMS1] Falling back to PUMBA...

[PUMBA-Best Buy] Dailo starting for Best Buy FY2023...
[PUMBA-Best Buy] Dailo: found Consumer Discretionary/BESTBUY/ (14 files)
[PUMBA-Best Buy] Dailo: picking 6 files for round 1
[PUMBA-Best Buy] Spawning 6 Gulei workers...
[PUMBA-Best Buy]   Gulei BESTBUY_2023_10K.md: 62 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2022_10K.md: 58 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2023Q4_EARNINGS.md: 18 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2024Q2_10Q.md: 31 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2021_10K.md: 55 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2024Q2_EARNINGS.md: 14 table chunks
[PUMBA-Best Buy]   Gulei BESTBUY_2023_10K.md: 2 Leng hits
[PUMBA-Best Buy]   Gulei BESTBUY_2022_10K.md: 0 Leng hits
[PUMBA-Best Buy]   Gulei BESTBUY_2023Q4_EARNINGS.md: 1 Leng hit
[PUMBA-Best Buy]   Gulei BESTBUY_2024Q2_10Q.md: 0 Leng hits
[PUMBA-Best Buy]   Gulei BESTBUY_2021_10K.md: 0 Leng hits
[PUMBA-Best Buy]   Gulei BESTBUY_2024Q2_EARNINGS.md: 0 Leng hits
[PUMBA-Best Buy]   GuleiSau for BESTBUY_2023_10K.md: picking best of 2 hits
[PUMBA-Best Buy] Round: 2/6 files returned chunks
[PUMBA-Best Buy] Returning 2 chunks to Judge.

[PMS1] PUMBA found 2 chunks. Re-running judge...
[PMS1] [pto_judge] batch=A_12 tokens in=2100 out=380
[PMS1] PUMBA fallback succeeded. Cell A1 filled.
```

## Failure demo

Three failure scenarios showing how errors surface from PUMBA → PMS1
→ orchestrator. These are what the user SEES in terminal output.

### Failure 1: PUMBA exhausts all files — user skips

```
[PMS1-Best Buy] PTO batch A_12 (FY2023 income_statement)... 38 chunks.
[PMS1-Best Buy] PTO failed: Insufficient: metric 'Segment Revenue' (cell A1).
[PMS1-Best Buy] Falling back to PUMBA...

[PUMBA-Best Buy] Dailo starting for Best Buy FY2023...
[PUMBA-Best Buy] Spawning 6 Gulei workers...
[PUMBA-Best Buy]   Gulei BESTBUY_2023_10K.md: 0 Leng hits
[PUMBA-Best Buy]   Gulei BESTBUY_2022_10K.md: 0 Leng hits
                   ... (all files surveyed across 3 rounds)
[PUMBA-Best Buy] PUMBA exhausted all files for Best Buy
[PUMBA-Best Buy] PUMBA failed to find the data. Options:
                   'skip' - skip this batch
                   'exit' - abort the entire PMS1 run
                 Your choice:
> skip
[PMS1-Best Buy] Batch A_12 skipped (PUMBA).
[PMS1-Best Buy] PTO batch B_12 (FY2022 income_statement)...
                  ... (pipeline continues with remaining batches)
```

User chose "skip" → PUMBA returns `[]` → orchestrator `continue`s
to next batch. Cell A1 remains unfilled. `compute_stencil` may
fail later if a formula depends on A1, but other batches proceed.

### Failure 1b: PUMBA exhausts all files — user exits

```
[PUMBA-Best Buy] PUMBA exhausted all files for Best Buy
[PUMBA-Best Buy] PUMBA failed to find the data. Options:
                   'skip' - skip this batch
                   'exit' - abort the entire PMS1 run
                 Your choice:
> exit

  *** ValueError raised ***
  "User aborted after PUMBA exhausted all files for Best Buy"
  → orchestrator.run() raises → tool_pms1 propagates
  → execute_tool returns error string to PSOAS LLM
  → PSOAS orchestrator decides what to do
```

### Failure 2: PUMBA finds chunks, Judge still fails — user skips

```
[PMS1-Best Buy] PTO failed: Insufficient: metric 'Revenue' (cell A1).
[PMS1-Best Buy] Falling back to PUMBA...

[PUMBA-Best Buy] Dailo starting for Best Buy FY2023...
[PUMBA-Best Buy] Spawning 6 Gulei workers...
[PUMBA-Best Buy]   Gulei BESTBUY_2023_10K.md: 2 Leng hits
[PUMBA-Best Buy]   GuleiSau: picking best of 2 hits
[PUMBA-Best Buy]   Gulei BESTBUY_2023Q4_EARNINGS.md: 1 Leng hit
[PUMBA-Best Buy] Round: 2/6 files returned chunks
[PUMBA-Best Buy] Returning 2 chunks to Judge.

[PMS1-Best Buy] PUMBA found 2 chunks. Re-running judge...
[PMS1-Best Buy] [pto_judge] batch=A_12 tokens in=2100 out=380
[PMS1-Best Buy] Judge failed on PUMBA chunks too: Insufficient: metric 'Revenue' (cell A1).
[PMS1-Best Buy] PUMBA chunks also insufficient. 'skip' to skip batch, 'exit' to abort:
> skip
[PMS1-Best Buy] Batch A_12 skipped.
[PMS1-Best Buy] PTO batch B_12 (FY2022 income_statement)...
                  ... (pipeline continues)
```

PUMBA found chunks that LOOKED right to Leng (haiku said found=true)
but Judge (opus) couldn't extract the value — maybe wrong period
column, maybe sub-table truncation cut off the row. User skips,
pipeline continues with remaining batches.

### Failure 3: PTO judge returns malformed JSON (PUMBA does NOT fire)

```
[PMS1-Best Buy] PTO batch A_12 (FY2023 income_statement)... 38 chunks.
[PMS1-Best Buy] [pto_judge] batch=A_12 tokens in=3200 out=450

  *** json.JSONDecodeError raised ***
  "Expecting value: line 1 column 1 (char 0)"
  (pto_judge is OUTSIDE the PUMBA try block — JSONDecodeError
   propagates directly. PUMBA never runs.)
```

Correct behavior. The judge LLM returned garbage (not JSON). The
chunks might be fine — retrying judge could work. PUMBA replaces
chunks, which doesn't help when the problem is judge output parsing.

### Error propagation chain

```
PUMBA exhausted / Dailo max turns
  → ch.input("skip or exit?")
  → user says "skip" → return []
    → orchestrator: continue (skip batch)
    → pipeline continues with remaining batches
  → user says "exit" → raise ValueError("User aborted...")
    → orchestrator.run() raises
    → tool_pms1.run_pms1_pipeline() propagates
    → execute_tool catches, returns error to PSOAS LLM

Judge fails on PUMBA chunks
  → ch.input("skip or exit?")
  → same branch as above

Judge JSON parse error (pto_judge itself)
  → json.JSONDecodeError (subclass of ValueError)
  → NOT caught by PUMBA try block (pto_judge is outside it)
  → propagates directly through orchestrator.run()
  → tool_pms1 → execute_tool → PSOAS LLM
```

## Error handling summary

Every LLM call in PUMBA can fail (malformed JSON, hallucinated
output, API errors). The principle: fail gracefully at every tier,
never crash the pipeline. A single Leng/Gulei failure should NOT
kill the entire PUMBA invocation.

```
TIER        FAILURE MODE              HANDLING
────        ────────────              ────────
Leng        JSON parse error          treat as {found: false}
Leng        API error/timeout         treat as {found: false}
GuleiPai    JSON parse error          return None (skip this file)
GuleiPai    out-of-range indices      silently drop, keep valid ones
GuleiPai    all indices invalid       return None
GuleiSau    JSON parse error          return first Leng hit's node_id
GuleiSau    out-of-range best idx     return first Leng hit's node_id
Gulei       0 table chunks in file    return None immediately
Gulei       any unhandled exception   _safe_gulei wrapper returns None
Dailo       list_dir path escape      return error string to Dailo LLM
Dailo       spawn_gulei bad filenames normalize Path(f).name, drop invalid
Dailo       spawn_gulei all blacklist "already surveyed" msg to Dailo
Dailo       spawn_gulei path vs name  normalize to basename before
                                      docstore lookup
Dailo       report_results + spawn    reject report, "see spawn first"
            in same turn
Dailo       end_turn w/o report       inject error, continue loop
Dailo       all files exhausted       ch.input("skip or exit?")
                                      skip → return [], exit → raise
Dailo       max turns exceeded        ch.input("skip or exit?")
                                      skip → return [], exit → raise
spawn_gulei result: stale node_id     treat as NOT FOUND in formatting
                    in docstore
Orchestr.   pto_judge JSONDecodeError propagate up (NOT PUMBA's problem)
Orchestr.   PUMBA node_id not in      skip that node_id, use remaining
            docstore
Orchestr.   Judge fails on PUMBA      ch.input("skip or exit?")
            chunks too                skip → continue, exit → raise
                                      (one-shot, no PUMBA recursion)
```

All JSON parsing uses `_parse_json_with_fences`, a shared helper
in pumba.py:

```python
def _parse_json_with_fences(raw: str) -> dict | None:
    """Parse JSON, stripping markdown fences. Returns None on failure.

    Same pattern as pto_judge. Every LLM call in PUMBA (GuleiPai,
    GuleiSau, Leng) pipes output through this. None = graceful
    degradation at the calling tier.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
```

## Open questions

### Deferred to implementation

- **LLM model assignments.** See model matrix below — Dailo gets
  thinking, everyone else does not. Exact profile names may change.

- **GuleiPai k=12 tuning.** 12 chunks per file is a starting point.
  May need tuning based on how many table chunks typical files have
  (~40-80 for 10Ks, ~15-30 for 10Qs/earnings). Could be
  proportional: min(12, len(chunks) // 3)?

- **Concurrency limits.** 72 concurrent haiku calls per round. May
  need rate limiting or semaphore if Anthropic API throttles.
  Not a problem for small corpus (4 firms, 34 files) but matters
  at scale.

## LLM model matrix

```
TIER        PROFILE (candidate)       THINKING    WHY
────        ───────────────────       ────────    ───
Dailo       anthropic_opusmedthink    YES         needs to reason about which
            or new profile                        files/dirs to explore, which
                                                  chunks to rank, when to stop.
                                                  agent loop = needs planning.

GuleiPai    anthropic_sonnetmed       NO          picks chunk indices from a
                                                  summary list. straightforward
                                                  matching task, no deep reasoning.

GuleiSau    anthropic_sonnetmed       NO          picks best of 2-3 candidates.
                                                  reads chunk text and compares.
                                                  simple comparison, no thinking.

Leng        anthropic_hayasui         NO          cheapest. extract-or-fail on
                                                  one chunk. 72 parallel calls
                                                  per round — must be cheap.
```

### Dailo thinking kwargs (anthropic-specific, for now)

Dailo is the ONLY tier that uses raw SDK + thinking. Its
`messages.create` call must pass thinking params explicitly.

GuleiPai/GuleiSau/Leng use `LLMBackend.complete()` — no thinking,
no tools, fully provider-agnostic. To swap Leng to deepseek-chat,
change `pumba_leng_profile` in config.py to `"deepseek_chattemp0"`.

```python
# Dailo: raw SDK call with thinking (anthropic-specific for now)
# Model name + params come from config.pumba_dailo_profile, NOT hardcoded.
dailo_profile = config.get_llm_profile(config.pumba_dailo_profile)
dailo_model = dailo_profile["model"]
dailo_max_tokens = dailo_profile["max_tokens"]

# Build thinking kwargs from profile
dailo_extra_kwargs = {}
if dailo_profile.get("thinking"):
    if "opus-4-8" in dailo_model:
        dailo_extra_kwargs["thinking"] = {"type": "adaptive"}
        dailo_extra_kwargs["output_config"] = {"effort": "high"}
    else:
        budget = dailo_profile.get("thinking_budget", 5000)
        dailo_extra_kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": budget,
        }

# In the while loop:
response = dailo_client.messages.create(
    model=dailo_model,
    system=dailo_system_prompt,
    messages=messages,
    tools=DAILO_TOOLS,
    max_tokens=dailo_max_tokens,
    **dailo_extra_kwargs,
)
```

```python
# GuleiPai/GuleiSau — LLMBackend, provider-agnostic:
raw = gulei_llm.complete(prompt)  # handles model, params internally

# Leng — LLMBackend, provider-agnostic:
raw = leng_llm.complete(prompt)   # handles model, params internally
```

**Future:** When `LLMBackend` gains `complete_with_tools()`, Dailo
can use it too and the `import anthropic` in pumba.py goes away
entirely. Until then, Dailo is the only anthropic-coupled component.

### Profile to add to llm_profiles.yaml

If the existing `anthropic_opusmedthink` profile is used for Dailo,
no new profile is needed — it already has `thinking: true` +
`thinking_budget: 5000` + `max_tokens: 8000`. The agent loop
code reads these fields from the profile dict.

If a different model/budget is desired, add e.g.:
```yaml
anthropic_dailothink:
  provider: anthropic
  model: claude-opus-4-8
  thinking: true
  thinking_budget: 5000
  max_tokens: 8000
```

## Prompt builder sketches

### System prompts (constants in pumba.py)

All GuleiPai/GuleiSau/Leng calls use system prompts. These are
short role-setting strings, NOT the full prompt (that's in the
user message via the `_build_*` functions below).

```python
LENG_SYSTEM = """\
You are a financial data screener. Given a table chunk from a \
filing, determine if it contains ALL requested metrics. Extract \
values if found. Output JSON only, no explanation."""

GULEI_PAI_SYSTEM = """\
You are a chunk selector. Given a list of table chunk summaries \
from a financial filing, pick the ones most likely to contain \
specific metrics. Output JSON only, no explanation."""

GULEI_SAU_SYSTEM = """\
You are a chunk reviewer. Given multiple table chunks that workers \
flagged as relevant, pick the single best one for the requested \
metrics. Output JSON only, no explanation."""
```

### _build_leng_prompt

```python
def _build_leng_prompt(
    file_name: str,
    section: str,
    chunk_text: str,
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    return (
        f"file: {file_name}\n"
        f"section: {section}\n"
        f"retrieve_target: {request.retrieve_target}\n"
        f"---\n"
        f"{chunk_text}\n"
        f"---\n\n"
        f"Extract ALL of these metrics from the chunk above:\n"
        f"  {metrics_str}\n"
        f"Period: {request.period}\n"
        f"Statement type: {request.statement}\n\n"
        f"For each metric, report its raw numeric value "
        f"(no commas, no $, no parentheses for negatives — "
        f"use minus sign).\n"
        f"If you CANNOT find ALL listed metrics in this chunk, "
        f"return found=false.\n\n"
        f'Output JSON only:\n'
        f'{{"found": true, '
        f'"metrics": {{"MetricName": 12345, ...}}}}\n'
        f'OR\n'
        f'{{"found": false}}'
    )
```

### _build_gulei_pai_prompt

```python
def _build_gulei_pai_prompt(
    table_chunks: list[tuple[str, str, str]],  # (node_id, section, preview)
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    lines = [
        f"File: (table chunks listed below)",
        f"Batch wants: {metrics_str} for {request.period}, "
        f"{request.statement}.",
        f"Retrieve target: {request.retrieve_target}",
        f"",
        f"Table chunks in this file:",
    ]
    for i, (nid, section, preview) in enumerate(table_chunks):
        lines.append(f"  [{i}] section='{section}'")
        lines.append(f"      '{preview}'")

    k = min(12, len(table_chunks))
    lines.append(f"")
    lines.append(
        f"Pick up to {k} chunk indices most likely to contain "
        f"ALL requested metrics."
    )
    lines.append(f'Output JSON only: {{"picks": [0, 4, 7, ...]}}')
    return "\n".join(lines)
```

### _build_gulei_sau_prompt

```python
def _build_gulei_sau_prompt(
    candidates: list[tuple[str, str]],  # (node_id, chunk_text)
    request: PTOBatchRequest,
) -> str:
    metrics_str = ", ".join(request.metrics)
    lines = [
        f"Batch wants: {metrics_str} for {request.period}, "
        f"{request.statement}.",
        f"Retrieve target: {request.retrieve_target}",
        f"",
        f"Workers flagged these table chunks as containing all "
        f"requested metrics:",
        f"",
    ]
    for i, (nid, text) in enumerate(candidates):
        lines.append(f"CANDIDATE {i}:")
        lines.append(text)
        lines.append("")

    lines.append(
        "Which single candidate best answers the batch request?"
    )
    lines.append(
        "Consider: correct period, correct statement, data quality."
    )
    lines.append(f'Output JSON only: {{"best": 0}}')
    return "\n".join(lines)
```

## Resolved design decisions

- **F1: Dailo exit.** Uses `report_results` tool (structured, like
  PTECA's finalize). Returns `node_ids` (max 3) + `exhausted` flag.
  Dailo only returns node_ids — cell/value extraction is Judge's job.
- **F2/F3: spawn_gulei result format.** Includes full chunk text for
  hits (Python expands node_ids from docstore). Dailo reads chunk
  text to rank >3 hits and pick top 3 for `report_results`.
- **F5: ValueError catch scope.** `pto_judge` (JSON parsing) is
  OUTSIDE try block. Only `pto_judge_to_stencil` (retrieval failures)
  is caught. JSONDecodeError from malformed judge output propagates
  up — that's a judge failure, not a retrieval failure.
