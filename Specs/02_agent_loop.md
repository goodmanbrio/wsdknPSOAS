# Agent Loop

The while loop that drives the orchestrator. Calls
`client.messages.create()`, checks `stop_reason`, dispatches tool
calls to `execute_tool`, accumulates messages, enforces guard rails.

## Architecture

```
run_harness(first_query=None)
    │
    ├── reset_counters() + registry.reset()
    ├── create session dir: temp/sessions/YYYYMMDDHHMMSS/  (relative to cwd)
    ├── create transcript file: transcript.md
    ├── messages = []   (persists across REPL follow-ups)
    ├── Welcome banner via rich.Panel:
    │     Panel("[bold cyan]PSOAS[/bold cyan] — Poony Sophomore Orchestrated Analyst Strapon",
    │           subtitle="[dim]type answers when prompted[/dim]",
    │           border_style="cyan")
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  OUTER REPL (spec 11)                                      │
│  Prompts for user input if first_query exhausted/None.      │
│  Each query: "## User" (first) / "## User (follow-up)"     │
│  appended to transcript. Appended to messages.              │
│  is_first_input flag tracks first vs follow-up.             │
│  turn_counter = 0 reset here (each new user query).         │
│  Empty input → print hint, continue (not exit).             │
│  Enters inner agent loop below.                             │
│  Exits on `exit`, EOF (_router._is_dead), or Ctrl-C        │
│  (unhandled — propagates as KeyboardInterrupt).            │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  INNER AGENT LOOP (one iteration = one turn)               │
│                                                            │
│  1. _router.start_spinner("ORCHESTRATOR")                  │
│     try:                                                   │
│       client.messages.create(                              │
│           system=SYSTEM_PROMPT,                            │
│           messages=messages,                               │
│           tools=TOOL_DEFINITIONS,                          │
│       )                                                    │
│     finally:                                               │
│       _router.stop_spinner()                               │
│     (try/finally guarantees spinner stops even on error)    │
│                                                            │
│  2. Check stop_reason:                                     │
│                                                            │
│     ┌─ "end_turn" ──────────────────────────────────┐      │
│     │  Print final text via orchestrator_out.print(  │
│     │    block.text, markdown=True)                  │      │
│     │  NOTE: no strip() guard — empty text blocks    │      │
│     │  are printed (unlike tool_use path).            │      │
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
│     │  d. tool_logs = harvest_logs()                  │      │
│     │     stored_vars = registry.harvest_recent()     │      │
│     │     (harvest BEFORE message accumulation)       │      │
│     │                                                │      │
│     │  e. Append assistant response + tool_results    │      │
│     │     to messages                                 │      │
│     │     _dump_turn(... tool_logs, stored_vars)      │      │
│     │                                                │      │
│     │  f. turn_counter += 1                           │      │
│     │                                                │      │
│     │  g. If turn_counter >= MAX_TURNS (6):           │      │
│     │     CHECKPOINT:                                 │      │
│     │     - Ask LLM to generate summary               │      │
│     │     - Print summary via console.print(Panel(...))│      │
│     │     - orchestrator_out.input("Continue? [y/n]") │      │
│     │     - If yes: reset turn_counter = 0              │      │
│     │     - If no: break → outer REPL re-prompts       │      │
│     │                                                │      │
│     └────────────────────────────────────────────────┘      │
│                                                            │
│  Continue loop                                             │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
(inner loop exited → back to outer REPL)
    │
    ▼
(outer REPL exited)
Session-end summary: Rich Panel showing transcript path + stored variables.
Return
```

### Connection to other components

