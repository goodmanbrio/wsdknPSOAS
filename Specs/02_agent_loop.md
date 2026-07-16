# Agent Loop

The while loop that drives the orchestrator. Calls
`client.messages.create()`, checks `stop_reason`, dispatches tool
calls to `execute_tool`, accumulates messages, enforces guard rails.

## Architecture

```
run_harness(user_query)
    │
    ├── create session dir: PSOAS/temp/sessions/YYYYMMDDHHMMSS/
    ├── create transcript file: transcript.md
    ├── messages = [{"role": "user", "content": user_query}]
    ├── turn_counter = 0
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  WHILE LOOP (one iteration = one turn)                     │
│                                                            │
│  1. client.messages.create(                                │
│         system=system_prompt,                              │
│         messages=messages,                                 │
│         tools=tool_definitions,                            │
│     )                                                      │
│                                                            │
│  2. Check stop_reason:                                     │
│                                                            │
│     ┌─ "end_turn" ──────────────────────────────────┐      │
│     │  Print final text via orchestrator_out.print() │      │
│     │  Append to transcript                          │      │
│     │  Break loop                                    │      │
│     └────────────────────────────────────────────────┘      │
│                                                            │
│     ┌─ "tool_use" ──────────────────────────────────┐      │
│     │                                                │      │
│     │  a. Count ToolUseBlocks in response            │      │
│     │                                                │      │
│     │  b. If count > MAX_TOOL_CALLS_PER_TURN (5):    │      │
│     │     Return error tool_result for ALL blocks     │      │
│     │     (none executed)                             │      │
│     │     Skip to step d                              │      │
│     │                                                │      │
│     │  c. Dispatch tool calls in PARALLEL:            │      │
│     │     ThreadPoolExecutor → execute_tool per block │      │
│     │     Collect results                             │      │
│     │     (terminal_router handles I/O contention)    │      │
│     │                                                │      │
│     │  d. Append assistant response + tool_results    │      │
│     │     to messages                                 │      │
│     │                                                │      │
│     │  e. Append turn to transcript file              │      │
│     │                                                │      │
│     │  f. turn_counter += 1                           │      │
│     │                                                │      │
│     │  g. If turn_counter >= MAX_TURNS (6):           │      │
│     │     CHECKPOINT:                                 │      │
│     │     - Ask LLM to generate summary               │      │
│     │     - Print summary via orchestrator_out         │      │
│     │     - orchestrator_out.input("Continue? [y/n]") │      │
│     │     - If yes: reset turn_counter = 0             │      │
│     │     - If no: break loop                          │      │
│     │                                                │      │
│     └────────────────────────────────────────────────┘      │
│                                                            │
│  Continue loop                                             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
(loop exited)
Final transcript append (end_turn text)
Return
```

### Connection to other components

```
agent_loop.py
    │
    ├── imports from: execute_tool (05)
    │                 opaque_registry (03)
    │                 system_prompt (04)
    │                 terminal_router (01)
    │
    ├── calls: client.messages.create()  (anthropic SDK)
    │          execute_tool()            (dispatches to tools)
    │          orchestrator_out.print()  (terminal output)
    │          orchestrator_out.input()  (checkpoint prompt)
    │          dump_turn()              (transcript append)
    │
    └── tool_definitions come from: 04_system_prompt or 05_execute_tool
                                    (wherever they're registered)
```

## Input contract

### run_harness(user_query: str) → None

| Param | Type | Description |
|---|---|---|
| `user_query` | `str` | The user's natural language request from CLI. e.g. `"Chart Best Buy and Amcor gross margins FY2022-2023"` |

No `index`, no `config`. Tools load their own dependencies
internally (e.g. PMS1 loads index + config on first call, caches
for subsequent calls).

## Output contract

### Return value

None. The harness is a side-effect machine:
- Prints to terminal via terminal_router
- Saves files via tools (stencil2chart → SVGs)
- Writes transcript to disk

### Transcript file

Written per-turn (append after each iteration). Crash-resilient —
if harness dies at turn 4, turns 1-3 are already on disk.

Location: `PSOAS/temp/sessions/YYYYMMDDHHMMSS/transcript.md`

Timestamp is session start time, not per-turn.

Format (deterministic, no LLM call):

