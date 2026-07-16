"""
terminal_router.py — Singleton that owns all terminal I/O.

No tool or harness component calls print() or input() directly —
everything goes through the router via ToolChannel.

Usage:
    from src.harness.terminal_router import register
    ch = register("PMS1")
    ch.print("Loading index...")
    answer = ch.input("Relax FY filter? [y/n]")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from queue import Queue

from rich.console import Console
from rich.panel import Panel
from rich.status import Status


# ── Console singleton ─────────────────────────────────────────────────
console = Console()


# ── Label styling (spec 10) ───────────────────────────────────────────

LABEL_STYLES = {
    "ORCHESTRATOR": "bold cyan",
    "PMS1":         "bold green",
    "PTECA":        "bold yellow",
    "S2C":          "bold magenta",
}


def _style_for(label: str) -> str:
    """Look up style by label prefix. 'PMS1-Best Buy' → 'PMS1' → green."""
    prefix = label.split("-")[0]
    return LABEL_STYLES.get(prefix, "bold white")


# ── AnswerSlot ────────────────────────────────────────────────────────

@dataclass
class AnswerSlot:
    """Thread-safe container for passing an answer from the UI thread
    back to the requesting tool thread."""
    _event: threading.Event = field(default_factory=threading.Event)
    value: str = ""

    def wait(self):
        self._event.wait()

    def set(self):
        self._event.set()


# ── ToolChannel ───────────────────────────────────────────────────────

class ToolChannel:
    """Bound to a label. Tool code calls .print() and .input() on this."""

    def __init__(self, label: str, router: "TerminalRouter"):
        self.label = label
        self._router = router

    def print(self, msg: str) -> None:
        self._router.print(self.label, msg)

    def input(self, question: str) -> str:
        return self._router.input(self.label, question)


# ── TerminalRouter ────────────────────────────────────────────────────

class TerminalRouter:
    """Singleton. Created once at module import. Owns all terminal I/O."""

    def __init__(self, con: Console):
        self._console = con
        self._input_queue: Queue = Queue()
        self._buffer: list[tuple[str, str]] = []
        self._active_input: bool = False
        self._lock = threading.Lock()
        self._is_dead: bool = False
        self._spinner: Status | None = None

    # ── Public: print ─────────────────────────────────────────────

    def print(self, label: str, msg: str) -> None:
        with self._lock:
            if self._active_input:
                self._buffer.append((label, msg))
            else:
                self._styled_print(label, msg)

    # ── Public: input ─────────────────────────────────────────────

    def input(self, label: str, question: str) -> str:
        if self._is_dead:
            return ""
        slot = AnswerSlot()
        self._input_queue.put((label, question, slot))
        slot.wait()
        return slot.value

    # ── Public: spinner ───────────────────────────────────────────

    def start_spinner(self, label: str) -> None:
        """Start a spinner. Called before LLM calls.
        Stops any existing spinner first."""
        with self._lock:
            if self._spinner is not None:
                self._spinner.stop()
            style = _style_for(label)
            self._spinner = self._console.status(
                f"[{style}]{label} thinking...", spinner="dots"
            )
            self._spinner.start()

    def stop_spinner(self) -> None:
        """Stop active spinner. Idempotent."""
        with self._lock:
            if self._spinner is not None:
                self._spinner.stop()
                self._spinner = None

    # ── UI loop (runs on dedicated thread) ────────────────────────

    def _ui_loop(self) -> None:
        """The ONLY place that writes to stdout / reads from stdin."""
        while True:
            label, question, answer_slot = self._input_queue.get()

            # Stop spinner so prompt renders cleanly
            self.stop_spinner()

            with self._lock:
                self._active_input = True

            # Show pending count
            pending = self._input_queue.qsize() + 1
            if pending > 1:
                self._console.print(
                    f"[dim][{pending} questions pending "
                    f"— answering 1 of {pending}][/dim]"
                )

            # Display question
            style = _style_for(label)
            self._console.print(
                f"\n[{style}]\\[{label}][/{style}] {question}"
            )

            try:
                answer = self._console.input("[bold]> [/]")
            except EOFError:
                self._is_dead = True
                answer = ""
                answer_slot.value = answer
                answer_slot.set()
                with self._lock:
                    self._active_input = False
                # Drain queue so no thread deadlocks
                while not self._input_queue.empty():
                    try:
                        _, _, slot = self._input_queue.get_nowait()
                        slot.value = ""
                        slot.set()
                    except Exception:
                        break
                break

            # Unblock requester
            answer_slot.value = answer
            answer_slot.set()

            # Flush buffer
            with self._lock:
                self._active_input = False
                to_flush = list(self._buffer)
                self._buffer.clear()

            if to_flush:
                self._console.print(
                    "[dim]--- buffered while you were typing ---[/dim]"
                )
                for buf_label, buf_msg in to_flush:
                    self._styled_print(buf_label, buf_msg)
                self._console.print("[dim]---[/dim]")

    # ── Internal ──────────────────────────────────────────────────

    def _styled_print(self, label: str, msg: str) -> None:
        """Print one styled line. Caller must hold no lock OR hold _lock."""
        style = _style_for(label)
        self._console.print(f"[{style}]\\[{label}][/{style}] {msg}")


# ── Module-level singleton + UI thread ────────────────────────────────

_router = TerminalRouter(console)
threading.Thread(target=_router._ui_loop, daemon=True).start()

# ── Channel registry ──────────────────────────────────────────────────

_channels: dict[str, ToolChannel] = {}
_channels_lock = threading.Lock()


def register(label: str) -> ToolChannel:
    """Register (or retrieve) a ToolChannel for the given label.
    Idempotent: same label → same ToolChannel."""
    with _channels_lock:
        if label not in _channels:
            _channels[label] = ToolChannel(label, _router)
        return _channels[label]
