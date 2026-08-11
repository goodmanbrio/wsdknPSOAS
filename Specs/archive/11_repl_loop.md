# REPL Loop

Outer conversational loop that wraps the agent loop (02). PSOAS boots
into an interactive prompt, accepts queries, runs the inner agent loop
to completion (end_turn), then prompts for follow-up. Session state
(messages, registry, transcript) persists across follow-ups.

## Architecture

```
python psoas.py
    │
    ├── sys.path setup (module-level in src/psoas.py, not in main())
    ├── run_harness(first_query=query)
    │     ├── client = anthropic.Anthropic()
    │     ├── config, model loaded once
    ├── session_dir + transcript created once
    ├── registry.reset() once
    ├── banner printed once
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  OUTER REPL                                                      │
│                                                                  │
│  1. prompt_user()                                                │
│     │                                                            │
│     ├── orchestrator_out.input("") → user types query            │
│     │                                                            │
│     ├── empty string → orchestrator_out.print("...")             │
│     │                  continue (re-prompt)                      │
│     │                                                            │
│     ├── "exit" (case-insensitive, stripped) → break outer loop    │
│     │                                                            │
│     ├── EOF (Ctrl+D) → break outer loop                          │
│     │                                                            │
│     └── anything else → user_input = the query                   │
│                                                                  │
│  2. Append {"role": "user", "content": user_input} to messages   │
│     Append to transcript: ## User (follow-up)                    │
│     turn_counter = 0                                             │
│                                                                  │
│  3. Run inner agent loop (spec 02 while loop)                    │
│     │                                                            │
│     └── Breaks on end_turn → back here                           │
│                                                                  │
│  4. Go to step 1                                                 │
│                                                                  │
╰──────────────────────────────────────────────────────────────────╘
    │
    ▼
Session end summary panel (spec 10)
```

### Connection to spec 02 (agent_loop)

```
CURRENT (spec 02 + spec 11 merged):

    run_harness(first_query: str | None = None)
        ├── setup (client, session, transcript, banner)
        ├── messages = []                ← empty, REPL fills it
        │
        ├── OUTER REPL (NEW)
        │     ├── prompt user → user_input
        │     ├── messages.append(user_input)
        │     ├── turn_counter = 0
        │     │
        │     ├── WHILE LOOP (inner, unchanged from spec 02)
        │     │     └── end_turn → break inner loop
        │     │
        │     └── continue outer loop (re-prompt)
        │
        ├── session summary
        └── done
```

### What changes in agent_loop.py

```
run_harness()
    │
    ├── MOVED OUT of inner loop into outer REPL setup:
    │     messages list init
    │     first user message append
    │
    ├── MOVED OUT of run_harness entirely into psoas.py:
    │     (nothing — run_harness still owns everything)
    │
    ├── NEW: outer while True around existing inner loop
    │     user prompt at top
    │     turn_counter = 0 at top
    │     inner loop unchanged
    │     after inner loop breaks → continue outer
    │
    └── end_turn handling CHANGED:
          OLD: break (exits run_harness entirely)
          NEW: break (exits inner loop, outer loop re-prompts)
```

## Input contract

### run_harness() → None

No arguments. User input comes from the interactive prompt inside
the REPL. CLI arg handling (if any) is psoas.py's job — it can
pass a first query via a new optional parameter.

| Param | Type | Description |
|---|---|---|
| `first_query` | `str \| None` | Optional. If provided, used as the first user input (skips first prompt). For scripting/testing. Default `None`. |

```python
def run_harness(first_query: str | None = None) -> None:
```

If `first_query` is provided, it's used as the first iteration's
input — the REPL skips the prompt for that iteration only, then
prompts normally for follow-ups.

### psoas.py changes

```python
def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    run_harness(first_query=query)
```

No more `sys.exit(1)` on missing arg. No arg = boot into REPL
with prompt. Arg given = use as first query, then REPL for follow-ups.

## Output contract

Same as spec 02. Side-effect machine:
- Terminal I/O via terminal_router
- Files via tools
- Transcript on disk