```
agent_loop.py
    │
    ├── imports from: execute_tool (05)  → execute_tool, set_session_dir, reset_counters
    │                 opaque_registry (03) → registry
    │                 system_prompt (04)  → SYSTEM_PROMPT, TOOL_DEFINITIONS
    │                 terminal_router (01) → register, console, _router, harvest_logs
    │
    ├── calls: client.messages.create()  (anthropic SDK)
    │          execute_tool()            (dispatches to tools)
    │          orchestrator_out.print()  (terminal output, markdown=True)
    │          orchestrator_out.input()  (checkpoint + REPL prompt)
    │          _dump_turn()             (transcript append, private)
    │          _router.start_spinner()  (LLM call spinner, try/finally guarded)
    │          _router.stop_spinner()
    │          _router._is_dead         (EOF detection)
    │          harvest_logs()           (collect tool logs per channel)
    │          registry.harvest_recent() (collect recently stored vars)
    │          registry._registry       (session-end variable listing)
    │          reset_counters()         (reset execute_tool counters)
    │          set_session_dir()        (direct call, not via module)
    │
    └── SYSTEM_PROMPT, TOOL_DEFINITIONS come from: 04_system_prompt

Note: PUMBA (spec 12) is currently PMS1-internal — called by
orchestrator.py when PTO fails. Not yet exposed as an
orchestrator-level tool.
```

## Input contract

### run_harness(first_query: str | None = None) → None

| Param | Type | Description |
|---|---|---|
| `first_query` | `str \| None` | Optional initial query. If provided, used as first REPL input. If `None`, enters pure REPL mode (prompts immediately). e.g. `"Chart Best Buy and Amcor gross margins FY2022-2023"` |

No `index`, no `config`. Tools load their own dependencies
internally (e.g. PMS1 loads index + config on first call, caches
for subsequent calls).

The function wraps the inner agent loop in an outer REPL (see spec 11
for REPL details). `messages` persists across follow-up queries within
the same session.

## Output contract

### Return value

None. The harness is a side-effect machine:
- Prints to terminal via terminal_router
- Saves files via tools (stencil2chart → SVGs)
- Writes transcript to disk

### Transcript file

Written per-turn (append after each iteration). Crash-resilient —
if harness dies at turn 4, turns 1-3 are already on disk.

Location: `temp/sessions/YYYYMMDDHHMMSS/transcript.md` (relative to cwd)

Timestamp is session start time, not per-turn.

Format (deterministic, no LLM call):

```markdown
# PSOAS Session 20260715154603

## User

Chart Best Buy and Amcor gross margins FY2022-2023

(Follow-up queries get "## User (follow-up)" header.)

## Turn 1

**Tool call:** `run_pms1({"firm": "Best Buy", "query": "Best Buy margins FY2022-2023"})`
**Tool call:** `run_pms1({"firm": "Amcor", "query": "Amcor margins FY2022-2023"})`

**Result:** `PMS1 complete. Best Buy stencil stored as $var_1. 5 rows, 2 periods.`
**Result:** `PMS1 complete. Amcor stencil stored as $var_2. 5 rows, 2 periods.`

## Turn 2

**Tool call:** `run_pteca({"stencils": ["$var_1"], "query": "..."})`
**Tool call:** `run_pteca({"stencils": ["$var_2"], "query": "..."})`

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
def _dump_turn(
    path: Path,
    turn_num: int,
    response,
    tool_results: list | None,
    tool_logs: dict[str, list[str]] | None = None,
    stored_vars: list | None = None,
):
    """Append one turn to the transcript file (private)."""
    with open(path, "a") as f:
        f.write(f"\n## Turn {turn_num}\n\n")
        # Text blocks first
        for block in response.content:
            if block.type == "text":
                f.write(f"{block.text}\n\n")
            elif block.type == "tool_use":
                args_str = json.dumps(block.input, ensure_ascii=False)
                f.write(f"**Tool call:** `{block.name}({args_str})`\n")
        # Stream A: tool operational logs (fenced code blocks with #### subheadings)
        if tool_logs:
            f.write("\n### Tool Logs\n")
            for label, lines in tool_logs.items():
                f.write(f"\n#### {label}\n```\n")
                for line in lines:
                    f.write(f"{line}\n")
                f.write("```\n")
        # Stream C: tool result strings (sent to LLM)
        if tool_results:
            f.write("\n")
            for tr in tool_results:
                content = tr["content"]
                f.write(f"**Result:** `{content}`\n")
        # Stream B: stored variable data (per-variable fenced blocks)
        if stored_vars:
            f.write("\n### Stored Variables\n\n")
            for handle, desc, data in stored_vars:
                f.write(f"**{handle}** ({desc}):\n```json\n")
                f.write(json.dumps(
                    data, indent=2, ensure_ascii=False, default=str
                ))
                f.write("\n```\n\n")
        f.write("\n")
