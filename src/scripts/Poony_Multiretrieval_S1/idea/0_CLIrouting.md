# CLI Terminal Router

Owns all terminal I/O. No tool ever calls `print()` or `input()`
directly. Lives in `terminal_router.py` at project root, module-level
global singleton.

Companion to `0_PMSetPTOetChartasHarnessTool.md` — that doc covers the
harness tool loop and opaque variable handles. This doc covers the
terminal I/O layer underneath.

---

## Why this exists

Tools run in parallel (e.g. PMS1 for Best Buy and PMS1 for Amcor
simultaneously). Problems:

- **stdout interleave:** two tools printing at the same time produces
  jumbled output
- **stdin collision:** two tools both call `input()` at the same time
  produces undefined behavior (both read from same stdin)

The terminal router serializes all I/O through one component.

---

## How tools use it

```python
# src/pto.py (top of file, once)
from terminal_router import register
pto_out = register("PMS1")

# anywhere in pto.py
pto_out.print("Loading index...")
answer = pto_out.input("No chunks. Relax FY?")
```

```python
# src/pteca.py (top of file, once)
from terminal_router import register
pteca_out = register("PTECA")

# anywhere in pteca.py
pteca_out.print("Stencil has 5 rows.")
answer = pteca_out.input("Include Revenue?")
```

```python
# harness.py (top of file, once)
from terminal_router import register
harness_out = register("ORCHESTRATOR")

harness_out.print("Running PMS1 for Best Buy...")
answer = harness_out.input("Which firms should I run?")
```

No function signature changes needed anywhere. The function that
needs the router just imports `register` at module level. Every
function above it in the call chain is untouched (see
`0_PMSetPTOetChartasHarnessTool.md` "Option B: Import it" for
rationale on why global import, not parameter threading).

---

## What's inside terminal_router.py

```
terminal_router.py
│
├── AnswerSlot (dataclass)
│     threading.Event + value holder
│     tool thread posts question, waits on event
│     UI thread fills value, sets event → tool unblocks
│
├── TerminalRouter (class)
│   │
│   ├── _input_queue       Queue of (label, question, answer_slot)
│   │                        from tools requesting input
│   ├── _active_input      which tool currently owns stdin (or None)
│   ├── _buffer            list of (label, msg) held while input active
│   ├── _lock              threading lock for buffer/active_input access
│   │
│   ├── .print(label, msg)
│   │     if no active input → print immediately
│   │     if active input   → append to buffer
│   │
│   ├── .input(label, question) → str
│   │     create AnswerSlot
│   │     post (label, question, slot) to _input_queue
│   │     _process_one()
│   │     slot.wait()  ← calling thread blocks here
│   │     return slot.value
│   │
│   └── ._process_one()
│         pulls from _input_queue
│         displays "[N pending — answering 1 of N]" if multiple
│         displays "[LABEL] question"
│         calls real input("> ")  ← only place in codebase that reads stdin
│         posts answer to AnswerSlot → requesting thread unblocks
│         flushes buffer to stdout (all at once)
│
├── ToolChannel (class)
│   │  label: str
│   │  router: TerminalRouter
│   │
│   ├── .print(msg)  → router.print(self.label, msg)
│   └── .input(question) → router.input(self.label, question)
│
├── _router = TerminalRouter()     singleton, created at import
│
└── register(label) → ToolChannel  public API
```

---

## Normal flow (no input active)

Tools call `pto_out.print(...)`, output goes straight to terminal:

```
ToolChannel.print("Loading...")
     │
     ▼
TerminalRouter.print("PMS1", "Loading...")
     │
     ├── active input? NO
     │
     ▼
print("[PMS1] Loading...")     ← real stdout, immediate
```

Terminal shows:

```
[PMS1] Loading index...
[PMS1] Sekei planning... done.
[PMS1] PTO batch A_124... 42 chunks.
```

---

## Input flow (one question)

Tool calls `pto_out.input(...)`:

```
ToolChannel.input("Relax FY?")
     │
     ▼
TerminalRouter.input("PMS1", "Relax FY?")
     │
     ├── create AnswerSlot
     ├── set _active_input = "PMS1"
     ├── post to _input_queue
     ├── _process_one():
     │     ├── display "[PMS1] Relax FY filter? [y/n]"
     │     ├── call real input("> ")   ← blocks until user types
     │     │
     │     │   (meanwhile, any .print() from other threads
     │     │    goes to _buffer instead of stdout)
     │     │
     │     ▼
     │   user types "y"
     │     ├── answer_slot.value = "y"
     │     ├── answer_slot.set()       ← wakes up requesting thread
     │     ├── _active_input = None
     │     ├── flush _buffer to stdout (all at once)
     │
     ▼
requesting thread unblocks, returns "y"
```

Terminal shows:

```
[PMS1] PTO batch A_124... 42 chunks.
[PMS1] Entity filter returned 0 chunks for FY2021.

[PMS1] Relax FY filter? [y/n]
> y

--- buffered while you were typing ---
[PMS1-Amcor] Loading index...
[PMS1-Amcor] Sekei planning... done.
---------------------------------------

[PMS1] Relaxed. Found 42 chunks.
```

---

## Multiple questions pending

Two tools both call `.input()` at the same time (parallel execution):