### Transcript format for follow-ups

Follow-up user inputs are appended to the same transcript file:

```markdown
## User (follow-up)

Now chart Amcor too.

## Turn 5

**Tool call:** `run_pms1({"firm": "Amcor", ...})`

**Result:** `PMS1 complete. Amcor stencil stored as $var_3.`
```

First user input keeps existing format (`## User`).
Subsequent inputs use `## User (follow-up)`.

## State persistence across follow-ups

| State | Persists? | Resets? | Notes |
|---|---|---|---|
| `messages` | Yes | No | Full conversation history. LLM retains context. |
| `registry` | Yes | No | Handles `$var_1`, `$var_2` still resolvable. LLM can reference prior stencils. |
| `client` | Yes | No | Same Anthropic client instance. |
| `model` | Yes | No | Same model string. |
| `session_dir` | Yes | No | Same session directory. One session = one psoas.py invocation. |
| `transcript` | Yes | No | Same file, appended. |
| `transcript_turn` | Yes | No | Monotonic. Never resets. Drives `## Turn N`. |
| `turn_counter` | — | Yes → 0 | Reset at top of each outer REPL iteration AND at checkpoint confirm. Fresh user input = fresh runway for checkpoint guard. |
| `is_first_input` | Yes | No (False after first input) | Controls `## User` vs `## User (follow-up)` in transcript. |

## Exit conditions

| Trigger | Behavior |
|---|---|
| User types `exit` (case-insensitive, stripped) | Break outer loop → session summary → return |
| Ctrl+D (EOF on stdin) | `_router._is_dead` becomes True, `input()` returns `""` → detected as `not user_input.strip() and _router._is_dead` → break outer loop → session summary → return |
| Empty input (just Enter) | Print hint, continue outer loop (re-prompt). NOT an exit. |

```python
# Inside outer REPL:
first_query = first_query          # consumed on first iteration, then None

while True:
    if first_query is not None:
        user_input = first_query
        first_query = None         # consumed — future iterations prompt
    else:
        user_input = orchestrator_out.input("")

        # D11: ToolChannel.input() never raises EOFError — returns ""
        # when _is_dead. Detect EOF via empty input + dead router.
        if not user_input.strip():
            if _router._is_dead:
                break        # EOF — exit outer REPL
            orchestrator_out.print("Type a query, or 'exit' to quit.")
            continue

        if user_input.strip().lower() == "exit":
            break

    # ... append to messages, run inner loop ...
```

## Guard rails

### Inherited from spec 02 (unchanged)

- Max 5 tool calls per turn (all cancelled if exceeded)
- Max 6 turns before checkpoint (turn_counter resets on follow-up
  AND on checkpoint confirm ('y'))

### New: no accidental exit

- Empty input → re-prompt with hint. NOT an exit.
- Only `exit` (literal, case-insensitive) terminates.
- Ctrl+D terminates (deliberate action, not a fat-finger).

## Dependencies

| Component | Spec | What it provides |
|---|---|---|
| agent_loop inner logic | `02_agent_loop.md` | The while loop, stop_reason dispatch, tool dispatch, checkpoint |
| terminal_router | `01_terminal_router.md` | `orchestrator_out.input("")` for REPL prompt |
| All other deps | `02_agent_loop.md` | Same as before — this spec only wraps the existing inner loop |

## File locations

```
src/harness/agent_loop.py   (outer REPL wraps inner loop)
src/psoas.py                (thin entry point, calls run_harness)
```

No new files.

## Ideal demo