```

Called after tool dispatch:

```python
tool_logs = harvest_logs()
stored_vars = registry.harvest_recent()
_dump_turn(transcript, transcript_turn, response, tool_results,
           tool_logs=tool_logs, stored_vars=stored_vars)
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
                f"exceeded ({len(tool_blocks)} requested)."
            ),
        }
        for tb in tool_blocks
    ]
    console.print(
        f"[bold red]\\[ERROR][/bold red] "
        f"Max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
        f"exceeded ({len(tool_blocks)} requested)."
    )
    # skip execution, append errors, continue loop
```

In addition to the error tool_results sent back to the LLM, a red
`[ERROR]` message is printed to the terminal via `console.print` so
the user sees the guard rail being triggered.

### Max turns before checkpoint: 6

```python
MAX_TURNS_BEFORE_CHECKPOINT = 6
```

After 6 turns without user interaction at the harness level, the
loop pauses:

1. One more LLM call with a user message: `"You have run 6 turns.
   Summarize what you have done so far and what remains."`
2. LLM generates summary text (end_turn response).
3. Print summary via `console.print(Panel(...))` (Rich formatted).
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
    _router.start_spinner("ORCHESTRATOR")
    try:
        summary_response = client.messages.create(
            model=model,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=[],
            max_tokens=1024,
        )
    finally:
        _router.stop_spinner()
    messages.append({
        "role": "assistant",
        "content": summary_response.content,
    })

    summary_text = ""
    for block in summary_response.content:
        if hasattr(block, "text"):
            summary_text += block.text

    console.print(Panel(
        summary_text,
        title="[bold]CHECKPOINT — 6 turns reached[/bold]",
        border_style="yellow",
    ))
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

The checkpoint summary LLM call also uses a `try/finally` guard
around `_router.start_spinner()`/`_router.stop_spinner()`, same as
the main LLM call.

### Max-tokens truncation recovery

When `stop_reason == "max_tokens"` and no `tool_blocks` are present
in the response, the harness does NOT silently exit. Instead:

1. Print truncation warning to the user.
2. Append the truncated assistant response to `messages`.
3. Inject a user message asking the LLM to continue or reduce.
4. Increment `turn_counter` and `transcript_turn`.
5. Dump the truncated turn to transcript.
6. `continue` the inner loop (retry).

This prevents silent data loss from long LLM outputs.

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
from rich.panel import Panel

from src.harness.terminal_router import register, console, _router, harvest_logs
from src.harness.execute_tool import execute_tool, set_session_dir, reset_counters
from src.harness.opaque_registry import registry
from src.harness.system_prompt import SYSTEM_PROMPT, TOOL_DEFINITIONS
from src.config import Config

orchestrator_out = register("ORCHESTRATOR")

MAX_TOOL_CALLS_PER_TURN = 5
MAX_TURNS_BEFORE_CHECKPOINT = 6


