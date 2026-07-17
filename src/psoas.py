#!/usr/bin/env python3
"""
psoas.py — PSOAS entry point.

Usage:
    python psoas.py                              # interactive REPL
    python psoas.py "Chart Best Buy margins"     # first query pre-loaded
"""

import sys
from pathlib import Path

# Ensure project root (parent of src/) is on sys.path so `from src.xxx` works.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.harness.agent_loop import run_harness  # noqa: E402


def main():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    run_harness(first_query=query)


if __name__ == "__main__":
    main()
