"""
trace.py — Always-on debug trace system.

Thread-local TraceBuffer captures non-deterministic events (LLM calls,
retrieval results, user interactions) during tool execution. One file
per component per batch, written to tests/debug/{session_ts}/.

Usage:
    from src.harness.trace import (
        TraceBuffer, set_current_trace, get_current_trace,
        clear_current_trace, with_trace,
    )
    trace = TraceBuffer("PTO", firm="Best Buy", batch_idx=0)
    set_current_trace(trace)
    try:
        ...  # LLM calls auto-capture via llm.py instrumentation
    finally:
        trace.flush_to_disk(debug_dir)
        clear_current_trace()
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


# ── Thread-local storage ─────────────────────────────────────────────────

_trace_local = threading.local()


def set_current_trace(trace: TraceBuffer) -> None:
    _trace_local.current = trace


def get_current_trace() -> TraceBuffer | None:
    return getattr(_trace_local, "current", None)


def clear_current_trace() -> None:
    _trace_local.current = None


def with_trace(parent_trace: TraceBuffer | None, fn):
    """Wrap fn so child thread inherits parent's TraceBuffer."""
    def wrapper(*args, **kwargs):
        set_current_trace(parent_trace)
        try:
            return fn(*args, **kwargs)
        finally:
            clear_current_trace()
    return wrapper


# ── TraceBuffer ──────────────────────────────────────────────────────────

@dataclass
class TraceBuffer:
    component: str                          # "Sekei", "PTO", "PUMBA", "PTECA"
    firm: str | None = None
    batch_idx: int | None = None
    events: list[dict] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def record_llm_call(
        self,
        messages: list[dict],
        response: str,
        model: str,
        label: str = "",
    ) -> None:
        """Append LLM call event. Thread-safe."""
        with self._lock:
            self.events.append({
                "type": "llm_call",
                "label": label,
                "model": model,
                "messages": messages,
                "response": response,
            })

    def record_retrieval(
        self,
        batch_result,
    ) -> None:
        """Append retrieval event with full chunk text. Thread-safe.

        batch_result: PTOBatchResult (imported late to avoid circular).
        """
        with self._lock:
            chunks = []
            for i, cwn in enumerate(batch_result.top_chunks):
                chunks.append({
                    "rank": i,
                    "node_id": cwn.node.node_id,
                    "score": cwn.score,
                    "text": cwn.node.text,
                    "metadata": dict(cwn.node.metadata),
                })
            self.events.append({
                "type": "retrieval",
                "batch_id": batch_result.batch_id,
                "method_log": {
                    "hyde_good": batch_result.method_log.hyde_good,
                    "hyde_bad": batch_result.method_log.hyde_bad,
                    "metadata_filters": batch_result.method_log.metadata_filters,
                },
                "chunks": chunks,
                "runner_up_count": len(batch_result.runner_ups),
            })

    def record_user_interaction(
        self,
        question: str,
        answer: str,
    ) -> None:
        with self._lock:
            self.events.append({
                "type": "user_interaction",
                "question": question,
                "answer": answer,
            })

    def flush_to_disk(self, debug_dir: Path) -> Path:
        """Write all events to a markdown file. Returns filepath."""
        debug_dir.mkdir(parents=True, exist_ok=True)
        filename = self._build_filename()
        filepath = debug_dir / filename

        with open(filepath, "w") as f:
            f.write(self._render_markdown())

        return filepath

    def _build_filename(self) -> str:
        firm_slug = self.firm.replace(" ", "_") if self.firm else ""
        if self.batch_idx is not None:
            return f"{firm_slug}_{self.component}_batch{self.batch_idx}.md"
        elif firm_slug:
            return f"{firm_slug}_{self.component}.md"
        else:
            return f"{self.component}.md"

    def _render_markdown(self) -> str:
        """Render all events as LLM-readable markdown."""
        lines: list[str] = []

        # Header
        title = self.component
        if self.firm:
            title += f" — {self.firm}"
        if self.batch_idx is not None:
            title = f"{self.component} Batch {self.batch_idx} — {self.firm}"
        lines.append(f"# {title}")
        lines.append("")

        llm_counter = 0
        for event in self.events:
            etype = event["type"]

            if etype == "retrieval":
                lines.append("---")
                lines.append("")
                lines.append("## Retrieval")
                ml = event["method_log"]
                lines.append(f"**HyDE good:** {ml['hyde_good']}")
                lines.append(f"**HyDE bad:** {ml['hyde_bad']}")
                lines.append(
                    f"**Chunks returned:** {len(event['chunks'])} "
                    f"| **Runner-ups:** {event['runner_up_count']}"
                )
                lines.append("")

                for chunk in event["chunks"]:
                    meta = chunk["metadata"]
                    lines.append(
                        f"### Chunk {chunk['rank']} — "
                        f"{chunk['node_id'][:12]}... "
                        f"(score: {chunk['score']:.3f})"
                    )
                    lines.append(
                        f"**File:** {meta.get('file_name', '?')} "
                        f"| **Section:** {meta.get('section', '?')} "
                        f"| **Type:** {meta.get('chunk_type', '?')}"
                    )
                    lines.append("")
                    lines.append(chunk["text"])
                    lines.append("")

            elif etype == "llm_call":
                llm_counter += 1
                label = event["label"] or "(unlabeled)"
                lines.append("---")
                lines.append("")
                lines.append(
                    f"## LLM Call {llm_counter}: {label}"
                )
                lines.append(f"**Model:** {event['model']}")
                lines.append("")
                lines.append("### Prompt")
                for msg in event["messages"]:
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        lines.append(f"**[{role}]**")
                        lines.append(content)
                    elif isinstance(content, list):
                        lines.append(f"**[{role}]**")
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    lines.append(block["text"])
                                elif block.get("type") == "tool_use":
                                    lines.append(
                                        f"[tool_use: {block['name']}]"
                                    )
                                elif block.get("type") == "tool_result":
                                    lines.append(
                                        f"[tool_result: {block.get('content', '')[:200]}]"
                                    )
                    lines.append("")
                lines.append("### Response")
                lines.append(event["response"])
                lines.append("")

            elif etype == "user_interaction":
                lines.append("---")
                lines.append("")
                lines.append("## User Interaction")
                lines.append(f"**Question:** {event['question']}")
                lines.append(f"**Answer:** {event['answer']}")
                lines.append("")

        return "\n".join(lines)
