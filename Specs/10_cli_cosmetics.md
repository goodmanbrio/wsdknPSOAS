# CLI Cosmetics

Thin visual layer on top of TerminalRouter using `rich`. Colors,
spinners, styled prompts. No logic changes — purely cosmetic.

## Architecture

```
TerminalRouter (01)
    │
    ├── uses: rich.console.Console for all terminal I/O
    │
    └── affects: _ui_loop(), _styled_print(), buffered flush
```

```
agent_loop (02) / PTECA loop (07)
    │
    └── spinner shown while waiting for LLM response
        (_router.start_spinner / stop_spinner)
```

## Dependencies

```
rich>=13.0
```

One package. No other cosmetic deps.

## Label colors

Each tool label gets a distinct color. Labels are matched by
prefix (before `-`) so `PMS1-Best Buy` inherits `PMS1`'s color.

```python
from rich.console import Console

console = Console()

LABEL_STYLES = {
    "ORCHESTRATOR": "bold cyan",
    "PMS1":         "bold green",
    "PTECA":        "bold yellow",
    "S2C":          "bold magenta",
    "PUMBA":        "bold red",
}

def _style_for(label: str) -> str:
    """Look up style by label prefix. e.g. 'PMS1-Best Buy' → 'PMS1' → green."""
    prefix = label.split("-")[0]
    return LABEL_STYLES.get(prefix, "bold white")
```

Terminal output:

```
[bold cyan][ORCHESTRATOR][/] PSOAS reporting for duty sir!     ← cyan
[bold green][PMS1-Best Buy][/] Loading index...                ← green
[bold green][PMS1-Best Buy][/] Sekei planning... done.         ← green
[bold green][PMS1-Amcor][/] Loading index...                   ← green
[bold yellow][PTECA-Best Buy][/] Stencil has 5 rows.           ← yellow
[bold magenta][S2C-1][/] Saved: output/stencil2charted_...svg   ← magenta
```

## TerminalRouter changes

### .print(label, msg, markdown) — styled output

Delegates to `_styled_print()`, an extracted helper method:

```python
def _styled_print(self, label: str, msg: str, markdown: bool = False):
    style = _style_for(label)
    if markdown:
        msg = msg.replace("\n", "  \n")  # CommonMark hard-break fix
        label_text = Text(f"[{label}]", style=style)
        self._console.print(Group(label_text, Markdown(msg)))
    else:
        self._console.print(f"[{style}]\\[{label}][/{style}] {msg}")
```

When `markdown=True`, the `\n` → `  \n` substitution ensures
CommonMark hard-break rendering (two trailing spaces). Uses
`Group(Text(...), Markdown(...))` so the label renders in the
tool's color and the body renders as rich Markdown. When buffered
(during active input), same styling applied at flush time via the
same helper.

### ._ui_loop() — styled input prompt

```python
with self._lock:
    self._active_input = True

style = _style_for(label)
if md:
    self._console.print(Group(
        Text(f"[{label}]", style=style),
        Markdown(question),
    ))
else:
    self._console.print(f"\n[{style}]\\[{label}][/{style}] {question}")
answer = self._console.input("[bold]> [/]")
```

The `md` flag comes from the 4-tuple `(label, question,
answer_slot, md)` unpacked from `_input_queue`.

### Buffered flush — separator

Buffer is snapshotted under lock into `to_flush` (not iterated
from `self._buffer` directly). Each entry is a 3-tuple
`(label, msg, markdown)`.

```python
with self._lock:
    self._active_input = False
    to_flush = list(self._buffer)
    self._buffer.clear()
if to_flush:
    self._console.print("[dim]--- buffered while you were typing ---[/dim]")
    for buf_label, buf_msg, buf_md in to_flush:
        self._styled_print(buf_label, buf_msg, buf_md)
    self._console.print("[dim]---[/dim]")
```

### Pending questions count

```python
# Only shown when multiple questions pending (guard: pending > 1)
self._console.print(f"[dim][{pending} questions pending — answering 1 of {pending}][/dim]")
```

## Spinner during LLM calls

### agent_loop.py (orchestrator)

```python
from src.harness.terminal_router import _router

# In the while loop, wrap client.messages.create():
_router.start_spinner("ORCHESTRATOR")
try:
    response = client.messages.create(
        model=model,
        system=system_prompt,
        messages=messages,
        tools=tools,
        max_tokens=4096,
    )
finally:
    _router.stop_spinner()
```

