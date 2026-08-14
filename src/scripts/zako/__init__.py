"""Standalone Zako discovery public API."""

from __future__ import annotations

from typing import Any

from src.scripts.zako.scanner import Inventory, ScanError
from src.scripts.zako.selector import Selector
from src.scripts.zako.session import FAILURE, ZakoChannel, ZakoSession

DiscoveryResult = tuple[str, dict[str, list[str]]] | str


def zako_discover(
    query: str,
    config: Any,
    *,
    channel: ZakoChannel,
    selector: Selector | None = None,
) -> DiscoveryResult:
    """Run one blocking standalone Zako discovery session."""
    session = ZakoSession(
        query=query,
        config=config,
        channel=channel,
        selector=selector,
    )
    return session.run()


__all__ = [
    "DiscoveryResult",
    "FAILURE",
    "Inventory",
    "ScanError",
    "ZakoChannel",
    "ZakoSession",
    "zako_discover",
]