```markdown
# PSOAS Session 20260715154603

## User

Chart Best Buy and Amcor gross margins FY2022-2023

## Turn 1

**Tool call:** `run_pms1({"firm": "Best Buy", "query": "Best Buy margins FY2022-2023"})`
**Tool call:** `run_pms1({"firm": "Amcor", "query": "Amcor margins FY2022-2023"})`

**Result:** `PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods.`
**Result:** `PMS1 complete. Amcor stencil stored as $var_2. 5 rows, 2 periods.`

## Turn 2

**Tool call:** `run_pteca({"stencil": "$var_1", "query": "...", "firm": "Best Buy"})`
**Tool call:** `run_pteca({"stencil": "$var_2", "query": "...", "firm": "Amcor"})`

**Result:** `PTECA complete. 1 chart(s):\n$var_3: Best Buy chart 1 (2 series)`
**Result:** `PTECA complete. 1 chart(s):\n$var_4: Amcor chart 1 (2 series)`

## Turn 3

**Tool call:** `run_stencil2chart({"chart_input": "$var_3"})`
**Tool call:** `run_stencil2chart({"chart_input": "$var_4"})`

**Result:** `Saved: output/stencil2charted_20260715_1.svg`
**Result:** `Saved: output/stencil2charted_20260715_2.svg`

## Turn 4 (end)

Done. 2 charts saved to output/.
```

### Transcript dump function

```python
def dump_turn(path: Path, turn_num: int, response, tool_results: list | None):
    """Append one turn to the transcript file."""
    with open(path, "a") as f:
        f.write(f"\n## Turn {turn_num}\n\n")
        for block in response.content:
            if block.type == "text":
                f.write(f"{block.text}\n\n")
            elif block.type == "tool_use":
                args_str = json.dumps(block.input, ensure_ascii=False)
                f.write(f"**Tool call:** `{block.name}({args_str})`\n")
        if tool_results:
            f.write("\n")
            for tr in tool_results:
                content = tr["content"]
                f.write(f"**Result:** `{content}`\n")
        f.write("\n")
```

## Guard rails

### Max tool calls per turn: 5

```python
MAX_TOOL_CALLS_PER_TURN = 5
```

If the LLM returns more than 5 ToolUseBlocks in one response, ALL
are cancelled. Every tool_use_id gets an error tool_result. None
executed.

```python
if len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
    tool_results = [
        {
            "type": "tool_result",
            "tool_use_id": tb.id,
            "content": (
                f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
                f"exceeded ({len(tool_blocks)} requested). Reduce."
            ),
        }
        for tb in tool_blocks
    ]
    # skip execution, append errors, continue loop
```

### Max turns before checkpoint: 6

```python
MAX_TURNS_BEFORE_CHECKPOINT = 6
```

After 6 turns without user interaction at the harness level, the
loop pauses:

1. One more LLM call with a user message: `"You have run 6 turns.
   Summarize what you have done so far and what remains."`
2. LLM generates summary text (end_turn response).
3. Print summary via `orchestrator_out.print()`.
4. Prompt user: `orchestrator_out.input("Continue? [y/n]")`.
5. If `"y"`: reset `turn_counter = 0`, inject `{"role": "user",
   "content": "User confirmed: continue working."}` into messages
   so LLM has explicit signal to keep going, continue loop.
6. If `"n"`: break loop, dump final transcript.

```python
if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
    # Ask LLM for summary
    messages.append({
        "role": "user",
        "content": (
            "You have run 6 turns. Summarize what you have done "
            "so far and what remains."
        ),
    })
    summary_response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=messages,
        tools=[],
        max_tokens=1024,
    )
    messages.append({
        "role": "assistant",
        "content": summary_response.content,
    })

    summary_text = ""
    for block in summary_response.content:
        if hasattr(block, "text"):
            summary_text += block.text

    orchestrator_out.print(f"--- CHECKPOINT ---\n{summary_text}")
    answer = orchestrator_out.input("Continue? [y/n]")

    # Dump checkpoint to transcript (uses transcript_turn, not turn_counter)
    transcript_turn += 1
    with open(transcript, "a") as f:
        f.write(f"\n## Turn {transcript_turn} (CHECKPOINT)\n\n")
        f.write(f"**Summary:** {summary_text}\n\n")
        f.write(f"**Continue?** {answer}\n\n")

    if answer.strip().lower() in ("y", "yes"):
        turn_counter = 0
        # Inject confirmation so LLM knows to keep working
        messages.append({
            "role": "user",
            "content": "User confirmed: continue working.",
        })
    else:
        break
```

Note: the summary LLM call uses the same `messages` history so the
LLM knows everything it's done. The summary turn itself does NOT
count toward `turn_counter` (checkpoint logic), but DOES increment
`transcript_turn` (monotonic, never resets). The "User confirmed:
continue working." message gives the LLM an explicit signal to
resume (without it, the LLM may emit end_turn after its own summary).

## Error handling