User sees animated dots while waiting for the LLM response.
Spinner auto-clears when the call returns. If a tool thread
triggers an input prompt mid-spinner, `_ui_loop` stops the
spinner before showing the prompt — no visual collision.

### tool_pteca.py (PTECA internal loop)

```python
_router.start_spinner("PTECA")
try:
    response = client.messages.create(
        model=model,
        system=PTECA_SYSTEM_PROMPT,
        messages=messages,
        tools=PTECA_TOOLS,
        max_tokens=max_tokens,
    )
finally:
    _router.stop_spinner()
```

### Spinner vs active input conflict

Spinner writes to stdout. If a TerminalRouter input prompt is
active on another thread, the spinner would collide.

**Problem with TOCTOU check:** Checking `_active_input` then
starting the spinner is a race — a tool thread can post an
input request between check and spinner start, causing both
spinner animation and input prompt to write to stdout simultaneously.

**Solution:** TerminalRouter OWNS the spinner. The `_ui_loop`
cooperatively stops any active spinner before showing a prompt,
and restarts it after the prompt completes.

```python
class TerminalRouter:
    _spinner: Status | None = None   # active rich Status, if any
    # ... existing fields ...

    def start_spinner(self, label: str):
        """Start a spinner. Called by agent_loop/PTECA before LLM calls.
        Stops any existing spinner first (prevents orphaned animations
        when parallel tools both start spinners)."""
        with self._lock:
            if self._spinner is not None:
                self._spinner.stop()
            style = _style_for(label)
            self._spinner = self._console.status(
                f"[{style}]{label} thinking...", spinner="dots"
            )
            self._spinner.start()

    def stop_spinner(self):
        """Stop the active spinner. Idempotent."""
        with self._lock:
            if self._spinner is not None:
                self._spinner.stop()
                self._spinner = None
```

And in `_ui_loop`, before showing the input prompt:

```python
    # ── inside _ui_loop, before displaying question ──
    # Stop spinner so prompt renders cleanly
    self.stop_spinner()

    style = _style_for(label)
    if md:
        self._console.print(Group(
            Text(f"[{label}]", style=style),
            Markdown(question),
        ))
    else:
        self._console.print(f"\n[{style}]\\[{label}][/{style}] {question}")
    answer = self._console.input("[bold]> [/]")
```

Caller usage (replaces `_llm_call_with_spinner`):

```python
# agent_loop.py / tool_pteca.py
_router.start_spinner("ORCHESTRATOR")
try:
    response = client.messages.create(**kwargs)
finally:
    _router.stop_spinner()
```

No TOCTOU — the _ui_loop holds the lock when stopping the
spinner, and the spinner state is protected by the same lock.
If a prompt arrives mid-spinner, _ui_loop acquires the lock,
stops the spinner, then shows the prompt cleanly.

## Welcome banner

At the top of `run_harness()`, before the first LLM call:

```python
from rich.panel import Panel

console.print(Panel(
    "[bold cyan]PSOAS[/bold cyan] — Poony Sophomore Orchestrated Analyst Strapon",
    subtitle="[dim]type answers when prompted[/dim]",
    border_style="cyan",
))
```

```
╭──────────────────────────────────────────────────────────────╮
│ PSOAS — Poony Sophomore Orchestrated Analyst Strapon         │
│                                      type answers when prompted │
╰──────────────────────────────────────────────────────────────╯
```

## Session end summary

After the loop exits, print a summary panel:

```python
from rich.panel import Panel

summary_lines = []
for handle, entry in registry._registry.items():
    summary_lines.append(f"  {handle}: {entry['description']}")

console.print(Panel(
    "\n".join([
        f"[bold]Session complete.[/bold]",
        f"Transcript: {transcript}",
        "",
        "Variables stored:",
        *summary_lines,
    ]),
    border_style="green",
))
```

```
╭──────────────────────────────────────────────────────────────╮
│ Session complete.                                            │
│ Transcript: temp/sessions/20260715154603/transcript.md │
│                                                              │
│ Variables stored:                                            │
│   $var_1: Best Buy stencil                                   │
│   $var_2: Amcor stencil                                      │
│   $var_3: Best Buy chart 1 (2 series)                        │
│   $var_4: Amcor chart 1 (2 series)                           │
╰──────────────────────────────────────────────────────────────╯
```

## Checkpoint styling (02 guard rail)

The 6-turn checkpoint gets a distinct look:

```python
from rich.panel import Panel

console.print(Panel(
    summary_text,
    title="[bold]CHECKPOINT — 6 turns reached[/bold]",
    border_style="yellow",
))
answer = orchestrator_out.input("Continue? [y/n]")
```

## Error styling

Tool errors returned to the LLM are also printed to terminal
for user visibility:

```python
console.print(f"[bold red]\\[ERROR][/bold red] {error_msg}")
```

## Implementation notes

### Console singleton

One `Console()` instance shared across all modules. Lives in
terminal_router.py alongside the TerminalRouter singleton:

```python
from rich.console import Console

console = Console()
_router = TerminalRouter(console)
```

TerminalRouter takes console as a constructor arg so all output
goes through one Console instance. Other modules (agent_loop,
tool_pteca) import `console` from terminal_router for spinners.

```python
# agent_loop.py
from src.harness.terminal_router import register, console, _router
```

### No logic changes

This spec changes ZERO control flow. Every change is a
`print()` → `console.print()` swap or a spinner wrapper around
an existing LLM call. If rich is not installed, the system works
identically with raw print/input (graceful fallback possible but
not required for MVP).

## File locations

```
src/harness/terminal_router.py   (Console integration, styled output)
src/harness/agent_loop.py        (spinner + banner + summary)
src/tools/tool_pteca.py          (spinner)
```

No new files.

## Ideal demo (full visual)

```
╭──────────────────────────────────────────────────────────────╮
│ PSOAS — Poony Sophomore Orchestrated Analyst Strapon         │
│                                      type answers when prompted │
╰──────────────────────────────────────────────────────────────╯

⠋ ORCHESTRATOR thinking...

[ORCHESTRATOR] PSOAS reporting for duty sir!
[ORCHESTRATOR] Two firms: Best Buy and Amcor. Running PMS1 for both.

[PMS1-Best Buy] Loading index...
[PMS1-Amcor] Loading index...
[PMS1-Best Buy] Sekei planning... done. 5 cells, 2 batches.
[PMS1-Amcor] Sekei planning... done. 5 cells, 2 batches.
[PMS1-Best Buy] PTO batch A_124... 42 chunks.
[PMS1-Amcor] PTO batch A_124... 0 chunks.

[PMS1-Amcor] Relax fiscal year filter? [y/n]
> y

--- buffered while you were typing ---
[PMS1-Best Buy] PTO judge... done.
[PMS1-Best Buy] Stencil computed. 5 cells filled.
---

[PMS1-Amcor] Relaxed. Found 31 chunks.
[PMS1-Amcor] Stencil computed. 5 cells filled.

⠋ ORCHESTRATOR thinking...

[PTECA-Best Buy] Stencil has 5 rows: Revenue, GP, Gross Margin, NI, Net Margin.

[PTECA-Best Buy] Include Net Profit Margin alongside Gross Margin?
> yes

⠋ PTECA thinking...

[PTECA-Best Buy] Done. 1 chart(s).
[PTECA-Amcor] Stencil has 5 rows.
[PTECA-Amcor] Same structure. Only margins?
> yes

[PTECA-Amcor] Done. 1 chart(s).

⠋ ORCHESTRATOR thinking...

[S2C-1] Saved: output/stencil2charted_20260715_1.svg
[S2C-2] Saved: output/stencil2charted_20260715_2.svg

[ORCHESTRATOR] Done. 2 charts saved.

╭──────────────────────────────────────────────────────────────╮
│ Session complete.                                            │
│ Transcript: temp/sessions/20260715154603/transcript.md │
│                                                              │
│ Variables stored:                                            │
│   $var_1: Best Buy stencil                                   │
│   $var_2: Amcor stencil                                      │
│   $var_3: Best Buy chart 1 (2 series)                        │
│   $var_4: Amcor chart 1 (2 series)                           │
╰──────────────────────────────────────────────────────────────╯
```

(In actual terminal, labels would be colored per LABEL_STYLES.)

## Resolved questions

- **Package.** `rich` only. No textual, no click, no curses.
- **Console singleton.** One instance in terminal_router, shared.
- **Spinner conflict.** `start_spinner` stops any existing spinner
  before starting a new one (prevents orphaned animations from
  parallel tool threads). `_ui_loop` also stops spinner before
  showing an input prompt.
- **No logic changes.** Purely cosmetic. print→console.print swaps
  and spinner wrappers.
- **Label colors.** Prefix-matched. PMS1-Best Buy inherits PMS1's
  green.
