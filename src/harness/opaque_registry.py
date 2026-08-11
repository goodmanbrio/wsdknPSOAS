"""
opaque_registry.py — In-memory key-value store for large tool outputs.

Prevents context bloat by giving the orchestrator LLM opaque handles
($var_N) instead of full data.

Usage:
    from src.harness.opaque_registry import registry
    handle = registry.store(stencil_dict, "Best Buy stencil")  # "$var_1"
    data = registry.resolve("$var_1")
"""

from __future__ import annotations

import threading
from typing import Any


class OpaqueRegistry:
    def __init__(self):
        self._counter: int = 0
        self._registry: dict[str, dict] = {}
        self._recent: list[tuple[str, str, Any]] = []
        self._lock = threading.RLock()

    def store(self, data: Any, description: str) -> str:
        with self._lock:
            self._counter += 1
            handle = f"$var_{self._counter}"
            self._registry[handle] = {
                "data": data,
                "description": description,
            }
            self._recent.append((handle, description, data))
        return handle

    def resolve(self, handle: str) -> Any:
        with self._lock:
            if handle not in self._registry:
                available = self.list_vars()
                raise KeyError(
                    f"Handle {handle} does not exist. Available:\n{available}"
                )
            return self._registry[handle]["data"]

    def list_vars(self) -> str:
        with self._lock:
            if not self._registry:
                return "(no variables stored)"
            lines = []
            for handle, entry in self._registry.items():
                lines.append(f"{handle}: {entry['description']}")
            return "\n".join(lines)

    def preview(self, handle: str) -> str:
        data = self.resolve(handle)
        full = str(data)
        if len(full) <= 500:
            return full
        return full[:500] + "..."

    def dump(self, handle: str) -> str:
        return str(self.resolve(handle))

    def harvest_recent(self) -> list[tuple[str, str, Any]]:
        """Drain and return variables stored since last harvest.

        Returns [(handle, description, data), ...]. Called from
        agent_loop after tool dispatch to enrich the transcript.
        """
        with self._lock:
            batch = list(self._recent)
            self._recent.clear()
            return batch

    def reset(self):
        with self._lock:
            self._counter = 0
            self._registry.clear()
            self._recent.clear()


# ── Singleton ─────────────────────────────────────────────────────────
registry = OpaqueRegistry()
