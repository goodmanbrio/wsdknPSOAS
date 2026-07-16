# Terminal Router

Singleton that owns all terminal I/O. No tool or harness component
calls `print()` or `input()` directly — everything goes through the
router.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  TERMINAL (user's screen)                                    │
│                                                              │
│  stdout ← only _ui_loop() writes here                        │
│  stdin  ← only _ui_loop() reads here                         │
└──────────────────────────────┬───────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  TerminalRouter     │
                    │  (singleton)        │
                    │                     │
                    │  _input_queue ──────┤ Queue[(label, question, AnswerSlot)]
                    │                     │   tools post here when they need input
                    │                     │
                    │  _buffer ───────────┤ list[(label, msg)]
                    │                     │   prints held while input is active
                    │                     │
                    │  _active_input ─────┤ bool
                    │                     │   True = someone owns stdin right now
                    │                     │
                    │  .print(label, msg) │
                    │  .input(label, q)   │
                    │  ._ui_loop()        │
                    └──┬──────┬──────┬────┘
                       │      │      │
              register │      │      │ register
               "PMS1"  │      │      │ "PTECA"
                       ▼      │      ▼
                 ToolChannel   │  ToolChannel
                 .print(msg)   │  .print(msg)
                 .input(q)     │  .input(q)
                       │       │       │
                       ▼       │       ▼
                 src/tools/    │  src/tools/
                 pms1 code     │  pteca code
                               │
                      register "ORCHESTRATOR"
                               ▼
                         ToolChannel
                         .print(msg)
                         .input(q)
                               │
                               ▼
                         src/harness/
                         agent loop
```

## Input contract

### register(label: str) → ToolChannel

The only public API. **Idempotent:** if a channel with the same label
already exists, returns the existing ToolChannel. Otherwise creates
a new one and caches it.

| Param | Type | Description |
|---|---|---|
| `label` | `str` | Tool identity shown in CLI output. e.g. `"PMS1"`, `"PTECA"`, `"ORCHESTRATOR"` |

Returns a `ToolChannel` bound to that label.

```python
_channels: dict[str, ToolChannel] = {}
_channels_lock = threading.Lock()

def register(label: str) -> ToolChannel:
    with _channels_lock:
        if label not in _channels:
            _channels[label] = ToolChannel(label, _router)
        return _channels[label]
```

Note: lock needed because `execute_tool` calls `register()` from
tool threads (e.g. `register(f"PMS1-{firm}")` per-invocation).
Without it, two threads could race the check-then-insert for the
same label.

```python
from src.harness.terminal_router import register

pto_out = register("PMS1")
```

### ToolChannel.print(msg: str) → None

Post a message to the terminal. Auto-prefixed with `[label]`.

| Param | Type | Description |
|---|---|---|
| `msg` | `str` | Message to display. No label needed — ToolChannel adds it. |

```python
pto_out.print("Loading index...")
# terminal shows: [PMS1] Loading index...
```

### ToolChannel.input(question: str) → str

Ask the user a question. Blocks the calling thread until the user
answers. Auto-prefixed with `[label]`.

| Param | Type | Description |
|---|---|---|
| `question` | `str` | Question to display. |

Returns the user's answer as a string.

```python
answer = pto_out.input("Relax FY filter? [y/n]")
# terminal shows:
#   [PMS1] Relax FY filter? [y/n]
#   > _
# user types "y", answer = "y"
```

## Output contract

### ToolChannel.print

No return value. Side effect: message appears on terminal (or is
buffered if an input prompt is active).

### ToolChannel.input

Returns `str` — the user's raw input, stripped of trailing newline.

## Internal components

### AnswerSlot

Thread-safe container for passing an answer from the UI thread back
to the requesting tool thread.

```python
@dataclass
class AnswerSlot:
    _event: threading.Event     # set() when answer is ready
    value: str = ""             # filled by UI thread before set()

    def wait(self):
        self._event.wait()      # blocks calling thread

    def set(self):
        self._event.set()       # unblocks calling thread
```

### TerminalRouter

Singleton. Created once at module import.

```python
class TerminalRouter:
    _input_queue: Queue         # (label, question, AnswerSlot)
    _buffer: list               # (label, msg) — held during active input
    _active_input: bool         # True while a question is displayed
    _lock: threading.Lock       # protects _buffer and _active_input
    _is_dead: bool              # True after EOF — all future input() returns ""
```

#### .print(label, msg)

```
with _lock:
    if _active_input:
        _buffer.append((label, msg))
    else:
        print(f"[{label}] {msg}") to real stdout
```

#### .input(label, question) → str

```
if _is_dead:
    return ""               ← EOF already received, no-op
create AnswerSlot
post (label, question, answer_slot) to _input_queue
answer_slot.wait()          ← blocks calling thread
return answer_slot.value
```

#### ._ui_loop()

Runs on the main thread (or a dedicated UI thread). This is the
ONLY place in the entire codebase that calls real `print()` and
real `input()`.

```
while True:
    label, question, answer_slot = _input_queue.get()

    # set active
    _active_input = True

    # show pending count
    pending = _input_queue.qsize() + 1
    if pending > 1:
        print(f"[{pending} questions pending — answering 1 of {pending}]")

    # display question
    print(f"\n[{label}] {question}")
    try:
        answer = input("> ")
    except EOFError:
        # stdin closed (Ctrl+D) — mark dead, unblock requester
        # with empty string, drain remaining queue, exit loop
        _is_dead = True
        answer = ""
        answer_slot.value = answer
        answer_slot.set()
        _active_input = False
        # drain queue so no thread deadlocks on wait()
        while not _input_queue.empty():
            _, _, slot = _input_queue.get_nowait()
            slot.value = ""
            slot.set()
        break

    # unblock requester
    answer_slot.value = answer
    answer_slot.set()

    # flush buffer — snapshot under lock, print outside lock
    with _lock:
        _active_input = False
        to_flush = list(_buffer)
        _buffer.clear()
    for buf_label, buf_msg in to_flush:
        print(f"[{buf_label}] {buf_msg}")
```

## Behavior: normal print (no input active)

```
pto_out.print("Loading index...")
    │
    ▼
TerminalRouter.print("PMS1", "Loading index...")
    │
    ├── _active_input? NO
    │
    ▼
real print("[PMS1] Loading index...")    ← immediate
```

## Behavior: print during active input (buffered)

```
Thread 1 (BBY):   pto_out.input("Relax FY?")     ← owns stdin
Thread 2 (Amcor): pto_out.print("42 chunks.")     ← while user is typing

Thread 2's print:
    TerminalRouter.print("PMS1-Amcor", "42 chunks.")
        │
        ├── _active_input? YES (Thread 1 owns stdin)
        │
        ▼
    _buffer.append(("PMS1-Amcor", "42 chunks."))   ← held, not printed

User answers Thread 1's question:
    > y
        │
        ├── answer_slot.set()          ← unblocks Thread 1
        ├── _active_input = False
        ├── flush _buffer:
        │     print("[PMS1-Amcor] 42 chunks.")     ← now it appears
        ▼
```

## Behavior: multiple questions pending

```
Thread 1 posts:  pto_out.input("Relax FY for BBY?")
Thread 2 posts:  pto_out.input("Relax FY for Amcor?")

_input_queue = [
    ("PMS1-BBY",   "Relax FY for BBY?",   slot_1),
    ("PMS1-Amcor", "Relax FY for Amcor?", slot_2),
]

_ui_loop processes slot_1 first:

    [2 questions pending — answering 1 of 2]

    [PMS1-BBY] Relax FY for BBY?
    > y

    --- buffer flush ---
    [PMS1-Amcor] PTO batch B_124... 0 chunks.
    ---

_ui_loop processes slot_2:

    [1 question pending — answering 1 of 1]

    [PMS1-Amcor] Relax FY for Amcor?
    > n

Thread 1 resumes with "y".
Thread 2 resumes with "n".
```

## Dependencies

None. Standard library only:

- `threading` (Lock, Event)
- `queue` (Queue)
- `dataclasses` (dataclass)

## File location

```
PSOAS/src/harness/terminal_router.py
```

## Ideal demo

### Sequential (MVP)

```
$ python psoas.py "Chart Best Buy gross margins FY2022-2023"

[ORCHESTRATOR] Running PMS1 for Best Buy...
[PMS1] Loading index...
[PMS1] Sekei planning... done. 5 cells, 2 batches.
[PMS1] PTO batch A_124... 42 chunks filtered.
[PMS1] PTO judge... tokens in=3200 out=450.
[PMS1] PTO batch B_124... 38 chunks filtered.
[PMS1] PTO judge... tokens in=3100 out=420.
[PMS1] Stencil computed. 5 cells filled.
[ORCHESTRATOR] Running PTECA...

[PTECA] Stencil has 5 rows: Revenue, GP, Gross Margin, NI, Net Margin.
[PTECA] Include Revenue and GP as separate chart, or only margins?
> only margins

[PTECA] Keeping: Gross Margin, Net Profit Margin. 1 chart.
[ORCHESTRATOR] Rendering chart...
[ORCHESTRATOR] Saved: output/stencil2charted_20260714_1.svg
[ORCHESTRATOR] Done.
```

### Parallel with buffering

```
$ python psoas.py "Chart Best Buy and Amcor gross margins FY2022-2023"

[ORCHESTRATOR] Running PMS1 for Best Buy and Amcor in parallel...
[PMS1-BBY] Loading index...
[PMS1-Amcor] Loading index...
[PMS1-BBY] Sekei planning... done.
[PMS1-Amcor] Sekei planning... done.
[PMS1-BBY] PTO batch A_124... 42 chunks.
[PMS1-Amcor] PTO batch A_124... 0 chunks.

[PMS1-Amcor] Entity filter returned 0 chunks for FY2022.
[PMS1-Amcor] Relax fiscal year filter? [y/n]
> y

--- buffered while you were typing ---
[PMS1-BBY] PTO judge... done.
[PMS1-BBY] PTO batch B_124... 38 chunks.
[PMS1-BBY] PTO judge... done.
[PMS1-BBY] Stencil computed. 5 cells filled.
---

[PMS1-Amcor] Relaxed. Found 31 chunks.
[PMS1-Amcor] PTO judge... done.
[PMS1-Amcor] Stencil computed. 5 cells filled.
[ORCHESTRATOR] Both stencils ready. Running PTECA...
```

## Resolved questions

- **Threading model.** Build threaded from the start (Queue +
  AnswerSlot + _ui_loop). No simple-first-swap-later. Matches
  `0_CLIrouting.md` design. UI loop starts at module import as
  a daemon thread:
  ```python
  _router = TerminalRouter()
  threading.Thread(target=_router._ui_loop, daemon=True).start()
  ```
  Blocks on `_input_queue.get()` until first input request — no
  CPU waste. No explicit start() call needed from harness code.

- **Label namespacing.** `execute_tool` derives the label from input
  params (e.g. `firm="Best Buy"` → `register("PMS1-Best Buy")`),
  creates a ToolChannel per invocation, and passes it to the tool's
  entry function as an optional `channel` param. The tool uses
  `channel` if provided, falls back to its module-level default.
  See tool contract specs (06-09) for per-tool details.