```
╭──────────────────────────────────────────────────────────────╮
│ PSOAS — Poony Sophomore Orchestrated Analyst Strapon         │
│                                      type answers when prompted │
╰──────────────────────────────────────────────────────────────╯

> Chart Best Buy gross and net margins FY2022-2023

⠋ ORCHESTRATOR thinking...

[ORCHESTRATOR] PSOAS reporting for duty sir! Bloody hell, one firm,
margins only. Running PMS1.

[PMS1-Best Buy] Loading index...
[PMS1-Best Buy] Sekei planning... done. 5 cells, 2 batches.
[PMS1-Best Buy] Running 2 batches...
[PMS1-Best Buy] Stencil computed. 5 cells filled.

⠋ ORCHESTRATOR thinking...

[PTECA-Best Buy] Stencil has 5 rows: Revenue, Gross Profit,
                 Gross Margin, Net Income, Net Profit Margin.
[PTECA-Best Buy] Done. 1 chart(s).

⠋ ORCHESTRATOR thinking...

[S2C-1] Saved: temp/sessions/20260716.../output/stencil2charted_..._1.svg

[ORCHESTRATOR] Done. 1 chart saved. Goshadig.

> Now chart Amcor too, same metrics

⠋ ORCHESTRATOR thinking...

[ORCHESTRATOR] Right. Running PMS1 for Amcor.

[PMS1-Amcor] Loading index...
[PMS1-Amcor] Sekei planning... done. 5 cells, 2 batches.
[PMS1-Amcor] Running 2 batches...
[PMS1-Amcor] Stencil computed. 5 cells filled.

⠋ ORCHESTRATOR thinking...

[PTECA-Amcor] Stencil has 5 rows.
[PTECA-Amcor] Done. 1 chart(s).

[S2C-2] Saved: temp/sessions/20260716.../output/stencil2charted_..._2.svg

[ORCHESTRATOR] Amcor done. 1 chart saved.

>
[ORCHESTRATOR] Type a query, or 'exit' to quit.

> exit

╭──────────────────────────────────────────────────────────────╮
│ Session complete.                                            │
│ Transcript: temp/sessions/20260716154603/transcript.md       │
│                                                              │
│ Variables stored:                                            │
│   $var_1: Best Buy stencil                                   │
│   $var_2: Best Buy chart 1 (2 series)                        │
│   $var_3: Amcor stencil                                      │
│   $var_4: Amcor chart 1 (2 series)                           │
╰──────────────────────────────────────────────────────────────╯
```

### With CLI arg (first query pre-loaded)

```
$ python src/psoas.py "Chart Best Buy gross margins FY2022-2023"

╭──────────────────────────────────────────────────────────────╮
│ PSOAS — Poony Sophomore Orchestrated Analyst Strapon         │
│                                      type answers when prompted │
╰──────────────────────────────────────────────────────────────╯

⠋ ORCHESTRATOR thinking...

[ORCHESTRATOR] PSOAS reporting for duty sir! ...
... (same as above, first query runs immediately)

[ORCHESTRATOR] Done. 1 chart saved.

> _                  ← now in REPL, waiting for follow-up
```

### Empty input demo

```
>
[ORCHESTRATOR] Type a query, or 'exit' to quit.

>
[ORCHESTRATOR] Type a query, or 'exit' to quit.

> Actually, chart Boeing too

⠋ ORCHESTRATOR thinking...
...
```

## Resolved questions

- **Registry across follow-ups.** Preserved. User can reference prior
  handles ("use the same chart format"). LLM sees full message history
  including prior tool results mentioning `$var_N`.
- **turn_counter reset.** Reset to 0 on each follow-up AND on
  checkpoint confirm ('y'). Fresh user input = not a rogue LLM loop.
  Checkpoint guard only triggers on consecutive LLM turns without
  user harness-level interaction.
- **max_tokens recovery.** When stop_reason == "max_tokens" and no
  tool_blocks present, inner loop appends a continuation prompt as
  user message and retries. Counts toward turn_counter. Does not
  surface to outer REPL.
- **transcript_turn reset.** NEVER resets. Monotonic within session.
  Turn 1, 2, 3, 4, 5, 6... regardless of follow-ups.
- **Exit behavior.** Only `exit` (literal, case-insensitive) or
  Ctrl+D. Empty input re-prompts. PM fat-fingers protected.
- **CLI arg.** Optional. If given, used as first query (REPL skips
  first prompt). Then REPL for follow-ups. No arg = pure REPL from
  start.
- **No new files.** Changes are in agent_loop.py (outer REPL wrap)
  and psoas.py (optional arg).