def run_harness(first_query: str | None = None) -> None:
    client = anthropic.Anthropic()
    registry.reset()
    reset_counters()

    # Load orchestrator model from config
    config = Config.from_env()
    orch_profile = config.get_llm_profile("anthropic_orchestrator")
    model = orch_profile["model"]

    # Session dir + transcript
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    session_dir = Path(f"temp/sessions/{ts}")
    session_dir.mkdir(parents=True, exist_ok=True)
    transcript = session_dir / "transcript.md"
    transcript.write_text(f"# PSOAS Session {ts}\n")

    set_session_dir(session_dir)

    # Welcome banner
    console.print(Panel(
        "[bold cyan]PSOAS[/bold cyan] — "
        "Poony Sophomore Orchestrated Analyst Strapon",
        subtitle="[dim]type answers when prompted[/dim]",
        border_style="cyan",
    ))

    messages = []             # persists across REPL follow-ups
    transcript_turn = 0       # never resets (drives transcript numbering)
    is_first_input = True

    # ── Outer REPL (spec 11) ─────────────────────────────
    while True:
        # Prompt for user input
        if first_query is not None:
            user_input = first_query
            first_query = None  # consumed
        else:
            user_input = orchestrator_out.input("")

            if not user_input.strip():
                if _router._is_dead:
                    break  # EOF (Ctrl+D)
                orchestrator_out.print(
                    "Type a query, or 'exit' to quit."
                )
                continue

            if user_input.strip().lower() == "exit":
                break

        # Append user message + transcript
        messages.append({"role": "user", "content": user_input})

        with open(transcript, "a") as f:
            if is_first_input:
                f.write(f"\n## User\n\n{user_input}\n")
            else:
                f.write(f"\n## User (follow-up)\n\n{user_input}\n")

        is_first_input = False
        turn_counter = 0  # resets on each new user query

        # ── Inner agent loop ─────────────────────────────
        while True:
            # LLM call with try/finally spinner guard
            _router.start_spinner("ORCHESTRATOR")
            try:
                response = client.messages.create(
                    model=model,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=4096,
                )
            finally:
                _router.stop_spinner()

            # ── end_turn ─────────────────────────────────
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        orchestrator_out.print(block.text, markdown=True)
                messages.append(
                    {"role": "assistant", "content": response.content}
                )
                transcript_turn += 1
                _dump_turn(transcript, transcript_turn, response, None)
                break  # → outer REPL re-prompts

            # ── tool_use ─────────────────────────────────
            tool_blocks = [
                b for b in response.content if b.type == "tool_use"
            ]

            if not tool_blocks:
                # max_tokens truncation recovery
                if response.stop_reason == "max_tokens":
                    orchestrator_out.print(
                        "Response truncated (max_tokens). Retrying..."
                    )
                    messages.append(
                        {"role": "assistant", "content": response.content}
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "Your previous response was truncated "
                            "(max_tokens). Please continue or reduce "
                            "tool calls."
                        ),
                    })
                    turn_counter += 1
                    transcript_turn += 1
                    _dump_turn(
                        transcript, transcript_turn, response, None
                    )
                    continue
                break

            # Print LLM text blocks (only if non-empty after strip)
            for block in response.content:
                if hasattr(block, "text") and block.text.strip():
                    orchestrator_out.print(block.text, markdown=True)

            # Guard rail: max tool calls per turn
            if len(tool_blocks) > MAX_TOOL_CALLS_PER_TURN:
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": (
                            f"Error: max {MAX_TOOL_CALLS_PER_TURN} tool "
                            f"calls per turn exceeded "
                            f"({len(tool_blocks)} requested)."
                        ),
                    }
                    for tb in tool_blocks
                ]
                console.print(
                    f"[bold red]\\[ERROR][/bold red] "
                    f"Max {MAX_TOOL_CALLS_PER_TURN} tool calls per turn "
                    f"exceeded ({len(tool_blocks)} requested)."
                )
            else:
                tool_results = _dispatch_parallel(tool_blocks)

            # Harvest BEFORE message accumulation
            tool_logs = harvest_logs()
            stored_vars = registry.harvest_recent()

            # Accumulate messages
            messages.append(
                {"role": "assistant", "content": response.content}
            )
            messages.append({"role": "user", "content": tool_results})

            # Transcript
            turn_counter += 1
            transcript_turn += 1
            _dump_turn(
                transcript, transcript_turn, response, tool_results,
                tool_logs=tool_logs, stored_vars=stored_vars,
            )

            # Guard rail: checkpoint
            if turn_counter >= MAX_TURNS_BEFORE_CHECKPOINT:
                messages.append({
                    "role": "user",
                    "content": (
                        "You have run 6 turns. Summarize what you have "
                        "done so far and what remains."
                    ),
                })

                _router.start_spinner("ORCHESTRATOR")
                try:
                    summary = client.messages.create(
                        model=model,
                        system=SYSTEM_PROMPT,
                        messages=messages,
                        tools=[],
                        max_tokens=1024,
                    )
                finally:
                    _router.stop_spinner()

                messages.append(
                    {"role": "assistant", "content": summary.content}
                )

                text = ""
                for block in summary.content:
                    if hasattr(block, "text"):
                        text += block.text

                console.print(Panel(
                    text,
                    title="[bold]CHECKPOINT — 6 turns reached[/bold]",
                    border_style="yellow",
                ))
                answer = orchestrator_out.input("Continue? [y/n]")

                transcript_turn += 1
                with open(transcript, "a") as f:
                    f.write(
                        f"\n## Turn {transcript_turn} (CHECKPOINT)\n\n"
                    )
                    f.write(f"**Summary:** {text}\n\n")
                    f.write(f"**Continue?** {answer}\n\n")

                if answer.strip().lower() in ("y", "yes"):
                    turn_counter = 0
                    messages.append({
                        "role": "user",
                        "content": "User confirmed: continue working.",
                    })
                else:
                    break  # → outer REPL re-prompts

    # ── Session-end summary (after outer REPL exits) ──────
    summary_lines = []
    for handle, entry in registry._registry.items():
        summary_lines.append(f"  {handle}: {entry['description']}")

    console.print(Panel(
        "\n".join([
            "[bold]Session complete.[/bold]",
            f"Transcript: {transcript}",
            "",
            "Variables stored:",
            *(summary_lines if summary_lines else ["  (none)"]),
        ]),
        border_style="green",
    ))
