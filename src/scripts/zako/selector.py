"""The strict, single-call Bunragman selector boundary for Zako."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol

from src.harness.sysprompts import load_sysprompt
from src.scripts.config import Config
from src.scripts.zako.scanner import Inventory


class SelectionFormatError(ValueError):
    """The model response violates the bare JSON-array contract."""


class Selector(Protocol):
    """Injected selector dependency used by the Zako session."""

    def __call__(self, query: str, inventory: dict[str, Any]) -> str:
        ...


class BunragmanSelector:
    """Production adapter using one raw completion per discovery attempt."""

    def __init__(self, config: Config, backend: Any | None = None) -> None:
        from src.scripts.llm import get_zako_bunragman_llm

        self._backend = backend or get_zako_bunragman_llm(config)
        self._system_prompt = load_sysprompt(
            "zako_bunragman", config.zako_bunragman_profile
        )

    def __call__(self, query: str, inventory: dict[str, Any]) -> str:
        request = {"query": query, "inventory": inventory}
        prompt = json.dumps(request, ensure_ascii=False)
        return self._backend.complete(
            prompt,
            system_prompt=self._system_prompt,
            label="ZAKO-Bunragman",
        )


def parse_selection(raw: str, candidate_ids: set[str]) -> list[str]:
    """Parse and validate one strict raw Bunragman selection response."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SelectionFormatError("selection is not valid JSON") from exc

    if not isinstance(parsed, list) or any(
        not isinstance(item, str) for item in parsed
    ):
        raise SelectionFormatError("selection must be a JSON array of strings")

    selected: list[str] = []
    seen: set[str] = set()
    for identifier in parsed:
        if identifier not in candidate_ids or identifier in seen:
            continue
        seen.add(identifier)
        selected.append(identifier)
    return selected


def selector_from_callable(
    selector: Callable[[str, dict[str, Any]], str],
) -> Selector:
    """Document the callable shape while keeping test doubles lightweight."""
    return selector
