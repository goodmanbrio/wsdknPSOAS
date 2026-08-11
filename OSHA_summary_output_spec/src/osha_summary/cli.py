"""Command-line entrypoint for one answer-sheet summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from .summary import summarize_answer_sheet


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("summary budget must be positive")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize one synthesized research answer sheet."
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Exact original main user query.",
    )
    parser.add_argument(
        "--answer-sheet",
        type=Path,
        help="Path to the upstream synthesized Markdown answer sheet.",
    )
    parser.add_argument(
        "--summary-budget",
        type=_positive_integer,
        required=True,
        help="Assigned Ssummary token budget.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    answer_sheet: str | None = None
    if args.answer_sheet is not None:
        try:
            answer_sheet = args.answer_sheet.read_text(encoding="utf-8")
        except FileNotFoundError:
            answer_sheet = None

    summary = summarize_answer_sheet(
        args.query,
        answer_sheet,
        args.summary_budget,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
