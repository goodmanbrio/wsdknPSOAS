#!/usr/bin/env python3
"""
psoas.py — PSOAS entry point.

Usage:
    python psoas.py "Chart Best Buy and Amcor gross margins FY2022-2023"
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so `from src.xxx` works.
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from src.harness.agent_loop import run_harness  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Usage: python psoas.py \"<query>\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    run_harness(query)


if __name__ == "__main__":
    main()