If `execute_tool` raises an exception, catch it and return the error
as a tool_result string. The orchestrator LLM sees the error and
decides what to do (retry, skip, ask user, give up).

```python
try:
    result = execute_tool(tb.name, tb.input)
except Exception as exc:
    result = f"Error in {tb.name}: {type(exc).__name__}: {exc}"
```

The harness never crashes from a tool failure. The LLM absorbs it.

## Parallel tool dispatch

When the LLM returns multiple ToolUseBlocks (and count <= 5),
dispatch them in parallel via ThreadPoolExecutor.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _dispatch_parallel(tool_blocks: list) -> list[dict]:
    """Execute tool calls in parallel. Returns tool_results in order."""
    results = [None] * len(tool_blocks)

    with ThreadPoolExecutor(max_workers=len(tool_blocks)) as pool:
        future_to_idx = {
            pool.submit(_safe_execute, tb): i
            for i, tb in enumerate(tool_blocks)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            tb = tool_blocks[idx]
            results[idx] = {
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": future.result(),
            }

    return results


def _safe_execute(tb) -> str:
    """Execute one tool call, catch exceptions."""
    try:
        return execute_tool(tb.name, tb.input)
    except Exception as exc:
        return f"Error in {tb.name}: {type(exc).__name__}: {exc}"
```

Results are returned in the same order as the tool_blocks (not
completion order) to keep the tool_results aligned with tool_use_ids.

## Implementation sketch

```python
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from src.harness.terminal_router import register
from src.harness.execute_tool import execute_tool, set_session_dir
from src.harness.opaque_registry import registry
from src.config import Config

orchestrator_out = register("ORCHESTRATOR")

MAX_TOOL_CALLS_PER_TURN = 5
MAX_TURNS_BEFORE_CHECKPOINT = 6


def run_harness(user_query: str):
    client = anthropic.Anthropic()
    registry.reset()

    # Load orchestrator model from config
    config = Config.from_env()
    orch_profile = config.get_llm_profile("anthropic_orchestrator")
    model = orch_profile["model"]

    # Session dir + transcript
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    session_dir = Path(f"PSOAS/temp/sessions/{ts}")
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.md"
    transcript.write_text(f"# PSOAS Session {ts}\n\n## User\n\n{user_query}\n")

    set_session_dir(session_dir)

    messages = [{"role": "user", "content": user_query}]
    turn_counter = 0          # resets at checkpoint (drives checkpoint logic)
    transcript_turn = 0       # never resets (drives transcript numbering)

    while True:
        response = client.messages.create(
            model=model,
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )

        # ── end_turn ──────────────────────────────────────
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    orchestrator_out.print(block.text)
            messages.append({"role": "assistant", "content": response.content})
            transcript_turn += 1
            dump_turn(transcript, transcript_turn, response, None)
            break

        # ── tool_use ──────────────────────────────────────
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_blocks:
            # Handle truncated response (max_tokens)
            if response.stop_reason == "max_tokens":
                orchestrator_out.print("⚠ Response truncated (max_tokens). Retrying...")
                messages.append({"role": "assistant", "content": response.content})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was truncated (max_tokens). "
                        "Please continue or reduce tool calls."
                    ),
                })
                turn_counter += 1
                transcript_turn += 1
                dump_turn(transcript, transcript_turn, response, None)
                continue
            break

        # Print LLM text blocks (commentary alongside tool calls)
        for block in response.content:
            if hasattr(block, "text") and block.text.strip():
                orchestrator_out.print(block.text)

        # Guard rail: max tool calls per turn
        if len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tb.id,
                    "content": (
                        f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool calls "
                        f"per turn exceeded ({len(tool_blocks)} requested)."
                    ),
                }
                for tb in tool_blocks
            ]
        else:
            # Parallel dispatch
            tool_results = _dispatch_parallel(tool_blocks)

        # Accumulate messages
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        # Transcript
        turn_counter += 1
        transcript_turn += 1
        dump_turn(transcript, transcript_turn, response, tool_results)

        # Guard rail: checkpoint
        if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
            messages.append({
                "role": "user",
                "content": (
                    "You have run 6 turns. Summarize what you have done "
                    "so far and what remains."
                ),
            })
            summary = client.messages.create(
                model=model,
                system=system_prompt,
                messages=messages,
                tools=[],
                max_tokens=1024,
            )
            messages.append({"role": "assistant", "content": summary.content})

            text = ""
            for block in summary.content:
                if hasattr(block, "text"):
                    text += block.text

            orchestrator_out.print(f"--- CHECKPOINT ---\n{text}")
            answer = orchestrator_out.input("Continue? [y/n]")

            # Dump checkpoint to transcript
            transcript_turn += 1
            with open(transcript, "a") as f:
                f.write(f"\n## Turn {transcript_turn} (CHECKPOINT)\n\n")
                f.write(f"**Summary:** {text}\n\n")
                f.write(f"**Continue?** {answer}\n\n")

            if answer.strip().lower() in ("y", "yes"):
                turn_counter = 0
                # Inject confirmation so LLM knows to keep working
                messages.append({
                    "role": "user",
                    "content": "User confirmed: continue working.",
                })
            else:
                break
