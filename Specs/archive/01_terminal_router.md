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
                    │  _input_queue ──────┤ Queue[(label, question, AnswerSlot, markdown)]
                    │                     │   tools post here when they need input
                    │                     │
                    │  _buffer ───────────┤ list[(label, msg, markdown)]
                    │                     │   prints held while input is active
                    │                     │
                    │  _active_input ─────┤ bool
                    │                     │   True = someone owns stdin right now
                    │                     │
                    │  .print(label, msg)    │
                    │  .input(label, q)      │
                    │  .start_spinner(label)  │
                    │  .stop_spinner()        │
                    │  ._ui_loop()            │
                    └──┬──────┬──────┬────────┘
                       │      │      │
              register │      │      │ register
               "PMS1"  │      │      │ "PTECA"
                       ▼      │      ▼
                 ToolChannel   │  ToolChannel
                 .print(msg)   │  .print(msg)
                 .input(q)     │  .input(q)
                 _log: list    │  _log: list
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

### ToolChannel fields

```python
class ToolChannel:
    def __init__(self, label: str, router: "TerminalRouter"):
        self.label = label
        self._router = router
        self._log: list[str] = []   # D12 transcript enrichment
```

`_log` accumulates every message passed to `print()`. Harvested
by `harvest_logs()` at transcript-write time (see below).

### ToolChannel.print(msg: str, markdown: bool = False) → None

Post a message to the terminal. Auto-prefixed with `[label]`.

| Param | Type | Description |
|---|---|---|
| `msg` | `str` | Message to display. No label needed — ToolChannel adds it. |
| `markdown` | `bool` | If True, msg body is rendered as Markdown via rich. Default False. |

