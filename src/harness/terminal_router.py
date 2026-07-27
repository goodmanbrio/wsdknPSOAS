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

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.text import Text


# ── Console singleton ─────────────────────────────────────────────────
console = Console()

# ── Thread-local channel overrides ───────────────────────────────────
# Allows tool wrappers to redirect prints from a bare channel (e.g.
# "PMS1") to a firm-specific channel (e.g. "PMS1-Best Buy") for the
# duration of a tool call. Each thread has its own override map.
_thread_overrides = threading.local()


# ── Label styling (spec 10) ───────────────────────────────────────────

LABEL_STYLES = {
    "ORCHESTRATOR": "bold cyan",
    "PMS1":         "bold green",
    "PTECA":        "bold yellow",
    "S2C":          "bold magenta",
    "PUMBA":        "bold red",
    "PMS2":         "bold blue",
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


# ── Override lookup ───────────────────────────────────────────────────

def _get_override(channel: "ToolChannel") -> "ToolChannel | None":
    """Return thread-local override for this channel's label, or None."""
    overrides = getattr(_thread_overrides, "map", {})
    return overrides.get(channel.label)


# ── ToolChannel ───────────────────────────────────────────────────────

class ToolChannel:
    """Bound to a label. Tool code calls .print() and .input() on this."""

    def __init__(self, label: str, router: "TerminalRouter"):
        self.label = label
        self._router = router
        self._log: list[str] = []

    def print(self, msg: str, markdown: bool = False) -> None:
        target = _get_override(self) or self
        target._log.append(msg)
        self._router.print(target.label, msg, markdown=markdown)

    def input(self, question: str, markdown: bool = False) -> str:
        target = _get_override(self) or self
        return self._router.input(target.label, question, markdown=markdown)


# ── TerminalRouter ────────────────────────────────────────────────────

class TerminalRouter:
    """Singleton. Created once at module import. Owns all terminal I/O."""

    def __init__(self, con: Console):
        self._console = con
        self._input_queue: Queue = Queue()
        self._buffer: list[tuple[str, str, bool]] = []
        self._active_input: bool = False
        self._lock = threading.Lock()
        self._is_dead: bool = False
        self._spinner: Status | None = None

    # ── Public: print ─────────────────────────────────────────────

    def print(self, label: str, msg: str, markdown: bool = False) -> None:
        with self._lock:
            if self._active_input:
                self._buffer.append((label, msg, markdown))
            else:
                self._styled_print(label, msg, markdown=markdown)

    # ── Public: input ─────────────────────────────────────────────

    def input(self, label: str, question: str, markdown: bool = False) -> str:
        if self._is_dead:
            return ""
        slot = AnswerSlot()
        self._input_queue.put((label, question, slot, markdown))
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
            label, question, answer_slot, md = self._input_queue.get()

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
            if md:
                label_text = Text(f"[{label}]", style=style)
                self._console.print(Group(label_text, Markdown(question)))
            else:
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
                        _, _, slot, _ = self._input_queue.get_nowait()
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
                for buf_label, buf_msg, buf_md in to_flush:
                    self._styled_print(buf_label, buf_msg, markdown=buf_md)
                self._console.print("[dim]---[/dim]")

    # ── Internal ──────────────────────────────────────────────────

    def _styled_print(self, label: str, msg: str, markdown: bool = False) -> None:
        """Print one styled line. Caller must hold no lock OR hold _lock."""
        style = _style_for(label)
        if markdown:
            # Trailing two spaces before \n = CommonMark hard break.
            # Without this, Markdown() renders \n as a space (softbreak).
            msg = msg.replace("\n", "  \n")
            label_text = Text(f"[{label}]", style=style)
            self._console.print(Group(label_text, Markdown(msg)))
        else:
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


def override_channel(label: str, channel: ToolChannel) -> None:
    """Set a thread-local override: prints/inputs on the channel with
    the given exact label redirect to `channel` for the current thread.

    Use to funnel bare "PMS1" prints into "PMS1-Best Buy" during a
    tool call. Thread-safe — each thread has its own override map.
    """
    if not hasattr(_thread_overrides, "map"):
        _thread_overrides.map = {}
    _thread_overrides.map[label] = channel


def clear_override(label: str) -> None:
    """Remove a thread-local override set by override_channel()."""
    if hasattr(_thread_overrides, "map"):
        _thread_overrides.map.pop(label, None)


def harvest_logs() -> dict[str, list[str]]:
    """Drain and return accumulated logs from all tool channels.

    Returns {label: [msg, ...]} for channels that logged since last
    harvest. Clears all logs including ORCHESTRATOR, but excludes
    ORCHESTRATOR from the returned dict (its output is already
    captured as LLM response content in the transcript).
    """
    with _channels_lock:
        result = {}
        for label, ch in _channels.items():
            if ch._log:
                if label != "ORCHESTRATOR":
                    result[label] = list(ch._log)
                ch._log.clear()
        return result