```

## Dependencies

| Component | Spec | What it provides |
|---|---|---|
| terminal_router | `01_terminal_router.md` | `register()` → ToolChannel for CLI I/O |
| execute_tool | `05_execute_tool.md` | Tool dispatch function |
| opaque_registry | `03_opaque_registry.md` | `_store`/`_resolve` (used inside execute_tool, not directly by agent_loop) |
| system_prompt | `04_system_prompt.md` | `system_prompt` string + `tools` list |
| config | `src/config.py` | `Config.from_env()` — LLM profile for orchestrator model |
| anthropic SDK | `requirements.txt` | `anthropic.Anthropic()` |

## File location

```
PSOAS/src/harness/agent_loop.py
```

## Ideal demo

```
$ python psoas.py "Chart Best Buy and Amcor gross margins FY2022-2023"

[ORCHESTRATOR] Planning...

                                        ← Turn 1: LLM calls run_pms1 x2
[PMS1-BBY] Loading index...
[PMS1-Amcor] Loading index...              (parallel, terminal router
[PMS1-BBY] Sekei planning... done.          handles interleaved output)
[PMS1-Amcor] Sekei planning... done.
[PMS1-BBY] PTO batch A_124... 42 chunks.
[PMS1-Amcor] PTO batch A_124... 0 chunks.

[PMS1-Amcor] Entity filter returned 0 chunks for FY2022.
[PMS1-Amcor] Relax fiscal year filter? [y/n]
> y

--- buffered ---
[PMS1-BBY] PTO judge... done.
[PMS1-BBY] Stencil computed.
---

[PMS1-Amcor] Relaxed. Found 31 chunks.
[PMS1-Amcor] PTO judge... done.
[PMS1-Amcor] Stencil computed.

                                        ← Turn 2: LLM calls run_pteca x2
[PTECA-BBY] Stencil has 5 rows.
[PTECA-BBY] Include Revenue and GP, or only margins?
> only margins

--- buffered ---
[PTECA-Amcor] Stencil has 5 rows.
---

[PTECA-BBY] Keeping: Gross Margin, Net Margin. 1 chart.

[PTECA-Amcor] Same structure. Only margins?
> yes

[PTECA-Amcor] Done.

                                        ← Turn 3: LLM calls run_s2c x2
[ORCHESTRATOR] Saved: output/stencil2charted_20260715_1.svg
[ORCHESTRATOR] Saved: output/stencil2charted_20260715_2.svg

                                        ← Turn 4: end_turn
[ORCHESTRATOR] Done. 2 charts saved.

Transcript: PSOAS/temp/sessions/20260715154603/transcript.md
```

## Resolved questions

- **Tools load own deps.** `run_harness` takes only `user_query`.
  PMS1 loads index + config on first call.
- **Orchestrator model.** Loaded from `Config.get_llm_profile("anthropic_orchestrator")`.
  Not hardcoded. Profile defined in `llm_profiles.yaml`.
- **Max tool calls per turn: 5.** All cancelled if exceeded.
- **Max turns before checkpoint: 6.** LLM generates summary, user
  confirms continue. `turn_counter` resets on continue (checkpoint
  logic). `transcript_turn` never resets (monotonic for transcript
  numbering). Checkpoint itself is dumped to transcript as
  `## Turn N (CHECKPOINT)`.
- **Transcript: per-turn append.** Crash-resilient. Deterministic
  markdown formatting, no LLM call. Uses `transcript_turn` (never
  resets) for section numbering — distinct from `turn_counter`
  (resets at checkpoint).
- **Text blocks in tool_use responses.** Printed via
  `orchestrator_out.print()` before tool dispatch. LLM commentary
  like "Running PMS1 for both." is visible to the user.
- **max_tokens stop_reason.** If LLM response is truncated, inject
  continuation prompt, increment counters, dump to transcript,
  `continue` loop. No silent exit.
- **Parallel dispatch.** ThreadPoolExecutor for multiple
  ToolUseBlocks. Terminal router handles I/O contention.
- **Error handling.** Exceptions caught, returned as error string
  in tool_result. Harness never crashes from tool failure.