Body: resolves override target via `_get_override(self) or self`
(see thread-local overrides below), appends `msg` to `target._log`
(the resolved override target's log, not necessarily `self._log`),
then calls `_router.print(target.label, msg, markdown=markdown)`.

```python
pto_out.print("Loading index...")
# terminal shows: [PMS1] Loading index...
```

### ToolChannel.input(question: str, markdown: bool = False) → str

Ask the user a question. Blocks the calling thread until the user
answers. Auto-prefixed with `[label]`.

| Param | Type | Description |
|---|---|---|
| `question` | `str` | Question to display. |
| `markdown` | `bool` | If True, question is rendered as Markdown via rich. Default False. |

Body: resolves override target via `_get_override(self) or self`,
then calls `_router.input(target.label, question, markdown=markdown)`.

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
    _event: threading.Event = field(default_factory=threading.Event)
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
    _console: Console           # rich Console instance, passed via __init__(con)
    _input_queue: Queue         # (label, question, AnswerSlot, markdown)
    _buffer: list[tuple[str, str, bool]]  # (label, msg, markdown) — held during active input
    _active_input: bool         # True while a question is displayed
    _lock: threading.Lock       # protects _buffer and _active_input
    _is_dead: bool              # True after EOF — all future input() returns ""
    _spinner: Status | None     # active rich Status, if any (spec 10)
```

Constructor: `TerminalRouter(con: Console)` — stores `con` as
`self._console`.

#### .print(label: str, msg: str, markdown: bool = False)

```
with _lock:
    if _active_input:
        _buffer.append((label, msg, markdown))
    else:
        _styled_print(label, msg, markdown)
```

Uses `_styled_print()` (extracted helper, see spec 10) instead
of raw `print()`.

#### .input(label: str, question: str, markdown: bool = False) → str

```
if _is_dead:
    return ""               ← EOF already received, no-op
create AnswerSlot
post (label, question, answer_slot, markdown) to _input_queue
answer_slot.wait()          ← blocks calling thread
return answer_slot.value
```

Queue tuple is 4-element: `(label, question, AnswerSlot, markdown)`.

#### ._ui_loop()

Runs on the main thread (or a dedicated UI thread). This is the
ONLY place in the entire codebase that calls real `print()` and
real `input()`.

```
while True:
    label, question, answer_slot, md = _input_queue.get()

    # stop spinner so prompt renders cleanly (spec 10)
    self.stop_spinner()

    # set active
    with _lock:
        _active_input = True

    # show pending count
    pending = _input_queue.qsize() + 1
    if pending > 1:
        self._console.print(f"[dim][{pending} questions pending — answering 1 of {pending}][/dim]")

    # display question (if md, render as Markdown via rich)
    style = _style_for(label)
    if md:
        self._console.print(Group(
            Text(f"[{label}]", style=style),
            Markdown(question),
        ))
    else:
        self._console.print(f"\n[{style}]\\[{label}][/{style}] {question}")

    try:
        answer = self._console.input("[bold]> [/]")
    except EOFError:
        # stdin closed (Ctrl+D) — mark dead, unblock requester
        # with empty string, drain remaining queue, exit loop
        _is_dead = True
        answer = ""
        answer_slot.value = answer
        answer_slot.set()
        with self._lock:
            _active_input = False
        # drain queue so no thread deadlocks on wait()
        while not _input_queue.empty():
            try:
                _, _, slot, _ = _input_queue.get_nowait()
                slot.value = ""
                slot.set()
            except Exception:
                break
        break

    # unblock requester
    answer_slot.value = answer
    answer_slot.set()

    # flush buffer — snapshot under lock, print outside lock
    with _lock:
        _active_input = False
        to_flush = list(_buffer)
        _buffer.clear()
    if to_flush:
        self._console.print("[dim]--- buffered while you were typing ---[/dim]")
        for buf_label, buf_msg, buf_md in to_flush:
            _styled_print(buf_label, buf_msg, buf_md)
        self._console.print("[dim]---[/dim]")
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
_styled_print("PMS1", "Loading index...")    ← immediate
    → _console.print(f"[bold green]\\[PMS1][/bold green] Loading index...")
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
    _buffer.append(("PMS1-Amcor", "42 chunks.", False))  ← held, not printed

User answers Thread 1's question:
    > y
        │
        ├── answer_slot.set()          ← unblocks Thread 1
        ├── _active_input = False
        ├── flush _buffer:
        │     _styled_print("PMS1-Amcor", "42 chunks.", False)  ← now it appears
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

    [PMS1-Amcor] Relax FY for Amcor?
    > n

Thread 1 resumes with "y".
Thread 2 resumes with "n".
```

## Dependencies

- `threading` (Lock, Event, local)
- `queue` (Queue)
- `dataclasses` (dataclass)
- `rich` (Console, Text, Group, Markdown, Status, Panel) — layered by spec 10
  Note: `Panel` is imported but unused in terminal_router.py — it is used in agent_loop.py.

## File location

```
src/harness/terminal_router.py
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

## Thread-local channel overrides (D13)

PMS1 internal modules use module-level `register("PMS1")` to get a
default channel. When the orchestrator dispatches firm-specific work
on worker threads, it needs those modules' output to route to a
firm-specific channel (e.g. `"PMS1-Best Buy"`) without changing the
module-level variable.

Solution: thread-local overrides stored in `threading.local()`.

```python
_thread_overrides = threading.local()

def override_channel(label: str, channel: ToolChannel):
    """Set a thread-local override: any ToolChannel with the given
    base label will redirect to `channel` on this thread."""
    if not hasattr(_thread_overrides, "map"):
        _thread_overrides.map = {}
    _thread_overrides.map[label] = channel

def clear_override(label: str):
    """Remove the thread-local override for `label`."""
    if hasattr(_thread_overrides, "map"):
        _thread_overrides.map.pop(label, None)

def _get_override(channel: ToolChannel) -> ToolChannel | None:
    """Return thread-local override for this channel's label, or None."""
    overrides = getattr(_thread_overrides, "map", {})
    return overrides.get(channel.label)
# Call sites use `or self` fallback:
#   target = _get_override(self) or self
```

`ToolChannel.print` and `ToolChannel.input` both call
`_get_override(self) or self` to resolve the actual target before
delegating to `_router`.

## harvest_logs() (D12)

Iterates all registered channels, clears ALL `_log` lists
(including ORCHESTRATOR), but excludes ORCHESTRATOR from the
returned dict (its output is already captured as LLM response
content in the transcript). Used at transcript-write time to
capture per-tool output.

```python
def harvest_logs() -> dict[str, list[str]]:
    """Drain and return accumulated logs from all tool channels.
    Clears all logs including ORCHESTRATOR, but excludes
    ORCHESTRATOR from the returned dict."""
    with _channels_lock:
        result = {}
        for label, ch in _channels.items():
            if ch._log:
                if label != "ORCHESTRATOR":
                    result[label] = list(ch._log)
                ch._log.clear()
        return result
```

## Symbols added post-spec-01

Summary of all public/module-level symbols in terminal_router.py
that were introduced by later specs or dev iterations:

| Symbol | Added by | Description |
|---|---|---|
| `console = Console()` | spec 10 | Rich console singleton. All terminal output goes through this. |
| `_thread_overrides = threading.local()` | D13 | Thread-local storage for channel overrides. |
| `LABEL_STYLES` dict | spec 10 + spec 12 | Maps label prefixes to rich styles. 5 entries: ORCHESTRATOR, PMS1, PTECA, S2C, PUMBA. |
| `_style_for(label)` | spec 10 | Look up rich style by label prefix (splits on `-`). Falls back to `"bold white"`. |
| `_get_override(channel)` | D13 | Resolve thread-local redirect for a ToolChannel. Returns `None` if no override set (call sites use `or self` fallback). |
| `override_channel(label, channel)` | D13 | Set thread-local override: calls from `label`'s channel redirect to `channel` on this thread. |
| `clear_override(label)` | D13 | Remove thread-local override for `label`. |
| `harvest_logs()` | D12 | Drain all channel `_log` lists (including ORCHESTRATOR). Returns `{label: [msgs]}`, excludes ORCHESTRATOR from return dict. |
| `start_spinner(label)` | spec 10 | Start rich Status spinner. Stops any existing spinner first. |
| `stop_spinner()` | spec 10 | Stop active spinner. Idempotent. |
| `_styled_print(label, msg, markdown)` | spec 10 | Factored helper for styled output. When `markdown=True`, applies `msg = msg.replace("\n", "  \n")` for CommonMark hard-break compatibility, then renders via `Group(Text(...), Markdown(...))`. |

## Resolved questions

- **Threading model.** Build threaded from the start (Queue +
  AnswerSlot + _ui_loop). No simple-first-swap-later. Matches
  `0_CLIrouting.md` design. UI loop starts at module import as
  a daemon thread:
  ```python
  _router = TerminalRouter(console)
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
