"""Run standalone Zako against the real ingested Packs corpus.

This is an explicit live runner. It is not collected as a pytest test and
may make a real LLM request.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scripts.config import Config
from src.scripts.zako import zako_discover


DEFAULT_QUERY = (
    "Compare LITE and COHR competitive positions in AI optical transceivers, "
    "including market growth, product exposure, and key risks."
)


class TerminalChannel:
    def print(self, message: str, markdown: bool = False) -> None:
        print("\n" + message)

    def input(self, question: str, markdown: bool = False) -> str | None:
        return input("\n" + question + "\n> ")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Zako against data/files_ingested/Packs."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=DEFAULT_QUERY,
        help="Research query to use for source discovery.",
    )
    args = parser.parse_args()

    result = zako_discover(
        args.query,
        Config(),
        channel=TerminalChannel(),
    )

    print("\nRESULT:")
    if isinstance(result, str):
        print(result)
        return

    query, sources = result
    print(f"Query: {query}")
    for source, files in sources.items():
        print(f"\n{source} ({len(files)} files)")
        for path in files:
            print(f"  - {path}")


if __name__ == "__main__":
    main()