```

## Dependencies

| Component | Spec | What it provides |
|---|---|---|
| terminal_router | `01_terminal_router.md` | `register()`, `console`, `_router`, `harvest_logs()` |
| execute_tool | `05_execute_tool.md` | `execute_tool()`, `set_session_dir()`, `reset_counters()` |
| opaque_registry | `03_opaque_registry.md` | `registry` — `.reset()`, `.harvest_recent()`, `._registry` (dict) |
| system_prompt | `04_system_prompt.md` | `SYSTEM_PROMPT` string + `TOOL_DEFINITIONS` list |
| config | `src/config.py` | `Config.from_env()` — LLM profile for orchestrator model |
| anthropic SDK | `requirements.txt` | `anthropic.Anthropic()` |
| rich | `requirements.txt` | `Panel` for welcome banner, checkpoint, session-end |

## File location

```
src/harness/agent_loop.py
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

Transcript: temp/sessions/20260715154603/transcript.md
```

## Resolved questions

- **Tools load own deps.** `run_harness` takes only `first_query` (optional).
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
  `orchestrator_out.print(block.text, markdown=True)` before tool
  dispatch, but only if `block.text.strip()` is non-empty. Empty or
  whitespace-only text blocks are silently skipped. LLM commentary
  like "Running PMS1 for both." is visible to the user.
  **Contrast: end_turn text blocks do NOT have a strip() guard** —
  all text blocks are printed unconditionally. This asymmetry exists
  in the implementation.
- **max_tokens stop_reason.** If LLM response is truncated, inject
  continuation prompt, increment counters, dump to transcript,
  `continue` loop. No silent exit.
- **Parallel dispatch.** ThreadPoolExecutor for multiple
  ToolUseBlocks. Terminal router handles I/O contention.
- **Error handling.** Exceptions caught, returned as error string
  in tool_result. Harness never crashes from tool failure.