```
Thread 1 (BBY):   pto_out.input("Relax FY for BBY?")     ← posted first
Thread 2 (Amcor): pto_out.input("Relax FY for Amcor?")   ← posted second

_input_queue = [
    ("PMS1-BBY",   "Relax FY for BBY?",   answer_slot_1),
    ("PMS1-Amcor", "Relax FY for Amcor?", answer_slot_2),
]
```

Router processes one at a time. Thread 2 blocks until thread 1's
question is answered.

Terminal shows:

```
[2 questions pending — answering 1 of 2]

[PMS1-BBY] Relax FY filter? [y/n]
> y

--- buffered ---
[PMS1-Amcor] PTO batch B_124... 0 chunks.
--------------

[1 question pending — answering 1 of 1]

[PMS1-Amcor] Relax FY filter? [y/n]
> n

(normal output resumes)
```

Thread 1 unblocks with "y". Thread 2 unblocks with "n". Each
resumes independently.

---

## How the blocking works (implementation sketch)

```python
import threading
from queue import Queue
from dataclasses import dataclass, field


@dataclass
class AnswerSlot:
    """A thread-safe slot for passing an answer back to a waiting tool."""
    value: str = ""
    _event: threading.Event = field(default_factory=threading.Event)

    def wait(self):
        self._event.wait()

    def set(self):
        self._event.set()


class TerminalRouter:
    def __init__(self):
        self._input_queue: Queue = Queue()
        self._buffer: list[tuple[str, str]] = []
        self._active_input: str | None = None
        self._lock = threading.Lock()

    def print(self, label: str, msg: str):
        with self._lock:
            if self._active_input is not None:
                # Someone is answering a question — buffer this
                self._buffer.append((label, msg))
            else:
                print(f"[{label}] {msg}")

    def input(self, label: str, question: str) -> str:
        slot = AnswerSlot()
        self._input_queue.put((label, question, slot))
        self._process_one()
        slot.wait()
        return slot.value

    def _process_one(self):
        """Process the next pending input question."""
        if self._input_queue.empty():
            return

        label, question, slot = self._input_queue.get()

        with self._lock:
            self._active_input = label

        # Show pending count
        pending = self._input_queue.qsize() + 1
        if pending > 1:
            print(f"\n[{pending} questions pending — answering 1 of {pending}]")

        # Display question and read answer
        print(f"\n[{label}] {question}")
        answer = __builtins__["input"]("> ")  # real stdin

        # Unblock the requesting thread
        slot.value = answer
        slot.set()

        # Flush buffer
        with self._lock:
            self._active_input = None
            for buf_label, buf_msg in self._buffer:
                print(f"[{buf_label}] {buf_msg}")
            self._buffer.clear()


class ToolChannel:
    """Bound channel for a specific tool. Auto-labels all output."""
    def __init__(self, label: str, router: TerminalRouter):
        self.label = label
        self.router = router

    def print(self, msg: str):
        self.router.print(self.label, msg)

    def input(self, question: str) -> str:
        return self.router.input(self.label, question)


# ── Singleton ──────────────────────────────────────────────────
_router = TerminalRouter()


def register(label: str) -> ToolChannel:
    """Register a tool and get a labeled channel for terminal I/O."""
    return ToolChannel(label, _router)
```

---

## Full architecture diagram

```
┌──────────────────────────────────────────────────────────────┐
│  TERMINAL (user's screen)                                    │
│                                                              │
│  stdout ← only TerminalRouter writes here                    │
│  stdin  ← only TerminalRouter reads here                     │
└──────────────────────────────┬───────────────────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │  TerminalRouter     │
                    │  (singleton)        │
                    │                     │
                    │  _input_queue       │
                    │  _buffer            │
                    │  _lock              │
                    │  _active_input      │
                    └──┬─────┬─────┬─────┘
                       │     │     │
              register │     │     │ register
                 "PMS1"│     │     │ "PTECA"
                       │     │     │
                       ▼     │     ▼
                 ToolChannel  │  ToolChannel
                 label="PMS1" │  label="PTECA"
                 .print()     │  .print()
                 .input()     │  .input()
                       │      │      │
                       ▼      │      ▼
                  src/pto.py  │  src/pteca.py
                  src/sekei.py│
                  src/orch.py │
                              │
                              │ register "ORCHESTRATOR"
                              ▼
                        ToolChannel
                        label="ORCHESTRATOR"
                        .print()
                        .input()
                              │
                              ▼
                        harness.py
```

---

## Integration with harness tool loop

The harness tool loop (see `0_PMSetPTOetChartasHarnessTool.md`)
calls `execute_tool` which runs PMS1/PTECA/stencil2chart. Those
tools use `ToolChannel.print()` and `ToolChannel.input()` for all
terminal I/O. The harness itself uses its own `ToolChannel` labeled
"ORCHESTRATOR".

No changes needed to the tool loop design. The terminal router is
a layer underneath — it replaces raw `print()`/`input()` calls with
routed equivalents.

---

## Existing code changes needed

Per file, find-and-replace:

- `print(...)` → `pto_out.print(...)` (or equivalent channel)
- `input(...)` → `pto_out.input(...)` (or equivalent channel)
- Add `from terminal_router import register` at top
- Add `xyz_out = register("XYZ")` at module level

No function signature changes. No parameter threading.

---

## What to build

```
terminal_router.py        (~80 lines, project root)
├── AnswerSlot            dataclass: Event + value
├── TerminalRouter        class: queues, buffer, lock, print/input/process
├── ToolChannel           class: label + bound print/input
├── _router               singleton
└── register(label)       public API
```
